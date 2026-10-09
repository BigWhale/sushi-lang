"""Sushi's integer operators have ONE compile-time home, and it agrees with the machine.

The constant evaluator (`semantics/const_eval.py`) is where an operation the
compiler reads is computed: a `const` initializer, and every literal pair in a body,
which `reject_overflowing_nest` runs through the same evaluator. The backend used
to hold a second home -- `_fold_arithmetic_constants` and `_fold_bitwise_constants` in
`backend/expressions/operators.py` folded `+ - *` and `& | ^ <<` on two `ir.Constant`
operands, re-derived two's complement by hand, wrapped in silence where the evaluator
reports CE2077, and left `/ % >>` to LLVM -- and nothing made the two agree (#681).

The backend folds nothing now, and LLVM is the run-time home by definition. This module
reads the backend's operator emitter as Python source and refuses a fold in it: a
`_fold*` function, or a read of an `ir.Constant`'s Python value.
"""
from __future__ import annotations

import ast
import inspect

from sushi_lang.backend.expressions import operators


def test_the_backend_folds_no_integer_operator():
    """The backend emits the instruction and computes nothing.

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
