"""The kind of a library has one answer, `library_kind` (`sushi_lang/backend/library_format.py`).

A manifest with no `kind` is a source library, `source` and `binary` are themselves, and
every other value is CE3512. When each reader of the manifest `kind` gives its own default,
one reader can take the binary path for a manifest that another reader takes as source.
This scan refuses a read of the MANIFEST `kind` outside the home: in Python
(`metadata.get("kind"`, `metadata["kind"]`), and in Sushi (`(meta, "kind")`), where the
twin is `library_kind` in `src_sushi/toolchain/slib.sushi`. A dependency or a re-export
record also has a `kind` (`record.get("kind")`, `dep.get("kind")`), and the scan does not
refuse those. It reads the source only and does not start the compiler.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "sushi_lang"
HOME = PACKAGE / "backend" / "library_format.py"
SUSHI_HOME = PACKAGE / "sushi_stdlib" / "src_sushi" / "toolchain" / "slib.sushi"
SUSHI_ROOTS = (PACKAGE / "sushi_stdlib" / "src_sushi", REPO / "toolchain" / "src")

_PY_READ = re.compile(r"""\bmetadata\s*(\.get\s*\(\s*|\[\s*)["']kind["']""")
_SUSHI_READ = re.compile(r"""\(\s*meta\s*,\s*["']kind["']""")


def _readers(paths, home, pattern):
    return [
        f"{path.relative_to(REPO)}:{number}"
        for path in paths if path != home
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]


def test_only_library_kind_reads_the_manifest_kind():
    readers = _readers(sorted(PACKAGE.rglob("*.py")), HOME, _PY_READ)
    assert not readers, (
        "the manifest kind is read outside sushi_lang/backend/library_format.py; "
        "call library_kind(metadata) instead:\n  " + "\n  ".join(readers))


def test_only_the_sushi_twin_reads_the_manifest_kind():
    paths = sorted(p for root in SUSHI_ROOTS for p in root.rglob("*.sushi")
                   if "__sushi_cache__" not in p.parts)
    readers = _readers(paths, SUSHI_HOME, _SUSHI_READ)
    assert not readers, (
        "the manifest kind is read outside src_sushi/toolchain/slib.sushi; "
        "call library_kind(meta) instead:\n  " + "\n  ".join(readers))


def test_the_homes_read_it():
    assert "def library_kind(" in HOME.read_text(encoding="utf-8")
    assert "public fn library_kind(" in SUSHI_HOME.read_text(encoding="utf-8"), (
        "the Sushi twin of library_kind is gone; correct this gate and its row in "
        "docs/agents/one-seam-rules.md")
