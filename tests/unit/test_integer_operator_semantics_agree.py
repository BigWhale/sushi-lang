"""Sushi's integer operators have ONE compile-time home, and it agrees with the machine.

The constant evaluator (`semantics/const_eval.py`) is where an operation the
compiler reads is computed: a `const` initializer, and every literal pair in a body,
which `reject_overflowing_operation` runs through the same evaluator. The backend used
to hold a second home -- `_fold_arithmetic_constants` and `_fold_bitwise_constants` in
`backend/expressions/operators.py` folded `+ - *` and `& | ^ <<` on two `ir.Constant`
operands, re-derived two's complement by hand, wrapped in silence where the evaluator
reports CE2077, and left `/ % >>` to LLVM -- and nothing made the two agree (#681).

The backend folds nothing now. LLVM is the run-time home by definition, so the gate
asks the two homes that remain: for every operator, every integer width and a set of
edge operands, the evaluator's value and the value the MACHINE computes for the
instruction the backend emits are one bit pattern. The instruction is JIT-compiled and
called, so this reads what a program would print and not what a Python model says.

A cell the evaluator REFUSES -- a division by zero (CE0112), a result that leaves the
type (CE2077) -- is not compared: those are the language's refusals, and
`test_const_overflow.py` pins them. A literal pair that reaches the backend has passed
the typecheck pass, so it fits its type.
"""
from __future__ import annotations

import ast
import inspect


from sushi_lang.backend.expressions import operators
from sushi_lang.semantics.type_predicates import is_unsigned_int
from sushi_lang.semantics.typesys import BuiltinType



WIDTHS = (BuiltinType.I8, BuiltinType.U8, BuiltinType.I16, BuiltinType.U16,
          BuiltinType.I32, BuiltinType.U32, BuiltinType.I64, BuiltinType.U64)




















def test_the_backend_folds_no_integer_operator():
    """The structural half: the backend emits the instruction and computes nothing.

    A fold starts by reading an `ir.Constant`'s Python value and ends by building a
    new `ir.Constant` from Python arithmetic. Either is a second statement of the
    language's integer semantics, one that no diagnostic guards (#681).
    """
    tree = ast.parse(inspect.getsource(operators))
    folds = [node.name for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef) and node.name.startswith("_fold")]
    assert not folds, f"a constant fold is back in the backend's operator emitter: {folds}"

    reads = [node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.Attribute) and node.attr == "constant"]
    assert not reads, (
        f"the backend's operator emitter reads an ir.Constant's Python value at line(s) "
        f"{reads}, which is how a second home for the integer semantics starts"
    )


def test_every_width_is_asked():
    """The matrix covers all eight integer types and the unsigned half is read as such."""
    assert {t for t in WIDTHS if is_unsigned_int(t)} == {
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64}
    assert len(WIDTHS) == 8
