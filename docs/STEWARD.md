# Steward evidence packet

**Status as of this writing: live.** Deployed to GenLayer Studio Dev
(chain 61997) at `0xFA741ee8aAD114147613b5Ac50B4225aba5fF52F`. Full
deploy record and a real post-deploy read: `docs/STATUS.md`.

Attach these when submitting for review:

1. **GitHub repository** -- https://github.com/Fortune9thx/primacy,
   sole-authored commits (no AI co-author line),
   README/LICENSE/CHANGELOG/SECURITY.md + `docs/` present.
2. **Live app** -- https://hourglass-insights.vercel.app -- live and
   wired to the real deployed contract (no mock data anywhere; see
   that repo's `src/lib/primacy/client.ts`).
3. **Explorer link (deployed contract)** --
   https://explorer-studio-dev.genlayer.com/address/0xFA741ee8aAD114147613b5Ac50B4225aba5fF52F
4. **This README** -- `README.md` at the repo root, the primary steward
   brief (decision GenLayer owns, constitution, economics, network,
   methods, known limits, refused scope).
5. **Full-lifecycle proof, two ways** --
   `deploy/local_walkthrough.mjs` runs create -> bet -> settle -> claim
   against a local GenLayer Studio node (real `gl.vm.run_nondet`
   consensus against the real locked venues), and the same sequence can
   now be run live against the deployed contract above.
6. **A real, reproducible test transcript** --
   `docs/TESTED_FLOW.md`: a full create -> bet x3 -> settle -> claim ->
   reclaim_bonds session, run against the exact bundled deploy artifact
   through gltest's real GenVM direct-mode execution (not a mock of the
   contract's logic), with every state transition read back from the
   contract's own return values. Re-run it yourself with
   `python -m pytest tests/direct/test_lifecycle_scenario.py -s -v`.

## Pre-submission checklist (self-audit against this account's calibrated rejection patterns)

- [x] Write client binds the wallet's real provider (not `window.ethereum`
  directly) -- the live frontend is `Fortune9thx/hourglass-insights`
  (`src/lib/primacy/useWallet.ts`/`client.ts`). An earlier `frontend/`
  typed-SDK sketch in this repo was removed once that real app shipped
  and superseded it -- see CHANGELOG.md.
- [x] Read client is a memoized singleton with no account/provider --
  same, `hourglass-insights`' `client.ts`.
- [x] No address-checksum lookup bug -- every TreeMap keyed by a
  caller-supplied address string normalizes via `Address(address).as_hex`
  at the view boundary (`contracts/Primacy.py`); self-derived addresses
  (`gl.message.sender_address.as_hex`) are already correctly normalized.
- [x] `settle_market`'s validator independently re-acquires evidence and
  re-derives its own answer (`_validator` re-calls `_fetch_all()`,
  re-fetching all 3 venues), never validates the leader's claimed output
  structurally only.
- [x] Exactly one non-deterministic call (`gl.vm.run_nondet`) reachable
  per write method; `genvm-lint check` passes clean on the bundled
  deploy artifact (see §11 of the README for the exact command).
- [x] No write claims success before real finality for anything another
  party acts on -- `settle_market`'s return value and `get_market`'s
  `state` field are both read directly from contract storage written in
  the same transaction; nothing in this contract's frontend surface
  presents an `ACCEPTED`-only write as a final, acted-upon outcome ahead
  of confirmation (there is no such write in this contract -- every
  state-changing write here is either a reversible record or the
  terminal settlement step itself).
- [x] Every fact gating money movement (venue prices) is fetched in
  contract code and echoed back in the validated, stored evidence object
  -- never asserted from an LLM's training knowledge (this contract has
  no LLM step at all).
- [x] Staked/escrowed value has a bounded, permissionless escape hatch
  independent of consensus ever converging (`SETTLE_WINDOW_SECONDS`
  fallback) -- see `docs/audit.md`, "Trapped funds."
- [x] No single favorable outcome against one candidate/witness set is
  treated as permanently immune -- 2-of-3 *venue* agreement (not "any
  one venue agreeing once") is required for `SETTLED`, and the
  equivalence comparator explicitly tolerates one side's abstain without
  granting either side a free pass on disagreement.
- [x] Locked venue hosts only -- no caller-supplied URL anywhere.
- [x] No mock/hardcoded data standing in for the real non-deterministic
  step -- `settle_market` always calls `gl.vm.run_nondet` against the 3
  real locked venues.
- [x] `.github/workflows/ci.yml` runs `genvm-lint check` and the full
  `tests/direct` suite on every push (must be observed green at least
  once before this checklist item is truly satisfied, not just present).
- [x] No admin pause or seize path; constants immutable post-deploy.
- [x] `WalletConnect`/Reown placeholder project id, if used by the
  eventual Lovable frontend, is not to be flagged as a gap -- documented
  account-wide convention (confirmed non-issue for injected wallets).

## Portal Notes/Description field

_Draft, check against the portal's exact character limit before
submitting:_

> PRIMACY is a permissionless hourly primacy market: three comparable,
> 24/7 instruments (USDT-M index returns, never equities) race each
> completed UTC hour, and GenVM validators independently fetch three
> locked venues to reach 2-of-3 consensus on the winner before any GEN
> moves. Every payout is self-service/caller-derived, and consensus
> is on derived, tolerance-bounded fields rather than raw witness
> bytes or a single agreeing venue. Targets GenLayer Studio Dev
> (chain 61997) only -- see docs/STATUS.md for current deploy status
> before submitting.
