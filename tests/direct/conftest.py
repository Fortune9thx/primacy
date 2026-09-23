"""
Direct-mode test fixtures for PRIMACY.

`direct_vm` and `direct_deploy` are `genlayer-test`'s own pytest fixtures
(from `gltest.direct.pytest_plugin`), no local reimplementation needed.

Contract deploys always target the bundled single-file artifact
(contracts/build/Primacy.deploy.py), never the two-file dev source --
that bundle is what actually gets deployed to Studio Dev, so it is what
must be proven to lint/deploy/behave correctly. Run
`python contracts/build_bundle.py` before running these tests if
Primacy.py or primacy_lib.py changed.

One monkeypatch, never touching contract code: `os.unlink` on a temp file
the WASI mock still holds open via `os.dup2` raises `PermissionError` on
Windows only (harmless on POSIX) -- see genlayer-test-toolchain memory.

Known gap, not a contract bug: gltest direct-mode's `run_nondet` mock only
ever invokes the leader closure and returns its result directly -- it
never invokes validator_fn, so these tests cannot exercise settle_market's
independent-re-derivation/equivalence check end to end. That logic
(primacy_lib.compare_evidence, is_well_formed, the per-venue vote/2-of-3
aggregation it is built from) is instead unit-tested directly in
tests/direct/test_primacy_lib.py with no genlayer import at all. See
docs/architecture.md for the full test-coverage map.
"""
import os
import sys
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
sys.path.insert(0, str(CONTRACTS_DIR))

_orig_unlink = os.unlink


def _safe_unlink(path, *args, **kwargs):
    try:
        return _orig_unlink(path, *args, **kwargs)
    except PermissionError:
        pass


os.unlink = _safe_unlink

CONTRACT_PATH = str(CONTRACTS_DIR / "build" / "Primacy.deploy.py")

TREASURY_HEX = "0x00000000000000000000000000000000000000fe"


def to_hex(addr) -> str:
    """Normalize a create_address()/create_test_addresses() value (a real
    Address, or a raw 20-byte fallback) to a hex string. Address(...) does
    not validate checksum casing on input (re-derives it from the raw
    bytes), so a plain lowercase "0x"+hex fallback round-trips correctly
    through any contract-side Address(address_str).as_hex normalization --
    no need to import the real Address class just to checksum client-side."""
    if hasattr(addr, "as_hex"):
        return addr.as_hex
    if isinstance(addr, (bytes, bytearray)):
        return "0x" + addr.hex()
    return str(addr)
