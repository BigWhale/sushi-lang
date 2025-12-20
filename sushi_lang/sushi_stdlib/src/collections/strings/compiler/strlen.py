"""
String Length Intrinsic

Pure LLVM IR implementation of strlen for null-terminated strings.
Calculates the length of a C string by scanning for the null terminator.

This replaces the C standard library strlen() function.
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types


def emit_strlen_intrinsic(module: ir.Module) -> ir.Function:
    """Emit the string length intrinsic function.

    Calculates the length of a null-terminated string by scanning for '\0'.
    Returns the number of bytes before the null terminator.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 llvm_strlen(i8* str)
    """
    func_name = "llvm_strlen"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64 = get_basic_types()

    # Function signature: i32 llvm_strlen(i8* str)
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    loop_header = func.append_basic_block("loop_header")
    loop_body = func.append_basic_block("loop_body")
    loop_exit = func.append_basic_block("loop_exit")

    # Entry: Initialize counter
    builder = ir.IRBuilder(entry_block)
    counter = builder.alloca(i32, name="counter")
    builder.store(ir.Constant(i32, 0), counter)
    builder.branch(loop_header)

    # Loop header: Load current character
    builder = ir.IRBuilder(loop_header)
    idx = builder.load(counter, name="idx")
    char_ptr = builder.gep(func.args[0], [idx], name="char_ptr")
    char = builder.load(char_ptr, name="char")

    # Check if character is null terminator
    null_char = ir.Constant(i8, 0)
    is_null = builder.icmp_unsigned("==", char, null_char, name="is_null")
    builder.cbranch(is_null, loop_exit, loop_body)

    # Loop body: Increment counter and continue
    builder = ir.IRBuilder(loop_body)
    idx = builder.load(counter, name="idx")
    next_idx = builder.add(idx, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, counter)
    builder.branch(loop_header)

    # Loop exit: Return counter
    builder = ir.IRBuilder(loop_exit)
    length = builder.load(counter, name="length")
    builder.ret(length)

    return func
