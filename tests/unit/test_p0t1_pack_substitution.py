"""P0-T1: pack-capable monomorphization substitution model.

Focused TDD-style checks for the frozen pack-representation contract:

- a regular 1:1 generic still builds a 1:1 substitution (unchanged);
- a synthetic generic whose last type_param has ``is_pack=True`` binds a
  ``TypePack`` of the trailing types into the substitution;
- ``substitute_type`` raises when a pack binding is hit in scalar position.

A comprehensive suite is authored separately (P0-TA); these are minimal.
"""
import types as _pytypes

import pytest

from sushi_lang.semantics.generics.types import TypeParameter, TypePack
from sushi_lang.semantics.generics.monomorphize.functions import FunctionMonomorphizer
from sushi_lang.semantics.generics.monomorphize.transformer import TypeSubstitutor
from sushi_lang.semantics.typesys import BuiltinType, UnknownType


class _FakeMono:
    """Minimal stand-in for Monomorphizer.

    ``build_substitution`` only reaches back into the parent for
    ``_validate_type_constraints``; record its calls so we can assert the
    no-pack path is unchanged and the pack path only validates leading params.
    """

    def __init__(self):
        self.constraint_calls = []

    def _validate_type_constraints(self, params, args):
        self.constraint_calls.append((tuple(params), tuple(args)))


def _generic(name, type_params):
    g = _pytypes.SimpleNamespace()
    g.name = name
    g.type_params = list(type_params)
    return g


def _param(name, is_pack=False):
    tp = TypeParameter(name)
    if is_pack:
        # Phase 0 sets the pack marker as an attribute on synthetic fixtures;
        # ast.py is intentionally untouched. object.__setattr__ because
        # TypeParameter is a frozen dataclass.
        object.__setattr__(tp, "is_pack", True)
    return tp


# ---------------------------------------------------------------------------
# (a) regular 1:1 generic: unchanged behavior
# ---------------------------------------------------------------------------

def test_regular_generic_builds_1to1_substitution():
    mono = _FakeMono()
    fm = FunctionMonomorphizer(mono)
    generic = _generic("f", [_param("T"), _param("E")])

    sub = fm.build_substitution(generic, (BuiltinType.I32, BuiltinType.STRING))

    assert sub == {"T": BuiltinType.I32, "E": BuiltinType.STRING}
    # Perk constraints validated exactly as before: full param/arg lists, 1:1.
    assert mono.constraint_calls == [
        ((generic.type_params[0], generic.type_params[1]),
         (BuiltinType.I32, BuiltinType.STRING))
    ]


def test_regular_generic_arity_mismatch_raises():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("T"), _param("E")])
    with pytest.raises(ValueError, match="Type argument count mismatch"):
        fm.build_substitution(generic, (BuiltinType.I32,))


# ---------------------------------------------------------------------------
# (b) trailing pack param absorbs trailing args into a TypePack
# ---------------------------------------------------------------------------

def test_trailing_pack_absorbs_all_trailing_args():
    mono = _FakeMono()
    fm = FunctionMonomorphizer(mono)
    # f<T, ...Ts>: leading T binds 1:1, Ts is a pack.
    generic = _generic("f", [_param("T"), _param("Ts", is_pack=True)])

    sub = fm.build_substitution(
        generic, (BuiltinType.I32, BuiltinType.STRING, BuiltinType.F32)
    )

    assert sub["T"] == BuiltinType.I32
    assert sub["Ts"] == TypePack((BuiltinType.STRING, BuiltinType.F32))
    # Constraints validated only on the leading 1:1 params.
    assert mono.constraint_calls == [((generic.type_params[0],), (BuiltinType.I32,))]


def test_pack_absorbs_zero_trailing_args():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("T"), _param("Ts", is_pack=True)])

    sub = fm.build_substitution(generic, (BuiltinType.I32,))

    assert sub["T"] == BuiltinType.I32
    assert sub["Ts"] == TypePack(())  # empty pack is valid


def test_pack_only_generic():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("Ts", is_pack=True)])

    sub = fm.build_substitution(generic, (BuiltinType.I32, BuiltinType.STRING))

    assert sub == {"Ts": TypePack((BuiltinType.I32, BuiltinType.STRING))}


def test_pack_not_last_raises():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("Ts", is_pack=True), _param("T")])
    with pytest.raises(ValueError, match="not the\\s+last"):
        fm.build_substitution(generic, (BuiltinType.I32, BuiltinType.STRING))


def test_more_than_one_pack_raises():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("As", is_pack=True), _param("Bs", is_pack=True)])
    with pytest.raises(ValueError, match="at most one"):
        fm.build_substitution(generic, (BuiltinType.I32,))


def test_too_few_args_for_leading_params_raises():
    fm = FunctionMonomorphizer(_FakeMono())
    # f<A, B, ...Ts>: needs at least 2 leading args.
    generic = _generic("f", [_param("A"), _param("B"), _param("Ts", is_pack=True)])
    with pytest.raises(ValueError, match="at least 2"):
        fm.build_substitution(generic, (BuiltinType.I32,))


# ---------------------------------------------------------------------------
# (c) scalar-position guard
# ---------------------------------------------------------------------------

def test_substitute_type_rejects_pack_in_scalar_position_typeparam():
    sub = TypeSubstitutor(_FakeMono())
    mapping = {"Ts": TypePack((BuiltinType.I32, BuiltinType.STRING))}
    with pytest.raises(ValueError, match="scalar type position"):
        sub.substitute_type(TypeParameter("Ts"), mapping)


def test_substitute_type_rejects_pack_in_scalar_position_unknowntype():
    sub = TypeSubstitutor(_FakeMono())
    mapping = {"Ts": TypePack((BuiltinType.I32,))}
    with pytest.raises(ValueError, match="scalar type position"):
        sub.substitute_type(UnknownType("Ts"), mapping)


def test_substitute_type_scalar_binding_unchanged():
    sub = TypeSubstitutor(_FakeMono())
    mapping = {"T": BuiltinType.I32}
    assert sub.substitute_type(TypeParameter("T"), mapping) == BuiltinType.I32
    assert sub.substitute_type(UnknownType("T"), mapping) == BuiltinType.I32


# ---------------------------------------------------------------------------
# TypePack public shape
# ---------------------------------------------------------------------------

def test_typepack_str_and_hash():
    p = TypePack((BuiltinType.I32, BuiltinType.STRING))
    assert str(p) == "pack(i32, string)"
    # frozen dataclass: hashable + value equality
    assert p == TypePack((BuiltinType.I32, BuiltinType.STRING))
    assert hash(p) == hash(TypePack((BuiltinType.I32, BuiltinType.STRING)))
    assert {p}  # usable in a set
