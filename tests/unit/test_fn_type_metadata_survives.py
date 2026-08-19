"""Every rebuild of a `FunctionType` carries its metadata (#368).

`peek` and `poke` ride on the parameter's own `ReferenceType`, so they survive a rebuild by
construction. `nom` lives in `param_modes`, and `captures` beside it, so a transformation that
rebuilds the type has to carry both. Dropping `param_modes` cost two double frees and made
`nom` in a fn-type annotation unusable.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.semantics.generics.monomorphize.transformer import TypeSubstitutor
from sushi_lang.semantics.generics.type_strings import resolve_type_from_string
from sushi_lang.semantics.generics.types import TypeParameter, substitute_type_params
from sushi_lang.semantics.param_modes import ParamMode
from sushi_lang.semantics.type_resolution import resolve_type_recursively
from sushi_lang.semantics.typesys import (
    BuiltinType,
    EnumType,
    EnumVariantInfo,
    FunctionType,
    StructType,
    UnknownType,
)

I32 = BuiltinType.I32
STRING = BuiltinType.STRING
CAPTURES = ("captured_name",)
MODES = (ParamMode.NOM, ParamMode.BORROW)

WIDGET = StructType(name="Widget", fields=())
STDERROR = EnumType(name="StdError",
                    variants=(EnumVariantInfo(name="Error", associated_types=()),))


def _fn(param_types) -> FunctionType:
    """A fn type whose two metadata fields are both non-default."""
    return FunctionType(param_types=tuple(param_types), ok_type=I32,
                        err_type=UnknownType("StdError"),
                        captures=CAPTURES, param_modes=MODES)


def _assert_metadata_survived(before: FunctionType, after) -> None:
    assert isinstance(after, FunctionType)
    # The rebuild must have happened, or the assertion below proves nothing.
    assert after.param_types != before.param_types or after.err_type != before.err_type
    assert after.modes == (ParamMode.NOM, ParamMode.BORROW)
    assert after.captures == CAPTURES


def test_resolve_type_recursively_carries_the_metadata():
    before = _fn((UnknownType("Widget"), STRING))
    after = resolve_type_recursively(before, {"Widget": WIDGET}, {"StdError": STDERROR})
    _assert_metadata_survived(before, after)


def test_generic_substitution_carries_the_metadata():
    # One PURE substitution, for both callers. `generics/extensions.py` used to carry a
    # second copy of the same rule with its own arms, and #389 was a hole in it.
    before = _fn((TypeParameter(name="T"), STRING))
    after = substitute_type_params(before, {"T": I32})
    _assert_metadata_survived(before, after)


def test_type_substitutor_carries_the_metadata():
    before = _fn((TypeParameter(name="T"), STRING))
    after = TypeSubstitutor(None).substitute_type(before, {"T": I32})
    _assert_metadata_survived(before, after)


# A manifest fn type: `str()` writes the marker, so reading it back must accept one. It
# used to raise a CE0022 internal error on the parameter text `nom string`.

class _Tables:
    class _Structs:
        by_name: dict = {}

    class _Enums:
        by_name = {"StdError": STDERROR}

    struct_table = _Structs()
    enum_table = _Enums()


@pytest.mark.parametrize("modes", [
    (ParamMode.NOM,),
    (ParamMode.BORROW,),
])
def test_a_manifest_fn_type_round_trips_its_modes(modes):
    before = FunctionType(param_types=(STRING,), ok_type=I32,
                          err_type=STDERROR, param_modes=modes)
    after = resolve_type_from_string(str(before), _Tables())
    assert after.modes == modes
    assert after == before


# The static half. The round-trip tests above cover the five transformations that exist
# today; this covers the one somebody adds next. No allowlist.

SEMANTICS = Path(__file__).resolve().parents[2] / "sushi_lang" / "semantics"


def _function_type_constructions():
    """Every `FunctionType(...)` call under semantics/, with its keyword names."""
    for path in sorted(SEMANTICS.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "FunctionType":
                continue
            yield path, node.lineno, {kw.arg for kw in node.keywords}


def test_every_construction_states_the_modes():
    offenders = [
        f"{path.relative_to(SEMANTICS)}:{lineno}"
        for path, lineno, kwargs in _function_type_constructions()
        if "param_modes" not in kwargs
    ]
    assert not offenders, (
        "a FunctionType is built without stating `param_modes`:\n  "
        + "\n  ".join(offenders)
        + "\nA rebuild of an existing type should use dataclasses.replace, so both metadata "
        "fields ride along. A type built from a signature should pass "
        "declared_modes(params). Defaulting to None silently makes every parameter a "
        "borrow (#368)."
    )


def test_the_gate_still_sees_the_constructions():
    """The mirror: if the class is renamed, the gate must not go quietly green."""
    found = list(_function_type_constructions())
    assert len(found) >= 4, (
        f"the gate found only {len(found)} FunctionType constructions under semantics/; "
        "either they moved behind a helper (point the gate at it) or the class was renamed."
    )
