# Deploy status

**Live on GenLayer Studio Dev (chain 61997).**

| | |
|---|---|
| Contract address | `0xFA741ee8aAD114147613b5Ac50B4225aba5fF52F` |
| Deploy tx | `0x04d21c7400f0721eb992426a0eb205a3e4363e42a28ebb991e55827c97e98b35` |
| Status | `FINALIZED` / `FINISHED_WITH_RETURN` |
| Explorer | https://explorer-studio-dev.genlayer.com/address/0xFA741ee8aAD114147613b5Ac50B4225aba5fF52F |
| Deployed | 2026-09-24 |

Verified live with a real read immediately after deploy:

```
$ genlayer call 0xFA741ee8aAD114147613b5Ac50B4225aba5fF52F get_constitution
{
  bps_tol: 2,
  create_bond_wei: '2000000000000000000',
  fee_bps: 200,
  ...
  lanes: { CRYPTO_EQUITY_PROXIES: [ 'MSTR', 'COIN', 'HOOD' ], MAJORS: [ 'BTC', 'ETH', 'SOL' ] },
  treasury: '0xC6E6d3b2acCaECeCeB40Ad4bD3dF123DDCB4e537',
  venues: [ 'binance', 'bitget', 'gate' ]
}
```

## The real root cause (and correcting an earlier wrong conclusion)

Every deploy attempt before this one failed with `invalid_contract
runner malformed` / `invalid_contract runner absent`. That was real --
every probe and transaction quoted in the history below genuinely
happened and genuinely failed. But the conclusion drawn from it, stated
plainly in this file for most of this build's life, was **wrong**:
"Studio Dev is broken for any contract over ~305 bytes." It is not.

The actual cause was in `contracts/build_bundle.py`: it inserted a
5-line comment block (`# AUTO-GENERATED...`, `# Source:...`, a
`# --- begin ... ---` marker) directly between the `# { "Depends": ...
}` header and the rest of the file. GenVM's runner-comment parser on
live Studio Dev (`v0.3.0-rc7`) cannot handle that -- it needs real
content immediately after the Depends line. `genvm-lint` (a different,
local implementation) never caught this because it doesn't reproduce
that specific parsing behavior.

This was found, not guessed: another Intelligent Contract, unrelated to
this project, was found live on Studio Dev via the explorer (real
recent transactions, real method calls, real `FINISHED_WITH_RETURN`
results) using the **identical** Depends hash, at a **larger** size
(51,805 bytes vs. Primacy's 38,500). That single fact ruled out the
hash and ruled out raw size as the cause, which the entire diagnosis
below had converged on. Fetching that contract's actual source via
`genlayer code <address>` showed the real difference: its Depends
header is followed immediately by its module docstring, with no
comment block in between. Stripping the same block from Primacy's
bundle and testing (first for free via schema-check, then for real
via a paid `deployContract()` call) confirmed it immediately --
`FINISHED_WITH_RETURN`, both leader and validator `SUCCESS`.

**Fix**: `contracts/build_bundle.py` no longer emits anything between
the header line and the real content. See its own comment for the
exact constraint.

The correction matters more than the mistake: the original diagnosis
mistook a bug in this project's own tooling for a platform-wide GenLayer
outage, including treating GenLayer's own example contract's failure
(real, and still worth investigating separately -- see below) as
confirmation rather than a second, independent data point that needed
its own scrutiny. The fix was to go find a *counter-example* -- a
contract that actually works right now -- rather than continuing to
accumulate more failures of the same kind.

## What is still true from the earlier diagnosis, and what isn't

**Still true**: every quoted probe and transaction below genuinely
returned the errors shown. `genlayerlabs/genlayer-studio#1757`
(GenLayer's own filed issue) is real, and GenLayer's own
`examples/contracts/llm_erc20.py` did fail identically every time it
was tested this session, using GenLayer's own pinned hash. That
contract may have the exact same leading-comment problem, or a
different one -- it was not re-tested with a stripped header, since
the point of that test was Primacy's own bundle, not GenLayer's example.
That remains a real, open, separately worth-filing observation about
GenLayer's own example contract, not evidence about Studio Dev as a
whole.

**Not true, and retracted**: the "~305-byte platform-wide ceiling,"
the claim that "no meaningful contract fits under the threshold," and
the instruction not to attempt further deploys. All three are
superseded by the live deployment above.

## Full prior diagnosis (kept for the record, superseded by the above)

### Attempt A -- smallest official-style contract, same Depends hash

A minimal single-field `Hello` contract (302 bytes, identical
`py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng` Depends
hash Primacy uses), submitted to the free, no-gas `gen_getContractSchemaForCode`
RPC call:

```
Result: OK
{"ctor":{"params":[],"kwparams":{}},"methods":{"get_greeting":{"params":[],"kwparams":{},"readonly":true,"ret":"string"}}}
```

### Attempt B -- Primacy's real bundled contract, unmodified (with the since-removed comment block)

`contracts/build/Primacy.deploy.py` (38,500 bytes, same Depends hash,
same call):

```
Result: FAILED
{"kind": "VM_ERROR", "message": "invalid_contract runner malformed"}
genvm_log: runner load -> runner: chain:0x0...:d:q805cc3mbb7k055ay5hek4sg80r2s85yyftpzrpq7g50hy1cc45g, size: 38500
```

At the time, A + B read as "our bundle" per the standard diagnostic (A
works, B fails) -- correctly, as it turned out, though the size-padding
follow-up below pointed away from that conclusion and toward a wrong
one instead.

### Follow-up: isolating size as the (wrongly blamed) variable

The same minimal `Hello` contract's *content* was held fixed and only
padded with a trailing comment to various sizes:

| Padded size (bytes) | Result |
|---|---|
| 302 | OK |
| 306 | **FAIL** (`invalid_contract runner malformed`) |
| 400, 512, 600, 768, 900, 1000 | FAIL |
| 4096, 8192, 12288, 16384, 20480, 24576, 28672, 32768, 38305 | FAIL |

In hindsight this padding was appended as a trailing comment on an
already-complete minimal contract, not inserted between the header and
the content -- a different structural position than Primacy's actual
bug. The correlation with size was real but coincidental to how the
test was constructed, not evidence that size itself was the cause.

### GenLayer's own official example also failed, every time it was tested

Fetched `genlayerlabs/genlayer-studio`'s own live
`examples/contracts/llm_erc20.py`, using its own pinned Depends hash
`py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0`, and
submitted it unmodified -- multiple times across multiple days, always
the same result:

```
Result: FAILED
{"kind": "VM_ERROR", "message": "invalid_contract runner malformed"}  (later: "invalid_contract runner absent")
```

This was treated as confirmation that the platform itself was broken.
It should instead have been treated as a second data point needing its
own investigation -- possibly the same leading-comment issue, possibly
something else in that example file. Not re-tested with this session's
fix; worth checking independently before citing it as still-broken.

### A real, paid deploy attempt also failed the same way (with the bug still present)

Before the fix, a real `deployContract()` call (not the free schema
probe) was made against Studio Dev with a funded account:

- Deploy tx: `0x2fffd4dc6dfec0f45040832d1b091a66ef577254d993306a8cbb85869a23354d`
- Final status: `FINALIZED` / `FINISHED_WITH_ERROR`
- Real fee consumed: `100000000000010352` wei (~0.1 GEN)
- Leader receipt result (base64-decoded): `invalid_contract runner malformed`
- The validator independently reached the same result and voted `agree`

This confirmed the bug reproduced through a real paid transaction, not
just the free probe -- correctly, since the bundle really did have the
bug at that point. It was, again, wrongly read as proof the *platform*
was broken rather than proof this project's *bundle* was.

## Re-deploying

```bash
python contracts/build_bundle.py
node deploy/deploy.mjs   # DEPLOYER_PRIVATE_KEY + TREASURY_ADDRESS in env
```

`deploy/deploy.mjs` had its own bug too: it read the deployed address
from `tx.txDataDecoded.contractAddress`, which is consistently
undefined on live Studio Dev responses. Fixed to read
`tx.data.contract_address` (with the old path kept as a fallback), and
to check `txExecutionResultName` explicitly before declaring success.
