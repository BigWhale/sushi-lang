"""Every callee kind agrees with its declared signature."""
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


# 1. The resolver returns the declared mode, for every kind

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


# 3. The callee registers cleanup iff the mode is nom

@pytest.mark.parametrize("mode", list(ParamMode))
def test_the_callee_owns_its_parameter_iff_the_mode_is_nom(mode):
    param = _param(mode)
    assert param_mode(param) is mode
    assert callee_owns_param(param) == (mode is ParamMode.NOM)


def test_ownership_does_not_depend_on_the_kind_of_callee():
    """The whole point: the same declaration means the same thing everywhere."""
    import inspect
    signature = inspect.signature(callee_owns_param)
    assert list(signature.parameters) == ["param"]


# 2. A later use is CE2405 iff the mode is nom -- through the real compiler

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


# 3. A callee the compiler could not resolve declares nothing, so nothing is judged

UNRESOLVED = """\
fn main() i32:
    let string s = "Ford"
    let i32 a = no_such(nom s).realise(0)
    println("{a}")
    return Result.Ok(0)
"""

MISMARKED_BORROW = """\
fn look(string s) i32:
    return Result.Ok(s.len())

fn main() i32:
    let string s = "Ford"
    let i32 n = look(nom s).realise(0)
    println("{n}")
    return Result.Ok(0)
"""

MISSING_NOM = """\
fn eat(nom string s) ~:
    println(s)
    return Result.Ok(~)

fn main() i32:
    let string s = "Ford"
    eat(s)
    return Result.Ok(0)
"""

BUILT_IN_MISMARKED = """\
use <io/files>

fn look(string p) bool:
    return Result.Ok(exists(nom p))

fn main() i32:
    let string path = "cfg.txt"
    let bool there = look(path).realise(false)
    println("{there}")
    return Result.Ok(0)
"""


def test_an_unresolved_callee_is_not_judged_against_an_invented_signature(analyze):
    """The mode resolver has no signature for `no_such`, so the marker means nothing.

    Reporting CE2427 here told the user to drop a `nom` that nobody declared -- advice
    that breaks correct code when the callee turns out to declare `nom` after all.
    """
    codes = {item.code for item in analyze(UNRESOLVED).items}
    assert "CE2008" in codes, sorted(codes)
    assert "CE2427" not in codes, sorted(codes)


def test_a_resolved_callee_is_still_judged_in_both_directions(analyze):
    assert "CE2427" in {item.code for item in analyze(MISMARKED_BORROW).items}
    assert "CE2427" in {item.code for item in analyze(MISSING_NOM).items}


def test_a_built_in_callee_is_still_judged(analyze):
    """A registry stdlib callee carries no FuncSig, but is resolved -- so it is judged.

    `open(nom p, ...)` used to say this. It stopped being a built-in in HANDLES.md
    Phase 5: `open()` is an ordinary Sushi function in <io/fs> now and carries a FuncSig
    like any other, so it exercises the DECLARED arm above instead of this one.
    """
    assert "CE2427" in {item.code for item in analyze(BUILT_IN_MISMARKED).items}
