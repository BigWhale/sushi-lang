"""The derived `hash` and `clone` are registered by ONE mechanism (#804).

"Build a lazy emitter, then register a zero-argument method" was written twice, once per
derived method, and "ask the hashability gate, then register" was written at six sites,
the backend's HashMap key path among them. The two seams are
`builtin_methods.derived_method` and `hashing.register_hash_if_hashable`, and a
derived method's argument count is its family's row (`MethodFamily.arity`), not a
validator of its own.
"""
from __future__ import annotations

import ast
from pathlib import Path

from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.typesys import (
    BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo, ForeignPtrType, StructType)

SOURCE = Path(__file__).resolve().parents[2] / "sushi_lang"
HASHING = SOURCE / "semantics" / "generics" / "hashing.py"
CLONING = SOURCE / "semantics" / "generics" / "cloning.py"

#: The hashability gates. Only `hashing.py` may call one to decide a registration.
_GATES = ("can_struct_be_hashed", "can_enum_be_hashed", "can_array_be_hashed")


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def test_the_control_sees_a_gate_call():
    """The always-fires control: the reader sees the gates where they are called."""
    assert set(_GATES) <= _called_names(HASHING)


def test_no_module_but_hashing_decides_a_derived_hash():
    offenders = sorted(
        f"{path.relative_to(SOURCE)}: {name}"
        for path in SOURCE.rglob("*.py") if path != HASHING
        for name in _called_names(path) & set(_GATES))
    assert not offenders, (
        "a module decides a derived hash for itself instead of calling "
        "register_hash_if_hashable: " + ", ".join(offenders))


def test_both_derived_families_register_through_one_seam():
    for path in (HASHING, CLONING):
        assert "derived_method" in _called_names(path), path.name
        copies = {name for name in _defined_names(path)
                  if name.startswith(("_lazy_", "_register_"))}
        assert not copies, f"{path.name} keeps its own copy: {sorted(copies)}"


def test_a_derived_method_has_no_arity_validator_of_its_own():
    for path in (HASHING, CLONING):
        validators = {name for name in _defined_names(path)
                      if name.startswith("_validate_")}
        assert not validators, f"{path.name}: {sorted(validators)}"


def test_the_derived_families_carry_their_count():
    from sushi_lang.semantics.passes.types.method_registry import arity_of_family
    assert dict(arity_of_family("derived_hash")) == {"hash": 0}
    assert dict(arity_of_family("derived_clone")) == {"clone": 0}


def test_register_hash_if_hashable_answers_and_registers():
    from sushi_lang.semantics.generics.hashing import register_hash_if_hashable

    derived = DerivedMethodTable()
    point = StructType(name="Point", fields=(("x", BuiltinType.I32),))
    handle = StructType(name="Handle", fields=(("p", ForeignPtrType()),))
    colour = EnumType(name="Colour", variants=(EnumVariantInfo("Red", ()),))
    array = DynamicArrayType(base_type=BuiltinType.I32)

    assert register_hash_if_hashable(point, derived) is True
    assert register_hash_if_hashable(colour, derived) is True
    assert register_hash_if_hashable(array, derived) is True
    assert register_hash_if_hashable(handle, derived) is False

    for ty in (point, colour, array):
        method = derived.get_method(ty, "hash")
        assert method is not None and method.return_type == BuiltinType.U64
    assert derived.get_method(handle, "hash") is None
