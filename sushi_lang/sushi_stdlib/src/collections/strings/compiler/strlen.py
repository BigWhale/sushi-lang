"""String Length Intrinsic"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types


def emit_strlen_intrinsic(module: ir.Module) -> ir.Function:
    """Emit `i32 llvm_strlen(i8* str)`."""
    func_name = "llvm_strlen"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64 = get_basic_types()

    fn_ty = ir.FunctionType(i32, [i8_ptr])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    entry_block = func.append_basic_block("entry")
    loop_header = func.append_basic_block("loop_header")
    loop_body = func.append_basic_block("loop_body")
    loop_exit = func.append_basic_block("loop_exit")

    builder = ir.IRBuilder(entry_block)
    counter = builder.alloca(i32, name="counter")
    builder.store(ir.Constant(i32, 0), counter)
    builder.branch(loop_header)

    builder = ir.IRBuilder(loop_header)
    idx = builder.load(counter, name="idx")
    char_ptr = builder.gep(func.args[0], [idx], name="char_ptr")
    char = builder.load(char_ptr, name="char")

    null_char = ir.Constant(i8, 0)
    is_null = builder.icmp_unsigned("==", char, null_char, name="is_null")
    builder.cbranch(is_null, loop_exit, loop_body)

    builder = ir.IRBuilder(loop_body)
    idx = builder.load(counter, name="idx")
    next_idx = builder.add(idx, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, counter)
    builder.branch(loop_header)

    builder = ir.IRBuilder(loop_exit)
    length = builder.load(counter, name="length")
    builder.ret(length)

    return func
