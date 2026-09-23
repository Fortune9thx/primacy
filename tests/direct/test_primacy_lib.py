"""
Pure-Python unit tests for contracts/primacy_lib.py -- no `genlayer` import,
no GenVM sandbox, no gltest deploy. Runs anywhere plain pytest runs.

This is the primary proof for the leader/validator equivalence logic
(primacy_lib.compare_evidence and everything it's built from), since
gltest direct-mode's run_nondet mock cannot exercise a real validator_fn
at all (see tests/direct/conftest.py's docstring and docs/architecture.md).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "contracts"))

import pytest

import primacy_lib as lib


# ---------------------------------------------------------------------------
# price parsing / bps math
# ---------------------------------------------------------------------------

class TestPriceParsing:
    def test_parses_integer_string(self):
        assert lib.parse_price_to_scaled("100") == 100 * lib.PRICE_SCALE

    def test_parses_decimal_string(self):
        assert lib.parse_price_to_scaled("100.5") == 100 * lib.PRICE_SCALE + 5 * 10**17

    def test_parses_negative(self):
        assert lib.parse_price_to_scaled("-1.5") == -(1 * lib.PRICE_SCALE + 5 * 10**17)

    def test_truncates_excess_decimals(self):
        # 19 fractional digits -> truncated to 18
        v = lib.parse_price_to_scaled("1.1234567890123456789")
        assert v == lib.PRICE_SCALE + 123456789012345678

    def test_rejects_malformed(self):
        with pytest.raises(ValueError):
            lib.parse_price_to_scaled("not-a-number")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            lib.parse_price_to_scaled("")


class TestComputeBps:
    def test_positive_return(self):
        open_s = 100 * lib.PRICE_SCALE
        close_s = 101 * lib.PRICE_SCALE
        assert lib.compute_bps(open_s, close_s) == 100  # +1% = 100 bps

    def test_negative_return(self):
        open_s = 100 * lib.PRICE_SCALE
        close_s = 99 * lib.PRICE_SCALE
        assert lib.compute_bps(open_s, close_s) == -100

    def test_zero_open_raises(self):
        with pytest.raises(ValueError):
            lib.compute_bps(0, 100)

    def test_negative_open_raises(self):
        with pytest.raises(ValueError):
            lib.compute_bps(-1, 100)


class TestOverflowGuards:
    def test_u256_accepts_in_range(self):
        assert lib.assert_u256(0) == 0
        assert lib.assert_u256(lib.U256_MAX) == lib.U256_MAX

    def test_u256_rejects_negative(self):
        with pytest.raises(ValueError):
            lib.assert_u256(-1)

    def test_u256_rejects_too_large(self):
        with pytest.raises(ValueError):
            lib.assert_u256(lib.U256_MAX + 1)

    def test_i256_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            lib.assert_i256(lib.I256_MAX + 1)
        with pytest.raises(ValueError):
            lib.assert_i256(lib.I256_MIN - 1)


# ---------------------------------------------------------------------------
# venue adapters
# ---------------------------------------------------------------------------

class TestParseBinance:
    def test_ok_candle(self):
        body = [[0, "100.0", "105", "95", "101.0", "10", 3600_000]]
        result = lib.parse_binance_candle(body, 0)
        assert result["ok"] is True
        assert result["open"] == 100 * lib.PRICE_SCALE
        assert result["close"] == 101 * lib.PRICE_SCALE

    def test_missing_body(self):
        assert lib.parse_binance_candle([], 0)["reason"] == "missing_body"
        assert lib.parse_binance_candle(None, 0)["reason"] == "missing_body"

    def test_malformed_row(self):
        assert lib.parse_binance_candle([["short"]], 0)["reason"] == "malformed_candle"

    def test_missing_close(self):
        row = [0, "100", "105", "95", None, "10", 3600_000]
        assert lib.parse_binance_candle([row], 0)["reason"] == "missing_close"

    def test_zero_open(self):
        row = [0, "0", "105", "95", "101", "10", 3600_000]
        assert lib.parse_binance_candle([row], 0)["reason"] == "zero_open"

    def test_candle_not_closed(self):
        row = [0, "100", "105", "95", "101", "10", 1000]  # closeTime way before end
        assert lib.parse_binance_candle([row], 0)["reason"] == "candle_not_closed"

    def test_decode_fail_on_bad_price(self):
        row = [0, "not-a-price", "105", "95", "101", "10", 3600_000]
        assert lib.parse_binance_candle([row], 0)["reason"] == "decode_fail"


class TestParseBitget:
    def test_ok_candle(self):
        body = {"data": [["0", "100.0", "105", "95", "101.0", "10"]]}
        result = lib.parse_bitget_candle(body, 0)
        assert result["ok"] is True
        assert result["open"] == 100 * lib.PRICE_SCALE

    def test_missing_body(self):
        assert lib.parse_bitget_candle({}, 0)["reason"] == "missing_body"
        assert lib.parse_bitget_candle({"data": []}, 0)["reason"] == "missing_body"

    def test_missing_close(self):
        body = {"data": [["0", "100", "105", "95", None]]}
        assert lib.parse_bitget_candle(body, 0)["reason"] == "missing_close"


class TestParseGate:
    def test_ok_candle(self):
        body = [{"t": 0, "o": "100.0", "h": "105", "l": "95", "c": "101.0"}]
        result = lib.parse_gate_candle(body, 0)
        assert result["ok"] is True
        assert result["close"] == 101 * lib.PRICE_SCALE

    def test_missing_body(self):
        assert lib.parse_gate_candle([], 0)["reason"] == "missing_body"

    def test_malformed_row(self):
        assert lib.parse_gate_candle(["not-a-dict"], 0)["reason"] == "malformed_candle"

    def test_missing_close(self):
        body = [{"t": 0, "o": "100", "c": None}]
        assert lib.parse_gate_candle(body, 0)["reason"] == "missing_close"


class TestParseResponseBody:
    def test_bounds_response_size(self):
        huge = "x" * (lib.MAX_RESPONSE_BYTES + 1)
        assert lib.parse_response_body("binance", huge, 0)["reason"] == "response_too_large"

    def test_decode_fail_on_bad_json(self):
        assert lib.parse_response_body("binance", "not json", 0)["reason"] == "decode_fail"

    def test_missing_body_on_none(self):
        assert lib.parse_response_body("binance", None, 0)["reason"] == "missing_body"

    def test_unknown_venue(self):
        assert lib.parse_response_body("kraken", "[]", 0)["reason"] == "unknown_venue"

    def test_dispatches_to_correct_parser(self):
        import json
        body = json.dumps([[0, "100", "105", "95", "101", "10", 3600_000]])
        result = lib.parse_response_body("binance", body, 0)
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# per-venue winner + 2-of-3 aggregation
# ---------------------------------------------------------------------------

def _candle(open_p, close_p):
    return {"ok": True, "open": lib.parse_price_to_scaled(str(open_p)), "close": lib.parse_price_to_scaled(str(close_p))}


class TestEvaluateVenue:
    def test_highest_bps_wins(self):
        candles = {"MSTR": _candle(100, 99), "COIN": _candle(100, 105), "HOOD": _candle(100, 101)}
        winner, bps, reason = lib.evaluate_venue(candles)
        assert winner == "COIN"
        assert reason is None
        assert bps["COIN"] > bps["HOOD"] > bps["MSTR"]

    def test_all_negative_least_negative_wins(self):
        candles = {"MSTR": _candle(100, 90), "COIN": _candle(100, 80), "HOOD": _candle(100, 99)}
        winner, _, reason = lib.evaluate_venue(candles)
        assert winner == "HOOD"
        assert reason is None

    def test_exact_tie_abstains(self):
        candles = {"MSTR": _candle(100, 101), "COIN": _candle(100, 101), "HOOD": _candle(100, 99)}
        winner, _, reason = lib.evaluate_venue(candles)
        assert winner is None
        assert reason == "tie"

    def test_any_abstain_propagates(self):
        candles = {"MSTR": _candle(100, 101), "COIN": {"ok": False, "reason": "missing_close"}, "HOOD": _candle(100, 99)}
        winner, bps, reason = lib.evaluate_venue(candles)
        assert winner is None
        assert reason == "missing_close"
        assert bps == {}


class TestAggregate2of3:
    def test_two_agree_settles(self):
        status, winner = lib.aggregate_2of3({"binance": "COIN", "bitget": "COIN", "gate": None})
        assert status == "SETTLED"
        assert winner == "COIN"

    def test_three_different_inconclusive(self):
        status, winner = lib.aggregate_2of3({"binance": "MSTR", "bitget": "COIN", "gate": "HOOD"})
        assert status == "INCONCLUSIVE"
        assert winner is None

    def test_one_vote_two_abstain_inconclusive(self):
        status, winner = lib.aggregate_2of3({"binance": "COIN", "bitget": None, "gate": None})
        assert status == "INCONCLUSIVE"
        assert winner is None

    def test_all_abstain_inconclusive(self):
        status, winner = lib.aggregate_2of3({"binance": None, "bitget": None, "gate": None})
        assert status == "INCONCLUSIVE"
        assert winner is None


# ---------------------------------------------------------------------------
# canonical evidence + well-formedness + equivalence comparator
# ---------------------------------------------------------------------------

LANE = lib.LANES["CRYPTO_EQUITY_PROXIES"]


def _per_venue_all_agree_on_coin():
    candles = {"MSTR": _candle(100, 99), "COIN": _candle(100, 105), "HOOD": _candle(100, 101)}
    return {"binance": candles, "bitget": candles, "gate": candles}


class TestBuildEvidence:
    def test_settled_when_2of3_agree(self):
        ev = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        assert ev["status"] == "SETTLED"
        assert ev["winner"] == "COIN"
        assert ev["votes"] == {"binance": "COIN", "bitget": "COIN", "gate": "COIN"}

    def test_is_well_formed_on_its_own_output(self):
        ev = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        assert lib.is_well_formed(ev, LANE) is True

    def test_malformed_missing_field_rejected(self):
        ev = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        del ev["bps"]
        assert lib.is_well_formed(ev, LANE) is False

    def test_winner_outside_lane_rejected(self):
        ev = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        ev["winner"] = "BTC"
        assert lib.is_well_formed(ev, LANE) is False

    def test_status_inconsistent_with_votes_rejected(self):
        ev = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        ev["status"] = "INCONCLUSIVE"  # votes still say 3x COIN -> inconsistent
        assert lib.is_well_formed(ev, LANE) is False


class TestCompareEvidence:
    def test_identical_evidence_accepts(self):
        mine = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        leader = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        assert lib.compare_evidence(mine, leader, LANE) is True

    def test_different_raw_prices_same_votes_accepts(self):
        """Different raw JSON (slightly different prices from a re-fetch a
        moment later) but the same derived votes/winner/status, within
        BPS_TOL, must still ACCEPT -- this is the whole point of comparing
        derived fields, not raw bodies."""
        mine_candles = {"MSTR": _candle(100, 99), "COIN": _candle(100, 105.01), "HOOD": _candle(100, 101)}
        leader_candles = {"MSTR": _candle(100, 99), "COIN": _candle(100, 105.0), "HOOD": _candle(100, 101)}
        mine = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600,
                                   {"binance": mine_candles, "bitget": mine_candles, "gate": mine_candles})
        leader = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600,
                                     {"binance": leader_candles, "bitget": leader_candles, "gate": leader_candles})
        assert mine["bps"]["binance"]["COIN"] != leader["bps"]["binance"]["COIN"]
        assert lib.compare_evidence(mine, leader, LANE) is True

    def test_bps_beyond_tolerance_rejects(self):
        mine_candles = {"MSTR": _candle(100, 99), "COIN": _candle(100, 110), "HOOD": _candle(100, 101)}
        leader_candles = {"MSTR": _candle(100, 99), "COIN": _candle(100, 105), "HOOD": _candle(100, 101)}
        mine = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600,
                                   {"binance": mine_candles, "bitget": mine_candles, "gate": mine_candles})
        leader = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600,
                                     {"binance": leader_candles, "bitget": leader_candles, "gate": leader_candles})
        assert lib.compare_evidence(mine, leader, LANE) is False

    def test_different_winner_rejects(self):
        mine = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        other_candles = {"MSTR": _candle(100, 110), "COIN": _candle(100, 99), "HOOD": _candle(100, 101)}
        leader = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600,
                                     {"binance": other_candles, "bitget": other_candles, "gate": other_candles})
        assert lib.compare_evidence(mine, leader, LANE) is False

    def test_malformed_leader_rejected_before_comparison(self):
        mine = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600, _per_venue_all_agree_on_coin())
        assert lib.compare_evidence(mine, {"garbage": True}, LANE) is False

    def test_one_side_abstain_still_accepts_if_2of3_matches(self):
        agree = {"MSTR": _candle(100, 99), "COIN": _candle(100, 105), "HOOD": _candle(100, 101)}
        leader = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600,
                                     {"binance": agree, "bitget": agree, "gate": agree})
        # mine: gate abstains (missing close), binance+bitget still agree -> same 2-of-3 outcome
        abstain_candles = dict(agree)
        abstain_candles["COIN"] = {"ok": False, "reason": "missing_close"}
        mine = lib.build_evidence("1", "CRYPTO_EQUITY_PROXIES", 3600,
                                   {"binance": agree, "bitget": agree, "gate": abstain_candles})
        assert lib.compare_evidence(mine, leader, LANE) is True


# ---------------------------------------------------------------------------
# parimutuel payout math
# ---------------------------------------------------------------------------

class TestComputeFee:
    def test_two_percent(self):
        assert lib.compute_fee(1000 * 10**18) == 20 * 10**18


class TestComputeClaimPayout:
    def test_pro_rata_split(self):
        # winning pool 300, distributable 294 (after 2% fee on 300... simplified numbers here)
        payout = lib.compute_claim_payout(
            my_stake=100, winning_pool_total=300, distributable=294,
            claimed_stake_before=0, claimed_amount_before=0,
        )
        assert payout == 98  # 100 * 294 // 300

    def test_last_claimant_absorbs_dust(self):
        # winning_pool_total=3, distributable=10 -> per-unit floor is 3 each, 1 wei dust
        p1 = lib.compute_claim_payout(1, 3, 10, claimed_stake_before=0, claimed_amount_before=0)
        assert p1 == 3
        p2 = lib.compute_claim_payout(1, 3, 10, claimed_stake_before=1, claimed_amount_before=p1)
        assert p2 == 3
        # last claimant: claimed_stake_before + my_stake == winning_pool_total -> gets remainder
        p3 = lib.compute_claim_payout(1, 3, 10, claimed_stake_before=2, claimed_amount_before=p1 + p2)
        assert p3 == 4  # 10 - 6, not floor(1*10/3)=3 -- dust goes to the last claimant
        assert p1 + p2 + p3 == 10

    def test_zero_winning_pool_raises(self):
        with pytest.raises(ValueError):
            lib.compute_claim_payout(1, 0, 10, 0, 0)


# ---------------------------------------------------------------------------
# hour-boundary / URL-building helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_is_hour_boundary(self):
        assert lib.is_hour_boundary(3600) is True
        assert lib.is_hour_boundary(3601) is False
        assert lib.is_hour_boundary(0) is True

    def test_build_venue_url_uses_locked_host(self):
        url = lib.build_venue_url("binance", "COIN", 3600)
        assert url.startswith(lib.VENUE_HOSTS["binance"])
        assert "pair=COINUSDT" in url

    def test_build_venue_url_rejects_unknown_venue(self):
        with pytest.raises(KeyError):
            lib.build_venue_url("kraken", "COIN", 3600)
