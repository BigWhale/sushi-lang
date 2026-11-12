"""A stand-in for the compiler that records the SHIR strict variable of each compile.

The runner names this file in SUSHI_COMPILER_STUB, and then starts it in place of the
compiler: in a child of the fork-server, or as a fresh process. It adds one line to the
file that SUSHI_STRICT_PROBE_LOG names: the value of SUSHI_SHIR_STRICT (`-` when it is not
set) and the way that the compile started (`forked` or `fresh`). Then it runs the real
compiler with the same arguments.

The fork-server imports the compiler pipeline before it forks, and a fresh process has not
imported it when the stub starts. So the stub reads the way from `sys.modules`.
"""
from __future__ import annotations

import os
import sys

LOG_ENV = "SUSHI_STRICT_PROBE_LOG"
STRICT_ENV = "SUSHI_SHIR_STRICT"


def main() -> int:
    way = "forked" if "sushi_lang.compiler.pipeline" in sys.modules else "fresh"
    with open(os.environ[LOG_ENV], "a", encoding="utf-8") as log:
        log.write(f"{os.environ.get(STRICT_ENV, '-')} {way}\n")
    sys.argv = ["sushic", *sys.argv[1:]]
    from sushi_lang.compiler import main as compile_main
    return compile_main()


if __name__ == "__main__":
    sys.exit(main())
