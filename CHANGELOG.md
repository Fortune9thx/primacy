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
