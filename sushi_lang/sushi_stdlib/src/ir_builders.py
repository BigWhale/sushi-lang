"""
IR Builder Abstractions

Reusable patterns for LLVM IR generation that reduce boilerplate and enforce consistency.
Follows the Builder pattern to simplify complex IR construction.

Design Principles:
- Single Responsibility: Each builder handles one type of pattern
- Open/Closed: Easy to extend with new patterns without modifying existing code
- DRY: Eliminates repeated boilerplate across stdlib modules
"""

from typing import Callable, Optional, List, Tuple, Any
import llvmlite.ir as ir


# ==============================================================================
# Struct Builders
# ==============================================================================

class IRStructBuilder:
    """Helper for building common struct types and operations."""

    @staticmethod
    def build_fat_pointer(
        builder: ir.IRBuilder,
        string_type: ir.LiteralStructType,
        data_ptr: ir.Value,
        size: ir.Value
    ) -> ir.Value:
        """Build a fat pointer struct { i8*, i32 }.

        Args:
            builder: IR builder
            string_type: Fat pointer struct type
            data_ptr: Data pointer (i8*)
            size: Size in bytes (i32)

        Returns:
            Fat pointer struct value
        """
        undef_struct = ir.Constant(string_type, ir.Undefined)
        struct_with_data = builder.insert_value(undef_struct, data_ptr, 0, name="struct_with_data")
        struct_complete = builder.insert_value(struct_with_data, size, 1, name="struct_complete")
        return struct_complete

    @staticmethod
    def extract_fat_pointer_fields(
        builder: ir.IRBuilder,
        fat_ptr: ir.Value
    ) -> Tuple[ir.Value, ir.Value]:
        """Extract data pointer and size from fat pointer struct.

        Args:
            builder: IR builder
            fat_ptr: Fat pointer struct { i8*, i32 }

        Returns:
            Tuple of (data_ptr, size)
        """
        data = builder.extract_value(fat_ptr, 0, name="data")
        size = builder.extract_value(fat_ptr, 1, name="size")
        return data, size

    @staticmethod
    def build_iterator(
        builder: ir.IRBuilder,
        iterator_type: ir.LiteralStructType,
        index: ir.Value,
        length: ir.Value,
        data_ptr: ir.Value
    ) -> ir.Value:
        """Build an iterator struct { i32, i32, ptr }.

        Args:
            builder: IR builder
            iterator_type: Iterator struct type
            index: Current index (i32)
            length: Total length (i32, or -1 for streaming)
            data_ptr: Data pointer

        Returns:
            Iterator struct value
        """
        undef_struct = ir.Constant(iterator_type, ir.Undefined)
        struct_with_index = builder.insert_value(undef_struct, index, 0, name="with_index")
        struct_with_length = builder.insert_value(struct_with_index, length, 1, name="with_length")
        struct_complete = builder.insert_value(struct_with_length, data_ptr, 2, name="iterator")
        return struct_complete

    @staticmethod
    def build_dynamic_array(
        builder: ir.IRBuilder,
        array_type: ir.LiteralStructType,
        length: ir.Value,
        capacity: ir.Value,
        data_ptr: ir.Value
    ) -> ir.Value:
        """Build a dynamic array struct { i32 len, i32 cap, ptr data }.

        Args:
            builder: IR builder
            array_type: Array struct type
            length: Current length (i32)
            capacity: Allocated capacity (i32)
            data_ptr: Data pointer

        Returns:
            Array struct value
        """
        undef_struct = ir.Constant(array_type, ir.Undefined)
        struct_with_len = builder.insert_value(undef_struct, length, 0, name="with_len")
        struct_with_cap = builder.insert_value(struct_with_len, capacity, 1, name="with_cap")
        struct_complete = builder.insert_value(struct_with_cap, data_ptr, 2, name="array")
        return struct_complete


# ==============================================================================
# Loop Builders
# ==============================================================================

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
        """Build a counting loop: for (i = start; i < end; i++) { body_fn(i) }

        Args:
            func: Function to add blocks to
            builder: IR builder positioned at entry point
            start: Loop start value (i32)
            end: Loop end value (i32)
            body_fn: Callback receiving (builder, loop_var) for loop body
            i32: i32 type
            exit_block: Optional exit block (created if not provided)

        Returns:
            Exit block (for further code generation)
        """
        # Create blocks
        loop_cond_block = func.append_basic_block("loop_cond")
        loop_body_block = func.append_basic_block("loop_body")
        if exit_block is None:
            exit_block = func.append_basic_block("loop_exit")

        # Initialize loop counter
        i_ptr = builder.alloca(i32, name="i_ptr")
        builder.store(start, i_ptr)
        builder.branch(loop_cond_block)

        # Loop condition: i < end
        builder = ir.IRBuilder(loop_cond_block)
        i = builder.load(i_ptr, name="i")
        cond = builder.icmp_unsigned("<", i, end, name="loop_cond")
        builder.cbranch(cond, loop_body_block, exit_block)

        # Loop body
        builder = ir.IRBuilder(loop_body_block)
        i = builder.load(i_ptr, name="i")
        body_fn(builder, i)

        # Increment and continue
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
        """Build a character transformation loop and return result.

        This is optimized for string transformations (upper, lower, etc.).
        Allocates new string, transforms each character, returns fat pointer.

        Note: This function completes the IR function (adds return).

        Args:
            func: Function being built
            builder: IR builder positioned in entry block
            module: LLVM module
            data: Source string data pointer (i8*)
            size: Source string size (i32)
            transform_fn: Character transformation function (i32 -> i32)
            malloc_fn: malloc function for allocation
            i8, i32, i64: LLVM types
            string_type: Fat pointer struct type
        """
        # Allocate new string
        size_i64 = builder.zext(size, i64, name="size_i64")
        new_data = builder.call(malloc_fn, [size_i64], name="new_data")

        # Create exit block
        exit_block = func.append_basic_block("loop_exit")

        # Build counting loop
        def transform_body(body_builder: ir.IRBuilder, i: ir.Value):
            # Load character
            src_ptr = body_builder.gep(data, [i], name="src_ptr")
            ch = body_builder.load(src_ptr, name="ch")

            # Transform (i8 -> i32 -> transform -> i8)
            ch_i32 = body_builder.zext(ch, i32, name="ch_i32")
            transformed_i32 = body_builder.call(transform_fn, [ch_i32], name="transformed_i32")
            transformed = body_builder.trunc(transformed_i32, i8, name="transformed")

            # Store in destination
            dst_ptr = body_builder.gep(new_data, [i], name="dst_ptr")
            body_builder.store(transformed, dst_ptr)

        IRLoopBuilder.build_counting_loop(
            func, builder,
            ir.Constant(i32, 0), size,
            transform_body, i32,
            exit_block
        )

        # Exit: build and return fat pointer
        builder = ir.IRBuilder(exit_block)
        result = IRStructBuilder.build_fat_pointer(builder, string_type, new_data, size)
        builder.ret(result)


# ==============================================================================
# Conditional Builders
# ==============================================================================

class IRConditionalBuilder:
    """Helper for building conditional structures."""

    @staticmethod
    def build_simple_conditional(
        func: ir.Function,
        builder: ir.IRBuilder,
        condition: ir.Value,
        then_fn: Callable[[ir.IRBuilder], None],
        else_fn: Optional[Callable[[ir.IRBuilder], None]] = None,
        merge_block: Optional[Any] = None
    ) -> Any:
        """Build if-then-else structure.

        Args:
            func: Function to add blocks to
            builder: IR builder positioned before conditional
            condition: Boolean condition (i1 or i8)
            then_fn: Callback for then block
            else_fn: Optional callback for else block
            merge_block: Optional merge block (created if not provided)

        Returns:
            Merge block (for further code generation)
        """
        then_block = func.append_basic_block("then")
        if else_fn:
            else_block = func.append_basic_block("else")
        if merge_block is None:
            merge_block = func.append_basic_block("merge")

        # Branch to then/else
        if else_fn:
            builder.cbranch(condition, then_block, else_block)
        else:
            builder.cbranch(condition, then_block, merge_block)

        # Then block
        builder = ir.IRBuilder(then_block)
        then_fn(builder)
        if not builder.block.is_terminated:
            builder.branch(merge_block)

        # Else block (if provided)
        if else_fn:
            builder = ir.IRBuilder(else_block)
            else_fn(builder)
            if not builder.block.is_terminated:
                builder.branch(merge_block)

        return merge_block

    @staticmethod
    def build_early_return_check(
        func: ir.Function,
        builder: ir.IRBuilder,
        condition: ir.Value,
        return_value: ir.Value,
        continue_block: Optional[Any] = None
    ) -> Any:
        """Build early return pattern: if (condition) return value; else continue.

        Args:
            func: Function to add blocks to
            builder: IR builder positioned before check
            condition: Boolean condition (i1 or i8)
            return_value: Value to return if condition is true
            continue_block: Optional continuation block (created if not provided)

        Returns:
            Continue block (for further code generation)
        """
        return_block = func.append_basic_block("early_return")
        if continue_block is None:
            continue_block = func.append_basic_block("continue")

        builder.cbranch(condition, return_block, continue_block)

        # Return block
        builder = ir.IRBuilder(return_block)
        builder.ret(return_value)

        return continue_block


# ==============================================================================
# Memory Allocation Helpers
# ==============================================================================

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
        """Allocate memory and copy bytes from source.

        Args:
            builder: IR builder
            malloc_fn: malloc function
            memcpy_fn: memcpy function
            src_ptr: Source pointer (i8*)
            byte_count: Number of bytes to copy (i32)
            i64: i64 type for malloc

        Returns:
            Pointer to allocated and copied data (i8*)
        """
        # Allocate
        byte_count_i64 = builder.zext(byte_count, i64, name="byte_count_i64")
        new_data = builder.call(malloc_fn, [byte_count_i64], name="new_data")

        # Copy using llvm.memcpy intrinsic
        is_volatile = ir.Constant(ir.IntType(1), 0)
        builder.call(memcpy_fn, [new_data, src_ptr, byte_count, is_volatile])

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
        """Allocate and return a substring as a fat pointer struct.

        Args:
            builder: IR builder
            malloc_fn: malloc function
            memcpy_fn: memcpy function
            string_type: Fat pointer struct type
            src_data: Source string data pointer (i8*)
            start_offset: Byte offset to start from (i32)
            byte_length: Number of bytes to copy (i32)
            i32, i64: LLVM types

        Returns:
            Fat pointer struct value
        """
        # Calculate source pointer
        src_ptr = builder.gep(src_data, [start_offset], name="src_ptr")

        # Allocate and copy
        new_data = IRMemoryBuilder.allocate_and_copy(
            builder, malloc_fn, memcpy_fn, src_ptr, byte_length, i64
        )

        # Build fat pointer
        return IRStructBuilder.build_fat_pointer(builder, string_type, new_data, byte_length)
