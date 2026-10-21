"""A stand-in for the compiler that prints a chosen diagnostic line and exits 2.

The runner names this file in SUSHI_COMPILER_STUB. The last line of the source
file names the line to print: `# STUB: <stage>, <construct>` prints the stop code CE0155
with that stage and construct, and `# STUB: CE1001` prints that code alone.
"""
from __future__ import annotations

import sys

STOP = ("{path}:1:1: error [CE0155]: the new pipeline does not emit this program yet: "
        "{what}.")


def main() -> int:
    path = sys.argv[1]
    with open(path, encoding="utf-8") as source:
        what = source.read().strip().splitlines()[-1].split("STUB:", 1)[1].strip()
    if what.startswith("CE"):
        print(f"{path}:1:1: error [{what}]: a stub error.", file=sys.stderr)
    else:
        print(STOP.format(path=path, what=what), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
