# Security

See `docs/audit.md` for the full threat model (risk -> mitigation ->
leftover, per category: trapped funds, wrong winner, spam markets,
witness mismatch, overflow, side switch, short window).

## Known, disclosed limitations

- **A real deploy attempt on 2026-09-23 failed on the network side, not
  the contract side.** The transaction
  (`0x001588dbd3bcaf0bb5ac0de97fdf7e5686dbafc479f3e7e18ab211bdc00be973`)
  reached `FINALIZED` consensus but `txExecutionResultName:
  FINISHED_WITH_ERROR` with payload `"invalid_contract runner malformed"`
  -- confirmed as the still-open `genlayerlabs/genlayer-studio#1757`
  platform bug, which currently blocks schema extraction and deployment
  for ANY contract on Studio Dev, reproduced independently via the free
  `gen_getContractSchemaForCode` RPC call against two different Depends
  hashes (this contract's pinned one, and the one GenLayer's own current
  official examples use). No address exists yet;
  `deploy/deployments.json` is still empty. Before retrying: re-run the
  free schema-extraction probe and check whether #1757 has been closed --
  see `docs/architecture.md`'s toolchain section.
- **Integration tests have not yet been run against Studio Dev** (see
  `tests/integration/README.md`) -- real GenVM validator consensus on
  `settle_market`'s `gl.vm.run_nondet` call, and real behavior from the
  three locked venues, are proven only by direct-mode tests + pure-Python
  unit tests as of this commit, not by a live run.
- **`BPS_TOL = 2` is a judgment call, not a formally derived bound** --
  see `docs/audit.md`'s "Witness mismatch" section for the accepted,
  fail-closed-direction residual risk.
- **Treasury must be a real EOA, never an Intelligent Contract address**
  -- `deploy/deploy.mjs` validates this at deploy time; see
  `docs/architecture.md`'s "self-service payouts" section for why a
  contract address here would silently fail to receive its half of every
  settlement fee, with no rescue path.
- **A single long-stuck pending transaction can, per prior GenLayer
  platform findings on this account, brick a whole contract's
  readability** -- a known, network-level characteristic, not something
  this contract's design can prevent.

## Reporting

If you find a security issue in this contract, open a GitHub issue on
this repository with a clear reproduction. Do not disclose a live-fund-
affecting exploit publicly before the treasury/creator community has had
a chance to respond.
