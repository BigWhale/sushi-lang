"""
String Comparison Intrinsic

Pure LLVM IR implementation of strcmp for fat pointer strings.
Compares two strings lexicographically, handling variable lengths.

This replaces the C standard library strcmp() function.
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types


def emit_strcmp_intrinsic(module: ir.Module) -> ir.Function:
    """Emit the string comparison intrinsic function.

    Compares two fat pointer strings {i8* data, i32 size} lexicographically.
    Returns:
    - negative value if str1 < str2
    - 0 if str1 == str2
    - positive value if str1 > str2

    This works with Sushi's fat pointer representation, comparing bounded strings
    rather than null-terminated strings.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 llvm_strcmp({ i8*, i32 } str1, { i8*, i32 } str2)
    """
    func_name = "llvm_strcmp"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: i32 llvm_strcmp({ i8*, i32 } str1, { i8*, i32 } str2)
    fn_ty = ir.FunctionType(i32, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str1"
    func.args[1].name = "str2"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    loop_header = func.append_basic_block("loop_header")
    loop_body = func.append_basic_block("loop_body")
    char_diff_block = func.append_basic_block("char_diff")
    loop_continue = func.append_basic_block("loop_continue")
    loop_exit = func.append_basic_block("loop_exit")
    size_diff_block = func.append_basic_block("size_diff")

    # Entry: Extract data pointers and sizes from fat pointers
    builder = ir.IRBuilder(entry_block)
    data1 = builder.extract_value(func.args[0], 0, name="data1")
    size1 = builder.extract_value(func.args[0], 1, name="size1")
    data2 = builder.extract_value(func.args[1], 0, name="data2")
    size2 = builder.extract_value(func.args[1], 1, name="size2")
    builder.branch(size_check_block)

    # Size check: Determine the minimum size to compare
    builder = ir.IRBuilder(size_check_block)
    size1_lt_size2 = builder.icmp_signed("<", size1, size2, name="size1_lt_size2")
    min_size = builder.select(size1_lt_size2, size1, size2, name="min_size")

    # Initialize loop counter
    idx = builder.alloca(i32, name="idx")
    builder.store(ir.Constant(i32, 0), idx)
    builder.branch(loop_header)

    # Loop header: Check if idx < min_size
    builder = ir.IRBuilder(loop_header)
    idx_val = builder.load(idx, name="idx_val")
    cond = builder.icmp_signed("<", idx_val, min_size, name="loop_cond")
    builder.cbranch(cond, loop_body, loop_exit)

    # Loop body: Compare characters at current index
    builder = ir.IRBuilder(loop_body)
    idx_val = builder.load(idx, name="idx_val")

    # Load characters from both strings
    ptr1 = builder.gep(data1, [idx_val], name="ptr1")
    ptr2 = builder.gep(data2, [idx_val], name="ptr2")
    char1 = builder.load(ptr1, name="char1")
    char2 = builder.load(ptr2, name="char2")

    # Compare characters
    chars_equal = builder.icmp_unsigned("==", char1, char2, name="chars_equal")
    builder.cbranch(chars_equal, loop_continue, char_diff_block)

    # Character difference: return difference
    builder = ir.IRBuilder(char_diff_block)
    char1_i32 = builder.zext(char1, i32, name="char1_i32")
    char2_i32 = builder.zext(char2, i32, name="char2_i32")
    diff = builder.sub(char1_i32, char2_i32, name="char_diff")
    builder.ret(diff)

    # Loop continue: Increment index
    builder = ir.IRBuilder(loop_continue)
    idx_val = builder.load(idx, name="idx_val")
    next_idx = builder.add(idx_val, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, idx)
    builder.branch(loop_header)

    # Loop exit: All compared characters were equal, check sizes
    builder = ir.IRBuilder(loop_exit)
    builder.branch(size_diff_block)

    # Size difference: return size1 - size2
    builder = ir.IRBuilder(size_diff_block)
    size_diff = builder.sub(size1, size2, name="size_diff")
    builder.ret(size_diff)

    return func
