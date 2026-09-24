# Changelog

## Unreleased

- Initial implementation: `contracts/primacy_lib.py` (pure logic) +
  `contracts/Primacy.py` (GenVM contract), bundled to
  `contracts/build/Primacy.deploy.py` for deploy.
- 96 `tests/direct` tests (59 pure-Python `primacy_lib` unit tests + 37
  `gltest` direct-mode contract tests), all passing.
- `genvm-lint check` clean against the bundled deploy artifact.
- Live-network integration test scaffold (`tests/integration/`) --
  documented runbook, not yet executed against a real funded account.
- Typed SDK (`frontend/src/lib/primacy/`) + read-only agent API
  (`frontend/app/api/`).
- Deploy script (`deploy/deploy.mjs`) targeting Studio Dev (chain 61997).
- Full steward documentation set: `README.md`, `docs/architecture.md`,
  `docs/audit.md`, `docs/STEWARD.md`.
- Real frontend shipped and live: `Fortune9thx/hourglass-insights` ->
  https://hourglass-insights.vercel.app, fail-closed with no mock data
  anywhere. Removed this repo's own `frontend/` typed-SDK sketch (the
  real app fully supersedes it, and its stale TypeScript was failing
  this repo's own CI once published -- deleting unused, broken code
  instead of patching a sketch nothing depends on).
- Deploy attempted 2026-09-23, failed on the network side: a real
  transaction (`0x001588db...`) reached FINALIZED but
  FINISHED_WITH_ERROR (`invalid_contract runner malformed`). Bisected
  the root cause precisely: Studio Dev's runner-loading path currently
  rejects any contract over ~305 bytes, confirmed against GenLayer's own
  official example contract too -- not a bug in this bundle. Full
  evidence in `docs/STATUS.md` (new).
- `deploy/local_walkthrough.mjs` (new): full create -> bet -> settle ->
  claim lifecycle script against a local GenLayer Studio node, so the
  protocol can be proven end to end without touching the broken network.
- Not yet deployed to a live network -- `deploy/deployments.json` is
  still empty; do not attempt another Studio Dev deploy until
  `docs/STATUS.md`'s re-check confirms the platform bug is fixed.
- Re-checked Studio Dev 2026-09-24: still broken, confirmed fresh
  against GenLayer's own unmodified example contract; the error
  signature changed (`runner malformed` -> `runner absent`) but the
  platform is not deployable. Logged as an update in `docs/STATUS.md`
  rather than a rewrite, so the original bisection evidence stays
  intact.
- Added `tests/direct/test_lifecycle_scenario.py` and
  `docs/TESTED_FLOW.md` (new): a single narrated test that runs a full
  realistic session -- one keeper opens an hour, three independent
  bettors take different sides, a second keeper settles through the
  real consensus path, the winner and both bond-payers claim -- against
  the exact bundled deploy artifact via gltest's real GenVM direct-mode
  execution, with a captured, reproducible transcript. 97/97 tests
  passing, `genvm-lint check` clean, both re-run fresh for this.
- **Deployed live to Studio Dev (chain 61997)**, 2026-09-24:
  `0xFA741ee8aAD114147613b5Ac50B4225aba5fF52F`
  (`0x04d21c74...c97e98b35`, `FINALIZED`/`FINISHED_WITH_RETURN`), verified
  with a real `get_constitution` read immediately after. The earlier
  "Studio Dev is broken above ~305 bytes" conclusion was wrong -- root
  cause was a comment block `contracts/build_bundle.py` inserted between
  the Depends header and the contract's real content, which GenVM's
  runner-comment parser on live Studio Dev rejects. Found by locating an
  unrelated, larger, already-deployed contract on the network sharing
  the identical Depends hash, diffing its source against ours, and
  confirming the fix with both a free probe and a real paid deploy
  before committing to it. `docs/STATUS.md` keeps the full prior
  diagnosis, marked superseded, alongside the correction and the real
  deploy record. Also fixed a real bug in `deploy/deploy.mjs`'s own
  address-parsing (`tx.txDataDecoded.contractAddress` is never populated
  on live responses; the real field is `tx.data.contract_address`).
