"""The `<sys/platform>` files agree with each other and with the host (#1089).

A per-platform module is one bundled file per platform and architecture, and a module
that imports it names a constant without knowing which file the compiler picked. So
every file declares the same names, of the same types, in the same order. The values
come from `tests/platform_probe/probe.c`, and the host's file must be exactly what the
probe prints when it is compiled on the host: a value copied by hand, or a probe edit
with no new file, fails here on the platform it is wrong for.

This reads the files as text and compiles C. It starts no Sushi compiler.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sushi_lang.semantics.stdlib_registry import (
    PLATFORM_SOURCE_MODULES, platform_key, platform_source)

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tests" / "platform_probe" / "probe.c"
DECLARATION = re.compile(r"^public const (\w+) (\w+) = ", re.MULTILINE)
FILES = PLATFORM_SOURCE_MODULES["sys/platform"]


def _declarations(path: Path) -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2))
            for match in DECLARATION.finditer(path.read_text(encoding="utf-8"))]


def test_every_platform_file_exists():
    missing = [str(path) for path in FILES.values() if not path.is_file()]
    assert not missing, f"a platform file is missing: {missing}"


def test_every_platform_file_declares_the_same_names():
    reference_key, reference = next(iter(FILES.items()))
    expected = _declarations(reference)
    assert expected, f"{reference} declares no constant"
    for key, path in FILES.items():
        assert _declarations(path) == expected, (
            f"{key} and {reference_key} declare different constants")


@pytest.mark.parametrize("key", sorted(FILES))
def test_every_constant_has_a_doc_block(key):
    lines = FILES[key].read_text(encoding="utf-8").splitlines()
    undocumented = [line for index, line in enumerate(lines)
                    if line.startswith("public const ")
                    and not lines[index - 1].rstrip().endswith(":##")]
    assert not undocumented, f"{key}: no doc block above {undocumented}"


def test_the_host_file_is_what_the_probe_prints(tmp_path):
    source = platform_source("sys/platform")
    assert source is not None, f"no <sys/platform> file for the host {platform_key()}"
    compiler = shutil.which("cc")
    assert compiler is not None, "the platform probe needs a C compiler, `cc`"
    binary = tmp_path / "probe"
    subprocess.run([compiler, str(PROBE), "-o", str(binary)], check=True)
    printed = subprocess.run([str(binary)], check=True, capture_output=True,
                             text=True).stdout
    assert printed == source.read_text(encoding="utf-8"), (
        f"{source.name} is not what the probe prints on {platform_key()}; "
        f"regenerate it: cc {PROBE.relative_to(ROOT)} -o probe && ./probe > {source}")
