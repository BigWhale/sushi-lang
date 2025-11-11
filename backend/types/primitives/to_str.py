"""
Built-in extension methods for primitive types (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool).

Implemented methods:
- to_str() -> string: Convert primitive values to string representation

Future possibilities:
- i32.abs() -> i32
- i32.min(other) -> i32
- i32.max(other) -> i32
- f64.abs() -> f64
- bool.not() -> bool (alias for unary not)
"""

from typing import Any
from semantics.ast import MethodCall
from semantics.typesys import BuiltinType, Type
import llvmlite.ir as ir
from internals import errors as er
from stdlib.src.common import register_builtin_method, BuiltinMethod
from stdlib.src import conversions, ir_common
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from internals.errors import raise_internal_error


# Validation function for to_str() method
def _validate_to_str(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate to_str() method call on primitive types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{target_type}.to_str", expected=0, got=len(call.args))


# Type conversion specifications - maps builtin type to (is_signed, bit_width) or conversion function
_TYPE_CONVERSION_SPECS = {
    BuiltinType.I8: ('integer', True, 8),
    BuiltinType.I16: ('integer', True, 16),
    BuiltinType.I32: ('integer', True, 32),
    BuiltinType.I64: ('integer', True, 64),
    BuiltinType.U8: ('integer', False, 8),
    BuiltinType.U16: ('integer', False, 16),
    BuiltinType.U32: ('integer', False, 32),
    BuiltinType.U64: ('integer', False, 64),
    BuiltinType.F32: ('float', False, 32),  # is_double=False
    BuiltinType.F64: ('float', True, 64),   # is_double=True
    BuiltinType.BOOL: ('bool', None, None),
    BuiltinType.STRING: ('string', None, None),
}


def _emit_generic_to_str(prim_type: BuiltinType) -> Any:
    """Create a to_str() emitter function for the given primitive type.

    This factory function eliminates the need for 12 nearly identical emitter functions
    by creating them dynamically based on type specifications.

    Args:
        prim_type: The primitive type to create an emitter for.

    Returns:
        An emitter function that converts the primitive type to string.
    """
    spec = _TYPE_CONVERSION_SPECS.get(prim_type)
    if not spec:
        raise_internal_error("CE0073", type=prim_type)

    kind, param1, param2 = spec

    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Generic to_str() emitter created by factory."""
        if len(call.args) != 0:
            raise_internal_error("CE0078", got=len(call.args))

        if kind == 'integer':
            return codegen.runtime.formatting.emit_integer_to_string(receiver_value, is_signed=param1, bit_width=param2)
        elif kind == 'float':
            return codegen.runtime.formatting.emit_float_to_string(receiver_value, is_double=param1)
        elif kind == 'bool':
            return codegen.runtime.formatting.emit_bool_to_string(receiver_value)
        elif kind == 'string':
            # Identity operation for strings
            return receiver_value
        else:
            raise_internal_error("CE0075", kind=kind)

    return emitter


# Register to_str() methods for all primitive types
primitive_types = [
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
]

# Generate emitters dynamically using the factory function
emitters = {prim_type: _emit_generic_to_str(prim_type) for prim_type in primitive_types}

# Helper function for semantic analysis integration
def validate_builtin_primitive_method_with_validator(call: MethodCall, target_type: Type, reporter: Any, validator: Any) -> None:
    """Validate builtin primitive method calls using the registered validators.

    This function looks up the registered BuiltinMethod and calls its semantic validator.
    It handles both to_str and hash methods.
    """
    from stdlib.src.common import get_builtin_method

    method = get_builtin_method(target_type, call.method)
    if method is None:
        raise_internal_error("CE0074", type=target_type, method=call.method)

    # Call the method's registered semantic validator
    method.semantic_validator(call, target_type, reporter)


for prim_type in primitive_types:
    register_builtin_method(
        prim_type,
        BuiltinMethod(
            name="to_str",
            parameter_types=[],
            return_type=BuiltinType.STRING,
            description=f"Convert {prim_type} to string representation",
            semantic_validator=_validate_to_str,
            llvm_emitter=emitters[prim_type],
        )
    )


# ==============================================================================
# Standalone IR Generation (Phase 4)
# ==============================================================================

def generate_module_ir() -> ir.Module:
    """Generate standalone LLVM IR module for primitive type extension methods.

    This function generates IR for all primitive type .to_str() methods without
    depending on the compiler's codegen infrastructure. It uses the ir_common
    utilities for external function declarations and string operations.

    Returns:
        An LLVM IR module containing all primitive to_str() implementations.
    """
    module = ir_common.create_stdlib_module("core.primitives")

    # Generate to_str() for all primitive types
    _generate_i8_to_str(module)
    _generate_i16_to_str(module)
    _generate_i32_to_str(module)
    _generate_i64_to_str(module)
    _generate_u8_to_str(module)
    _generate_u16_to_str(module)
    _generate_u32_to_str(module)
    _generate_u64_to_str(module)
    _generate_f32_to_str(module)
    _generate_f64_to_str(module)
    _generate_bool_to_str(module)
    _generate_string_to_str(module)

    return module


# Helper functions for generating individual to_str() methods

def _generate_i8_to_str(module: ir.Module) -> None:
    """Generate i8.to_str() -> string"""
    _generate_integer_to_str(module, ir.IntType(INT8_BIT_WIDTH), "i8", is_signed=True, bit_width=INT8_BIT_WIDTH)


def _generate_i16_to_str(module: ir.Module) -> None:
    """Generate i16.to_str() -> string"""
    _generate_integer_to_str(module, ir.IntType(16), "i16", is_signed=True, bit_width=16)


def _generate_i32_to_str(module: ir.Module) -> None:
    """Generate i32.to_str() -> string"""
    _generate_integer_to_str(module, ir.IntType(INT32_BIT_WIDTH), "i32", is_signed=True, bit_width=INT32_BIT_WIDTH)


def _generate_i64_to_str(module: ir.Module) -> None:
    """Generate i64.to_str() -> string"""
    _generate_integer_to_str(module, ir.IntType(INT64_BIT_WIDTH), "i64", is_signed=True, bit_width=INT64_BIT_WIDTH)


def _generate_u8_to_str(module: ir.Module) -> None:
    """Generate u8.to_str() -> string"""
    _generate_integer_to_str(module, ir.IntType(INT8_BIT_WIDTH), "u8", is_signed=False, bit_width=INT8_BIT_WIDTH)


def _generate_u16_to_str(module: ir.Module) -> None:
    """Generate u16.to_str() -> string"""
    _generate_integer_to_str(module, ir.IntType(16), "u16", is_signed=False, bit_width=16)


def _generate_u32_to_str(module: ir.Module) -> None:
    """Generate u32.to_str() -> string"""
    _generate_integer_to_str(module, ir.IntType(INT32_BIT_WIDTH), "u32", is_signed=False, bit_width=INT32_BIT_WIDTH)


def _generate_u64_to_str(module: ir.Module) -> None:
    """Generate u64.to_str() -> string"""
    _generate_integer_to_str(module, ir.IntType(INT64_BIT_WIDTH), "u64", is_signed=False, bit_width=INT64_BIT_WIDTH)


def _generate_f32_to_str(module: ir.Module) -> None:
    """Generate f32.to_str() -> string"""
    _generate_float_to_str(module, ir.FloatType(), "f32", is_double=False)


def _generate_f64_to_str(module: ir.Module) -> None:
    """Generate f64.to_str() -> string"""
    _generate_float_to_str(module, ir.DoubleType(), "f64", is_double=True)


def _generate_bool_to_str(module: ir.Module) -> None:
    """Generate bool.to_str() -> string"""
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i8_ptr = i8.as_pointer()
    i32 = ir.IntType(INT32_BIT_WIDTH)
    string_struct = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: sushi_bool_to_str(i8 value) -> {i8*, i32}
    fn_ty = ir.FunctionType(string_struct, [i8])
    func = ir.Function(module, fn_ty, name="sushi_bool_to_str")

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    # Use ir_common helper to generate bool to string conversion
    result = conversions.emit_bool_to_string(module, builder, func.args[0])

    builder.ret(result)


def _generate_string_to_str(module: ir.Module) -> None:
    """Generate string.to_str() -> string (identity operation)"""
    i8_ptr = ir.IntType(INT8_BIT_WIDTH).as_pointer()
    i32 = ir.IntType(INT32_BIT_WIDTH)
    string_struct = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: sushi_string_to_str({i8*, i32} value) -> {i8*, i32}
    fn_ty = ir.FunctionType(string_struct, [string_struct])
    func = ir.Function(module, fn_ty, name="sushi_string_to_str")

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    # Identity operation - just return the input struct
    builder.ret(func.args[0])


def _generate_integer_to_str(
    module: ir.Module,
    int_type: ir.Type,
    type_name: str,
    is_signed: bool,
    bit_width: int
) -> None:
    """Generate integer to_str() method implementation.

    Args:
        module: The LLVM module to add the function to.
        int_type: The LLVM integer type.
        type_name: The name of the type (for function naming).
        is_signed: True for signed integers, False for unsigned.
        bit_width: Bit width of the integer type.
    """
    i8_ptr = ir.IntType(INT8_BIT_WIDTH).as_pointer()
    i32 = ir.IntType(INT32_BIT_WIDTH)
    string_struct = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: sushi_{type}_to_str(int_type value) -> {i8*, i32}
    fn_ty = ir.FunctionType(string_struct, [int_type])
    func = ir.Function(module, fn_ty, name=f"sushi_{type_name}_to_str")

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    # Use conversions helper to generate integer to string conversion
    result = conversions.emit_integer_to_string(
        module, builder, func.args[0], is_signed, bit_width
    )

    builder.ret(result)


def _generate_float_to_str(
    module: ir.Module,
    float_type: ir.Type,
    type_name: str,
    is_double: bool
) -> None:
    """Generate float to_str() method implementation.

    Args:
        module: The LLVM module to add the function to.
        float_type: The LLVM float type.
        type_name: The name of the type (for function naming).
        is_double: True for f64, False for f32.
    """
    i8_ptr = ir.IntType(INT8_BIT_WIDTH).as_pointer()
    i32 = ir.IntType(INT32_BIT_WIDTH)
    string_struct = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: sushi_{type}_to_str(float_type value) -> {i8*, i32}
    fn_ty = ir.FunctionType(string_struct, [float_type])
    func = ir.Function(module, fn_ty, name=f"sushi_{type_name}_to_str")

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    # Use ir_common helper to generate float to string conversion
    result = conversions.emit_float_to_string(
        module, builder, func.args[0], is_double
    )

    builder.ret(result)