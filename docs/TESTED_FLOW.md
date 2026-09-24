# Tested flow

**What this document is:** proof that a full, realistic Primacy session --
one keeper opening an hour, three independent bettors taking different
sides, the hour closing, a second keeper settling it through the real
consensus path, and every payout/bond being claimed -- runs correctly
against the exact contract artifact that would ship to chain
(`contracts/build/Primacy.deploy.py`).

**What this document is not:** a live network transaction. Studio Dev is
confirmed broken for any contract deploy right now -- see `docs/STATUS.md`,
re-checked as recently as today. There is no transaction hash or block
explorer link in this document because none exists yet. Nothing below
should be read as a deploy.

## What actually ran

`tests/direct/test_lifecycle_scenario.py` is a single, narrated pytest
test, run through `gltest`'s direct-mode GenVM execution -- the same
runtime every other test in this repo uses, not a hand-rolled simulation
of the contract's logic. It really deploys `Primacy.py` +
`primacy_lib.py` (bundled), really calls `create_market`,
`place_bet` x3, `settle_market`, `claim`, and `reclaim_bonds` against
that deployed instance, and every assertion below reads the contract's
own return values back -- nothing is asserted independently of what the
contract itself reported.

Venue HTTP responses (Binance/Bitget/Gate candle data) are mocked at the
transport layer only, the same way every other settlement test in this
suite avoids depending on live exchange APIs for a reproducible result.
The code that *parses* those responses and decides the winner --
`primacy_lib.evaluate_venue`, `aggregate_2of3`, `build_evidence` -- is
the real, unmodified contract code, exercised through the real
`gl.vm.run_nondet` leader path `settle_market` uses on any real network.

gltest direct-mode only invokes the leader closure, never the validator
closure, so the independent-re-derivation/equivalence check itself is
proven separately in `tests/direct/test_primacy_lib.py`'s
`TestCompareEvidence`, with no `genlayer` import at all -- pure-Python,
so there is nothing about the GenVM runtime for it to depend on.

## The transcript

Run yourself with:

```bash
python contracts/build_bundle.py
python -m pytest tests/direct/test_lifecycle_scenario.py -s -v
```

Captured 2026-09-24T08:42:23Z, against a freshly rebuilt bundle
(`contracts/build/Primacy.deploy.py`, 38,500 bytes), unmodified output:

```
tests/direct/test_lifecycle_scenario.py::test_full_human_session_create_bet_settle_claim
========================================================================
PRIMACY -- full human-session lifecycle test
contract artifact: C:\Users\HP\Desktop\primacy\contracts\build\Primacy.deploy.py
lane: MAJORS (BTC, ETH, SOL)
========================================================================

[deploy] treasury=0x00000000000000000000000000000000000000fe

[create_market] eve posts 2 GEN create bond, opens hour 2030-03-03T01:00:00Z -> market_id=1
  contract confirms: state=OPEN, lane=MAJORS

[place_bet] bob stakes 12 GEN on BTC
[place_bet] carol stakes 8 GEN on ETH
[place_bet] dave stakes 5 GEN on SOL
  contract confirms: total_pool=25 GEN across 3 bettors

[time] hour closes at 2030-03-03T02:00:00Z; venue closes mocked at the transport layer only: BTC +12%, ETH +4%, SOL +1% across Binance, Bitget and Gate -- primacy_lib's real parser/evaluator runs on each.

[settle_market] frank posts 1 GEN settle bond -> result=SETTLED
  contract confirms: state=SETTLED, winner=BTC
  source evidence: winner=BTC, votes={'binance': 'BTC', 'bitget': 'BTC', 'gate': 'BTC'}

[claim] bob claims 24.5 GEN (sole BTC bettor, so the entire distributable pool: 25 GEN pool - 0.5 GEN fee = 24.5 GEN)

[claim] carol (bet ETH, lost) correctly rejected: UserError('nothing to claim')

[reclaim_bonds] eve reclaims her create bond: 2 GEN
[reclaim_bonds] frank reclaims his settle bond: 1 GEN

========================================================================
RESULT: full create -> bet x3 -> settle -> claim -> reclaim_bonds lifecycle completed against the real bundled contract. Every state transition and payout above was read back from the contract's own return values, not asserted independently.
========================================================================
PASSED

============================== 1 passed in 1.35s ==============================
```

## Full suite, same session

```bash
python -m pytest tests/direct -q
```

```
97 passed in 38.95s
```

(96 pre-existing unit/contract tests + the 1 new lifecycle scenario
above.)

```bash
python contracts/build_bundle.py && genvm-lint check contracts/build/Primacy.deploy.py
```

```
✓ Lint passed (3 checks)
✓ Validation passed
  Contract: Primacy
  Methods: 22 (16 view, 6 write)
```

## Why this, and not a Studio Dev deploy

Because there currently is no working Studio Dev to deploy to --
re-verified today against GenLayer's own unmodified example contract,
not just Primacy's (see `docs/STATUS.md`'s 2026-09-24 update). Faking a
transaction hash or a populated board to make this look more "live"
than it is would be exactly the kind of thing this project has refused
to do everywhere else (`hourglass-insights`' fail-closed client, its
honest empty-state banner, this repo's own refusal to spend GEN against
a confirmed-broken network). This document is the honest version of
"prove it works": real contract code, real GenVM execution, a
real, readable, reproducible transcript -- with no invented network
evidence standing in for a deploy that hasn't happened yet.

When Studio Dev's runner-loading bug is fixed (bar: GenLayer's own
`examples/contracts/llm_erc20.py` deploys cleanly through the Studio Dev
UI), the exact same lifecycle -- `deploy/local_walkthrough.mjs` -- can be
re-run against the live network with a real transaction hash for every
step, and this document will be updated to link them.
