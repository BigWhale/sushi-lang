"""A stand-in for the compiler dumps, for the runner tests of the golden directives.

The runner names this file in SUSHI_COMPILER_STUB, and then starts it in place of the
compiler: in a child of the fork-server, or as a fresh process. It takes each
`--dump-shir PATH` and `--dump-slir PATH` off the command line, writes a dump to PATH, and
runs the real compiler with the other arguments.

The dump is the kind on its first line, then each line of the source that starts with
`fn `. A source that holds `# unstable dump` gets a dump whose word order follows the hash
seed of the process. A source that holds `# no dump` gets no dump.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FLAGS = {"--dump-shir": "shir", "--dump-slir": "slir"}
WORDS = [f"word{i}" for i in range(32)]


def dump(kind: str, source: str) -> str:
    if "# unstable dump" in source:
        return f"{kind}\n{' '.join(set(WORDS))}\n"
    lines = [line for line in source.splitlines() if line.startswith("fn ")]
    return "".join(f"{line}\n" for line in [kind, *lines])


def main() -> int:
    argv = sys.argv[1:]
    rest, dumps = [], []
    index = 0
    while index < len(argv):
        if argv[index] in FLAGS and index + 1 < len(argv):
            dumps.append((FLAGS[argv[index]], argv[index + 1]))
            index += 2
        else:
            rest.append(argv[index])
            index += 1
    source = next((a for a in rest if a.endswith(".sushi")), None)
    if source is not None and dumps:
        text = (Path(os.environ.get("SUSHI_CWD", os.getcwd())) / source).read_text(
            encoding="utf-8")
        if "# no dump" not in text:
            for kind, target in dumps:
                Path(target).write_text(dump(kind, text), encoding="utf-8")
    sys.argv = ["sushic", *rest]
    from sushi_lang.compiler import main as compile_main
    return compile_main()


if __name__ == "__main__":
    sys.exit(main())
