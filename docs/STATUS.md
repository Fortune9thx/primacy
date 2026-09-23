# Deploy status

**Not deployed. Studio Dev is currently unable to load ANY contract larger
than ~305 bytes.** This is a platform bug, confirmed precisely below --
not fixable by shrinking, rewriting, or changing the header of Primacy's
own bundle.

## The evidence

### Attempt A -- smallest official-style contract, same Depends hash

A minimal single-field `Hello` contract (302 bytes, identical
`py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng` Depends
hash Primacy uses), submitted to the free, no-gas `gen_getContractSchemaForCode`
RPC call:

```
Result: OK
{"ctor":{"params":[],"kwparams":{}},"methods":{"get_greeting":{"params":[],"kwparams":{},"readonly":true,"ret":"string"}}}
```

### Attempt B -- Primacy's real bundled contract, unmodified

`contracts/build/Primacy.deploy.py` (38,500 bytes, same Depends hash,
same call):

```
Result: FAILED
{"kind": "VM_ERROR", "message": "invalid_contract runner malformed"}
genvm_log: runner load -> runner: chain:0x0...:d:q805cc3mbb7k055ay5hek4sg80r2s85yyftpzrpq7g50hy1cc45g, size: 38500
```

A + B alone would read as "our bundle" per the standard diagnostic (A
works, B fails). It is not -- see below.

### Follow-up: isolating size as the actual variable

Before concluding "our bundle," the same minimal `Hello` contract's
*content* was held fixed and only padded with a trailing comment to
various sizes, using the identical Depends hash throughout:

| Padded size (bytes) | Result |
|---|---|
| 302 | OK |
| 306 | **FAIL** (`invalid_contract runner malformed`) |
| 400, 512, 600, 768, 900, 1000 | FAIL |
| 4096, 8192, 12288, 16384, 20480, 24576, 28672, 32768, 38305 | FAIL |

The exact boundary was bisected: **302 bytes succeeds, 306 bytes fails,
every size tested above that also fails, all the way up to Primacy's
real 38,500-byte bundle.** The failing `genvm_log` is byte-for-byte
identical in shape across every failing size, including the same
internal runner id (`q805cc3mbb7k055ay5hek4sg80r2s85yyftpzrpq7g50hy1cc45g`)
GenLayer's own filed issue reports.

### Confirmation: GenLayer's own official example fails too

Fetched `genlayerlabs/genlayer-studio`'s own live
`examples/contracts/llm_erc20.py` (2,839 bytes, using **its own pinned
Depends hash**, `py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0`
-- not Primacy's) and submitted it unmodified to the same free
`gen_getContractSchemaForCode` call:

```
Result: FAILED
{"kind": "VM_ERROR", "message": "invalid_contract runner malformed"}
genvm_log: runner load -> runner: chain:0x0...:d:q805cc3mbb7k055ay5hek4sg80r2s85yyftpzrpq7g50hy1cc45g, size: 2839
```

Identical error, identical internal runner id, on GenLayer's own
unmodified example, using GenLayer's own pinned hash. This is exactly
`genlayerlabs/genlayer-studio#1757`'s own reported reproduction --
independently reconfirmed here, plus the ~305-byte boundary that issue
didn't isolate.

## This is NOT the ~52KB GenVM/Bradbury deploy-size wall

A separate, unrelated constraint exists on Bradbury (a different
network): an outer-encoded deploy payload limit around 52-54KB
(`intrinsic gas too low` / `BlockPubdataLimitReached`), documented from
prior GenLayer builds on this account. **That is not what's happening
here.** The Bradbury wall is a real payload-encoding limit that only
bites on large, complex contracts near that size; what's broken on
Studio Dev right now is a runner-loading bug that bites at ~305 bytes --
roughly 170x smaller than the Bradbury wall, on a completely different
network, and confirmed by content-held-constant padding (§ above) to be
about byte count alone, not encoding overhead or contract complexity.
Do not conflate the two, and do not "fix" this by trying Bradbury-style
size trimming -- there is no size Primacy could shrink to that would
matter while this bug is live on Studio Dev.

## Conclusion

This is not Primacy's bundle, its header, its Depends hash, or its
content. **Studio Dev's runner-loading path currently breaks for any
contract over ~305 bytes**, which is far too small for any real,
useful Intelligent Contract -- a single stored string field and one
view method already exceeds it once padded past a few hundred bytes.
Shrinking Primacy's contract cannot fix this; there is no meaningful
contract that fits under the threshold.

Matches, and sharpens, `genlayerlabs/genlayer-studio#1757` ("Studio Dev
rejects its own v0.3 contract during schema extraction," filed
2026-09-04, still open) -- that issue's own reproduction didn't isolate
a size threshold, just that GenLayer's own example contract fails. This
session's bisection adds the precise ~305-byte boundary as sharper
evidence for that same issue.

## What this means for the product right now

- **No contract is deployed.** `deploy/deployments.json` stays empty.
- `VITE_CONTRACT_ADDRESS` stays unset in the frontend.
- The product UI is **live and honestly empty**:
  https://hourglass-insights.vercel.app -- fail-closed by construction
  (no mock data anywhere, see that repo's `src/lib/primacy/client.ts` and
  `AppShell.tsx`), showing a "Contract not deployed on Studio Next
  (61997)" banner, zeroed stats, and every write disabled until a real
  live contract exists.
- **Do not spend GEN on Studio Dev again until studio-dev's own UI can
  successfully deploy `genlayerlabs/genlayer-studio`'s own
  `examples/contracts/llm_erc20.py` unmodified** -- that is the cleanest
  bar for "the platform bug is fixed," verifiable for free via the
  Studio Dev web UI's own deploy flow with no wallet/GEN required to
  check. Re-run the free `getContractSchemaForCode` probe below first in
  any case; if it still fails above ~305 bytes, nothing has changed.
- For exercising the full contract lifecycle in the meantime without
  touching the broken live network at all, see
  `deploy/local_walkthrough.mjs` and its own header comment for running
  create → bet → settle → claim against a local GenLayer Studio
  instance.

## Re-checking this later

```js
const { createClient } = require("genlayer-js");
const { studioDevnet } = require("genlayer-js/chains");
const client = createClient({ chain: studioDevnet });
const code = require("fs").readFileSync("contracts/build/Primacy.deploy.py", "utf-8");
client.getContractSchemaForCode(code).then(
  (s) => console.log("FIXED -- schema OK:", JSON.stringify(s).slice(0, 200)),
  (e) => console.log("still broken:", e.message.slice(0, 200)),
);
```

If this ever prints "FIXED", re-run `contracts/build_bundle.py`, then
`node deploy/deploy.mjs` with a funded account, and update this file
with the real result.
