"""The backend compares and combines integer operands of ONE width, or it stops (#840).

The typecheck pass refuses a mixed width for `== != < <= > >=` and for `& | ^` (CE2510),
and `_emit_shift` brings a shift count to the value's width itself. So the comparison
and bitwise emitters never receive two integer operands of different widths. They used
to reconcile the widths anyway: the comparison squeezed both operands to i32 and
compared them signed (an i64 truncated, an unsigned operand took the wrong sign), and
the bitwise emitter extended or truncated the right operand. A mixed pair that a future
typecheck change lets through would have compiled to a wrong value with no diagnostic.
Now it is the internal error CE0139.
"""
from __future__ import annotations

import pytest
from llvmlite import ir

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.expressions import operators
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.ast import BinaryOp, IntLit
from sushi_lang.semantics.passes.collect import PerkImplementationTable
from sushi_lang.semantics.typesys import BuiltinType


COMPARISONS = ("==", "!=", "<", "<=", ">", ">=")
BITWISE = ("&", "|", "^")

MIXED_PAIRS = ((64, 32), (32, 64), (8, 32), (16, 8))


def _codegen() -> LLVMCodegen:
    codegen = LLVMCodegen("operator_widths", perk_impl_table=PerkImplementationTable())
    fn = ir.Function(codegen.module, ir.FunctionType(ir.VoidType(), []), "probe")
    codegen.func = fn
    codegen.builder = ir.IRBuilder(fn.append_basic_block("entry"))
    return codegen


def _emit_comparison_of(codegen: LLVMCodegen, op: str, lhs: ir.Value, rhs: ir.Value):
    node = BinaryOp(loc=None, op=op, left=IntLit(loc=None, value=1), right=IntLit(loc=None, value=2))
    values = {id(node.left): lhs, id(node.right): rhs}
    codegen.expressions.emit_expr = lambda expr, **_: values[id(expr)]  # type: ignore[method-assign]
    return operators.emit_comparison(codegen, node, to_i1=True)


@pytest.mark.parametrize("op", COMPARISONS)
@pytest.mark.parametrize("widths", MIXED_PAIRS)
def test_a_mixed_width_comparison_is_an_internal_error(op, widths):
    codegen = _codegen()
    lhs = ir.Constant(ir.IntType(widths[0]), 1)
    rhs = ir.Constant(ir.IntType(widths[1]), 2)
    with pytest.raises(InternalCompilerError) as caught:
        _emit_comparison_of(codegen, op, lhs, rhs)
    assert caught.value.code == "CE0139"


@pytest.mark.parametrize("op", BITWISE)
@pytest.mark.parametrize("widths", MIXED_PAIRS)
def test_a_mixed_width_bitwise_operation_is_an_internal_error(op, widths):
    codegen = _codegen()
    lhs = ir.Constant(ir.IntType(widths[0]), 1)
    rhs = ir.Constant(ir.IntType(widths[1]), 2)
    with pytest.raises(InternalCompilerError) as caught:
        operators.emit_bitwise(codegen, op, lhs, rhs, BuiltinType.I64)
    assert caught.value.code == "CE0139"


@pytest.mark.parametrize("op", BITWISE)
def test_an_equal_width_bitwise_operation_is_one_instruction(op):
    codegen = _codegen()
    lhs = ir.Constant(ir.IntType(64), 1)
    rhs = ir.Constant(ir.IntType(64), 2)
    result = operators.emit_bitwise(codegen, op, lhs, rhs, BuiltinType.I64)
    assert result.type == ir.IntType(64)
    assert result.opname == {"&": "and", "|": "or", "^": "xor"}[op]


@pytest.mark.parametrize("op", COMPARISONS)
def test_an_equal_width_comparison_keeps_the_operand_width(op):
    codegen = _codegen()
    lhs = ir.Constant(ir.IntType(64), 1)
    rhs = ir.Constant(ir.IntType(64), 2)
    result = _emit_comparison_of(codegen, op, lhs, rhs)
    assert result.opname == "icmp"
    assert [operand.type for operand in result.operands] == [ir.IntType(64), ir.IntType(64)]


def test_the_i32_squeeze_is_gone():
    assert not hasattr(operators, "_ensure_i32")
