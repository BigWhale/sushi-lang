"""The SLIR verifier over a callee of a binary or a hybrid library (docs/design/ir.md 8.6, 8.13).

A function, a method or a static of such a library has no declaring unit in the build: its
symbol is its `link_symbol`, and the `libraries` step files it in the function table or in
the extension table with that symbol. The verifier finds the declared parameters of a
`Direct` library callee there. Each test calls a Python function over data and does not run
the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.passes.collect.functions import (
    ExtensionMethod, ExtensionTable, FuncSig, FunctionTable, Param)
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock, SlirFunction, SlirLocal
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.slir.verify import (
    CONVENTIONS, DeclaredParams, declared_params, verify_function)
from sushi_lang.semantics.typesys import BuiltinType, StructType

BAD = Span(7, 5, 7, 20)
OWNED, GUARANTEED = Convention.OWNED, Convention.GUARANTEED
I32, STRING, BLANK = BuiltinType.I32, BuiltinType.STRING, BuiltinType.BLANK
CRATE = StructType(name="Crate", fields=(("weight", I32),))
UNIT = "lib/lc_bin/lc_callee_lib"


def _library_tables() -> tuple[FunctionTable, ExtensionTable]:
    funcs, extensions = FunctionTable(), ExtensionTable()
    funcs.declare("shout", FuncSig("shout", params=[Param("s", STRING, None, None, 0,
                                                          is_nom=True)],
                                   unit_name=UNIT, link_symbol="lc_callee_lib$shout"))
    weight = [Param("weight", I32, None, None, 0)]
    extensions.add_method(ExtensionMethod(CRATE, "heavy", params=[], unit_name=UNIT,
                                          link_symbol="lc_callee_lib$Crate_heavy"))
    extensions.add_method(ExtensionMethod(CRATE, "of", params=weight, unit_name=UNIT,
                                          is_static=True,
                                          link_symbol="lc_callee_lib$Crate_of"))
    return funcs, extensions


def test_a_function_of_a_library_is_found_by_its_link_symbol():
    funcs, extensions = _library_tables()
    assert declared_params(funcs, extensions, st.Direct("lc_callee_lib$shout", "shout")) \
        == DeclaredParams((OWNED,))
    assert declared_params(funcs, extensions, st.Direct("other$shout", "shout")) is None


def test_a_method_and_a_static_of_a_library_are_found_by_their_link_symbol():
    funcs, extensions = _library_tables()
    assert declared_params(funcs, extensions,
                           st.Direct("lc_callee_lib$Crate_heavy", "Crate.heavy")) \
        == DeclaredParams((GUARANTEED,))
    assert declared_params(funcs, extensions,
                           st.Direct("lc_callee_lib$Crate_of", "Crate.of")) \
        == DeclaredParams((GUARANTEED,))
    assert declared_params(funcs, extensions,
                           st.Direct("other$Crate_of", "Crate.of")) is None


def test_an_instance_of_a_template_of_a_binary_library_is_found_with_no_unit():
    """The instance has no declaring unit in the build, so its symbol has no unit prefix."""
    funcs, extensions = FunctionTable(), ExtensionTable()
    funcs.declare("only_id__i32", FuncSig("only_id__i32", params=[Param("x", I32, None, None,
                                                                        0)]))
    assert declared_params(funcs, extensions, st.Direct("only_id__i32", "only_id__i32")) \
        == DeclaredParams((GUARANTEED,))
    assert declared_params(funcs, extensions,
                           st.Direct("unit$only_id__i32", "only_id__i32")) is None


def test_a_call_of_a_library_function_with_a_wrong_argument_count_is_refused():
    funcs, extensions = _library_tables()
    call = st.Call(p.Local(LocalId(1)), st.Direct("lc_callee_lib$shout", "shout"), (), BAD)
    block = SlirBlock(BlockId(0), (call,), tm.Return(BAD))
    function = SlirFunction("unit$probe", "probe", "unit", (),
                            (SlirLocal(LocalId(0), BLANK, None, None),
                             SlirLocal(LocalId(1), STRING, None, None)),
                            (block,), BlockId(0))
    with pytest.raises(InternalCompilerError) as caught:
        verify_function(function, drops=frozenset(),
                        declared=lambda each: declared_params(funcs, extensions, each))
    assert caught.value.code == "CE0158"
    assert caught.value.params["rule"] == CONVENTIONS
    assert "has 0 arguments, and @shout declares 1" in caught.value.params["detail"]
