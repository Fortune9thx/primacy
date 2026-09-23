# Integration tests (live Studio Dev)

`tests/direct/` (96 tests, all green, no network needed) covers every
deterministic guard and state transition, plus the full leader/validator
equivalence comparator as pure-Python unit tests. It cannot prove two
things gltest's direct-mode structurally cannot exercise:

1. **Real GenVM consensus on `gl.vm.run_nondet`** -- direct-mode's WASI
   mock only ever invokes the leader closure once and returns its result
   directly (`gltest/direct/wasi_mock.py:_handle_run_nondet`); it never
   invokes `validator_fn` or simulates a 5-validator round. Whether real
   validators, each independently fetching the same three locked venues,
   actually reach agreement (or correctly disagree) can only be observed
   live.
2. **Real HTTP behavior from Binance/Bitget/Gate** -- direct-mode tests
   feed `gl.nondet.web.get` from `mock_web`, not the real internet. A
   locked venue changing its response shape, rate-limiting, or genuinely
   producing an unclosed candle is only visible against the real network.

## Prerequisites

- `genlayer network set studio-dev` (or the account already configured
  for chain 61997, RPC `https://studio-dev.genlayer.com/api`).
- A **funded** Studio Dev account. Fund it from the Studio account
  selector's faucet droplet. Needs enough GEN to cover `CREATE_BOND` (2),
  `SETTLE_BOND` (1), `MIN_BET` (1) per market exercised, plus real
  transaction fees -- budget at least 10 GEN for a full run.
- `.env` in the repo root (gitignored) with:
  ```
  DEPLOYER_PRIVATE_KEY=0x...
  ```
- Studio Dev **resets state periodically** -- a market created in one run
  will not exist in a later one. Integration tests always deploy a fresh
  contract instance per run; never hardcode a market id or contract
  address from a prior run.

## Running

```bash
gltest tests/integration -v
```

Do **not** run these with plain `pytest` -- `gltest`'s own CLI wires up
`gltest.config.yaml`'s network config and the `accounts` fixture; plain
`pytest` has no RPC endpoint to talk to and every test will error, not
skip, if invoked that way. `tests/integration/test_live_studio_dev.py`
is marked `pytest.mark.integration` for `gltest -m "not integration"`
style filtering if that's ever wired into CI (currently it is not --
CI runs `tests/direct` only, exactly like every other GenLayer project
on this account, since integration tests need a funded key CI does not
have).

## What each test proves, and why it needs a live node

- **A full create -> bet -> settle -> claim lifecycle actually reaches
  `FINISHED_WITH_RETURN` / a queryable state** -- proves the header's
  pinned dependency hash resolves on the real network (see
  `docs/architecture.md` for why this specific hash, not the one
  originally quoted in the build brief, is what's pinned).
- **`settle_market`'s `gl.vm.run_nondet` reaches real validator
  agreement** for a genuinely live, unmocked set of venue fetches --
  the one thing direct-mode cannot prove per item 1 above.
- **A deliberately volatile real market** (functionally equivalent bets
  on more than one symbol in the same lane) exercises the real 2-of-3
  path against real, not manufactured, venue disagreement.

## Known live-network characteristics to expect, not treat as bugs

These are documented platform characteristics from prior GenLayer builds
on this account, not defects in this contract -- see
`docs/architecture.md`'s "known platform limits" section for the full
list and the memory this is drawn from:

- `waitForTransactionReceipt({status: FINALIZED})` can time out on a
  genuinely successful write; check `txExecutionResultName` via
  `getTransaction()` instead of trusting a timeout as failure.
- State-read propagation after a write reports ACCEPTED can lag by
  anywhere from under 15s to several minutes; retry reads with real
  patience (several minutes of budget), not a fixed short window.
- `gen_call` reads can intermittently fail for a genuinely valid,
  deployed contract; cross-check against `getTransaction()` before
  concluding the contract itself is broken.
