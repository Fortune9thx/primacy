# Architecture

## Toolchain versions (pinned, no `latest`)

| Component | Version |
|---|---|
| `genlayer-js` (deploy scripts; the real frontend, `hourglass-insights`, pins this separately) | `2.0.0-rc.1` |
| `genlayer` CLI (npm) | `0.40.0-rc.3` |
| `genlayer-py` | `0.19.0rc2` |
| `genlayer-test` (gltest) | `0.30.0rc2` |
| `genvm-linter` (genvm-lint) | `0.11.1rc2` |
| Contract `Depends` header | `py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng` |

### On the Depends hash

The build brief that started this project quoted
`py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` as "the
official Depends header." That hash's runner asset has been removed from
GenLayer's own release hosting -- confirmed independently across
multiple unrelated prior GenLayer projects on this machine, all hitting
an identical `ImportError: unexpected end of memory` / missing-runner-
asset failure for local `genvm-lint`/`gltest` regardless of project or
contract content. This is upstream infrastructure, not a local
toolchain or project-specific problem.

The hash actually pinned in `contracts/Primacy.py` line 1
(`5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng`) was confirmed
live-working on Studio Devnet by two unrelated prior projects on this
account before this one started, and is independently re-confirmed here:
`genvm-lint check` passes cleanly against the pinned RC toolchain above,
and all 96 `tests/direct` tests deploy and execute against it via
`gltest`'s own SDK auto-resolution.

## Single-file deploy constraint

GenVM contracts are deployed as **one source file**. Confirmed
empirically: `genvm-lint check contracts/Primacy.py` (the two-file dev
version, which does `from primacy_lib import (...)`) fails validation
with `Import error: No module named 'primacy_lib'` -- the linter/loader
never resolves a sibling local module, only the single file it was
pointed at.

This project keeps the pure logic (`contracts/primacy_lib.py`: venue
adapters, bps math, 2-of-3 aggregation, the equivalence comparator,
payout math -- everything with zero `genlayer` import) separate from the
GenVM contract (`contracts/Primacy.py`) for one reason: `primacy_lib.py`
can be unit-tested directly with plain `pytest`, no GenVM sandbox, no
gltest deploy, and no dependency on the local toolchain's runner-hash
resolution working at all. This matters because that resolution *has*
broken, repeatedly, across unrelated GenLayer projects on this account
(see the Depends-hash section above) -- pure logic that doesn't need the
sandbox to be provable shouldn't be hostage to whether the sandbox
happens to be working today.

`contracts/build_bundle.py` reconciles the two: it concatenates
`primacy_lib.py` + `Primacy.py` (stripping the dev-only local import)
into `contracts/build/Primacy.deploy.py`, which is the **only** artifact
that is ever linted, tested, or deployed. Run it after editing either
source file:

```bash
python contracts/build_bundle.py
```

It also prints the bundle's byte size against the 52,224-byte deploy-size
ceiling observed on prior GenLayer projects (Bradbury-specific in origin,
treated here as a conservative cross-network budget since Studio Dev's
own limit has not been independently characterized).

## Why `gl.vm.run_nondet`, not `gl.eq_principle.prompt_non_comparative`

PRIMACY's non-deterministic step is "fetch real HTTP data from three
locked venues and derive a winner" -- there is no LLM judgment call
anywhere in `settle_market`. `gl.eq_principle.prompt_non_comparative`
reconciles via GenVM's internal `ExecPromptTemplate`/
`EqNonComparativeValidator` protocol path specifically for LLM output; a
prior GenLayer project on this account confirmed live
(`genlayer-master-audit-prompt` memory, item 66) that a **hand-rolled**
`gl.vm.run_nondet(leader_fn, validator_fn)` where `validator_fn` makes
its own free-standing LLM call and reconciles it in plain Python produces
a real `DETERMINISTIC_VIOLATION` consensus vote on live GenVM, even when
the results actually matched -- because that specific combination (LLM
call inside a hand-rolled validator) isn't the platform-sanctioned
pattern for LLM reconciliation.

That finding is about LLM calls specifically. `gl.vm.run_nondet` with a
hand-rolled `validator_fn` that independently re-fetches HTTP data and
compares derived fields in plain Python **is** the documented,
platform-sanctioned pattern for non-LLM non-determinism (matches the
`WizardOfCoin` official example's `run_nondet_unsafe`/`run_nondet`
leader+validator shape). PRIMACY's `validator_fn` never calls an LLM; it
only ever calls `gl.nondet.web.get` and pure-Python parsing/comparison
functions.

## `gl.nondet.web.get` returns a `Response` object, not a string

Confirmed empirically while building this contract (not previously
documented in this account's accumulated GenLayer memory): `gl.nondet.web.get(url)`
returns an object with `.status` (int), `.headers` (dict), and `.body`
(**bytes**, not `str`) -- calling `.encode()` on the raw return value
(assuming it was already a decoded string) raises
`AttributeError: 'Response' object has no attribute 'encode'`. Discovered
via `gltest` direct-mode: `direct_vm.mock_web(pattern, {"body": "..."})`
wraps the mock body in exactly this `Response` shape before handing it to
the contract, which is presumably faithful to the real GenVM SDK's own
return type (the mock's job is to simulate the real call shape, not
invent a simplified one). `contracts/Primacy.py`'s `settle_market` leader
closure explicitly unwraps `resp.status` (aborting the venue with reason
`non_200` if not 200) and decodes `resp.body` from bytes to `str` before
handing it to `primacy_lib.parse_response_body`, which stays pure and
untouched by this SDK-specific detail.

## Real deterministic wall-clock: `datetime.now(timezone.utc)`, not `gl.message.raw["datetime"]`

Both are documented elsewhere as real, confirmed-safe deterministic
clock sources on some pinned GenVM runtimes (this account's accumulated
GenLayer memory has both). For this specific project, `datetime.now(timezone.utc)`
was chosen because it is the one `gltest` direct-mode's own `vm.warp()`
implementation actually makes testable: reading `gltest/direct/vm.py`'s
`VMContext.activate()` shows it patches `datetime.datetime` with a
warp-aware subclass whose `.now()` returns `vm._datetime`, dynamically,
for the whole test's `with vm.activate():` scope -- but
`_refresh_gl_message()` (the method `vm.warp()` calls to propagate the
new time) never touches `gl.message.raw["datetime"]` at all, only
sender/origin/value/chain_id. A contract reading `gl.message.raw["datetime"]`
would see it frozen at deploy time for the whole test, no matter how many
times `vm.warp()` is called afterward -- making every time-gated
guard (`below min lead`, `betting closed`, `not expired`, the settle-
window fallback) untestable in direct-mode. `datetime.now(timezone.utc)`
has neither problem, and is independently corroborated as production-safe
by this account's own COMPAX v2 audit finding (`genlayer-master-audit-prompt`
memory, item 59): "real deterministic wall-clock exists in contracts...
live-verified in production use."

## Why payouts are self-service (caller-derived), not push-based

The build brief itself flags "Dominion HIGH #1" (payouts via IC-to-IC
`emit_transfer`, not reliably reaching a real EOA) as a bug to avoid, and
separately says "use the Studio-safe transfer path for paying the
caller." This account's own accumulated GenLayer memory
(`genlayer-js-api`, `compax-v2-progress`) documents `_Recipient(Address(x)).emit_transfer(value=...)`
as the real, GenLayer-team-supplied, confirmed-working pattern for a
contract paying a real EOA -- and separately documents that the specific
failure mode is a transfer **to another Intelligent Contract's own
address**, not to a plain EOA, which silently fails to deliver with no
rescue path.

Reconciling both: every payout method here (`claim`, `claim_refund`,
`reclaim_bonds`) resolves its recipient to `gl.message.sender_address` --
the caller receives their own funds by calling the method themselves.
This eliminates the entire "wrong/IC recipient" bug class by
construction, since nobody is ever paid except the transaction's own real
signer, and satisfies "the Studio-safe transfer path for paying the
caller" literally. The one necessary exception is `settle_market`'s
treasury half of the settlement fee, and `reclaim_bonds`' zero-bet
create-bond slash -- both resolve to `self.treasury`, a single `Address`
set once in `__init__` from a constructor argument and never mutated
afterward (no setter method exists). If a deployer passes an
Intelligent Contract's own address as `treasury`, that half of the fee
(and any zero-bet slash) will silently fail to deliver exactly as
documented above -- `deploy/deploy.mjs` requires and validates a plain
EOA for this argument.

## [BLOCKER, 2026-09-23] Studio Dev cannot currently deploy ANY contract

A real deploy attempt (`0x001588dbd3bcaf0bb5ac0de97fdf7e5686dbafc479f3e7e18ab211bdc00be973`,
~0.1 GEN spent) reached `FINALIZED` consensus but
`txExecutionResultName: FINISHED_WITH_ERROR`, payload `"invalid_contract
runner malformed"`. This is confirmed to be `genlayerlabs/genlayer-studio#1757`
("Studio Dev rejects its own v0.3 contract during schema extraction"),
filed 2026-09-04 and still open -- not a bug in this contract, and not
fixable by choosing a different Depends hash: the same error was
independently reproduced (via the free, no-gas `gen_getContractSchemaForCode`
RPC call, which should always be tried before a real `deployContract()`
call) against both this project's pinned hash AND the hash GenLayer's own
current official example contracts use. Both resolve, network-side, to
the identical malformed internal runner id
(`chain:0x0...:d:q805cc3mbb7k055ay5hek4sg80r2s85yyftpzrpq7g50hy1cc45g`)
that #1757 itself reports. Other accounts' unrelated transactions were
finalizing normally at the same time, so this is specifically a
deployment/schema-extraction-path failure, not a general network outage.

**Before any future deploy attempt**: run
`client.getContractSchemaForCode(code)` first (free, instant, no real
transaction) and confirm it succeeds before calling `deployContract()` --
do not spend real GEN on a deploy attempt while this stays unconfirmed.
Local `genvm-lint check`/`gltest` succeeding is NOT evidence the live
node can deploy the same contract; the two have been observed to diverge.

## Storage layout

Every per-market field is a flat `TreeMap[str, PRIMITIVE]` keyed by
`str(market_id)` (or a composite `f"{market_id}:{symbol}"` /
`f"{market_id}:{address_hex}"` key for symbol/address-scoped data), never
a `dataclass`/nested-container value type. This is the deliberately
zero-risk default from this account's own accumulated GenLayer memory
(`genlayer-allow-storage-broken`): TreeMap value-type reliability on live
networks has fluctuated across dependency hashes and dates in prior
projects, while `TreeMap[str, str]`/`TreeMap[str, u256]` have been
reliably readable throughout. The one nested-container field
(`user_market_ids: TreeMap[str, DynArray[str]]`, address -> list of
market ids they've bet on) is only ever written via
`.get_or_insert_default(key).append(...)`, never a bare
`self.field[key] = DynArray[str]()` assignment -- a previously-confirmed
crash pattern (manually constructing a `DynArray` and assigning it into
a `TreeMap` via `__setitem__` fails at runtime even though it passes
static linting) with a confirmed-working fix using the storage
framework's own allocation helper instead.

`Primacy.__init__` only assigns the two scalar fields (`treasury`,
`next_market_id`); every `TreeMap`/`DynArray` field is declared as a
class-level type annotation only, with **no** `= TreeMap()`/`= DynArray()`
bare-init assignment in `__init__` -- matching the official GenLayer
boilerplate's minimal-`__init__` pattern, which is the confirmed fix for
a documented crash where multiple differently-typed `TreeMap` fields with
more than one bare-initialized in the same class crash `gltest`
direct-mode deploy (`AssertionError: Is right the same storage type?`).

## Test coverage map

| Layer | File | What it proves | Needs `genlayer`? | Needs network? |
|---|---|---|---|---|
| Pure logic | `tests/direct/test_primacy_lib.py` (59 tests) | Price parsing, bps math, all 3 venue adapters + every abstain reason, per-venue winner selection, 2-of-3 aggregation, well-formedness, **the full equivalence comparator including "different raw JSON, same votes -> accept"**, overflow guards, parimutuel payout math incl. last-claimant dust | No | No |
| Contract | `tests/direct/test_contract.py` (37 tests) | Every state transition, every `UserError` guard, bond escrow/return/slash, fee split, pagination, evidence storage | Yes (gltest direct-mode) | No |
| Integration | `tests/integration/test_live_studio_dev.py` | Real GenVM validator consensus on `run_nondet`; real HTTP from the 3 locked venues | Yes | Yes (Studio Dev, funded key) |

gltest direct-mode's `run_nondet` mock **only ever invokes the leader
closure** and returns its result directly
(`gltest/direct/wasi_mock.py:_handle_run_nondet`) -- it never invokes
`validator_fn` or simulates a multi-validator round. This is why the
equivalence comparator (the actual thing `validator_fn` is built from) is
proven as pure-Python unit tests in the first row of the table above,
not as a contract-level gltest test -- there is structurally no way to
exercise a real validator disagreement/agreement in direct-mode, on this
contract or any other.

## Known platform limits (development-time findings, this project)

See README.md §14 for the steward-facing summary. This section is the
detailed version for anyone extending the contract:

1. `gl.nondet.web.get` returns `{status, headers, body: bytes}`, not a
   decoded string -- unwrap explicitly (see above).
2. `gl.message.raw["datetime"]` is frozen at deploy time in gltest
   direct-mode, regardless of `vm.warp()` calls -- use
   `datetime.now(timezone.utc)` for any time logic that needs to be
   testable (see above). Not yet independently re-verified live on
   Studio Dev in this project (would need a real multi-hour-spanning
   live test run); flagged here for whoever runs the integration suite
   next.
3. Contracts are single-file deploys -- see "Single-file deploy
   constraint" above.
4. `genvm-lint`'s SDK auto-resolution for this pinned hash currently
   resolves to a `genvm-manager` release around `v0.6.0-rc5`/`rc6`
   depending on which tool (`genvm-lint` vs `gltest`) resolves it and
   when -- both were observed to succeed against the same pinned hash
   during this project's development, so this is not treated as a
   version-pinning risk, just noted in case a future run resolves to a
   materially different build and something regresses.
