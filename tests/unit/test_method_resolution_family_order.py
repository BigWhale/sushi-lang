"""Validation and codegen resolve the built-in method families in ONE order.

Issue #273: the two layers ordered the primitive and auto-derived families
oppositely, and the function-value clone joined each layer in a different
relative place. The receiver kinds are disjoint today, so nothing dispatches
differently -- but the two files stated the invariant in opposite order, and a
type that ever satisfies two families would diverge silently. The canonical
order below is arbitrary where the kinds are disjoint; what matters is that
both layers state the SAME one (docs/design/method-resolution.md).
"""
from __future__ import annotations

import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
VALIDATION = SOURCE_ROOT / "semantics" / "passes" / "types" / "calls" / "methods.py"
CODEGEN = SOURCE_ROOT / "backend" / "expressions" / "calls" / "dispatcher.py"

# The canonical family order, highest priority first.
CANONICAL_ORDER = [
    "PERK",
    "DERIVED_HASH",
    "DERIVED_CLONE",
    "FUNCTION_CLONE",
    "PRIMITIVE",
    "EXTENSION",
]

# How each family's dispatch step is recognised in each file. The marker is the
# step itself (the probe call / the arm's condition), never a comment.
VALIDATION_MARKERS = {
    "PERK": r"perk_impl_table\.get_method",
    "DERIVED_HASH": r'call\.method == "hash"',
    "DERIVED_CLONE": r'call\.method == "clone"',
    "FUNCTION_CLONE": r"is_builtin_function_method",
    "PRIMITIVE": r"validate_primitive_method",
    "EXTENSION": r"resolve_extension_method\(validator",
}

# The validation side is measured inside the DISPATCHER's body and not over the whole
# file: the extension rungs live in `resolve_extension_method`, a shared helper declared
# above the dispatcher, so a first-occurrence scan of the file reads the helper's
# definition rather than the step that calls it.
VALIDATION_SCOPE = "def validate_method_call("
CODEGEN_MARKERS = {
    "PERK": r"try_emit_perk_method",
    "DERIVED_HASH": r"try_emit_struct_hash",
    "DERIVED_CLONE": r"try_emit_struct_clone",
    "FUNCTION_CLONE": r"try_emit_function_clone",
    "PRIMITIVE": r"try_emit_primitive_method",
    "EXTENSION": r"Extension method not found",
}


def _family_order(path: Path, markers: dict[str, str], scope: str | None = None) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if scope is not None:
        start = text.index(scope)
        text = text[start:]
    positions = {}
    for family, marker in markers.items():
        match = re.search(marker, text)
        assert match is not None, f"{path.name}: no occurrence of the {family} marker {marker!r}"
        positions[family] = match.start()
    return sorted(positions, key=positions.get)


def test_validation_resolves_families_in_the_canonical_order():
    assert _family_order(VALIDATION, VALIDATION_MARKERS,
                         scope=VALIDATION_SCOPE) == CANONICAL_ORDER


def test_codegen_resolves_families_in_the_canonical_order():
    assert _family_order(CODEGEN, CODEGEN_MARKERS) == CANONICAL_ORDER
