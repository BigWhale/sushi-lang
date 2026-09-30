"""Compare the host's `<sys/platform>` file with what the probe prints (#1089). By hand.

The probe is the generator of every value, so a check against it catches drift (a hand
edit, a probe edit with no new file, a header that changed), and nothing more. The
fixtures under `tests/stdlib/platform/` ask C for each value and are the real test. This
script is run by hand, like `tests/docs_sweep.py`, and is deliberately not a CI job.

    python tests/platform_probe/compare.py

It compares the `name = value` pairs and not the text, so a change to a doc sentence in
the probe needs no new file. It exits 1 on a difference and 2 when the host has no file.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sushi_lang.semantics.stdlib_registry import platform_key, platform_source  # noqa: E402

PROBE = Path(__file__).with_name("probe.c")
DECLARATION = re.compile(r"^public const (\w+) (\w+) = (.+)$", re.MULTILINE)


def values(text: str) -> dict[str, tuple[str, str]]:
    """Each constant's (type, value), by name."""
    return {m.group(2): (m.group(1), m.group(3).strip()) for m in DECLARATION.finditer(text)}


def main() -> int:
    source = platform_source("sys/platform")
    if source is None:
        print(f"no <sys/platform> file for the host {platform_key()}")
        return 2
    compiler = shutil.which("cc")
    if compiler is None:
        print("the probe needs a C compiler, `cc`")
        return 2
    with tempfile.TemporaryDirectory() as scratch:
        binary = Path(scratch) / "probe"
        subprocess.run([compiler, str(PROBE), "-o", str(binary)], check=True)
        printed = subprocess.run([str(binary)], check=True, capture_output=True,
                                 text=True).stdout
    probed, bundled = values(printed), values(source.read_text(encoding="utf-8"))
    differences = [
        f"  {name}: probe {probed.get(name)}, file {bundled.get(name)}"
        for name in sorted(probed.keys() | bundled.keys())
        if probed.get(name) != bundled.get(name)
    ]
    if differences:
        print(f"{source.name} differs from the probe on {platform_key()}:")
        print("\n".join(differences))
        print(f"regenerate it: cc {PROBE.relative_to(ROOT)} -o probe && ./probe > {source}")
        return 1
    print(f"{source.name}: {len(probed)} constants agree with the probe on {platform_key()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
