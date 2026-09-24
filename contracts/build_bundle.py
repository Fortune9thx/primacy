"""
Bundle contracts/primacy_lib.py + contracts/Primacy.py into a single
deployable file at contracts/build/Primacy.deploy.py.

GenVM contracts are deployed as ONE source file -- confirmed empirically
(genvm-lint check Primacy.py fails with "No module named 'primacy_lib'"
when the contract imports a sibling module). primacy_lib.py is kept
separate from Primacy.py so its pure logic (bps math, adapters, the
equivalence comparator) can be unit-tested directly with plain pytest,
with no genlayer import and no GenVM sandbox involved -- this script is
what reconciles that dev-time split with the single-file deploy
requirement. Always lint/test the BUNDLED output, not the two-file dev
version, since the bundle is what actually ships.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent
LIB_PATH = ROOT / "primacy_lib.py"
CONTRACT_PATH = ROOT / "Primacy.py"
OUT_DIR = ROOT / "build"
OUT_PATH = OUT_DIR / "Primacy.deploy.py"

DEPENDS_RE = re.compile(r'^#\s*\{\s*"Depends"')


def strip_lib_module(text: str) -> str:
    lines = text.split("\n")
    out = []
    for line in lines:
        if line.strip() == "from __future__ import annotations":
            continue
        out.append(line)
    return "\n".join(out)


def strip_contract_module(text: str) -> str:
    lines = text.split("\n")
    out = []
    skip_import_block = False
    for line in lines:
        if DEPENDS_RE.match(line):
            continue
        if line.strip().startswith("from primacy_lib import ("):
            skip_import_block = True
            continue
        if skip_import_block:
            if line.strip() == ")":
                skip_import_block = False
            continue
        out.append(line)
    return "\n".join(out)


def main() -> None:
    header_line = CONTRACT_PATH.read_text(encoding="utf-8").split("\n", 1)[0]
    if not DEPENDS_RE.match(header_line):
        raise SystemExit(f"Primacy.py line 1 is not a Depends header: {header_line!r}")

    lib_src = strip_lib_module(LIB_PATH.read_text(encoding="utf-8"))
    contract_src = strip_contract_module(CONTRACT_PATH.read_text(encoding="utf-8"))

    # Nothing may come between the Depends header and real content -- GenVM's
    # runner-comment parser on live Studio Dev (v0.3.0-rc7) rejects a bundle
    # with comment lines here as "invalid_contract runner malformed", even
    # though genvm-lint accepts it locally and the identical Depends hash
    # works fine for other deployed contracts. Confirmed empirically: the
    # exact same bundle content deploys and executes successfully
    # (FINISHED_WITH_RETURN) with this leading comment block removed, and
    # fails identically every time with it present. Do not reintroduce a
    # comment block directly after header_line. Comments elsewhere in the
    # file (the primacy_lib/Primacy.py section markers below) are fine --
    # only tested as unsafe immediately after the Depends line.
    bundled = "\n".join([
        header_line,
        lib_src.strip("\n"),
        "# --- end primacy_lib.py ---",
        "",
        "# --- begin Primacy.py (GenVM contract) ---",
        contract_src.strip("\n"),
        "# --- end Primacy.py ---",
        "",
    ])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(bundled, encoding="utf-8", newline="\n")
    size = len(bundled.encode("utf-8"))
    print(f"Wrote {OUT_PATH} ({size} bytes)")
    limit = 52_224
    if size > limit:
        print(f"WARNING: {size} bytes exceeds the {limit}-byte deploy-size ceiling")
    else:
        print(f"OK: {size}/{limit} bytes ({size / limit:.1%} of ceiling)")


if __name__ == "__main__":
    main()
