"""`calculate_llvm_type_size` answers the ABI size of a type, padding included.

A stdlib generator sizes a Result or Maybe payload with it, and copies that many bytes
of the Ok value. A struct with padding inside it, such as the tuple `(i32, u64, u64, i32)`,
has more bytes than the sum of its fields; a sum cuts its last field off. LLVM's own data
layout is the reference.
"""
from __future__ import annotations

import llvmlite.binding as llvm
import pytest
from llvmlite import ir

from sushi_lang.backend.expressions.memory import calculate_llvm_type_size
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_type, get_tuple_type

i8, i16, i32, i64 = ir.IntType(8), ir.IntType(16), ir.IntType(32), ir.IntType(64)

CASES = [
    i8, i32, i64, ir.DoubleType(), ir.FloatType(), i8.as_pointer(),
    get_string_type(),
    get_tuple_type([i32, i32, i64, i64]),
    get_tuple_type([i32, i64, i64, i32]),
    get_tuple_type([i8, i64]),
    get_tuple_type([i64, i8]),
    get_tuple_type([i16, i8, i32]),
    get_tuple_type([get_string_type(), i32]),
    get_tuple_type([i32, get_string_type()]),
    get_tuple_type([get_tuple_type([i64, i8]), i8]),
    ir.ArrayType(get_tuple_type([i64, i8]), 3),
    ir.LiteralStructType([i32, ir.ArrayType(i64, 4)]),
]


@pytest.fixture(scope="module")
def target_data():
    llvm.initialize_native_target()
    return llvm.Target.from_default_triple().create_target_machine().target_data


@pytest.mark.parametrize("llvm_type", CASES, ids=str)
def test_the_size_is_the_abi_size(target_data, llvm_type):
    assert calculate_llvm_type_size(llvm_type) == llvm_type.get_abi_size(target_data)
