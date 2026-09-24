# Threat model

Each row: risk -> mitigation -> leftover (what is *not* fully closed, and
why that residual risk is accepted rather than hidden).

## Trapped funds

**Risk**: GEN sent into the contract (bonds, bets) becomes permanently
unrecoverable because a state transition it depends on never fires.

**Mitigation**:
- `settle_market` has a **permissionless, bounded escape hatch**: if
  called after `SETTLE_WINDOW_SECONDS` (6h) past the hour's end and the
  market is still `OPEN`, it deterministically flips to `INCONCLUSIVE`
  without requiring any consensus round to succeed -- no dependency on
  `gl.vm.run_nondet` ever converging. This is a *different* liveness
  question from "who can call settle_market" (anyone, permissionlessly,
  already) -- "permissionlessly retriable" is not the same claim as
  "guaranteed to eventually converge," and this project's own accumulated
  GenLayer memory documents a real steward rejection on a prior project
  for exactly this gap.
- `INCONCLUSIVE` markets refund every position's exact stake via
  `claim_refund`, self-service, no admin action needed.
- `reclaim_bonds` is permissionlessly callable by anyone for the
  zero-bet slash path (pays a fixed treasury constant, not the caller),
  and by the creator/settler specifically for their own bond return --
  both only require the market be terminal, not any particular outcome.
- A "winner symbol with zero stake, pool > 0" result (see below) is
  itself redirected to `INCONCLUSIVE` + refunds rather than a payout with
  no eligible claimant, which would otherwise strand the whole pool.

**Leftover**: if a market's `SETTLE_WINDOW_SECONDS` fallback branch is
never called by anyone at all (nobody has any GEN-value incentive to,
since the settle bond return is the only reward and nobody is
economically forced to claim it), the market sits `OPEN` forever with
its bets still in the pool, refundable only once someone eventually does
call `settle_market`. This is accepted: `settle_market` remains
callable by literally anyone forever (no expiry on the *ability* to
call it, only a floor on when it must deterministically resolve), and
the 1 GEN settle-bond return is a real, if modest, standing incentive.

## Wrong winner

**Risk**: the contract records a symbol as the winner when it did not
actually have the highest completed-hour return.

**Mitigation**:
- Real GenVM consensus (`gl.vm.run_nondet`) requires independent
  validators to each fetch the same 3 locked venues and derive the same
  answer (within tolerance) before the transaction commits at all -- a
  single dishonest/wrong leader cannot unilaterally decide the outcome.
- Locked host+path constants for all 3 venues (`primacy_lib.VENUE_HOSTS`)
  -- no caller-supplied URL anywhere in the settlement path, closing the
  most common evidence-manipulation vector this account's accumulated
  GenLayer memory documents (a caller choosing which "evidence" gets
  fetched).
- `is_well_formed` rejects a malformed/self-inconsistent leader result
  *before* any comparison -- a leader can't shortcut consensus by
  returning a `status`/`winner` pair that doesn't actually follow from
  its own `votes`.
- The equivalence comparator (`compare_evidence`) is on *derived* fields
  with explicit, disclosed tolerance (§6 of the README) -- not on raw
  bytes -- so it can't be defeated by trivial timing-based body
  differences, and can't be gamed by a validator claiming disagreement
  over a difference that's actually within normal data-source noise.

**Leftover**: if all 3 locked venues themselves report a materially wrong
price for the same hour (a venue-side data error, not a contract-level
manipulation), the contract will faithfully and correctly reach consensus
on the wrong answer -- this is a real-world data-integrity risk inherent
to any oracle-style design and is not specific to this contract; it is
mitigated only by using 3 independent, reputable venues rather than 1.

## Spam markets

**Risk**: a creator opens many markets with no intention of ever
attracting real bets, degrading the board's usefulness or exhausting
some shared resource.

**Mitigation**:
- `CREATE_BOND` (2 GEN) is real, non-trivial GEN at risk per market.
- `MAX_OPEN_MARKETS_PER_CREATOR` (8) hard-caps how many simultaneously
  `OPEN` markets one creator can have.
- A zero-bet market's create bond is **slashed to treasury**, not
  returned -- creating markets nobody bets on is a real, unrecoverable
  cost, not a free action.
- `lane_start_index` prevents duplicate `(lane, start)` markets, so spam
  can't multiply the same slot.

**Leftover**: a creator with enough GEN can still open 8 simultaneous
markets across different hours/lanes and let all of them expire
bet-free, paying 16 GEN total for the privilege -- accepted as a real,
non-trivial economic cost that scales with abuse, not a free griefing
vector.

## Witness mismatch (different raw data, same real answer)

**Risk**: the leader and a validator fetch genuinely slightly different
raw bytes (a few seconds apart, or a venue's own float-formatting jitter)
and a naive raw-comparison consensus mechanism spuriously disagrees on an
outcome both sides actually agree with in substance.

**Mitigation**: see README §6 and `docs/architecture.md`'s "Why
`gl.vm.run_nondet`" section -- consensus is on derived `status`/`winner`/
per-venue-vote/bps-within-tolerance, never raw bytes.
`tests/direct/test_primacy_lib.py::TestCompareEvidence::test_different_raw_prices_same_votes_accepts`
is a direct regression test for exactly this scenario.

**Leftover**: `BPS_TOL = 2` is a judgment call, not a formally derived
bound -- a genuinely volatile hour near a symbol's decision boundary
could in principle see honest validators land on opposite sides of the
tolerance window purely from fetch-timing luck, producing a real
`INCONCLUSIVE` (safe, refund-everyone) rather than a `SETTLED` outcome.
This is treated as an acceptable, fail-closed-direction outcome, not a
bug: `INCONCLUSIVE` never mis-pays anyone.

## Overflow

**Risk**: a computed value (bps, fee, payout) wraps or otherwise produces
an incorrect result at extreme inputs.

**Mitigation**: `primacy_lib.assert_u256`/`assert_i256`/`safe_mul`/
`safe_add` explicitly bound every arithmetic result to the real
on-chain storage type's valid range (Python's own arbitrary-precision
integers never silently wrap, so this guards against a logically invalid
value being accepted, not a literal wraparound). `compute_bps` calls
`assert_i256` on its result; `safe_mul`/`safe_add` are used wherever a
value could in principle exceed `u256` range from user-supplied bet
amounts compounding.

**Leftover**: none identified at plausible real-world GEN amounts (a
u256 can represent roughly 1.15e59 wei of GEN; exceeding it would require
economically nonsensical inputs).

## Side switch

**Risk**: a bettor changes which symbol they're backing after already
staking, invalidating the "one symbol per wallet per market" pool
integrity the payout math depends on.

**Mitigation**: `place_bet` explicitly rejects (`"side locked"`) any bet
on a different symbol than the caller's existing position in that
market; same-symbol top-ups are the only way to increase an existing
position.
`tests/direct/test_contract.py::TestPlaceBet::test_switch_side_rejected`.

## Short window

**Risk**: a market's betting window (or settlement window) is short
enough to be manipulated or to not give real, informed participants a
fair chance to act.

**Mitigation**: `MIN_LEAD_SECONDS` (30 min) guarantees every market has
a real minimum public window between creation and betting close;
`SETTLE_WINDOW_SECONDS` (6h) is generous relative to any plausible
real-world API-outage duration for the 3 locked venues, minimizing
spurious `INCONCLUSIVE` fallbacks from a transient venue hiccup rather
than a genuine settlement failure.

**Leftover**: none identified -- both windows are fixed constants,
immutable after deploy, not tunable per-market by a creator (which would
reopen a manipulation surface: a creator picking an artificially short
window to disadvantage other bettors).

## `gl.message.sender_address` is not guaranteed to be a human EOA

**Risk**: every payout method (`claim`, `claim_refund`, `reclaim_bonds`)
pays `gl.message.sender_address` specifically because that's the
caller's own address (§7's self-service design). But if any of these
methods is ever invoked indirectly -- a wrapper/aggregator Intelligent
Contract calling `settle_market`/`claim`/etc. via cross-contract
`.emit()` rather than a direct signed transaction from a human wallet --
`gl.message.sender_address` inside that call resolves to the **calling
contract's own address**, not the original human's. A later payout to
that recorded address (the settler's fee half in `_finalize`, or a
`reclaim_bonds` settle-bond return) would then be targeting an
Intelligent Contract, which silently fails to deliver with no rescue
path (the same failure mode documented in §7/docs/architecture.md for
any IC-address payout target).

**Mitigation**: none possible in contract code -- GenVM exposes no
reliable on-chain EOA-vs-contract check to guard against this. This is
a hard requirement on *how* the contract is called, not something
closeable by adding a runtime assertion.

**Leftover, explicitly disclosed rather than papered over**: `claim`
and `claim_refund` are low-stakes here even if triggered this way (the
caller/wrapper contract would simply be unable to retrieve the funds it
routed a call through, not lose anyone else's funds -- a self-inflicted
griefing outcome at worst, since the position itself is keyed to
whatever address the deposit was made from originally). `settle_market`
is the more consequential case: if called via a wrapper contract, the
settler's fee-half payout in `_finalize` and that same address's
`reclaim_bonds` settle-bond return would both silently fail to deliver.
Since `settle_market` is permissionlessly callable by design and pays a
real (if modest) fee/bond incentive, the practical mitigation is
economic, not technical: a rational settler calls directly from their
own wallet to actually receive the incentive, so this mainly protects
against an accidental or adversarial wrapper-contract call pattern
rather than closing a fund-safety hole for a bettor's own stake.
