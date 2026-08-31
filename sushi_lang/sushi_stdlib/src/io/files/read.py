"""File.lines(), the one reading method the compiler still generates.

read(), readln() and readch() moved to `src_sushi/io/fs.sushi` and reach the descriptor
primitives like any other Sushi code. `lines()` stayed -- see `is_builtin_file_method`
for why, and ruling R13 for where the question goes.
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc


def generate_lines(module: ir.Module) -> None:
    """Generate IR for File.lines() -> Iterator<string>.

    The iterator is `{i32 index, i32 length, T* data}` -- a CURSOR over a contiguous
    buffer, with no `next` to call, so it cannot be lazy by construction. `lines()` is
    lazy anyway, through a sentinel: `length = -1` says "this is not a buffer", and the
    data slot carries a heap cell holding the DESCRIPTOR. `foreach` branches on the
    sentinel at run time and reads a line per iteration.

    The cell used to hold a `FILE*`, and there were two sentinels -- a null data slot
    meant stdin, a non-null one meant a file -- because the two reached different libc
    calls. `stdin` is an ordinary File over descriptor 0 now, so there is one shape and
    one reader.

    None of this is a design anyone is defending. Ruling R13 sends the whole question --
    the name, the return type, iterator against cursor, and where a mid-stream read
    failure goes -- to Phase 7, and asks Phase 5 to leave it working and touch nothing
    else about it.
    """
    malloc_fn = declare_malloc(module)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()
    string_fat_ptr = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)

    iterator_struct_ty = ir.LiteralStructType([i32, i32, string_fat_ptr.as_pointer()])

    fn_ty = ir.FunctionType(iterator_struct_ty, [i32])
    fn = ir.Function(module, fn_ty, name="sushi_file_lines")

    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    fd = fn.args[0]
    fd.name = "fd"

    iterator_slot = builder.alloca(iterator_struct_ty, name="file_lines_iter")

    # The descriptor rides in a heap cell so the iterator's data slot keeps its declared
    # pointer type. It is freed by whoever frees the iterator.
    cell = builder.call(malloc_fn, [ir.Constant(i64, 16)], name="fd_cell")
    builder.store(fd, builder.bitcast(cell, i32.as_pointer(), name="fd_cell_i32"))
    cell_typed = builder.bitcast(cell, string_fat_ptr.as_pointer(), name="fd_cell_ptr")

    zero = ir.Constant(i32, 0)
    builder.store(zero, builder.gep(iterator_slot, [zero, zero]))
    builder.store(ir.Constant(i32, -1),
                  builder.gep(iterator_slot, [zero, ir.Constant(i32, 1)]))
    builder.store(cell_typed,
                  builder.gep(iterator_slot, [zero, ir.Constant(i32, 2)]))

    builder.ret(builder.load(iterator_slot))
