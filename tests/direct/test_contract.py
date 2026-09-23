"""
Contract-level direct-mode tests for Primacy.py (deployed as the bundled
contracts/build/Primacy.deploy.py -- see conftest.py).

gltest direct-mode's run_nondet mock only ever calls the leader closure
and returns its result directly (gltest/direct/wasi_mock.py:
_handle_run_nondet) -- it never invokes validator_fn, so settle_market's
independent-re-derivation/equivalence check cannot be exercised end to
end here. That logic is proven separately and thoroughly in
test_primacy_lib.py's TestCompareEvidence class with no genlayer import
at all. These tests instead prove: state machine transitions, bond
escrow/return/slash, payout math, pagination, and every UserError guard
that fires deterministically inside a write method -- using mock_web to
feed the leader closure's 9 real HTTP fetches (3 venues x 3 symbols).
"""
import json
import re
from datetime import datetime, timezone

import pytest

from conftest import CONTRACT_PATH, TREASURY_HEX, to_hex

import primacy_lib as lib

LANE_ID = "CRYPTO_EQUITY_PROXIES"
LANE_SYMBOLS = lib.LANES[LANE_ID]

ANCHOR = datetime(2030, 1, 1, tzinfo=timezone.utc)
ANCHOR_EPOCH = int(ANCHOR.timestamp())
assert ANCHOR_EPOCH % lib.HOUR_SECONDS == 0


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _deploy(direct_deploy):
    return direct_deploy(CONTRACT_PATH, TREASURY_HEX)


def _binance_body(open_p, close_p, start):
    return json.dumps([[start * 1000, str(open_p), str(max(open_p, close_p)), str(min(open_p, close_p)),
                         str(close_p), "1", (start + lib.HOUR_SECONDS) * 1000]])


def _bitget_body(open_p, close_p, start):
    return json.dumps({"code": "00000", "data": [[str(start * 1000), str(open_p), str(max(open_p, close_p)),
                                                     str(min(open_p, close_p)), str(close_p), "1"]]})


def _gate_body(open_p, close_p, start):
    return json.dumps([{"t": start, "o": str(open_p), "h": str(max(open_p, close_p)),
                         "l": str(min(open_p, close_p)), "c": str(close_p)}])


_BODY_BUILDERS = {"binance": _binance_body, "bitget": _bitget_body, "gate": _gate_body}


def mock_all_venues(direct_vm, start, closes: dict, open_price=100, venues=lib.VENUES, skip=()):
    """closes: {symbol: close_price}. Registers all venue x symbol URLs so
    every venue independently derives the same winner (uniform data)."""
    for venue in venues:
        for sym in LANE_SYMBOLS:
            if (venue, sym) in skip:
                continue
            url = lib.build_venue_url(venue, sym, start)
            close_p = closes[sym]
            body = _BODY_BUILDERS[venue](open_price, close_p, start)
            direct_vm.mock_web(re.escape(url), {"body": body, "status": 200})


def mock_missing_close(direct_vm, start, venue, symbol):
    url = lib.build_venue_url(venue, symbol, start)
    if venue == "binance":
        body = json.dumps([[start * 1000, "100", "100", "100", None, "1", (start + lib.HOUR_SECONDS) * 1000]])
    elif venue == "bitget":
        body = json.dumps({"data": [[str(start * 1000), "100", "100", "100", None, "1"]]})
    else:
        body = json.dumps([{"t": start, "o": "100", "h": "100", "l": "100", "c": None}])
    direct_vm.mock_web(re.escape(url), {"body": body, "status": 200})


def _open_market(direct_vm, direct_deploy, creator, start=None):
    if start is None:
        start = ANCHOR_EPOCH + lib.HOUR_SECONDS
    direct_vm.warp(_iso(ANCHOR_EPOCH))
    contract = _deploy(direct_deploy)
    direct_vm.sender = creator
    direct_vm.value = lib.CREATE_BOND
    market_id = contract.create_market(LANE_ID, start)
    direct_vm.value = 0
    return contract, market_id, start


# ---------------------------------------------------------------------------
# create_market
# ---------------------------------------------------------------------------

class TestCreateMarket:
    def test_happy_path_and_bond_escrow(self, direct_vm, direct_deploy, direct_alice):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        market = contract.get_market(mid)
        assert market["state"] == "OPEN"
        assert market["creator"].lower() == to_hex(direct_alice).lower()
        assert market["start"] == start
        assert market["create_bond_returned"] is False

    def test_rejects_non_hour_boundary(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.warp(_iso(ANCHOR_EPOCH))
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        direct_vm.value = lib.CREATE_BOND
        with direct_vm.expect_revert("not hour boundary"):
            contract.create_market(LANE_ID, ANCHOR_EPOCH + lib.HOUR_SECONDS + 1)

    def test_rejects_below_min_lead(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.warp(_iso(ANCHOR_EPOCH))
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        direct_vm.value = lib.CREATE_BOND
        # next hour boundary is only 3600s out normally, but if "now" is
        # already inside the last MIN_LEAD_SECONDS before it, must reject.
        near_start = ANCHOR_EPOCH + lib.HOUR_SECONDS
        direct_vm.warp(_iso(near_start - lib.MIN_LEAD_SECONDS + 1))
        with direct_vm.expect_revert("below min lead"):
            contract.create_market(LANE_ID, near_start)

    def test_rejects_unknown_lane(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.warp(_iso(ANCHOR_EPOCH))
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        direct_vm.value = lib.CREATE_BOND
        with direct_vm.expect_revert("unknown lane"):
            contract.create_market("NOT_A_LANE", ANCHOR_EPOCH + lib.HOUR_SECONDS)

    def test_rejects_duplicate_lane_and_start(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.CREATE_BOND
        with direct_vm.expect_revert("duplicate market"):
            contract.create_market(LANE_ID, start)

    def test_rejects_wrong_bond_amount(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.warp(_iso(ANCHOR_EPOCH))
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        direct_vm.value = lib.CREATE_BOND - 1
        with direct_vm.expect_revert("wrong bond amount"):
            contract.create_market(LANE_ID, ANCHOR_EPOCH + lib.HOUR_SECONDS)

    def test_creator_open_market_cap(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.warp(_iso(ANCHOR_EPOCH))
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        for i in range(lib.MAX_OPEN_MARKETS_PER_CREATOR):
            direct_vm.value = lib.CREATE_BOND
            contract.create_market(LANE_ID, ANCHOR_EPOCH + lib.HOUR_SECONDS * (i + 1))
        direct_vm.value = lib.CREATE_BOND
        with direct_vm.expect_revert("creator cap reached"):
            contract.create_market(LANE_ID, ANCHOR_EPOCH + lib.HOUR_SECONDS * (lib.MAX_OPEN_MARKETS_PER_CREATOR + 1))


# ---------------------------------------------------------------------------
# place_bet
# ---------------------------------------------------------------------------

class TestPlaceBet:
    def test_bet_before_start_and_top_up_same_side(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "COIN")
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "COIN")
        pos = contract.get_user_position(mid, to_hex(direct_bob))
        assert pos["symbol"] == "COIN"
        assert pos["amount"] == lib.MIN_BET * 2

    def test_switch_side_rejected(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "COIN")
        direct_vm.value = lib.MIN_BET
        with direct_vm.expect_revert("side locked"):
            contract.place_bet(mid, "MSTR")

    def test_bet_after_start_rejected(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.warp(_iso(start))
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        with direct_vm.expect_revert("betting closed"):
            contract.place_bet(mid, "COIN")

    def test_bet_below_min_rejected(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET - 1
        with direct_vm.expect_revert("below min bet"):
            contract.place_bet(mid, "COIN")

    def test_unknown_symbol_rejected(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        with direct_vm.expect_revert("unknown symbol"):
            contract.place_bet(mid, "BTC")


# ---------------------------------------------------------------------------
# settle_market
# ---------------------------------------------------------------------------

class TestSettleMarket:
    def test_settle_before_end_rejected(self, direct_vm, direct_deploy, direct_alice, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        with direct_vm.expect_revert("not expired"):
            contract.settle_market(mid)

    def test_two_of_three_agree_settles(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "COIN")

        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101})
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        result = contract.settle_market(mid)
        assert result == "SETTLED"
        market = contract.get_market(mid)
        assert market["winner"] == "COIN"
        assert market["state"] == "SETTLED"

    def test_three_different_winners_inconclusive(self, direct_vm, direct_deploy, direct_alice, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        # binance -> MSTR wins, bitget -> COIN wins, gate -> HOOD wins
        closes_by_venue = {
            "binance": {"MSTR": 110, "COIN": 99, "HOOD": 101},
            "bitget": {"MSTR": 95, "COIN": 110, "HOOD": 101},
            "gate": {"MSTR": 95, "COIN": 99, "HOOD": 110},
        }
        for venue, closes in closes_by_venue.items():
            mock_all_venues(direct_vm, start, closes, venues=(venue,))
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        result = contract.settle_market(mid)
        assert result == "INCONCLUSIVE"
        assert contract.get_market(mid)["winner"] is None

    def test_one_vote_two_abstain_inconclusive(self, direct_vm, direct_deploy, direct_alice, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101}, venues=("binance",))
        mock_missing_close(direct_vm, start, "bitget", "COIN")
        mock_missing_close(direct_vm, start, "gate", "COIN")
        # bitget/gate still need the OTHER two symbols mocked so only COIN aborts them
        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101}, venues=("bitget", "gate"),
                         skip={("bitget", "COIN"), ("gate", "COIN")})
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        result = contract.settle_market(mid)
        assert result == "INCONCLUSIVE"

    def test_venue_tie_abstains_but_others_still_settle(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "COIN")
        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101}, venues=("binance", "bitget"))
        mock_all_venues(direct_vm, start, {"MSTR": 105, "COIN": 105, "HOOD": 101}, venues=("gate",))  # tie MSTR/COIN
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        result = contract.settle_market(mid)
        assert result == "SETTLED"
        evidence = contract.get_source_evidence(mid)
        assert evidence["abstain_reason"].get("gate") == "tie"

    def test_missing_close_abstains_that_venue(self, direct_vm, direct_deploy, direct_alice, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101}, venues=("binance", "bitget"))
        mock_missing_close(direct_vm, start, "gate", "MSTR")
        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101}, venues=("gate",),
                         skip={("gate", "MSTR")})
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)
        evidence = contract.get_source_evidence(mid)
        assert evidence["abstain_reason"].get("gate") == "missing_close"

    def test_winner_with_zero_stake_forces_inconclusive_and_refund(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "MSTR")  # bets on MSTR, but COIN will win with zero stake

        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101})
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        result = contract.settle_market(mid)
        assert result == "INCONCLUSIVE"
        evidence = contract.get_source_evidence(mid)
        assert evidence.get("audit_winner") == "COIN"

        direct_vm.sender = direct_bob
        direct_vm.value = 0
        refund = contract.claim_refund(mid)
        assert refund == lib.MIN_BET

    def test_settle_window_fallback_to_inconclusive(self, direct_vm, direct_deploy, direct_alice, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + lib.SETTLE_WINDOW_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        result = contract.settle_market(mid)
        assert result == "INCONCLUSIVE"

    def test_already_settled_rejected(self, direct_vm, direct_deploy, direct_alice, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + lib.SETTLE_WINDOW_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)
        direct_vm.value = lib.SETTLE_BOND
        with direct_vm.expect_revert("already settled"):
            contract.settle_market(mid)

    def test_evidence_stored_matches_accepted_object(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "COIN")
        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101})
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)
        evidence = contract.get_source_evidence(mid)
        assert evidence["status"] == "SETTLED"
        assert evidence["winner"] == "COIN"
        assert evidence["market_id"] == str(mid) if isinstance(mid, str) else str(int(mid))


# ---------------------------------------------------------------------------
# claim / claim_refund / reclaim_bonds / fees
# ---------------------------------------------------------------------------

def _settle_two_bettors(direct_vm, direct_deploy, alice, bob, carl, charlie):
    contract, mid, start = _open_market(direct_vm, direct_deploy, alice)
    direct_vm.sender = bob
    direct_vm.value = 100 * lib.MIN_BET
    contract.place_bet(mid, "COIN")
    direct_vm.sender = carl
    direct_vm.value = 50 * lib.MIN_BET
    contract.place_bet(mid, "COIN")

    mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101})
    direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
    direct_vm.sender = charlie
    direct_vm.value = lib.SETTLE_BOND
    contract.settle_market(mid)
    return contract, mid, start


class TestClaim:
    def test_fee_only_on_settled_with_winners_and_claim_splits_pro_rata(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie, direct_owner
    ):
        # direct_owner plays "carl" here as a distinct third address
        contract, mid, start = _settle_two_bettors(direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner, direct_charlie)
        market = contract.get_market(mid)
        total_pool = market["total_pool"]
        assert total_pool == 150 * lib.MIN_BET

        direct_vm.sender = direct_bob
        direct_vm.value = 0
        payout_bob = contract.claim(mid)
        direct_vm.sender = direct_owner
        direct_vm.value = 0
        payout_carl = contract.claim(mid)

        fee = lib.compute_fee(total_pool)
        distributable = total_pool - fee
        assert payout_bob + payout_carl == distributable
        assert payout_bob == 2 * payout_carl  # bob staked 2x carl

    def test_claim_by_non_owner_rejected(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie, direct_owner):
        contract, mid, start = _settle_two_bettors(direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner, direct_charlie)
        stranger = direct_alice  # alice never bet
        direct_vm.sender = stranger
        direct_vm.value = 0
        with direct_vm.expect_revert("nothing to claim"):
            contract.claim(mid)

    def test_double_claim_rejected(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie, direct_owner):
        contract, mid, start = _settle_two_bettors(direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner, direct_charlie)
        direct_vm.sender = direct_bob
        direct_vm.value = 0
        contract.claim(mid)
        with direct_vm.expect_revert("nothing to claim"):
            contract.claim(mid)

    def test_last_claimant_absorbs_dust(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET + 1  # deliberately not evenly divisible
        contract.place_bet(mid, "COIN")
        direct_vm.sender = direct_owner
        direct_vm.value = lib.MIN_BET + 2
        contract.place_bet(mid, "COIN")

        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101})
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)

        total_pool = contract.get_market(mid)["total_pool"]
        distributable = total_pool - lib.compute_fee(total_pool)

        direct_vm.sender = direct_bob
        direct_vm.value = 0
        p1 = contract.claim(mid)
        direct_vm.sender = direct_owner
        direct_vm.value = 0
        p2 = contract.claim(mid)
        assert p1 + p2 == distributable  # no dust stranded


class TestClaimRefund:
    def test_not_inconclusive_rejected(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = 0
        with direct_vm.expect_revert("not inconclusive"):
            contract.claim_refund(mid)

    def test_refund_exact_stake_no_fee(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = 7 * lib.MIN_BET
        contract.place_bet(mid, "MSTR")
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + lib.SETTLE_WINDOW_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)

        direct_vm.sender = direct_bob
        direct_vm.value = 0
        refund = contract.claim_refund(mid)
        assert refund == 7 * lib.MIN_BET


class TestReclaimBonds:
    def test_create_bond_slashed_on_zero_bets(self, direct_vm, direct_deploy, direct_alice, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + lib.SETTLE_WINDOW_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)

        market_before = contract.get_market(mid)
        assert market_before["create_bond_slashed"] is False
        # anyone (even a non-creator) can trigger the permissionless slash
        direct_vm.sender = direct_charlie
        direct_vm.value = 0
        contract.reclaim_bonds(mid)
        market_after = contract.get_market(mid)
        assert market_after["create_bond_slashed"] is True
        assert market_after["create_bond_returned"] is True

    def test_create_bond_returned_to_creator_when_bets_exist(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "COIN")
        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101})
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)

        direct_vm.sender = direct_alice
        direct_vm.value = 0
        owed = contract.reclaim_bonds(mid)
        assert owed == lib.CREATE_BOND
        assert contract.get_market(mid)["create_bond_returned"] is True

    def test_settle_bond_returned_to_settler(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_bob
        direct_vm.value = lib.MIN_BET
        contract.place_bet(mid, "COIN")
        mock_all_venues(direct_vm, start, {"MSTR": 99, "COIN": 105, "HOOD": 101})
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)

        direct_vm.sender = direct_charlie
        direct_vm.value = 0
        owed = contract.reclaim_bonds(mid)
        assert owed == lib.SETTLE_BOND
        assert contract.get_market(mid)["settle_bond_returned"] is True

    def test_reclaim_before_terminal_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        direct_vm.sender = direct_alice
        direct_vm.value = 0
        with direct_vm.expect_revert("not expired"):
            contract.reclaim_bonds(mid)


# ---------------------------------------------------------------------------
# views / pagination
# ---------------------------------------------------------------------------

class TestViews:
    def test_get_open_markets_paginates(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.warp(_iso(ANCHOR_EPOCH))
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        for i in range(5):
            direct_vm.value = lib.CREATE_BOND
            contract.create_market(LANE_ID, ANCHOR_EPOCH + lib.HOUR_SECONDS * (i + 1))
        page1 = contract.get_open_markets(0, 2)
        assert len(page1) == 2
        page2 = contract.get_open_markets(2, 2)
        assert len(page2) == 2
        assert {m["market_id"] for m in page1} != {m["market_id"] for m in page2}

    def test_get_board_only_open(self, direct_vm, direct_deploy, direct_alice, direct_charlie):
        contract, mid, start = _open_market(direct_vm, direct_deploy, direct_alice)
        board = contract.get_board()
        assert any(m["market_id"] == mid or m["market_id"] == int(mid) for m in board)
        direct_vm.warp(_iso(start + lib.HOUR_SECONDS + lib.SETTLE_WINDOW_SECONDS + 10))
        direct_vm.sender = direct_charlie
        direct_vm.value = lib.SETTLE_BOND
        contract.settle_market(mid)
        board_after = contract.get_board()
        assert len(board_after) == 0

    def test_get_lane_unknown_rejected(self, direct_deploy):
        contract = _deploy(direct_deploy)
        with pytest.raises(Exception):
            contract.get_lane("NOT_A_LANE")

    def test_get_market_unknown_rejected(self, direct_deploy):
        contract = _deploy(direct_deploy)
        with pytest.raises(Exception):
            contract.get_market(999999)
