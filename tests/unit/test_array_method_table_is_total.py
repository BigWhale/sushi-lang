"""ONE table answers for every built-in array method, and the backend claims the same set.

The gate for `_ARRAY_METHODS` (`semantics/passes/types/arrays.py`). The module spelled the
method set THREE times -- a membership predicate, a validation chain of 18 `elif` arms and
a return-type chain of 15 -- and the three had already drifted: `to_string_checked` stood
in two of them and its return type was patched in by a special case in `method_registry.py`.
A set written more than once drifts again, so this gate makes the next drift a failure.

Four questions, each a separate way the set can go wrong:

1. the membership predicate answers from the table and from nothing else;
2. every row says what its method ANSWERS, and the answer arrives -- measured at the END
   state, through `ArrayMethodInferrer`, because five rows are interned by that caller and
   a check of the table alone would pass while the caller answered nothing;
3. the backend's own `is_builtin_array_method` claims exactly the table's names;
4. the backend's two dispatch `match` statements hold exactly one arm per name, and its
   FIXED half holds exactly the names the table lets a fixed array receive.

Each backend half is read out of the source, so a name added to one side and forgotten on
the other is a failure and not a silent NotImplementedError at codegen.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.semantics.passes.types.arrays import (
    _ARRAY_METHODS,
    INTERNED_BY_THE_CALLER,
    Receiver,
    is_builtin_array_method,
)
from sushi_lang.semantics.typesys import ArrayType, BuiltinType, DynamicArrayType


# What the module carried on the day the table replaced the three lists. A count is not a
# rule, so these are not asserted as a target -- they are the control that says the reader
# below found something to read.
MEASURED_NAMES = 24
MEASURED_FIXED_ARMS = 13

# The five whose answer is a `Maybe@(T)` the CALLER interns (`ArrayMethodInferrer`), and
# which therefore carry the sentinel rather than a rule of their own.
INTERNED_NAMES = {"get", "first", "last", "pop", "index_of"}


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
    assert spec.returns is not None


def test_the_sentinel_is_used_by_exactly_the_five_interned_names():
    """It may not spread: a row that carries it answers nothing of its own."""
    carried = {name for name, spec in _ARRAY_METHODS.items()
               if spec.returns is INTERNED_BY_THE_CALLER}
    assert carried == INTERNED_NAMES


# ------------------------------------------------------------------- the answer arrives


ARRAY_SOURCE = (
    "fn main() i32:\n"
    "    let u8[] bytes = from([104 as u8, 105 as u8])\n"
    "    let i32[] nums = from([1, 2, 3])\n"
    "    return Result.Ok(nums.len())\n"
)


def _receivers(spec):
    """The receiver kinds this row accepts, as real types."""
    if spec.receiver is Receiver.BYTES:
        return [DynamicArrayType(base_type=BuiltinType.U8)]
    if spec.receiver is Receiver.DYNAMIC:
        return [DynamicArrayType(base_type=BuiltinType.I32)]
    return [DynamicArrayType(base_type=BuiltinType.I32),
            ArrayType(base_type=BuiltinType.I32, size=3)]


@pytest.fixture
def array_validator(analyze_program):
    """A `TypeValidator` over a real analysis, so the intern seams have their tables."""
    from sushi_lang.semantics.passes.types import TypeValidator

    analysis = analyze_program(ARRAY_SOURCE)
    assert analysis.analyzer is not None
    return TypeValidator(analysis.reporter, analysis.analyzer.tables)


@pytest.mark.parametrize("name", sorted(_ARRAY_METHODS))
def test_every_name_answers_a_return_type(array_validator, name):
    """Measured at the END state: through the inferrer the typecheck pass really calls."""
    from sushi_lang.semantics.passes.types.method_registry import ArrayMethodInferrer

    spec = _ARRAY_METHODS[name]
    for receiver in _receivers(spec):
        answered = ArrayMethodInferrer(receiver, name, array_validator).infer_return_type()
        assert answered is not None, (name, str(receiver))


def test_a_name_outside_the_table_answers_nothing(array_validator):
    """The control for the test above: the inferrer does not answer for every name."""
    from sushi_lang.semantics.passes.types.method_registry import ArrayMethodInferrer

    answered = ArrayMethodInferrer(DynamicArrayType(base_type=BuiltinType.I32),
                                   "push_front", array_validator).infer_return_type()
    assert answered is None


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
