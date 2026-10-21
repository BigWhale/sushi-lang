"""Sushi's integer operators have ONE compile-time home: the kernel `semantics/integer_ops.py`.

The kernel is pure functions over an operator, its operand values and the integer type of
the operation. The AST constant evaluator (`semantics/const_eval.py`) calls it for a
`const` initializer, and the readable fold of the typecheck pass
(`passes/types/fold.py`, docs/design/ir.md 7.4) calls it for each operation in a body
that the compiler can read. So no caller holds a copy of the arithmetic.

The backend used to hold a second home -- `_fold_arithmetic_constants` and
`_fold_bitwise_constants` in `backend/expressions/operators.py` folded `+ - *` and
`& | ^ <<` on two `ir.Constant` operands, re-derived two's complement by hand, wrapped in
silence where the evaluator reports CE2077, and left `/ % >>` to LLVM -- and nothing made
the two agree (#681). The backend folds nothing now, and LLVM is the run-time home by
definition.

This module holds three rules. The kernel gives the machine's answer for each cell of the
table below, which names the cells where a reading at the wrong width or with Python's own
operator differs. The evaluator holds no integer operator of its own: it reads the source
of `const_eval.py` and refuses an operator that only an integer has. The backend's
operator emitters fold nothing: a `_fold*` function, or a read of an `ir.Constant`'s Python
value, is refused in the AST operator emitter and in the module that emits the SLIR
`Binary` and `Unary` rvalues (`backend/slir/values.py`).
"""
from __future__ import annotations

import ast
import inspect

import pytest

from sushi_lang.backend.expressions import operators
from sushi_lang.backend.slir import values as slir_values
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics import const_eval
from sushi_lang.semantics.integer_ops import (
    Folded, Refusal, binary, cast, compare, complement, negate,
)
from sushi_lang.semantics.typesys import BuiltinType

I8, U8, I32, I64, U64 = (BuiltinType.I8, BuiltinType.U8, BuiltinType.I32, BuiltinType.I64,
                         BuiltinType.U64)
U64_MAX = (1 << 64) - 1


@pytest.mark.parametrize("op,ty,left,right,answer", [
    # Division truncates toward zero, as sdiv and srem do; Python floors (#441).
    ("/", I32, -7, 2, Folded(-3)),
    ("%", I32, -7, 2, Folded(-1)),
    ("%", I32, 7, -2, Folded(1)),
    # The smallest signed value divided by -1 leaves the type. The remainder overflows
    # where the quotient does, and the value it reports is the quotient.
    ("/", I8, -128, -1, Folded(128, overflows=True)),
    ("%", I8, -128, -1, Folded(128, overflows=True)),
    # A remainder that its type cannot hold leaves the type too. A negated literal leaf is
    # not range-checked by the evaluator (that is CE2073's question), so it reaches here.
    ("%", U8, -7, -2, Folded(-1, overflows=True)),
    ("+", U8, 200, 100, Folded(300, overflows=True)),
    ("-", U8, 0, 1, Folded(-1, overflows=True)),
    ("*", I8, 64, 2, Folded(128, overflows=True)),
    ("*", I8, -64, 2, Folded(-128)),
    # A width-defined operator keeps the bits that the type holds and never overflows.
    ("<<", I8, 1, 7, Folded(-128)),
    ("<<", U8, 255, 4, Folded(240)),
    ("&", I8, -1, 127, Folded(127)),
    ("^", U8, 255, 15, Folded(240)),
    ("|", I32, 1, 2, Folded(3)),
    # A count past the width is defined (Go's rule): 0, or the sign bit of a signed `>>`.
    ("<<", U8, 1, 8, Folded(0)),
    (">>", I8, -7, 8, Folded(-1)),
    (">>", U8, 200, 8, Folded(0)),
    # `>>` of an unsigned value is logical, also above the `i64` maximum.
    (">>", U64, U64_MAX, 4, Folded(U64_MAX >> 4)),
    (">>", I64, -16, 2, Folded(-4)),
    ("/", U64, U64_MAX, 2, Folded(U64_MAX // 2)),
    ("+", U64, U64_MAX, 1, Folded(1 << 64, overflows=True)),
    # A zero divisor and a negative shift count have no value.
    ("/", I32, 1, 0, Refusal.DIVISION_BY_ZERO),
    ("%", U8, 1, 0, Refusal.DIVISION_BY_ZERO),
    ("<<", I32, 1, -1, Refusal.NEGATIVE_SHIFT),
    (">>", I32, 1, -1, Refusal.NEGATIVE_SHIFT),
])
def test_the_kernel_gives_the_machine_answer(op, ty, left, right, answer):
    assert binary(op, left, right, ty) == answer


@pytest.mark.parametrize("ty,value,answer", [
    (U8, 255, 0),
    (I64, 255, -256),
    (U64, 0, U64_MAX),
    (I8, 0, -1),
])
def test_the_complement_keeps_the_width(ty, value, answer):
    """`~255` is 0 as a `u8` and -256 as an `i64`: the type decides the value."""
    assert complement(value, ty) == answer


@pytest.mark.parametrize("ty,value,answer", [
    (I8, -128, Folded(128, overflows=True)),
    (I8, 127, Folded(-127)),
    (U8, 5, Folded(-5, overflows=True)),
    (U8, 0, Folded(0)),
])
def test_the_negation_is_overflow_checked(ty, value, answer):
    assert negate(value, ty) == answer


@pytest.mark.parametrize("ty,value,answer", [
    (U8, 300, 44),
    (I8, 200, -56),
    (U64, -1, U64_MAX),
    (I64, U64_MAX, -1),
    (I32, 7, 7),
])
def test_a_cast_keeps_the_bits_of_the_type(ty, value, answer):
    """A cast is the escape from the overflow rule: it truncates and never overflows."""
    assert cast(value, ty) == answer


@pytest.mark.parametrize("op,left,right,answer", [
    ("<", -1, 0, True),
    (">=", U64_MAX, 1 << 63, True),
    ("==", 3, 3, True),
    ("!=", 3, 3, False),
])
def test_a_comparison_reads_the_values(op, left, right, answer):
    assert compare(op, left, right) is answer


def test_a_type_that_is_no_integer_is_an_internal_error():
    with pytest.raises(InternalCompilerError):
        binary("+", 1, 2, BuiltinType.F64)


#: The Python operators that only an integer has. `%` is also the string format operator,
#: which the evaluator uses to print a float, so a `%` with a string on its left is text.
_INTEGER_ONLY_OPERATORS = (ast.LShift, ast.RShift, ast.BitAnd, ast.BitOr, ast.BitXor,
                           ast.FloorDiv, ast.Mod, ast.Invert)
_INTEGER_ONLY_FUNCTIONS = {"lshift", "rshift", "and_", "or_", "xor", "floordiv", "mod",
                           "inv", "invert"}


def _is_text_format(node: ast.AST) -> bool:
    return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str))


def test_the_evaluator_holds_no_integer_operator():
    """The evaluator calls the kernel; an integer operator in its source is a second home."""
    tree = ast.parse(inspect.getsource(const_eval))
    operators_found = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.AugAssign))
        and isinstance(node.op, _INTEGER_ONLY_OPERATORS) and not _is_text_format(node)
    ]
    functions_found = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        and node.value.id == "operator" and node.attr in _INTEGER_ONLY_FUNCTIONS
    ]
    width_reads = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sushi_lang.semantics.integer_width"
    ]
    assert not (operators_found or functions_found or width_reads), (
        f"const_eval.py computes an integer operator itself at line(s) "
        f"{sorted(operators_found + functions_found + width_reads)}; the kernel "
        f"`semantics/integer_ops.py` is the one home of the integer operator semantics"
    )


@pytest.mark.parametrize("module", [operators, slir_values], ids=lambda m: m.__name__)
def test_the_backend_folds_no_integer_operator(module):
    """The backend emits the instruction and computes nothing.

    A fold starts by reading an `ir.Constant`'s Python value and ends by building a
    new `ir.Constant` from Python arithmetic. Either is a second statement of the
    language's integer semantics, one that no diagnostic guards (#681).
    """
    tree = ast.parse(inspect.getsource(module))
    folds = [node.name for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef) and node.name.startswith("_fold")]
    assert not folds, f"a constant fold is back in the backend's operator emitter: {folds}"

    reads = [node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.Attribute) and node.attr == "constant"]
    assert not reads, (
        f"the backend's operator emitter reads an ir.Constant's Python value at line(s) "
        f"{reads}, which is how a second home for the integer semantics starts"
    )
