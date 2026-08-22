"""`get_element_size_constant` must answer for every type an element can have.

The table it replaced was written by hand, one arm per LLVM type, and it had arms for
i32, i8, pointer, float, double and struct -- and none for the other integer widths. So
`i16[]`, `i64[]`, `u16[]`, `u64[]` and the four matching `List@(T)` instantiations were a
CE0079 internal error on ordinary code (#375). Its sibling `calculate_llvm_type_size` was
total over `ir.IntType` all along, which is what makes "the two disagree" the shape of the
bug rather than "the language does not support i64".

This gate asks the question the hand-written table could not: for every element type the
type system can produce, does the helper return a size, and is that size right?
"""
from __future__ import annotations

import pytest
from llvmlite import ir

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.expressions.memory import (
    calculate_llvm_type_size,
    get_element_size_constant,
)
from sushi_lang.backend.types.core.sizing import TypeSizing
from sushi_lang.semantics.passes.collect import EnumTable, StructTable
from sushi_lang.semantics.typesys import BuiltinType

# Every primitive an array or a List can hold, with its size in bytes.
PRIMITIVE_SIZES = {
    BuiltinType.I8: 1,
    BuiltinType.I16: 2,
    BuiltinType.I32: 4,
    BuiltinType.I64: 8,
    BuiltinType.U8: 1,
    BuiltinType.U16: 2,
    BuiltinType.U32: 4,
    BuiltinType.U64: 8,
    BuiltinType.F32: 4,
    BuiltinType.F64: 8,
    BuiltinType.BOOL: 1,
}


@pytest.fixture
def codegen():
    """A codegen with a builder, which the struct arm needs for its GEP."""
    cg = LLVMCodegen()
    fn = ir.Function(cg.module, ir.FunctionType(ir.VoidType(), []), name="_size_probe")
    cg.builder = ir.IRBuilder(fn.append_basic_block("entry"))
    return cg


@pytest.mark.parametrize("builtin,expected", sorted(PRIMITIVE_SIZES.items(), key=lambda kv: kv[0].name))
def test_every_primitive_element_has_a_size(codegen, builtin, expected):
    llvm_type = codegen.types.ll_type(builtin)
    size = get_element_size_constant(codegen, llvm_type)
    assert int(size.constant) == expected, (
        f"{builtin.name} ({llvm_type}) sized {size.constant}, expected {expected}"
    )


def test_a_string_element_has_a_size(codegen):
    """A `string` is a 3-field fat pointer, and the struct arm measures it."""
    size = get_element_size_constant(codegen, codegen.types.string_struct)
    assert size is not None


def test_a_pointer_element_has_a_size(codegen):
    size = get_element_size_constant(codegen, ir.PointerType(codegen.types.i8))
    assert int(size.constant) == 8


# i1 is absent by construction, not by exception: it is not an element type, because `bool`
# is lowered to i8. Filtering it out beats skipping it, so a reported skip always means the
# environment could not run the case.
@pytest.mark.parametrize("width", [8, 16, 32, 64])
def test_the_two_size_helpers_agree_on_integers(codegen, width):
    """The sibling was total over integer widths; this one must be too.

    They disagreeing IS the bug: one is a hand-written chain of `==` comparisons, the
    other reads the width off the type.
    """
    llvm_type = ir.IntType(width)
    assert int(get_element_size_constant(codegen, llvm_type).constant) == (
        calculate_llvm_type_size(llvm_type)
    )


def test_all_three_size_authorities_agree(codegen):
    """There are THREE places that answer "how big is this", and they must not drift.

    `get_element_size_constant` and `calculate_llvm_type_size` answer from the LLVM type;
    `TypeSizing.get_type_size_bytes` answers from the semantic type. Only the first was
    partial, but "more than one answer site" is the shape behind #239, #272, #284 and
    #375 alike -- so the gate pins the agreement, not just the one that broke.
    """
    sizing = TypeSizing(StructTable(), EnumTable())
    disagreements = []
    for builtin, expected in PRIMITIVE_SIZES.items():
        llvm_type = codegen.types.ll_type(builtin)
        answers = {
            "get_element_size_constant": int(
                get_element_size_constant(codegen, llvm_type).constant),
            "calculate_llvm_type_size": calculate_llvm_type_size(llvm_type),
            "TypeSizing.get_type_size_bytes": sizing.get_type_size_bytes(builtin),
        }
        if set(answers.values()) != {expected}:
            disagreements.append(f"{builtin.name}: {answers} (expected {expected})")
    assert not disagreements, "the size authorities disagree:\n  " + "\n  ".join(disagreements)
