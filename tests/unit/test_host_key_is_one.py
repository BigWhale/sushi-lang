"""The host has one name, `host_key()` (`sushi_lang/internals/host.py`).

The key selects the `<sys/platform>` file and is the platform tag of a `.slib`. A binary
library compiles that file into its bitcode, so the two must agree. A second place that
reads the architecture of the host can spell it another way (`aarch64` for `arm64`), and
then a library built on the host is refused on that host. This scan refuses a read of
`platform.machine()` anywhere in `sushi_lang/` but `host.py`. It reads the Python source
only and does not start the compiler.
"""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sushi_lang"
HOME = PACKAGE / "internals" / "host.py"
_MACHINE = re.compile(r"\bplatform\.machine\s*\(|\bfrom\s+platform\s+import\b[^\n]*\bmachine\b")


def test_only_host_key_reads_the_architecture():
    readers = [
        f"{path.relative_to(PACKAGE.parent)}:{number}"
        for path in sorted(PACKAGE.rglob("*.py")) if path != HOME
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _MACHINE.search(line)
    ]
    assert not readers, (
        "the architecture of the host is read outside sushi_lang/internals/host.py; "
        "call host_key() instead:\n  " + "\n  ".join(readers))


def test_the_home_reads_it():
    assert _MACHINE.search(HOME.read_text(encoding="utf-8")), (
        "host.py no longer reads platform.machine(); correct this gate and its row in "
        "docs/agents/one-seam-rules.md")
