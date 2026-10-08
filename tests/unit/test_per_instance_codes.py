"""The per-instance remainder of a checked template is one table with one reader (#1070).

A copy of a template that checked clean reports only the codes in `PER_INSTANCE_CODES`
(R7, R8), and the reporter turns any other error of the copy into CE0149. This test
reads the table and the Python source only. It does not start the compiler.
"""
from __future__ import annotations

from pathlib import Path

from sushi_lang.internals.errors import PER_INSTANCE_CODES, REGISTRY

SUSHI_LANG = Path(__file__).resolve().parents[2] / "sushi_lang"
DEFINITION = SUSHI_LANG / "internals" / "errors" / "__init__.py"
READER = SUSHI_LANG / "internals" / "report.py"


def test_every_per_instance_code_is_registered() -> None:
    missing = sorted(code for code in PER_INSTANCE_CODES if code not in REGISTRY)
    assert not missing, f"PER_INSTANCE_CODES names unregistered codes: {missing}"


def test_the_backstop_code_is_registered() -> None:
    assert "CE0149" in REGISTRY
    assert "CE0149" not in PER_INSTANCE_CODES


def test_the_reporter_is_the_one_reader() -> None:
    readers = sorted(
        str(path.relative_to(SUSHI_LANG)) for path in SUSHI_LANG.rglob("*.py")
        if path not in (DEFINITION, READER)
        and "PER_INSTANCE_CODES" in path.read_text(encoding="utf-8"))
    assert not readers, (
        f"PER_INSTANCE_CODES is read outside internals/report.py: {readers}")
    assert "PER_INSTANCE_CODES" in READER.read_text(encoding="utf-8")
