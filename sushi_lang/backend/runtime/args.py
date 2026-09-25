"""Command-line argument handling for main() wrapper generation."""
from typing import TYPE_CHECKING
import llvmlite.ir as ir
from sushi_lang.semantics.typesys import DynamicArrayType, BuiltinType
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.backend import gep_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def allocate_string_array_data(codegen: 'LLVMCodegen', count: ir.Value) -> ir.Value:
    """Allocate memory for a string[] dynamic array data buffer."""
    string_struct_type = codegen.types.ll_type(BuiltinType.STRING)

    # Calculate element size with proper alignment
    # Formula: aligned_size = ((size + align - 1) / align) * align
    # Size is 12 bytes, alignment is 8 bytes, so stride is 16 bytes
    size_bytes = codegen.types.get_type_size_bytes(BuiltinType.STRING)  # 12
    alignment = codegen.types.get_type_alignment(BuiltinType.STRING)   # 8
    element_size = ((size_bytes + alignment - 1) // alignment) * alignment  # 16

    total_bytes = codegen.builder.mul(count, ir.Constant(codegen.i32, element_size), name="total_bytes")

    total_bytes_64 = codegen.builder.zext(total_bytes, ir.IntType(64), name="total_bytes_64")

    data_ptr = emit_malloc(codegen, codegen.builder, total_bytes_64)

    string_struct_ptr_type = ir.PointerType(string_struct_type)
    return codegen.builder.bitcast(data_ptr, string_struct_ptr_type, name="typed_string_array")


def populate_string_array_from_argv(
    codegen: 'LLVMCodegen',
    argc: ir.Value,
    argv: ir.Value,
    target_array_data: ir.Value
) -> None:
    """Convert C argv array to fat pointer strings in target array."""
    builder = codegen.builder
    zero_i32 = ir.Constant(codegen.i32, 0)
    one_i32 = ir.Constant(codegen.i32, 1)
    two_i32 = ir.Constant(codegen.i32, 2)
    strlen_func = codegen.runtime.libc_strings.strlen

    def store_one(argv_i_ptr: ir.Value, index: ir.Value) -> None:
        argv_i = builder.load(argv_i_ptr, name="argv_i")
        strlen_result = builder.call(strlen_func, [argv_i], name="strlen_result")
        string_slot = builder.gep(target_array_data, [index], name="string_slot")
        builder.store(argv_i, builder.gep(string_slot, [zero_i32, zero_i32], name="ptr_field"))
        builder.store(strlen_result, builder.gep(string_slot, [zero_i32, one_i32], name="len_field"))
        # argv strings alias C process memory - a borrowed view, never heap-owned.
        # Leaving this byte as malloc garbage risks the RAII destructor free()ing argv.
        builder.store(ir.Constant(codegen.i8, 0),
                      builder.gep(string_slot, [zero_i32, two_i32], name="owned_field"))

    emit_container_walk(codegen, argv, argc, store_one, prefix="argv")


def generate_argc_argv_conversion(codegen: 'LLVMCodegen', argc: ir.Value, argv: ir.Value) -> ir.Value:
    """Convert C-style argc/argv to Sushi string[] dynamic array."""
    builder = codegen.builder
    string_array_type = DynamicArrayType(BuiltinType.STRING)

    args_array_alloca = codegen.dynamic_arrays.declare_dynamic_array("cmd_args", string_array_type)

    typed_data_ptr = allocate_string_array_data(codegen, argc)

    len_ptr = gep_utils.gep_dynamic_array_len(codegen, args_array_alloca)
    cap_ptr = gep_utils.gep_dynamic_array_cap(codegen, args_array_alloca)
    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, args_array_alloca, "data_ptr_ptr")

    builder.store(argc, len_ptr)           # length = argc
    builder.store(argc, cap_ptr)           # capacity = argc
    builder.store(typed_data_ptr, data_ptr_ptr)  # data pointer

    populate_string_array_from_argv(codegen, argc, argv, typed_data_ptr)

    return args_array_alloca
