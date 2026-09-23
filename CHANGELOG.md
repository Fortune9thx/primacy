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
- Not yet deployed to a live network -- `deploy/deployments.json` is
  still empty; README's live demo/contract address fields are
  placeholders pending a real deploy.
