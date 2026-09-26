"""IR Builder Abstractions"""

from typing import Callable, Optional, Tuple, Any
import llvmlite.ir as ir
from sushi_lang.backend.memory.allocas import entry_alloca


class IRStructBuilder:
    """Helper for building common struct types and operations."""

    @staticmethod
    def build_fat_pointer(
        builder: ir.IRBuilder,
        string_type: ir.LiteralStructType,
        data_ptr: ir.Value,
        size: ir.Value,
        owned: int,
    ) -> ir.Value:
        """Build a string fat pointer struct { i8*, i32, i8 owned }."""
        undef_struct = ir.Constant(string_type, ir.Undefined)
        struct_with_data = builder.insert_value(undef_struct, data_ptr, 0, name="struct_with_data")
        struct_with_size = builder.insert_value(struct_with_data, size, 1, name="struct_with_size")
        struct_complete = builder.insert_value(
            struct_with_size, ir.Constant(ir.IntType(8), 1 if owned else 0), 2, name="struct_complete")
        return struct_complete

    @staticmethod
    def extract_fat_pointer_fields(
        builder: ir.IRBuilder,
        fat_ptr: ir.Value
    ) -> Tuple[ir.Value, ir.Value]:
        """Extract data pointer and size from fat pointer struct."""
        data = builder.extract_value(fat_ptr, 0, name="data")
        size = builder.extract_value(fat_ptr, 1, name="size")
        return data, size

class IRLoopBuilder:
    """Helper for building common loop patterns."""

    @staticmethod
    def build_counting_loop(
        func: ir.Function,
        builder: ir.IRBuilder,
        start: ir.Value,
        end: ir.Value,
        body_fn: Callable[[ir.IRBuilder, ir.Value], None],
        i32: ir.IntType,
        exit_block: Optional[Any] = None
    ) -> Any:
        """Build a counting loop: for (i = start; i < end; i++) { body_fn(i) }"""
        loop_cond_block = func.append_basic_block("loop_cond")
        loop_body_block = func.append_basic_block("loop_body")
        if exit_block is None:
            exit_block = func.append_basic_block("loop_exit")

        i_ptr = entry_alloca(builder, i32, name="i_ptr")
        builder.store(start, i_ptr)
        builder.branch(loop_cond_block)

        builder = ir.IRBuilder(loop_cond_block)
        i = builder.load(i_ptr, name="i")
        cond = builder.icmp_unsigned("<", i, end, name="loop_cond")
        builder.cbranch(cond, loop_body_block, exit_block)

        builder = ir.IRBuilder(loop_body_block)
        i = builder.load(i_ptr, name="i")
        body_fn(builder, i)

        i_next = builder.add(i, ir.Constant(i32, 1), name="i_next")
        builder.store(i_next, i_ptr)
        builder.branch(loop_cond_block)

        return exit_block

    @staticmethod
    def build_char_transform_loop(
        func: ir.Function,
        builder: ir.IRBuilder,
        module: ir.Module,
        data: ir.Value,
        size: ir.Value,
        transform_fn: ir.Function,
        malloc_fn: ir.Function,
        i8: ir.IntType,
        i32: ir.IntType,
        i64: ir.IntType,
        string_type: ir.LiteralStructType
    ) -> None:
        """Build a character transformation loop and return result."""
        size_i64 = builder.zext(size, i64, name="size_i64")
        new_data = builder.call(malloc_fn, [size_i64], name="new_data")

        exit_block = func.append_basic_block("loop_exit")

        def transform_body(body_builder: ir.IRBuilder, i: ir.Value):
            src_ptr = body_builder.gep(data, [i], name="src_ptr")
            ch = body_builder.load(src_ptr, name="ch")

            ch_i32 = body_builder.zext(ch, i32, name="ch_i32")
            transformed_i32 = body_builder.call(transform_fn, [ch_i32], name="transformed_i32")
            transformed = body_builder.trunc(transformed_i32, i8, name="transformed")

            dst_ptr = body_builder.gep(new_data, [i], name="dst_ptr")
            body_builder.store(transformed, dst_ptr)

        IRLoopBuilder.build_counting_loop(
            func, builder,
            ir.Constant(i32, 0), size,
            transform_body, i32,
            exit_block
        )

        builder = ir.IRBuilder(exit_block)
        result = IRStructBuilder.build_fat_pointer(builder, string_type, new_data, size, owned=1)
        builder.ret(result)


class IRMemoryBuilder:
    """Helper for common memory allocation patterns."""

    @staticmethod
    def allocate_and_copy(
        builder: ir.IRBuilder,
        malloc_fn: ir.Function,
        memcpy_fn: ir.Function,
        src_ptr: ir.Value,
        byte_count: ir.Value,
        i64: ir.IntType
    ) -> ir.Value:
        """Allocate memory and copy bytes from source."""
        byte_count_i64 = builder.zext(byte_count, i64, name="byte_count_i64")
        new_data = builder.call(malloc_fn, [byte_count_i64], name="new_data")

        is_volatile = ir.Constant(ir.IntType(1), 0)
        builder.call(memcpy_fn, [new_data, src_ptr, builder.zext(byte_count, ir.IntType(64)), is_volatile])

        return new_data

    @staticmethod
    def allocate_substring(
        builder: ir.IRBuilder,
        malloc_fn: ir.Function,
        memcpy_fn: ir.Function,
        string_type: ir.LiteralStructType,
        src_data: ir.Value,
        start_offset: ir.Value,
        byte_length: ir.Value,
        i32: ir.IntType,
        i64: ir.IntType
    ) -> ir.Value:
        """Allocate and return a substring as a fat pointer struct."""
        src_ptr = builder.gep(src_data, [start_offset], name="src_ptr")

        new_data = IRMemoryBuilder.allocate_and_copy(
            builder, malloc_fn, memcpy_fn, src_ptr, byte_length, i64
        )

        return IRStructBuilder.build_fat_pointer(builder, string_type, new_data, byte_length, owned=1)
