"""One predicate says whether a unit is reused, and one function gives the label of an object.

The analyzer skips the body passes of a reused bundled stdlib unit, and the pipeline takes
its object from the per-user cache. If the two asked two predicates, the pipeline could take
an object for a unit whose body ran, or build a module for a unit that has no SLIR.
`semantics/unit_reuse.py` holds `UnitReuse.is_reused`, and no other module defines one. The
label of an object goes into its key and into the text of its traps, so the key and the
SLIR stage read one function, `object_label`.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM = ROOT / "semantics" / "unit_reuse.py"
READERS = ("semantics/semantic_analyzer.py", "compiler/pipeline.py")
LABEL_READERS = ("compiler/fingerprint.py", "semantics/slir/lower.py")


def _files_with(text: str) -> list[str]:
    return sorted(str(path.relative_to(ROOT)) for path in ROOT.rglob("*.py")
                  if text in path.read_text(encoding="utf-8"))


def test_the_seam_is_measured():
    """The control: the seam spells both definitions, so the scan can see them."""
    source = SEAM.read_text(encoding="utf-8")
    assert "def is_reused(" in source
    assert "def object_label(" in source


def test_one_definition_of_the_predicate():
    assert _files_with("def is_reused(") == ["semantics/unit_reuse.py"]


def test_the_analyzer_and_the_pipeline_read_it():
    for reader in READERS:
        assert ".is_reused(" in (ROOT / reader).read_text(encoding="utf-8"), reader


def test_the_key_and_the_slir_stage_read_one_label():
    for reader in LABEL_READERS:
        assert "object_label(" in (ROOT / reader).read_text(encoding="utf-8"), reader
    assert _files_with("display_filename(") == [
        "internals/report.py", "semantics/unit_reuse.py"], _files_with("display_filename(")
