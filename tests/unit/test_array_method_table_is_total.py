"""ONE table answers for every built-in array method, and the backend claims the same set.

The gate for `_ARRAY_METHODS` (`semantics/passes/types/arrays.py`). The module spelled the
method set THREE times -- a membership predicate, a validation chain of 18 `elif` arms and
a return-type chain of 15 -- and the three had already drifted: `to_string_checked` stood
in two of them and its return type was patched in by a special case in `method_registry.py`.
A set written more than once drifts again, so this gate makes the next drift a failure.

Five questions, each a separate way the set can go wrong:

1. the membership predicate answers from the table and from nothing else;
2. every row says what its method ANSWERS, and the answer arrives -- measured through
   `array_method_return_type`, the one reader that the typecheck arm of the family
   (`check_array_method`) and the inference of the early passes both read;
3. every row that takes its arguments into a slot names a real argument, and the modes of
   the family are the slots of the table;
4. the backend's own `is_builtin_array_method` claims exactly the table's names;
5. the backend's two dispatch `match` statements hold exactly one arm per name, and its
   FIXED half holds exactly the names the table lets a fixed array receive.

Each backend half is read out of the source, so a name added to one side and forgotten on
the other is a failure and not a silent NotImplementedError at codegen.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.passes.types.arrays import (
    _ARRAY_METHODS,
    ARRAY_METHOD_MODES,
    Receiver,
    array_method_return_type,
    is_builtin_array_method,
)
from sushi_lang.semantics.typesys import (
    BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo,
)


# What the module carried on the day the table replaced the three lists. A count is not a
# rule, so these are not asserted as a target -- they are the control that says the reader
# below found something to read.
MEASURED_NAMES = 36
MEASURED_FIXED_ARMS = 20

#: The rows whose answer is a `Maybe` that the reader interns.
MAYBE_NAMES = {"get", "first", "last", "pop", "remove", "index_of", "index_of_from",
               "min", "max"}


class _Tables:
    """The tables that the reader interns a `Maybe` and a `Result` into."""

    def __init__(self):
        std_error = EnumType(name="StdError",
                             variants=(EnumVariantInfo(name="Error", associated_types=()),))
        self.by_name = {"StdError": std_error}
        self.order = list(self.by_name)
        self.derived = DerivedMethodTable()
        self.admits_opaque = False


class _Validator:
    """The two tables of the pass that the reader reads, and no other state."""

    def __init__(self):
        self.enum_table = _Tables()
        self.struct_table = type("Structs", (), {"by_name": {}})()
        self.drop_type_names: frozenset = frozenset()


# --------------------------------------------------------------------------- the table


def test_the_table_is_not_empty():
    """The control. Every assertion below is vacuous against an empty table."""
    assert len(_ARRAY_METHODS) == MEASURED_NAMES, sorted(_ARRAY_METHODS)


@pytest.mark.parametrize("name", sorted(_ARRAY_METHODS))
def test_membership_answers_from_the_table(name):
    assert is_builtin_array_method(name)


@pytest.mark.parametrize("name", ["", "nope", "map", "push_front", "Len", "lens"])
def test_a_name_outside_the_table_is_not_a_built_in(name):
    assert not is_builtin_array_method(name)


@pytest.mark.parametrize("name", sorted(_ARRAY_METHODS))
def test_every_row_is_complete(name):
    """A row states an arity, a receiver kind, an argument rule and an answer."""
    spec = _ARRAY_METHODS[name]
    assert spec.arity >= 0
    assert isinstance(spec.receiver, Receiver)
    assert callable(spec.arguments)
    assert callable(spec.returns)


def _bytes_answer(name):
    """The answer of `name` on a `u8[]`, which every row takes as its receiver."""
    return array_method_return_type(_Validator(), DynamicArrayType(BuiltinType.U8), name)


@pytest.mark.parametrize("name", sorted(_ARRAY_METHODS))
def test_every_answer_arrives(name):
    """Measured at the END state, through the one reader: a row that the reader answers
    nothing for would type its call as no value."""
    assert _bytes_answer(name) is not None, name


def test_the_maybe_rows_answer_a_maybe():
    maybes = {name for name in _ARRAY_METHODS
              if getattr(_bytes_answer(name), "generic_base", None) == "Maybe"}
    assert maybes == MAYBE_NAMES


def test_a_slot_names_a_real_argument_and_the_modes_are_the_slots():
    slots = {name for name, spec in _ARRAY_METHODS.items() if spec.slot is not None}
    assert slots == {"push", "insert"}
    for name in slots:
        spec = _ARRAY_METHODS[name]
        assert 0 <= spec.slot < spec.arity, name
    assert ARRAY_METHOD_MODES.slots == slots


# ------------------------------------------------------------------------- the backend


def _backend_module() -> ast.Module:
    from sushi_lang.backend.types.arrays import dispatcher

    return ast.parse(Path(dispatcher.__file__).read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef:
    for node in ast.walk(_backend_module()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"the backend dispatcher declares no '{name}'")


def _strings(node: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def backend_predicate_names() -> set[str]:
    """The name set of `backend/types/arrays/dispatcher.py:is_builtin_array_method`."""
    for node in ast.walk(_function("is_builtin_array_method")):
        if isinstance(node, ast.Return) and node.value is not None:
            return _strings(node.value)
    raise AssertionError("the backend predicate returns nothing")


def backend_dispatch_arms() -> list[set[str]]:
    """The names each half of `emit_array_method` claims: the fixed half, then the dynamic."""
    matches = [node for half in ("emit_fixed_array_method", "emit_dynamic_array_method")
               for node in ast.walk(_function(half)) if isinstance(node, ast.Match)]
    return [{value for case in node.cases for value in _strings(case.pattern)}
            for node in matches]


def test_the_backend_reader_found_something():
    """The control. A reader that answers an empty set passes every comparison below."""
    arms = backend_dispatch_arms()
    assert len(backend_predicate_names()) == MEASURED_NAMES
    assert len(arms) == 2, "emit_array_method holds one match per receiver kind"
    assert len(arms[0]) == MEASURED_FIXED_ARMS
    assert len(arms[1]) == MEASURED_NAMES


def test_the_backend_predicate_claims_the_table():
    assert backend_predicate_names() == set(_ARRAY_METHODS)


def test_the_backend_predicate_agrees_when_it_is_called():
    from sushi_lang.backend.types.arrays import is_builtin_array_method as backend_claims

    for name in _ARRAY_METHODS:
        assert backend_claims(name), name
    assert not backend_claims("push_front")


def test_every_name_has_a_dynamic_emitter():
    assert backend_dispatch_arms()[1] == set(_ARRAY_METHODS)


def test_the_fixed_emitters_are_the_rows_a_fixed_array_receives():
    """A `Receiver.ANY` row is the one a `T[N]` may call, so the two sets are one set."""
    fixed_rows = {name for name, spec in _ARRAY_METHODS.items()
                  if spec.receiver is Receiver.ANY}
    assert backend_dispatch_arms()[0] == fixed_rows
