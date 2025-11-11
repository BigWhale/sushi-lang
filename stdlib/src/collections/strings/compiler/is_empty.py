"""
Inline emission for string.is_empty() method.

This function is emitted directly into the compiled module during compilation,
allowing is_empty() to work without requiring `use <collections/strings>`.

Used by:
- Direct .is_empty() calls in user code
- foreach loops with .lines() iterators
"""

import llvmlite.ir as ir
from stdlib.src.type_definitions import get_string_types


def emit_string_is_empty_intrinsic(module: ir.Module) -> ir.Function:
    """Emit string.is_empty() intrinsic as inline LLVM IR.

    Returns true if the string has 0 bytes.
    This is an O(1) operation - just checks size field.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i8 llvm_string_is_empty({ i8*, i32 } str)
    """
    func_name = "llvm_string_is_empty"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: i8 llvm_string_is_empty({ i8*, i32 } str)
    fn_ty = ir.FunctionType(i8, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Create entry block
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract size field (index 1)
    size = builder.extract_value(func.args[0], 1, name="size")

    # Compare to 0
    is_empty = builder.icmp_unsigned("==", size, ir.Constant(i32, 0), name="is_empty")

    # Convert i1 to i8 (bool representation in Sushi)
    result = builder.zext(is_empty, i8, name="result")
    builder.ret(result)

    return func
