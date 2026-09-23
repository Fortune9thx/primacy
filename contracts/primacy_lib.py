"""
PRIMACY pure-Python logic: constants, venue adapters, bps math, 2-of-3
aggregation, and the leader/validator equivalence comparator.

Deliberately has ZERO import of `genlayer`/`gl` so it can be unit-tested
directly with plain pytest, with no GenVM sandbox, no gltest direct-mode
deploy, and no dependency on the local toolchain's runner-hash resolution
working. Primacy.py imports this module and wires it into gl.public
methods and gl.vm.run_nondet leader/validator closures.

This split exists because gltest direct-mode cannot exercise a
run_nondet validator_fn at all (it only ever invokes leader_fn) -- the
independently-reproducible logic validator_fn is built from (parsing,
bps math, 2-of-3 aggregation, the ACCEPT/REJECT comparator) has to be
provable some other way. See docs/architecture.md.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Constitution / constants (frozen; mirrored by Primacy.get_constitution())
# ---------------------------------------------------------------------------

LANES: dict[str, tuple[str, str, str]] = {
    "CRYPTO_EQUITY_PROXIES": ("MSTR", "COIN", "HOOD"),
    "MAJORS": ("BTC", "ETH", "SOL"),
}

VENUES: tuple[str, str, str] = ("binance", "bitget", "gate")

HOUR_SECONDS = 3600
MIN_LEAD_SECONDS = 1800
MIN_BET = 10**18                       # 1 GEN
CREATE_BOND = 2 * 10**18               # 2 GEN
SETTLE_BOND = 1 * 10**18               # 1 GEN
FEE_BPS = 200                          # 2% of settled winning pool
SETTLE_WINDOW_SECONDS = 6 * HOUR_SECONDS
BPS_TOL = 2
MAX_OPEN_MARKETS_PER_CREATOR = 8
MAX_PAGE_SIZE = 50
PRICE_SCALE = 10**18
MAX_RESPONSE_BYTES = 65_536

U256_MAX = 2**256 - 1
I256_MIN = -(2**255)
I256_MAX = 2**255 - 1

INSTRUMENT_LABEL = "USDT-M index return at locked venues for this completed UTC hour."

# Locked venue endpoints -- never accept a caller-supplied URL.
VENUE_HOSTS = {
    "binance": "https://fapi.binance.com/fapi/v1/indexPriceKlines",
    "bitget": "https://api.bitget.com/api/v3/market/candles",
    "gate": "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
}


# ---------------------------------------------------------------------------
# Overflow guards
# ---------------------------------------------------------------------------

def assert_u256(x: int) -> int:
    if not isinstance(x, int) or isinstance(x, bool):
        raise ValueError(f"not an int: {x!r}")
    if x < 0 or x > U256_MAX:
        raise ValueError(f"value out of u256 range: {x}")
    return x


def assert_i256(x: int) -> int:
    if not isinstance(x, int) or isinstance(x, bool):
        raise ValueError(f"not an int: {x!r}")
    if x < I256_MIN or x > I256_MAX:
        raise ValueError(f"value out of i256 range: {x}")
    return x


def safe_mul(a: int, b: int) -> int:
    result = a * b
    assert_u256(result if result >= 0 else -result)
    return result


def safe_add(a: int, b: int) -> int:
    result = a + b
    assert_u256(result)
    return result


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def is_hour_boundary(start: int) -> bool:
    return int(start) % HOUR_SECONDS == 0


def venue_query_params(venue: str, symbol: str, start: int) -> dict:
    """Build the locked query params for one venue+symbol+hour. URL/path are
    always the fixed VENUE_HOSTS constant -- callers never supply a URL."""
    start = int(start)
    end = start + HOUR_SECONDS
    start_ms, end_ms = start * 1000, end * 1000
    pair = f"{symbol}USDT"
    if venue == "binance":
        return {
            "pair": pair,
            "interval": "1h",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1,
        }
    if venue == "bitget":
        return {
            "category": "USDT-FUTURES",
            "symbol": pair,
            "interval": "1H",
            "type": "INDEX",
            "startTime": start_ms,
            "endTime": end_ms - 1,
            "limit": 1,
        }
    if venue == "gate":
        return {
            "contract": f"index_{symbol}_USDT",
            "interval": "1h",
            "from": start,
            "to": end - 1,
        }
    raise ValueError(f"unknown venue: {venue}")


def build_venue_url(venue: str, symbol: str, start: int) -> str:
    host = VENUE_HOSTS[venue]
    params = venue_query_params(venue, symbol, start)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{host}?{query}"


# ---------------------------------------------------------------------------
# Price parsing (no floats anywhere -- GenVM calldata has no float type)
# ---------------------------------------------------------------------------

def parse_price_to_scaled(raw) -> int:
    """Parse a decimal price (str or number) into an int scaled by PRICE_SCALE."""
    s = str(raw).strip()
    if s == "" or s.lower() in ("none", "null"):
        raise ValueError("empty price")
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, ""
    if not whole:
        whole = "0"
    if not whole.isdigit() or (frac and not frac.isdigit()):
        raise ValueError(f"malformed price: {raw!r}")
    frac = (frac + "0" * 18)[:18]
    value = int(whole) * PRICE_SCALE + (int(frac) if frac else 0)
    return -value if neg else value


def compute_bps(open_scaled: int, close_scaled: int) -> int:
    """bps = (close - open) * 10_000 / open, floor-divided, deterministic."""
    if open_scaled <= 0:
        raise ValueError("non-positive open price")
    numerator = safe_mul(close_scaled - open_scaled, 10_000)
    bps = numerator // open_scaled
    return assert_i256(bps)


# ---------------------------------------------------------------------------
# Per-venue candle parsing. Each returns {"ok": True, "open": int, "close": int}
# or {"ok": False, "reason": <short code>}.
# ---------------------------------------------------------------------------

def _abstain(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _ok(open_scaled: int, close_scaled: int) -> dict:
    return {"ok": True, "open": open_scaled, "close": close_scaled}


def parse_binance_candle(body, start: int) -> dict:
    if not isinstance(body, list) or len(body) == 0:
        return _abstain("missing_body")
    row = body[0]
    if not isinstance(row, list) or len(row) < 7:
        return _abstain("malformed_candle")
    open_raw, close_raw, close_time_ms = row[1], row[4], row[6]
    if open_raw is None or close_raw is None:
        return _abstain("missing_close")
    try:
        open_s = parse_price_to_scaled(open_raw)
        close_s = parse_price_to_scaled(close_raw)
    except ValueError:
        return _abstain("decode_fail")
    if open_s <= 0:
        return _abstain("zero_open")
    expected_close_ms = (int(start) + HOUR_SECONDS) * 1000
    if isinstance(close_time_ms, (int, float)) and close_time_ms < expected_close_ms - 1000:
        return _abstain("candle_not_closed")
    return _ok(open_s, close_s)


def parse_bitget_candle(body, start: int) -> dict:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) == 0:
        return _abstain("missing_body")
    row = data[0]
    if not isinstance(row, list) or len(row) < 5:
        return _abstain("malformed_candle")
    open_raw, close_raw = row[1], row[4]
    if open_raw is None or close_raw is None:
        return _abstain("missing_close")
    try:
        open_s = parse_price_to_scaled(open_raw)
        close_s = parse_price_to_scaled(close_raw)
    except ValueError:
        return _abstain("decode_fail")
    if open_s <= 0:
        return _abstain("zero_open")
    return _ok(open_s, close_s)


def parse_gate_candle(body, start: int) -> dict:
    if not isinstance(body, list) or len(body) == 0:
        return _abstain("missing_body")
    row = body[0]
    if not isinstance(row, dict):
        return _abstain("malformed_candle")
    open_raw, close_raw = row.get("o"), row.get("c")
    if open_raw is None or close_raw is None:
        return _abstain("missing_close")
    try:
        open_s = parse_price_to_scaled(open_raw)
        close_s = parse_price_to_scaled(close_raw)
    except ValueError:
        return _abstain("decode_fail")
    if open_s <= 0:
        return _abstain("zero_open")
    return _ok(open_s, close_s)


PARSERS = {
    "binance": parse_binance_candle,
    "bitget": parse_bitget_candle,
    "gate": parse_gate_candle,
}


def parse_response_body(venue: str, raw_text: str | None, start: int) -> dict:
    """Bound response size, decode JSON, dispatch to the venue's parser."""
    import json

    if raw_text is None:
        return _abstain("missing_body")
    if len(raw_text.encode("utf-8", errors="ignore")) > MAX_RESPONSE_BYTES:
        return _abstain("response_too_large")
    try:
        body = json.loads(raw_text)
    except (ValueError, TypeError):
        return _abstain("decode_fail")
    parser = PARSERS.get(venue)
    if parser is None:
        return _abstain("unknown_venue")
    return parser(body, start)


# ---------------------------------------------------------------------------
# Per-venue winner (highest bps among the lane's 3 symbols; exact tie abstains)
# ---------------------------------------------------------------------------

def evaluate_venue(candles: dict) -> tuple:
    """candles: {symbol: candle_result}. Returns (winner_or_None, bps_dict, reason_or_None)."""
    bps: dict[str, int] = {}
    for sym, c in candles.items():
        if not c.get("ok"):
            return None, {}, c.get("reason", "abstain")
        bps[sym] = compute_bps(c["open"], c["close"])
    best = max(bps.values())
    winners = [s for s, v in bps.items() if v == best]
    if len(winners) > 1:
        return None, bps, "tie"
    return winners[0], bps, None


# ---------------------------------------------------------------------------
# 2-of-3 aggregation across venues
# ---------------------------------------------------------------------------

def aggregate_2of3(votes: dict) -> tuple:
    """votes: {venue: symbol_or_None}. Returns (status, winner_or_None)."""
    counts: dict[str, int] = {}
    for v in votes.values():
        if v is None:
            continue
        counts[v] = counts.get(v, 0) + 1
    for sym, n in counts.items():
        if n >= 2:
            return "SETTLED", sym
    return "INCONCLUSIVE", None


# ---------------------------------------------------------------------------
# Canonical evidence object + well-formedness + equivalence comparator
# ---------------------------------------------------------------------------

def build_evidence(market_id: str, lane_id: str, start: int, per_venue_candles: dict) -> dict:
    """per_venue_candles: {venue: {symbol: candle_result}}. Pure, deterministic
    given identical inputs -- the leader and a validator each call this with
    their OWN independently-fetched per_venue_candles."""
    votes: dict[str, str | None] = {}
    abstain_reason: dict[str, str] = {}
    bps: dict[str, dict[str, int]] = {}
    for venue in VENUES:
        winner, venue_bps, reason = evaluate_venue(per_venue_candles[venue])
        votes[venue] = winner
        bps[venue] = venue_bps
        if reason is not None:
            abstain_reason[venue] = reason
    status, winner = aggregate_2of3(votes)
    return {
        "market_id": str(market_id),
        "lane_id": lane_id,
        "start": int(start),
        "votes": votes,
        "abstain_reason": abstain_reason,
        "bps": bps,
        "winner": winner,
        "status": status,
    }


def is_well_formed(evidence, lane_symbols: tuple) -> bool:
    if not isinstance(evidence, dict):
        return False
    required = {"market_id", "lane_id", "start", "votes", "abstain_reason", "bps", "winner", "status"}
    if not required.issubset(evidence.keys()):
        return False
    if evidence["status"] not in ("SETTLED", "INCONCLUSIVE"):
        return False
    winner = evidence["winner"]
    if winner is not None and winner not in lane_symbols:
        return False
    votes = evidence["votes"]
    if not isinstance(votes, dict) or not set(votes.keys()).issubset(set(VENUES)):
        return False
    for v in votes.values():
        if v is not None and v not in lane_symbols:
            return False
    bps = evidence["bps"]
    if not isinstance(bps, dict) or not set(bps.keys()).issubset(set(VENUES)):
        return False
    for venue_bps in bps.values():
        if not isinstance(venue_bps, dict):
            return False
        for sym, val in venue_bps.items():
            if sym not in lane_symbols or not isinstance(val, int) or isinstance(val, bool):
                return False
    # status/winner must actually follow from votes under the same 2-of-3 rule
    expected_status, expected_winner = aggregate_2of3(votes)
    if expected_status != evidence["status"] or expected_winner != winner:
        return False
    return True


def compare_evidence(mine: dict, leader: dict, lane_symbols: tuple) -> bool:
    """The equivalence comparator a validator_fn runs: REJECT malformed leader
    output before any comparison, then ACCEPT iff status/winner match, every
    venue vote matches or one side abstained, and per-symbol bps agree within
    BPS_TOL wherever both sides have a numeric reading for that venue."""
    if not is_well_formed(leader, lane_symbols):
        return False
    if not is_well_formed(mine, lane_symbols):
        return False
    if mine["status"] != leader["status"]:
        return False
    if mine["winner"] != leader["winner"]:
        return False
    for venue in VENUES:
        mv = mine["votes"].get(venue)
        lv = leader["votes"].get(venue)
        if mv is not None and lv is not None and mv != lv:
            return False
        m_bps = mine["bps"].get(venue, {})
        l_bps = leader["bps"].get(venue, {})
        for sym in lane_symbols:
            if sym in m_bps and sym in l_bps:
                if abs(m_bps[sym] - l_bps[sym]) > BPS_TOL:
                    return False
    return True


# ---------------------------------------------------------------------------
# Parimutuel payout math (last claimant absorbs floor-division dust)
# ---------------------------------------------------------------------------

def compute_fee(total_pool: int) -> int:
    return (total_pool * FEE_BPS) // 10_000


def get_constitution_dict() -> dict:
    return {
        "lanes": {k: list(v) for k, v in LANES.items()},
        "venues": list(VENUES),
        "hour_seconds": HOUR_SECONDS,
        "min_lead_seconds": MIN_LEAD_SECONDS,
        "min_bet_wei": MIN_BET,
        "create_bond_wei": CREATE_BOND,
        "settle_bond_wei": SETTLE_BOND,
        "fee_bps": FEE_BPS,
        "settle_window_seconds": SETTLE_WINDOW_SECONDS,
        "bps_tol": BPS_TOL,
        "max_open_markets_per_creator": MAX_OPEN_MARKETS_PER_CREATOR,
        "max_page_size": MAX_PAGE_SIZE,
        "instrument_label": INSTRUMENT_LABEL,
    }


def compute_claim_payout(
    my_stake: int,
    winning_pool_total: int,
    distributable: int,
    claimed_stake_before: int,
    claimed_amount_before: int,
) -> int:
    """Pro-rata payout, floor-divided; if this claim exhausts the winning
    pool's total staked amount, it instead receives the exact remainder so
    floor-division dust never gets permanently stranded in the contract."""
    if winning_pool_total <= 0:
        raise ValueError("zero winning pool")
    is_last = (claimed_stake_before + my_stake) >= winning_pool_total
    if is_last:
        return distributable - claimed_amount_before
    return (my_stake * distributable) // winning_pool_total
