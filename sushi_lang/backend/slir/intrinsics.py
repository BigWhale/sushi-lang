"""The calls of an `Intrinsic` callee: `interp`, `print` and `println` (docs/design/ir.md 8.6).

Each row gives the LLVM values of every operand to an emitter family of the backend (ruling
Q7), and the family reads the type of each operand from the callee. Every operand is
evaluated before the family emits anything (ir.md 11.9). An operand is only read.
"""
from __future__ import annotations

from typing import Callable

from llvmlite import ir

from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import constant_value, operand_value
from sushi_lang.backend.types.display import emit_interpolation
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.type_predicates import is_numeric_type
from sushi_lang.semantics.typesys import BuiltinType, Type


def operands(state: FunctionState, statement: st.Call) -> list[tuple[ir.Value, Type]]:
    """The value of each argument of an intrinsic call, with the type that the callee gives
    it, in order."""
    callee = statement.callee
    if not isinstance(callee, st.Builtin) or len(callee.types) != len(statement.args):
        state.fault(f"an intrinsic call with {len(statement.args)} arguments and the "
                    f"callee {callee}")
    values = [operand_value(state, operand) for operand, _ in statement.args]
    return list(zip(values, callee.types, strict=True))


def _interp(state: FunctionState, statement: st.Call) -> ir.Value:
    """A fresh owned string, from the string builder."""
    return emit_interpolation(state.codegen, operands(state, statement))


def _print(state: FunctionState, statement: st.Call) -> ir.Value:
    return _write(state, statement, is_line=False)


def _println(state: FunctionState, statement: st.Call) -> ir.Value:
    return _write(state, statement, is_line=True)


def _write(state: FunctionState, statement: st.Call, *, is_line: bool) -> ir.Value:
    """One value to the console, through the print emitter. A value that prints by its
    `Display` form stops for now. The call answers `~`."""
    parts = operands(state, statement)
    if len(parts) != 1:
        state.fault(f"a print of {len(parts)} values")
    (value, ty), = parts
    if ty != BuiltinType.STRING and ty != BuiltinType.BOOL and not is_numeric_type(ty):
        state.stop(statement.callee)
    state.codegen.runtime.formatting.emit_print_value(value, is_line=is_line,
                                                      semantic_type=ty)
    return constant_value(state, k.Blank())


#: One row for each intrinsic that the emitter handles, by the name of the intrinsic.
INTRINSICS: dict[str, Callable[[FunctionState, st.Call], ir.Value]] = {
    "interp": _interp,
    "print": _print,
    "println": _println,
}
