"""UTF-8 Character Count Intrinsic"""

import llvmlite.ir as ir


def emit_utf8_count_intrinsic(module: ir.Module) -> ir.Function:
    """Emit `i32 llvm_utf8_count(i8* data, i32 size)`."""
    func_name = "llvm_utf8_count"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()
    i32 = ir.IntType(32)
    i1 = ir.IntType(1)

    fn_ty = ir.FunctionType(i32, [i8_ptr, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "data"
    func.args[1].name = "size"

    entry_block = func.append_basic_block("entry")
    loop_header = func.append_basic_block("loop_header")
    loop_body = func.append_basic_block("loop_body")
    loop_continue = func.append_basic_block("loop_continue")
    loop_exit = func.append_basic_block("loop_exit")

    builder = ir.IRBuilder(entry_block)

    count = builder.alloca(i32, name="count")
    builder.store(ir.Constant(i32, 0), count)
    idx = builder.alloca(i32, name="idx")
    builder.store(ir.Constant(i32, 0), idx)
    builder.branch(loop_header)

    builder.position_at_end(loop_header)
    idx_val = builder.load(idx, name="idx_val")
    cond = builder.icmp_signed("<", idx_val, func.args[1], name="loop_cond")
    builder.cbranch(cond, loop_body, loop_exit)

    builder.position_at_end(loop_body)
    idx_val = builder.load(idx, name="idx_val")

    byte_ptr = builder.gep(func.args[0], [idx_val], name="byte_ptr")
    byte_val = builder.load(byte_ptr, name="byte_val")

    # Check if (byte & 0xC0) != 0x80
    # Continuation bytes: 10xxxxxx (0x80-0xBF)
    # Start/ASCII bytes: 0xxxxxxx, 110xxxxx, 1110xxxx, 11110xxx
    masked = builder.and_(byte_val, ir.Constant(i8, 0xC0), name="masked")
    is_continuation = builder.icmp_unsigned("==", masked, ir.Constant(i8, 0x80), name="is_continuation")
    is_start = builder.xor(is_continuation, ir.Constant(i1, 1), name="is_start")

    count_val = builder.load(count, name="count_val")
    incremented = builder.add(count_val, ir.Constant(i32, 1), name="incremented")
    new_count = builder.select(is_start, incremented, count_val, name="new_count")
    builder.store(new_count, count)

    builder.branch(loop_continue)

    builder.position_at_end(loop_continue)
    idx_val = builder.load(idx, name="idx_val")
    next_idx = builder.add(idx_val, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, idx)
    builder.branch(loop_header)

    builder.position_at_end(loop_exit)
    final_count = builder.load(count, name="final_count")
    builder.ret(final_count)

    return func
