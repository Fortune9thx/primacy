# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
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
# --- end primacy_lib.py ---

# --- begin Primacy.py (GenVM contract) ---
#
# PRIMACY -- a permissionless hourly primacy market on GenLayer Studio Dev
# (chain 61997). See README.md for the full steward brief and docs/ for
# the state machine and threat model.
#
# NOTE ON THE DEPENDS HASH: the build brief for this project quoted the
# older, now-dead `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`
# hash. That hash's runner asset has been removed from GenLayer's own
# release hosting (confirmed across multiple prior projects on this
# account -- upstream infra, not a local config problem). The hash pinned
# above is the one confirmed live-working against the pinned RC toolchain
# in this repo (genlayer-test==0.30.0rc2 / genvm-linter==0.11.1rc2 /
# genlayer-py==0.19.0rc2) via `genvm-lint check`, and separately confirmed
# live on Studio Devnet by prior projects on this account. See
# docs/architecture.md for the full toolchain-version table.
import json
from datetime import datetime, timezone

import genlayer as gl
from genlayer.storage import DynArray, TreeMap
from genlayer.types import Address, u256



@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


class Primacy(gl.contract.Contract):
    treasury: Address
    next_market_id: u256

    market_creator: TreeMap[str, str]
    market_lane: TreeMap[str, str]
    market_start: TreeMap[str, u256]
    market_state: TreeMap[str, str]
    market_winner: TreeMap[str, str]
    market_settler: TreeMap[str, str]
    market_total_pool: TreeMap[str, u256]
    market_create_bond_returned: TreeMap[str, u256]
    market_settle_bond_returned: TreeMap[str, u256]
    market_create_bond_slashed: TreeMap[str, u256]
    market_claimed_stake: TreeMap[str, u256]
    market_claimed_amount: TreeMap[str, u256]
    market_evidence_json: TreeMap[str, str]

    lane_start_index: TreeMap[str, str]
    creator_open_count: TreeMap[str, u256]

    position_amount: TreeMap[str, u256]
    position_symbol: TreeMap[str, str]
    position_claimed: TreeMap[str, u256]
    pool_by_symbol: TreeMap[str, u256]

    user_market_ids: TreeMap[str, DynArray[str]]
    all_market_ids: DynArray[str]

    def __init__(self, treasury: str):
        self.treasury = Address(treasury)
        self.next_market_id = u256(1)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _now_unix(self) -> int:
        # datetime.now(timezone.utc) is pinned to the transaction's own
        # deterministic datetime on GenVM (confirmed live, all validators
        # see the identical value) -- see docs/architecture.md. Preferred
        # over gl.message.raw["datetime"] here because it is also the API
        # gltest direct-mode's vm.warp() actually makes testable.
        return int(datetime.now(timezone.utc).timestamp())

    def _page_bounds(self, total: int, cursor: u256, limit: u256) -> tuple:
        lim = int(limit)
        if lim <= 0 or lim > MAX_PAGE_SIZE:
            lim = MAX_PAGE_SIZE
        start = int(cursor)
        if start < 0:
            start = 0
        end = start + lim
        if end > total:
            end = total
        return start, end

    def _market_view(self, mid: str) -> dict:
        lane_id = self.market_lane[mid]
        symbols = LANES[lane_id]
        pools = {sym: int(self.pool_by_symbol.get(f"{mid}:{sym}", u256(0))) for sym in symbols}
        start = int(self.market_start[mid])
        return {
            "market_id": int(mid),
            "creator": self.market_creator[mid],
            "lane_id": lane_id,
            "symbols": list(symbols),
            "start": start,
            "end": start + HOUR_SECONDS,
            "state": self.market_state[mid],
            "winner": self.market_winner[mid] or None,
            "settler": self.market_settler[mid] or None,
            "total_pool": int(self.market_total_pool[mid]),
            "pool_by_symbol": pools,
            "create_bond_returned": bool(int(self.market_create_bond_returned[mid])),
            "create_bond_slashed": bool(int(self.market_create_bond_slashed[mid])),
            "settle_bond_returned": bool(int(self.market_settle_bond_returned[mid])),
        }

    def _finalize(self, mid: str, status: str, winner, settler: str, evidence: dict | None) -> None:
        self.market_state[mid] = status
        self.market_winner[mid] = winner or ""
        self.market_settler[mid] = settler
        if evidence is not None:
            self.market_evidence_json[mid] = json.dumps(evidence, sort_keys=True)

        creator = self.market_creator[mid]
        cur = int(self.creator_open_count.get(creator, u256(0)))
        if cur > 0:
            self.creator_open_count[creator] = u256(cur - 1)

        total_pool = int(self.market_total_pool[mid])
        if status == "SETTLED" and winner is not None and total_pool > 0:
            fee = compute_fee(total_pool)
            settler_fee = fee // 2
            treasury_fee = fee - settler_fee
            if settler_fee > 0:
                _Recipient(Address(settler)).emit_transfer(value=u256(settler_fee))
            if treasury_fee > 0:
                _Recipient(self.treasury).emit_transfer(value=u256(treasury_fee))

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    @gl.public.write.payable
    def create_market(self, lane_id: str, start: u256) -> u256:
        if lane_id not in LANES:
            raise gl.vm.UserError("unknown lane")
        start_i = int(start)
        if not is_hour_boundary(start_i):
            raise gl.vm.UserError("not hour boundary")
        now = self._now_unix()
        if start_i < now + MIN_LEAD_SECONDS:
            raise gl.vm.UserError("below min lead")
        if int(gl.message.value) != CREATE_BOND:
            raise gl.vm.UserError("wrong bond amount")

        lane_start_key = f"{lane_id}:{start_i}"
        if lane_start_key in self.lane_start_index:
            raise gl.vm.UserError("duplicate market")

        creator = gl.message.sender_address.as_hex
        open_count = int(self.creator_open_count.get(creator, u256(0)))
        if open_count >= MAX_OPEN_MARKETS_PER_CREATOR:
            raise gl.vm.UserError("creator cap reached")

        market_id = self.next_market_id
        self.next_market_id = u256(int(market_id) + 1)
        mid = str(market_id)

        self.market_creator[mid] = creator
        self.market_lane[mid] = lane_id
        self.market_start[mid] = start
        self.market_state[mid] = "OPEN"
        self.market_winner[mid] = ""
        self.market_settler[mid] = ""
        self.market_total_pool[mid] = u256(0)
        self.market_create_bond_returned[mid] = u256(0)
        self.market_settle_bond_returned[mid] = u256(0)
        self.market_create_bond_slashed[mid] = u256(0)
        self.market_claimed_stake[mid] = u256(0)
        self.market_claimed_amount[mid] = u256(0)
        self.lane_start_index[lane_start_key] = mid
        self.creator_open_count[creator] = u256(open_count + 1)
        self.all_market_ids.append(mid)

        return market_id

    @gl.public.write.payable
    def place_bet(self, market_id: u256, symbol: str) -> str:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        lane_id = self.market_lane[mid]
        lane_symbols = LANES[lane_id]
        if symbol not in lane_symbols:
            raise gl.vm.UserError("unknown symbol")
        if self.market_state[mid] != "OPEN":
            raise gl.vm.UserError("betting closed")
        start = int(self.market_start[mid])
        now = self._now_unix()
        if now >= start:
            raise gl.vm.UserError("betting closed")
        if int(gl.message.value) < MIN_BET:
            raise gl.vm.UserError("below min bet")

        bettor = gl.message.sender_address.as_hex
        pos_key = f"{mid}:{bettor}"
        existing_symbol = self.position_symbol.get(pos_key, "")
        if existing_symbol != "" and existing_symbol != symbol:
            raise gl.vm.UserError("side locked")

        amount = int(gl.message.value)
        self.position_amount[pos_key] = u256(int(self.position_amount.get(pos_key, u256(0))) + amount)
        self.position_symbol[pos_key] = symbol

        pool_key = f"{mid}:{symbol}"
        self.pool_by_symbol[pool_key] = u256(int(self.pool_by_symbol.get(pool_key, u256(0))) + amount)
        self.market_total_pool[mid] = u256(int(self.market_total_pool[mid]) + amount)

        if existing_symbol == "":
            self.user_market_ids.get_or_insert_default(bettor).append(mid)

        return "ok"

    @gl.public.write.payable
    def settle_market(self, market_id: u256) -> str:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        if int(gl.message.value) != SETTLE_BOND:
            raise gl.vm.UserError("wrong bond amount")

        state = self.market_state[mid]
        if state != "OPEN":
            raise gl.vm.UserError("already settled")

        start = int(self.market_start[mid])
        end = start + HOUR_SECONDS
        lane_id = self.market_lane[mid]
        lane_symbols = LANES[lane_id]
        now = self._now_unix()
        settler = gl.message.sender_address.as_hex

        if now < end:
            raise gl.vm.UserError("not expired")

        if now >= end + SETTLE_WINDOW_SECONDS:
            self._finalize(
                mid,
                "INCONCLUSIVE",
                None,
                settler,
                {"status": "INCONCLUSIVE", "reason": "settle_window_expired", "start": start, "lane_id": lane_id},
            )
            return "INCONCLUSIVE"

        def _fetch_all() -> dict:
            per_venue: dict = {}
            for venue in VENUES:
                candles: dict = {}
                for sym in lane_symbols:
                    url = build_venue_url(venue, sym, start)
                    try:
                        resp = gl.nondet.web.get(url)
                    except Exception:
                        candles[sym] = {"ok": False, "reason": "fetch_error"}
                        continue
                    status = getattr(resp, "status", 200)
                    if status != 200:
                        candles[sym] = {"ok": False, "reason": "non_200"}
                        continue
                    body = getattr(resp, "body", resp)
                    if isinstance(body, (bytes, bytearray)):
                        raw = body.decode("utf-8", errors="ignore")
                    else:
                        raw = body
                    candles[sym] = parse_response_body(venue, raw, start)
                per_venue[venue] = candles
            return build_evidence(mid, lane_id, start, per_venue)

        def _validator(leader_res) -> bool:
            try:
                leader_val = leader_res.calldata
            except AttributeError:
                return False
            if not isinstance(leader_val, dict):
                return False
            mine = _fetch_all()
            return compare_evidence(mine, leader_val, lane_symbols)

        evidence = gl.vm.run_nondet(_fetch_all, _validator)

        status = evidence["status"]
        winner = evidence["winner"]

        if status == "SETTLED" and winner is not None:
            winning_pool = int(self.pool_by_symbol.get(f"{mid}:{winner}", u256(0)))
            if winning_pool == 0:
                evidence = dict(evidence)
                evidence["audit_winner"] = winner
                evidence["winner"] = None
                evidence["status"] = "INCONCLUSIVE"
                self._finalize(mid, "INCONCLUSIVE", None, settler, evidence)
                return "INCONCLUSIVE"
            self._finalize(mid, "SETTLED", winner, settler, evidence)
            return "SETTLED"

        self._finalize(mid, "INCONCLUSIVE", None, settler, evidence)
        return "INCONCLUSIVE"

    @gl.public.write
    def claim(self, market_id: u256) -> u256:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        if self.market_state[mid] != "SETTLED":
            raise gl.vm.UserError("nothing to claim")

        claimant = gl.message.sender_address.as_hex
        pos_key = f"{mid}:{claimant}"
        winner = self.market_winner[mid]
        my_symbol = self.position_symbol.get(pos_key, "")
        my_stake = int(self.position_amount.get(pos_key, u256(0)))
        already = int(self.position_claimed.get(pos_key, u256(0)))
        if my_symbol != winner or my_stake == 0 or already == 1:
            raise gl.vm.UserError("nothing to claim")

        total_pool = int(self.market_total_pool[mid])
        fee = compute_fee(total_pool)
        distributable = total_pool - fee
        winning_pool_total = int(self.pool_by_symbol[f"{mid}:{winner}"])
        claimed_stake_before = int(self.market_claimed_stake[mid])
        claimed_amount_before = int(self.market_claimed_amount[mid])

        payout = compute_claim_payout(
            my_stake, winning_pool_total, distributable, claimed_stake_before, claimed_amount_before
        )

        self.position_claimed[pos_key] = u256(1)
        self.market_claimed_stake[mid] = u256(claimed_stake_before + my_stake)
        self.market_claimed_amount[mid] = u256(claimed_amount_before + payout)

        if payout > 0:
            _Recipient(gl.message.sender_address).emit_transfer(value=u256(payout))

        return u256(payout)

    @gl.public.write
    def claim_refund(self, market_id: u256) -> u256:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        if self.market_state[mid] != "INCONCLUSIVE":
            raise gl.vm.UserError("not inconclusive")

        claimant = gl.message.sender_address.as_hex
        pos_key = f"{mid}:{claimant}"
        my_stake = int(self.position_amount.get(pos_key, u256(0)))
        already = int(self.position_claimed.get(pos_key, u256(0)))
        if my_stake == 0 or already == 1:
            raise gl.vm.UserError("nothing to claim")

        self.position_claimed[pos_key] = u256(1)
        _Recipient(gl.message.sender_address).emit_transfer(value=u256(my_stake))
        return u256(my_stake)

    @gl.public.write
    def reclaim_bonds(self, market_id: u256) -> u256:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        if self.market_state[mid] == "OPEN":
            raise gl.vm.UserError("not expired")

        caller = gl.message.sender_address.as_hex
        creator = self.market_creator[mid]
        settler = self.market_settler[mid]
        total_pool = int(self.market_total_pool[mid])

        did_something = False
        owed_to_caller = 0

        if int(self.market_create_bond_returned[mid]) == 0:
            if total_pool == 0:
                self.market_create_bond_returned[mid] = u256(1)
                self.market_create_bond_slashed[mid] = u256(1)
                _Recipient(self.treasury).emit_transfer(value=u256(CREATE_BOND))
                did_something = True
            elif caller == creator:
                self.market_create_bond_returned[mid] = u256(1)
                owed_to_caller += CREATE_BOND
                did_something = True

        if settler != "" and caller == settler and int(self.market_settle_bond_returned[mid]) == 0:
            self.market_settle_bond_returned[mid] = u256(1)
            owed_to_caller += SETTLE_BOND
            did_something = True

        if not did_something:
            raise gl.vm.UserError("nothing to claim")

        if owed_to_caller > 0:
            _Recipient(gl.message.sender_address).emit_transfer(value=u256(owed_to_caller))

        return u256(owed_to_caller)

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------

    @gl.public.view
    def get_constitution(self) -> dict:
        d = get_constitution_dict()
        d["treasury"] = self.treasury.as_hex
        return d

    @gl.public.view
    def get_config(self) -> dict:
        return {
            "treasury": self.treasury.as_hex,
            "next_market_id": int(self.next_market_id),
            "total_markets": len(self.all_market_ids),
        }

    @gl.public.view
    def get_lanes(self) -> dict:
        return {k: list(v) for k, v in LANES.items()}

    @gl.public.view
    def get_lane(self, lane_id: str) -> list:
        if lane_id not in LANES:
            raise gl.vm.UserError("unknown lane")
        return list(LANES[lane_id])

    @gl.public.view
    def get_market(self, market_id: u256) -> dict:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        return self._market_view(mid)

    @gl.public.view
    def get_markets(self, cursor: u256, limit: u256, state_filter: str) -> list:
        ids = [self.all_market_ids[i] for i in range(len(self.all_market_ids))]
        if state_filter:
            ids = [mid for mid in ids if self.market_state[mid] == state_filter]
        start, end = self._page_bounds(len(ids), cursor, limit)
        return [self._market_view(mid) for mid in ids[start:end]]

    @gl.public.view
    def get_open_markets(self, cursor: u256, limit: u256) -> list:
        return self.get_markets(cursor, limit, "OPEN")

    @gl.public.view
    def get_board(self) -> list:
        ids = [self.all_market_ids[i] for i in range(len(self.all_market_ids))]
        open_ids = [mid for mid in ids if self.market_state[mid] == "OPEN"]
        return [self._market_view(mid) for mid in open_ids[:MAX_PAGE_SIZE]]

    @gl.public.view
    def get_betting_state(self, market_id: u256, address: str) -> dict:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        addr = Address(address).as_hex
        pos_key = f"{mid}:{addr}"
        start = int(self.market_start[mid])
        now = self._now_unix()
        is_open = self.market_state[mid] == "OPEN" and now < start
        return {
            "state": self.market_state[mid],
            "is_open": is_open,
            "seconds_until_close": max(0, start - now),
            "my_symbol": self.position_symbol.get(pos_key, "") or None,
            "my_stake": int(self.position_amount.get(pos_key, u256(0))),
        }

    @gl.public.view
    def get_user_position(self, market_id: u256, address: str) -> dict:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        addr = Address(address).as_hex
        pos_key = f"{mid}:{addr}"
        return {
            "market_id": int(market_id),
            "symbol": self.position_symbol.get(pos_key, "") or None,
            "amount": int(self.position_amount.get(pos_key, u256(0))),
            "claimed": bool(int(self.position_claimed.get(pos_key, u256(0)))),
        }

    @gl.public.view
    def get_user_positions(self, address: str, cursor: u256, limit: u256) -> list:
        addr = Address(address).as_hex
        ids = self.user_market_ids.get(addr, None)
        mids = [ids[i] for i in range(len(ids))] if ids is not None else []
        start, end = self._page_bounds(len(mids), cursor, limit)
        out = []
        for mid in mids[start:end]:
            pos_key = f"{mid}:{addr}"
            out.append({
                "market_id": int(mid),
                "state": self.market_state[mid],
                "symbol": self.position_symbol.get(pos_key, "") or None,
                "amount": int(self.position_amount.get(pos_key, u256(0))),
                "claimed": bool(int(self.position_claimed.get(pos_key, u256(0)))),
            })
        return out

    @gl.public.view
    def get_claimable_markets(self, address: str, cursor: u256, limit: u256) -> list:
        addr = Address(address).as_hex
        ids = self.user_market_ids.get(addr, None)
        mids = [ids[i] for i in range(len(ids))] if ids is not None else []
        claimable = []
        for mid in mids:
            state = self.market_state[mid]
            if state == "OPEN":
                continue
            pos_key = f"{mid}:{addr}"
            already = bool(int(self.position_claimed.get(pos_key, u256(0))))
            if already:
                continue
            my_stake = int(self.position_amount.get(pos_key, u256(0)))
            if my_stake == 0:
                continue
            if state == "SETTLED" and self.position_symbol.get(pos_key, "") != self.market_winner[mid]:
                continue
            claimable.append(mid)
        start, end = self._page_bounds(len(claimable), cursor, limit)
        return [self._market_view(mid) for mid in claimable[start:end]]

    @gl.public.view
    def get_source_evidence(self, market_id: u256) -> dict:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        raw = self.market_evidence_json.get(mid, "")
        if not raw:
            return {}
        return json.loads(raw)

    @gl.public.view
    def get_user_activity(self, address: str, cursor: u256, limit: u256) -> list:
        return self.get_user_positions(address, cursor, limit)

    @gl.public.view
    def get_keeper_stats(self) -> dict:
        total = len(self.all_market_ids)
        now = self._now_unix()
        settleable = 0
        for i in range(total):
            mid = self.all_market_ids[i]
            if self.market_state[mid] != "OPEN":
                continue
            end = int(self.market_start[mid]) + HOUR_SECONDS
            if now >= end:
                settleable += 1
        return {"total_markets": total, "settleable_markets": settleable}

    @gl.public.view
    def get_market_by_lane_start(self, lane_id: str, start: u256) -> int | None:
        key = f"{lane_id}:{int(start)}"
        mid = self.lane_start_index.get(key, "")
        if not mid:
            return None
        return int(mid)
# --- end Primacy.py ---
