"""The typecheck pass resolves the built-in method families in ONE order.

Issue #273: validation and the old codegen dispatcher ordered the primitive and
auto-derived families oppositely. The receiver kinds are disjoint, so the canonical
order below is arbitrary where the kinds are disjoint; what matters is that it is
stated once (docs/design/method-resolution.md). The codegen dispatcher is deleted with
the AST body emitter, so its half of this gate went with it.

Since #751 the typecheck pass states its half in ONE place: the family table in
`semantics/passes/types/method_registry.py`, which the inference half and the
validation half both read. So the validation order below is the table's, and the two
rungs that are the LADDER's and not a family's -- the perk implementation and the
extension -- are read from `calls/methods.py`, where `beats_perk` puts the perk rung
between the two halves of the table.
"""
from __future__ import annotations

import re
from pathlib import Path

from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
VALIDATION = SOURCE_ROOT / "semantics" / "passes" / "types" / "calls" / "methods.py"
SHIR_LADDER = SOURCE_ROOT / "semantics" / "passes" / "types" / "calls" / "dotcall.py"

# The canonical family order, highest priority first.
CANONICAL_ORDER = [
    "PERK",
    "ARRAY_HASH",
    "DERIVED_HASH",
    "DERIVED_CLONE",
    "CONTRACT",
    "FUNCTION_CLONE",
    "PRIMITIVE",
    "EXTENSION",
]

# The family table's row name for each step it carries. The rungs that are not a
# family -- the perk implementation and the extension -- are the ladder's, and the
# markers below find them in the body of `validate_method_call`.
TABLE_ROWS = {
    "array_hash": "ARRAY_HASH",
    "derived_hash": "DERIVED_HASH",
    "derived_clone": "DERIVED_CLONE",
    "contract": "CONTRACT",
    "function": "FUNCTION_CLONE",
    "primitive": "PRIMITIVE",
}

# How each ladder rung is recognised. The marker is the step itself (the probe call),
# never a comment.
LADDER_MARKERS = {
    "PERK": r"_validate_perk_method\(validator",
    "FAMILIES_AFTER_PERK": r"beats_perk=False",
    "EXTENSION": r"resolve_extension_method\(validator",
}

# The validation side is measured inside the DISPATCHER's body and not over the whole
# file: the extension rungs live in `resolve_extension_method`, a shared helper declared
# above the dispatcher, so a first-occurrence scan of the file reads the helper's
# definition rather than the step that calls it.
VALIDATION_SCOPE = "def validate_method_call("


def _family_order(path: Path, markers: dict[str, str], scope: str | None = None, *,
                  one_function: bool = False) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if scope is not None:
        start = text.index(scope)
        text = text[start:]
        if one_function:
            end = text.find("\ndef ", len(scope))
            text = text if end == -1 else text[:end]
    positions = {}
    for family, marker in markers.items():
        match = re.search(marker, text)
        assert match is not None, f"{path.name}: no occurrence of the {family} marker {marker!r}"
        positions[family] = match.start()
    return sorted(positions, key=positions.get)


def test_validation_resolves_families_in_the_canonical_order():
    """The perk rung, then the table's rows that yield to it, then the extension rung."""
    order = ["PERK"]
    order += [TABLE_ROWS[family.name] for family in METHOD_TYPE_REGISTRY.families
              if not family.beats_perk]
    order.append("EXTENSION")
    assert order == CANONICAL_ORDER


def test_the_dispatcher_asks_the_rungs_in_that_order():
    """And the dispatcher reads them so: perk, the table's second half, extension."""
    assert _family_order(VALIDATION, LADDER_MARKERS, scope=VALIDATION_SCOPE) == [
        "PERK", "FAMILIES_AFTER_PERK", "EXTENSION"]


# The ladder over SHIR (docs/design/ir.md 7.3): the claim of the table, which puts a perk
# implementation between the two halves of the table, then the perk rung, then the
# extension rung.
SHIR_MARKERS = {
    "FAMILIES": r"METHOD_TYPE_REGISTRY\.claim\(",
    "PERK": r"perk_impl_table\.get_method\(",
    "EXTENSION": r"declaring_units\(",
}


def test_the_shir_ladder_asks_the_rungs_in_that_order():
    """The SHIR ladder reads the same order: the table, the perk, the extension."""
    assert _family_order(SHIR_LADDER, SHIR_MARKERS, scope="def resolve_dotted(",
                         one_function=True) == ["FAMILIES", "PERK", "EXTENSION"]
