"""Every callee kind agrees with its declared signature.

The gate for borrow by default. Before the flip, the two halves of the compiler each
derived the parameter convention from the callee's IMPLEMENTATION, and they reached
different answers for two of the six callee kinds. This file asserts that the two halves
now read the same declaration, cell by cell.

For each (callee kind, mode) cell:

  1. the resolver returns the declared mode;
  2. a later use of the argument is CE2405 **if and only if** the mode is `nom`;
  3. the callee registers cleanup **if and only if** the mode is `nom`.

Assertions 2 and 3 are the two halves that used to disagree. Assertion 3 is checked at
the decision point (`callee_owns_param`) rather than at its shadow in the emitted IR,
because the decision is what the two halves must share. Its runtime consequence -- the
program is leak-clean and double-free-clean -- is the `.sushi` corpus's job; see
`tests/memory/test_param_mode_matrix.sushi`.

`CalleeKind` is closed, so a kind with no row here fails statically.

See docs/design/borrow-model.md sections 3, 4 and 5.
"""
from __future__ import annotations

import pytest

from sushi_lang.backend.functions.helpers import callee_owns_param
from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.param_modes import (
    CalleeKind,
    ParamMode,
    declared_modes,
    modes_for,
    param_mode,
)


# --------------------------------------------------------------------------- #
# 1. The resolver returns the declared mode, for every kind
# --------------------------------------------------------------------------- #

# The kinds whose parameters are DECLARED in Sushi source. The other three consume by
# position and declare nothing: a struct field, an enum payload and a container slot.
DECLARING_KINDS = [
    CalleeKind.FUNCTION,
    CalleeKind.METHOD,
    CalleeKind.STDLIB,
    CalleeKind.FFI_EXTERN,
    CalleeKind.INDIRECT,
]

POSITIONAL_KINDS = [
    CalleeKind.CONSTRUCTOR,
    CalleeKind.CONTAINER,
]


def test_every_callee_kind_is_in_exactly_one_group():
    assert set(DECLARING_KINDS) | set(POSITIONAL_KINDS) == set(CalleeKind)
    assert not set(DECLARING_KINDS) & set(POSITIONAL_KINDS)


DECLARATIONS = {
    ParamMode.BORROW: "fn f(string x) ~:\n    println(x)\n    return Result.Ok(~)\n",
    ParamMode.NOM: "fn f(nom string x) ~:\n    println(x)\n    return Result.Ok(~)\n",
    ParamMode.PEEK: "fn f(peek string x) ~:\n    println(x)\n    return Result.Ok(~)\n",
    ParamMode.POKE: "fn f(poke string x) ~:\n    println(x)\n    return Result.Ok(~)\n",
}


def _param(mode: ParamMode):
    program, _tree = parse_to_ast(DECLARATIONS[mode])
    return program.functions[0].params[0]


@pytest.mark.parametrize("kind", DECLARING_KINDS)
@pytest.mark.parametrize("mode", list(ParamMode))
def test_the_resolver_returns_the_declared_mode(kind, mode):
    params = [_param(mode)]
    assert declared_modes(params) == (mode,)
    assert modes_for(params, kind) == (mode,)


@pytest.mark.parametrize("kind", POSITIONAL_KINDS)
def test_a_positional_sink_always_consumes(kind):
    assert modes_for([_param(ParamMode.BORROW)], kind) == (ParamMode.NOM,)


# --------------------------------------------------------------------------- #
# 3. The callee registers cleanup iff the mode is nom
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("mode", list(ParamMode))
def test_the_callee_owns_its_parameter_iff_the_mode_is_nom(mode):
    param = _param(mode)
    assert param_mode(param) is mode
    assert callee_owns_param(param) == (mode is ParamMode.NOM)


def test_ownership_does_not_depend_on_the_kind_of_callee():
    """The whole point: the same declaration means the same thing everywhere.

    `callee_owns_param` takes a parameter and nothing else. It cannot ask whether the
    body is a method, a generated stdlib body or a library body, which is what the four
    disagreeing conventions all did.
    """
    import inspect
    signature = inspect.signature(callee_owns_param)
    assert list(signature.parameters) == ["param"]


# --------------------------------------------------------------------------- #
# 2. A later use is CE2405 iff the mode is nom -- through the real compiler
# --------------------------------------------------------------------------- #

CALL_SITES = {
    ParamMode.BORROW: "    f(s)\n",
    ParamMode.NOM: "    f(nom s)\n",
    ParamMode.PEEK: "    f(peek s)\n",
    ParamMode.POKE: "    f(poke s)\n",
}


def _program(mode: ParamMode) -> str:
    return (
        DECLARATIONS[mode]
        + "\nfn main() i32:\n"
        + '    let string base = "Ford"\n'
        + '    let string s = "{base}!"\n'
        + CALL_SITES[mode]
        + "    println(s)\n"
        + "    return Result.Ok(0)\n"
    )


@pytest.mark.parametrize("mode", list(ParamMode))
def test_a_later_use_is_CE2405_iff_the_mode_is_nom(analyze, mode):
    codes = {item.code for item in analyze(_program(mode)).items}
    assert ("CE2405" in codes) == (mode is ParamMode.NOM), sorted(codes)


@pytest.mark.parametrize("mode", list(ParamMode))
def test_no_cell_reports_anything_else(analyze, mode):
    """No cell may reach a diagnostic that is not the one the mode calls for."""
    errors = {item.code for item in analyze(_program(mode)).items
              if item.code.startswith("CE")}
    assert errors <= {"CE2405"}, sorted(errors)
