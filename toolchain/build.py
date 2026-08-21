#!/usr/bin/env python3
"""Build every toolchain tool into toolchain/bin/.

Run from anywhere: ./toolchain/build.py. The build is manual and always
recompiles; the compiler's incremental cache keeps a repeat run cheap.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS = {
    "slib-info": "src/slib_info.sushi",
}

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent


def main() -> int:
    bin_dir = ROOT / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, src in TOOLS.items():
        source = ROOT / src
        out = bin_dir / name
        print(f"building {name} from toolchain/{src}")
        result = subprocess.run([str(REPO / "sushic"), str(source), "-o", str(out)])
        if result.returncode != 0:
            print(f"build failed for {name}", file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
