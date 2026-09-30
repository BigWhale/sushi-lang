"""The `<sys/platform>` files declare one and the same set of names (#1089).

A per-platform module is one bundled file per platform and architecture, and a module
that imports it names a constant without knowing which file the compiler picked. So
every file declares the same names, of the same types, in the same order. This reads the
files as text and nothing else.

The VALUES are not checked here. The fixtures under `tests/stdlib/platform/` ask C for
them on each CI platform, and `tests/platform_probe/compare.py` compares the host's file
with the probe by hand.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sushi_lang.semantics.stdlib_registry import PLATFORM_SOURCE_MODULES

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
