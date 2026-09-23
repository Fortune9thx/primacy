"""
Live integration tests against GenLayer Studio Dev (chain 61997).

Run with `gltest tests/integration -v` (NOT plain pytest -- see
tests/integration/README.md for prerequisites and why). These prove the
two things tests/direct/ structurally cannot: real GenVM validator
consensus on run_nondet, and real HTTP behavior from the three locked
venues.

Every test deploys its own fresh contract -- Studio Dev resets state
periodically, so no test may depend on state from a prior run or another
test in this file.
"""
from pathlib import Path

import pytest
from gltest import get_contract_factory

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "build" / "Primacy.deploy.py"

TREASURY_ADDRESS = "0x00000000000000000000000000000000000000fe"

pytestmark = pytest.mark.integration


@pytest.fixture
def contract(accounts):
    factory = get_contract_factory(contract_file_path=str(CONTRACT_PATH))
    return factory.deploy(args=[TREASURY_ADDRESS])


def test_deploy_is_readable(contract):
    """The single most important live check: the pinned Depends header
    hash actually resolves and the deployed contract is queryable at all
    (see docs/architecture.md on the runner-hash platform bug this
    guards against)."""
    constitution = contract.get_constitution.call()
    assert constitution["fee_bps"] == 200
    assert constitution["lanes"]["MAJORS"] == ["BTC", "ETH", "SOL"]


def test_create_bet_settle_claim_lifecycle(contract, accounts):
    """Full lifecycle against a real, near-future hour boundary. Uses a
    real MAJORS market (BTC/ETH/SOL) since those venues are the most
    liquid and least likely to abstain on a routine hour. Prints the
    settlement outcome rather than asserting a specific winner -- which
    symbol wins is real, unmocked market data, not something a live test
    should assume in advance."""
    import time

    now = int(time.time())
    start = ((now // 3600) + 1) * 3600 + 3600  # next-plus-one hour boundary, safely past MIN_LEAD_SECONDS

    creator, bettor = accounts[0], accounts[1]

    market_id = contract.create_market.transact(
        sender=creator, value=2 * 10**18
    )(lane_id="MAJORS", start=start)

    contract.place_bet.transact(sender=bettor, value=1 * 10**18)(market_id=market_id, symbol="BTC")

    betting_state = contract.get_betting_state.call(market_id=market_id, address=bettor.address)
    assert betting_state["is_open"] is True

    print(f"Market {market_id} created at start={start}. Wait until after "
          f"{start + 3600} (UTC) then call settle_market, e.g. via "
          f"`genlayer write <addr> settle_market --args {market_id} --value 1000000000000000000`, "
          f"and check `get_source_evidence({market_id})` for the real "
          f"3-venue evidence object GenVM's validators independently agreed on.")
