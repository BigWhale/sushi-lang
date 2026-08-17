"""Inline emission for string.is_empty() method."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types


def emit_string_is_empty_intrinsic(module: ir.Module) -> ir.Function:
    """Emit `i8 llvm_string_is_empty({i8*, i32} str)`."""
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
