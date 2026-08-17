"""The two invariants that hold `semantics/param_modes.py` together."""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.param_modes import (
    CalleeKind,
    ParamMode,
    declared_modes,
    effective_modes,
    mode_of_type,
    modes_for,
    normalize_modes,
    param_mode,
)
from sushi_lang.semantics.typesys import (
    BorrowMode,
    BuiltinType,
    DynamicArrayType,
    FunctionType,
    ReferenceType,
    UnknownType,
)

I32 = BuiltinType.I32
STRING = BuiltinType.STRING


# Invariant 1: PEEK/POKE iff ReferenceType

NON_REFERENCE_TYPES = [
    I32,
    STRING,
    DynamicArrayType(base_type=I32),
    UnknownType("Widget"),
    FunctionType(param_types=(I32,), ok_type=I32, err_type=UnknownType("StdError")),
    None,
]


@pytest.mark.parametrize("ty", NON_REFERENCE_TYPES)
def test_a_non_reference_type_is_never_by_pointer(ty):
    assert mode_of_type(ty, is_nom=False) is ParamMode.BORROW
    assert mode_of_type(ty, is_nom=True) is ParamMode.NOM


@pytest.mark.parametrize("mutability,expected", [
    (BorrowMode.PEEK, ParamMode.PEEK),
    (BorrowMode.POKE, ParamMode.POKE),
])
def test_a_reference_type_is_always_by_pointer(mutability, expected):
    ty = ReferenceType(referenced_type=I32, mutability=mutability)
    assert mode_of_type(ty, is_nom=False) is expected
    # A reference type WINS over the nom flag: the grammar cannot produce the pair, and
    # letting the flag win would break the invariant in the one place it matters.
    assert mode_of_type(ty, is_nom=True) is expected


@pytest.mark.parametrize("mode", list(ParamMode))
def test_by_pointer_agrees_with_the_mode_name(mode):
    assert mode.by_pointer == (mode in (ParamMode.PEEK, ParamMode.POKE))
    assert mode.consumes == (mode is ParamMode.NOM)
    assert (mode.marker is None) == (mode is ParamMode.BORROW)


# Invariant 1, through the real parser: a declaration and its derived mode

DECLARATIONS = [
    ("fn f(string x) ~:\n    return Result.Ok(~)\n", ParamMode.BORROW),
    ("fn f(nom string x) ~:\n    return Result.Ok(~)\n", ParamMode.NOM),
    ("fn f(peek string x) ~:\n    return Result.Ok(~)\n", ParamMode.PEEK),
    ("fn f(poke string x) ~:\n    return Result.Ok(~)\n", ParamMode.POKE),
]


@pytest.mark.parametrize("src,expected", DECLARATIONS)
def test_declared_mode_round_trips_through_the_parser(src, expected):
    program, _tree = parse_to_ast(src)
    param = program.functions[0].params[0]
    assert param_mode(param) is expected
    assert param.is_nom == (expected is ParamMode.NOM)
    assert isinstance(param.ty, ReferenceType) == expected.by_pointer


def test_declared_modes_of_a_mixed_signature():
    src = "fn f(string a, nom string b, peek i32 c, poke i32 d) ~:\n    return Result.Ok(~)\n"
    program, _tree = parse_to_ast(src)
    assert declared_modes(program.functions[0].params) == (
        ParamMode.BORROW, ParamMode.NOM, ParamMode.PEEK, ParamMode.POKE)


# Invariant 1, on a FunctionType: normalization makes the two spellings one type

def test_normalize_reads_peek_poke_off_the_type():
    types = (ReferenceType(referenced_type=I32, mutability=BorrowMode.PEEK), STRING)
    assert normalize_modes(types, None) == (ParamMode.PEEK, ParamMode.BORROW)
    # A caller cannot override what the type already says.
    assert normalize_modes(types, (ParamMode.NOM, ParamMode.NOM)) == (
        ParamMode.PEEK, ParamMode.NOM)


def test_no_modes_and_all_default_modes_are_the_same_type():
    types = (I32, STRING)
    stderr = UnknownType("StdError")
    a = FunctionType(param_types=types, ok_type=I32, err_type=stderr)
    b = FunctionType(param_types=types, ok_type=I32, err_type=stderr,
                     param_modes=(ParamMode.BORROW, ParamMode.BORROW))
    assert a == b
    assert hash(a) == hash(b)


def test_a_nom_parameter_makes_a_different_function_type():
    types = (STRING,)
    stderr = UnknownType("StdError")
    borrow = FunctionType(param_types=types, ok_type=I32, err_type=stderr)
    nom = FunctionType(param_types=types, ok_type=I32, err_type=stderr,
                       param_modes=(ParamMode.NOM,))
    assert borrow != nom
    assert nom != borrow


# Invariant 2: CalleeKind is closed

# What an UNMARKED by-value parameter means at each kind of callee. Every member of
# CalleeKind must appear here; a new one with no row fails the coverage test below.
UNMARKED_MEANS = {
    CalleeKind.FUNCTION: ParamMode.BORROW,
    CalleeKind.METHOD: ParamMode.BORROW,
    CalleeKind.STDLIB: ParamMode.BORROW,
    CalleeKind.FFI_EXTERN: ParamMode.BORROW,
    CalleeKind.INDIRECT: ParamMode.BORROW,
    CalleeKind.CONSTRUCTOR: ParamMode.NOM,   # a field takes ownership, always
    CalleeKind.CONTAINER: ParamMode.NOM,     # a container slot takes ownership, always
}


def test_every_callee_kind_has_a_row():
    assert set(UNMARKED_MEANS) == set(CalleeKind)


@pytest.mark.parametrize("kind", list(CalleeKind))
def test_unmarked_means_what_the_table_says(kind):
    assert effective_modes((ParamMode.BORROW,), kind) == (UNMARKED_MEANS[kind],)


@pytest.mark.parametrize("kind", list(CalleeKind))
def test_a_marked_mode_survives_every_kind(kind):
    # A by-pointer mode is never turned into a consume, whatever the callee is.
    assert effective_modes((ParamMode.PEEK,), kind)[0] is not ParamMode.NOM
    assert effective_modes((ParamMode.POKE,), kind)[0] is not ParamMode.NOM
    # An explicit `nom` stays a consume everywhere.
    assert effective_modes((ParamMode.NOM,), kind) == (ParamMode.NOM,)


def test_modes_for_reads_a_real_signature():
    src = "fn f(string a, nom string b) ~:\n    return Result.Ok(~)\n"
    program, _tree = parse_to_ast(src)
    params = program.functions[0].params
    assert modes_for(params, CalleeKind.METHOD) == (ParamMode.BORROW, ParamMode.NOM)
    assert modes_for(params, CalleeKind.STDLIB) == (ParamMode.BORROW, ParamMode.NOM)
    assert modes_for(params, CalleeKind.CONSTRUCTOR) == (ParamMode.NOM, ParamMode.NOM)
