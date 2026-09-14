"""Sushi's integer operators have ONE compile-time home, and it agrees with the machine.

The constant evaluator (`semantics/passes/const_eval.py`) is where an operation the
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

import ctypes
import inspect
import itertools

import pytest
from llvmlite import binding as llvm
from llvmlite import ir

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.expressions import operators
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import BinaryOp, IntLit, UnaryOp
from sushi_lang.semantics.integer_width import integer_bit_width, integer_range
from sushi_lang.semantics.passes.collect.constants import ConstantTable
from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
from sushi_lang.semantics.type_predicates import is_unsigned_int
from sushi_lang.semantics.typesys import BuiltinType


ARITHMETIC = ("+", "-", "*", "/", "%")
BITWISE = ("&", "|", "^", "<<", ">>")
OPERATORS = ARITHMETIC + BITWISE

WIDTHS = (BuiltinType.I8, BuiltinType.U8, BuiltinType.I16, BuiltinType.U16,
          BuiltinType.I32, BuiltinType.U32, BuiltinType.I64, BuiltinType.U64)

C_TYPES = {
    BuiltinType.I8: ctypes.c_int8, BuiltinType.U8: ctypes.c_uint8,
    BuiltinType.I16: ctypes.c_int16, BuiltinType.U16: ctypes.c_uint16,
    BuiltinType.I32: ctypes.c_int32, BuiltinType.U32: ctypes.c_uint32,
    BuiltinType.I64: ctypes.c_int64, BuiltinType.U64: ctypes.c_uint64,
}

REFUSALS = {"CE0112", "CE2077"}


def _edge_values(ty: BuiltinType) -> list[int]:
    """The operands worth asking about: the ends of the range, small values, a bit pattern."""
    low, high = integer_range(ty)
    width = integer_bit_width(ty)
    pattern = int("01" * (width // 2), 2)
    values = {0, 1, 2, 3, 7, low, low + 1, high, high - 1, pattern, high ^ pattern}
    if low < 0:
        values |= {-1, -2, -7}
    return sorted(values)


def _shift_counts(ty: BuiltinType) -> list[int]:
    """In range, at the edge, and past the width: Go's rule is defined there too."""
    width = integer_bit_width(ty)
    return [0, 1, width // 2, width - 1, width, width + 5]


def _pairs(op: str, ty: BuiltinType) -> list[tuple[int, int]]:
    values = _edge_values(ty)
    if op in ("<<", ">>"):
        return list(itertools.product(values, _shift_counts(ty)))
    return list(itertools.product(values, values))


def _literal(value: int) -> IntLit | UnaryOp:
    if value < 0:
        return UnaryOp(loc=None, op="neg", expr=IntLit(loc=None, value=-value))
    return IntLit(loc=None, value=value)


def _evaluator_says(op: str, ty: BuiltinType, left: int, right: int):
    """The evaluator's value, or the set of codes it answered with instead."""
    reporter = Reporter()
    evaluator = ConstantEvaluator(reporter, ConstantTable())
    node = BinaryOp(loc=None, op=op, left=_literal(left), right=_literal(right))
    held = evaluator.evaluate(node, ty, None)
    if held is None:
        return None, {item.code for item in reporter.items}
    return held.value, set()


def _machine_says(op: str, ty: BuiltinType, pairs: list[tuple[int, int]]) -> list[int]:
    """One JIT-compiled function per pair, each returning the backend's instruction."""
    codegen = LLVMCodegen("operator_agreement")
    ll_type = codegen.types.ll_type(ty)
    mask = (1 << integer_bit_width(ty)) - 1

    for index, (left, right) in enumerate(pairs):
        fn = ir.Function(codegen.module, ir.FunctionType(ll_type, []), f"cell_{index}")
        codegen.func = fn
        codegen.builder = ir.IRBuilder(fn.append_basic_block("entry"))
        lhs = ir.Constant(ll_type, left & mask)
        rhs = ir.Constant(ll_type, right & mask)
        if op in ARITHMETIC:
            result = operators.emit_arithmetic(codegen, op, lhs, rhs, ty)
        else:
            result = operators.emit_bitwise(codegen, op, lhs, rhs, ty)
        codegen.builder.ret(result)

    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()
    module = llvm.parse_assembly(str(codegen.module))
    module.verify()
    target_machine = llvm.Target.from_default_triple().create_target_machine()
    engine = llvm.create_mcjit_compiler(module, target_machine)
    engine.finalize_object()

    c_type = C_TYPES[ty]
    return [ctypes.CFUNCTYPE(c_type)(engine.get_function_address(f"cell_{index}"))()
            for index in range(len(pairs))]


@pytest.mark.parametrize("ty", WIDTHS, ids=[t.name.lower() for t in WIDTHS])
@pytest.mark.parametrize("op", OPERATORS)
def test_the_evaluator_and_the_machine_give_one_bit_pattern(op, ty):
    pairs = _pairs(op, ty)
    compared: list[tuple[int, int]] = []
    expected: list[int] = []
    for left, right in pairs:
        value, codes = _evaluator_says(op, ty, left, right)
        if value is None:
            assert codes and codes <= REFUSALS, (
                f"{ty.name} {left} {op} {right}: the evaluator answered {codes}, which is "
                f"neither a value nor one of the two refusals a literal pair may get"
            )
            continue
        compared.append((left, right))
        expected.append(value)

    assert compared, f"{op} on {ty.name}: every cell was refused, the table is wrong"

    machine = _machine_says(op, ty, compared)
    mask = (1 << integer_bit_width(ty)) - 1
    mismatches = [
        f"{left} {op} {right}: evaluator {held}, machine {got}"
        for (left, right), held, got in zip(compared, expected, machine, strict=True)
        if (held & mask) != (got & mask)
    ]
    assert not mismatches, (
        f"{ty.name}: the evaluator and the machine disagree on {len(mismatches)} of "
        f"{len(compared)} cells:\n  " + "\n  ".join(mismatches)
    )


def test_a_signed_shift_right_fills_from_the_sign_bit_past_the_width():
    """The one cell whose answer is not 0: Go's rule, spelled out (`_emit_shift`)."""
    value, codes = _evaluator_says(">>", BuiltinType.I8, -7, 8)
    assert value == -1, codes
    assert _machine_says(">>", BuiltinType.I8, [(-7, 8)]) == [-1]

    value, codes = _evaluator_says(">>", BuiltinType.U8, 200, 8)
    assert value == 0, codes
    assert _machine_says(">>", BuiltinType.U8, [(200, 8)]) == [0]


def test_the_backend_folds_no_integer_operator():
    """The structural half: the backend emits the instruction and computes nothing.

    A fold starts by reading an `ir.Constant`'s Python value and ends by building a
    new `ir.Constant` from Python arithmetic. Either is a second statement of the
    language's integer semantics, one that no diagnostic guards (#681).
    """
    src = inspect.getsource(operators)
    assert "_fold_" not in src, "a constant fold is back in the backend's operator emitter"
    assert ".constant" not in src, (
        "the backend's operator emitter reads an ir.Constant's Python value, which is "
        "how a second home for the integer semantics starts"
    )


def test_every_width_is_asked():
    """The matrix covers all eight integer types and the unsigned half is read as such."""
    assert {t for t in WIDTHS if is_unsigned_int(t)} == {
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64}
    assert len(WIDTHS) == 8
