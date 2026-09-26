"""IR Builder Abstractions"""

from typing import Callable, Optional, Tuple, Any
import llvmlite.ir as ir
from sushi_lang.backend.memory.allocas import entry_alloca


class IRStructBuilder:
    """Helper for building common struct types and operations."""

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

