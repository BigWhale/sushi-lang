"""
String operations and UTF-8 support for LLVM code generation.

This module provides functions for string manipulation, UTF-8 character handling,
and string-related code emission.
"""
from __future__ import annotations

import typing

from llvmlite import ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from backend.llvm_constants import FALSE_I1
from internals.errors import raise_internal_error

if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class StringOperations:
    """Manages string operations and UTF-8 support."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

        # UTF-8 support functions (defined in this module) - declared immediately for type safety
        self.utf8_char_count: ir.Function

    def declare_utf8_functions(self) -> None:
        """Declare UTF-8 support functions."""
        self._declare_and_define_utf8_char_count()

    def emit_string_literal(self, string_value: str) -> ir.Value:
        """Generate a global string constant and return a fat pointer struct.

        Args:
            string_value: The string literal value.

        Returns:
            Fat pointer struct {i8* data, i32 size} for the string literal.
        """
        # Create the global string constant WITHOUT null terminator
        string_data = string_value.encode('utf-8')
        size = len(string_data)
        string_type = ir.ArrayType(self.codegen.i8, size)

        # Create a unique name for this string literal
        str_name = f"_str_{len(string_value)}_{hash(string_value) & 0xFFFFFFFF}"

        # Check if this string already exists in the module
        existing = self.codegen.module.globals.get(str_name)
        if existing is not None:
            global_str = existing
        else:
            global_str = ir.GlobalVariable(self.codegen.module, string_type, name=str_name)
            global_str.initializer = ir.Constant(string_type, bytearray(string_data))
            global_str.linkage = 'internal'
            global_str.global_constant = True

        # Get pointer to the string data
        zero = ir.Constant(self.codegen.i32, 0)
        data_ptr = self.codegen.builder.gep(global_str, [zero, zero])

        # Build fat pointer struct: {i8* data, i32 size}
        string_struct_type = self.codegen.types.string_struct
        size_value = ir.Constant(self.codegen.i32, size)

        # Use insertvalue to build the struct
        undef_struct = ir.Constant(string_struct_type, ir.Undefined)
        struct_with_data = self.codegen.builder.insert_value(undef_struct, data_ptr, 0)
        struct_complete = self.codegen.builder.insert_value(struct_with_data, size_value, 1)

        return struct_complete

    def emit_string_comparison(self, op: str, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        """Generate string comparison for fat pointer structs.

        Compares strings by first checking if sizes match, then comparing
        data byte-by-byte using memcmp.

        Args:
            op: The comparison operator ("==" or "!=").
            lhs: Left-hand side string fat pointer struct.
            rhs: Right-hand side string fat pointer struct.

        Returns:
            An i1 value representing the comparison result.

        Raises:
            AssertionError: If required runtime functions are not declared.
            NotImplementedError: If the comparison operator is not supported.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        # Extract data pointers and sizes from fat pointer structs
        lhs_data = self.codegen.builder.extract_value(lhs, 0)
        lhs_size = self.codegen.builder.extract_value(lhs, 1)
        rhs_data = self.codegen.builder.extract_value(rhs, 0)
        rhs_size = self.codegen.builder.extract_value(rhs, 1)

        # First check if sizes are equal
        sizes_equal = self.codegen.builder.icmp_signed('==', lhs_size, rhs_size)

        # Create blocks for conditional comparison
        check_data_block = self.codegen.builder.append_basic_block(name="check_data")
        merge_block = self.codegen.builder.append_basic_block(name="merge")

        # Remember current block for phi node
        entry_block = self.codegen.builder.block

        # If sizes not equal, strings can't be equal
        self.codegen.builder.cbranch(sizes_equal, check_data_block, merge_block)

        # Block for checking data when sizes match
        self.codegen.builder.position_at_end(check_data_block)
        # Use memcmp to compare data
        memcmp_result = self.codegen.builder.call(self.codegen.runtime.libc_strings.memcmp, [lhs_data, rhs_data, lhs_size])
        data_equal = self.codegen.builder.icmp_signed('==', memcmp_result, ir.Constant(self.codegen.i32, 0))
        self.codegen.builder.branch(merge_block)

        # Merge block
        self.codegen.builder.position_at_end(merge_block)
        phi = self.codegen.builder.phi(self.codegen.i1)
        phi.add_incoming(ir.Constant(self.codegen.i1, 0), entry_block)  # Different sizes = not equal
        phi.add_incoming(data_equal, check_data_block)  # Same size, check data result

        if op == "==":
            return phi
        elif op == "!=":
            return self.codegen.builder.not_(phi)
        else:
            raise NotImplementedError(f"String comparison '{op}' not implemented")

    def emit_string_null_termination(self, string_ptr: ir.Value, offset: ir.Value) -> None:
        """Add null terminator to string at specified offset.

        Args:
            string_ptr: Pointer to string buffer.
            offset: Byte offset where to place null terminator.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        # Get pointer to the position where we want to place null terminator
        null_pos_ptr = self.codegen.builder.gep(string_ptr, [offset])

        # Store null terminator
        null_char = ir.Constant(self.codegen.i8, 0)
        self.codegen.builder.store(null_char, null_pos_ptr)

    def emit_string_concat(self, str1: ir.Value, str2: ir.Value) -> ir.Value:
        """Generate string concatenation by allocating new memory and copying both strings.

        Args:
            str1: First string fat pointer struct.
            str2: Second string fat pointer struct.

        Returns:
            New fat pointer struct containing concatenated string.

        Raises:
            AssertionError: If required runtime functions are not declared.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        # Extract data pointers and sizes from fat pointer structs
        data1 = self.codegen.builder.extract_value(str1, 0)
        size1 = self.codegen.builder.extract_value(str1, 1)
        data2 = self.codegen.builder.extract_value(str2, 0)
        size2 = self.codegen.builder.extract_value(str2, 1)

        # Calculate total size (no null terminator needed)
        total_size = self.codegen.builder.add(size1, size2)

        # Allocate memory for the new string
        malloc_func = self.codegen.get_malloc_func()
        total_size_i64 = self.codegen.builder.zext(total_size, ir.IntType(INT64_BIT_WIDTH))
        new_data = self.codegen.builder.call(malloc_func, [total_size_i64])

        # Copy first string using llvm.memcpy intrinsic
        memcpy_fn = self.codegen.module.declare_intrinsic(
            'llvm.memcpy',
            [ir.PointerType(self.codegen.i8), ir.PointerType(self.codegen.i8), self.codegen.i32]
        )
        is_volatile = FALSE_I1
        self.codegen.builder.call(memcpy_fn, [new_data, data1, size1, is_volatile])

        # Copy second string after first
        offset_ptr = self.codegen.builder.gep(new_data, [size1])
        self.codegen.builder.call(memcpy_fn, [offset_ptr, data2, size2, is_volatile])

        # Build and return fat pointer struct
        string_struct_type = self.codegen.types.string_struct
        undef_struct = ir.Constant(string_struct_type, ir.Undefined)
        struct_with_data = self.codegen.builder.insert_value(undef_struct, new_data, 0)
        struct_complete = self.codegen.builder.insert_value(struct_with_data, total_size, 1)

        return struct_complete

    def emit_to_cstr(self, string_struct: ir.Value) -> ir.Value:
        """Convert fat pointer string to null-terminated C string.

        This is used for C interop functions like printf, fopen, etc.
        Allocates size+1 bytes, copies data, adds null terminator.

        Args:
            string_struct: Fat pointer struct {i8* data, i32 size}.

        Returns:
            Null-terminated i8* suitable for C functions.

        Raises:
            AssertionError: If required runtime functions are not declared.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        # Extract data pointer and size
        data_ptr = self.codegen.builder.extract_value(string_struct, 0)
        size = self.codegen.builder.extract_value(string_struct, 1)

        # Allocate size+1 bytes for null terminator
        size_plus_one = self.codegen.builder.add(size, ir.Constant(self.codegen.i32, 1))
        malloc_func = self.codegen.get_malloc_func()
        size_i64 = self.codegen.builder.zext(size_plus_one, ir.IntType(INT64_BIT_WIDTH))
        c_str = self.codegen.builder.call(malloc_func, [size_i64])

        # Copy string data using llvm.memcpy intrinsic
        memcpy_fn = self.codegen.module.declare_intrinsic(
            'llvm.memcpy',
            [ir.PointerType(self.codegen.i8), ir.PointerType(self.codegen.i8), self.codegen.i32]
        )
        is_volatile = FALSE_I1
        self.codegen.builder.call(memcpy_fn, [c_str, data_ptr, size, is_volatile])

        # Add null terminator
        null_ptr = self.codegen.builder.gep(c_str, [size])
        self.codegen.builder.store(ir.Constant(self.codegen.i8, 0), null_ptr)

        return c_str

    def emit_cstr_to_fat_pointer(self, c_str: ir.Value) -> ir.Value:
        """Convert null-terminated C string to fat pointer struct.

        This is the inverse of emit_to_cstr() - used when C functions
        return strings that need to be converted to fat pointers.

        Args:
            c_str: Null-terminated i8* from C function.

        Returns:
            Fat pointer struct {i8* data, i32 size}.

        Raises:
            AssertionError: If required runtime functions are not declared.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        # Use strlen to get the size
        size = self.codegen.builder.call(self.codegen.runtime.libc_strings.strlen, [c_str])

        # Build fat pointer struct: {i8* data, i32 size}
        string_struct_type = self.codegen.types.string_struct
        undef_struct = ir.Constant(string_struct_type, ir.Undefined)
        struct_with_data = self.codegen.builder.insert_value(undef_struct, c_str, 0)
        struct_complete = self.codegen.builder.insert_value(struct_with_data, size, 1)

        return struct_complete

    def emit_string_byte_count(self, string_ptr: ir.Value) -> ir.Value:
        """Generate call to strlen for string BYTE count (not character count).

        IMPORTANT: This returns the number of BYTES in the UTF-8 string, NOT
        the number of Unicode characters. For Unicode character count, use
        emit_string_char_count() instead.

        For ASCII-only strings, byte count equals character count.
        For Unicode strings with multi-byte characters (e.g., emojis, accented
        letters), byte count will be larger than character count.

        Example:
            "Hello"   -> 5 bytes, 5 characters
            "Hello 👋" -> 10 bytes, 7 characters

        Args:
            string_ptr: Pointer to null-terminated UTF-8 string.

        Returns:
            Number of bytes in the string (excluding null terminator) as i32.

        See Also:
            emit_string_char_count() for Unicode-aware character counting.
        """
        return self.codegen.builder.call(self.codegen.runtime.libc_strings.strlen, [string_ptr])

    def emit_string_length(self, string_ptr: ir.Value) -> ir.Value:
        """DEPRECATED: Use emit_string_byte_count() for clarity.

        This method is maintained for backward compatibility but will be
        removed in a future version. Use emit_string_byte_count() instead
        to make it clear you want byte count, not character count.
        """
        return self.emit_string_byte_count(string_ptr)

    def emit_string_allocation(self, size: ir.Value) -> ir.Value:
        """Generate call to malloc for string allocation.

        Args:
            size: Size in bytes to allocate.

        Returns:
            Pointer to allocated memory.
        """
        # Use the existing malloc infrastructure from the main codegen
        malloc_func = self.codegen.get_malloc_func()
        # Convert i32 to i64 for size_t parameter if needed
        if isinstance(size.type, ir.IntType) and size.type.width == 32:
            size_i64 = self.codegen.builder.zext(size, ir.IntType(INT64_BIT_WIDTH))
        else:
            size_i64 = size
        return self.codegen.builder.call(malloc_func, [size_i64])

    def emit_string_char_count(self, string_ptr: ir.Value) -> ir.Value:
        """Generate call to utf8_char_count for Unicode-aware CHARACTER counting.

        IMPORTANT: This returns the number of Unicode CHARACTERS (codepoints),
        NOT the number of bytes. This is the semantically correct count for
        user-facing string operations.

        For ASCII-only strings, character count equals byte count.
        For Unicode strings with multi-byte characters (e.g., emojis, accented
        letters), character count will be smaller than byte count.

        Example:
            "Hello"   -> 5 characters, 5 bytes
            "Hello 👋" -> 7 characters, 10 bytes

        This is what users expect when they call .len() on a string.

        Args:
            string_ptr: Pointer to null-terminated UTF-8 string.

        Returns:
            Number of UTF-8 characters (codepoints) in the string as i32.

        Raises:
            AssertionError: If utf8_char_count function is not declared.

        See Also:
            emit_string_byte_count() for byte-level operations.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        return self.codegen.builder.call(self.utf8_char_count, [string_ptr])

    def _declare_and_define_utf8_char_count(self) -> None:
        """Declare and define the utf8_char_count function for Unicode-aware string length.

        This function counts the number of UTF-8 characters (code points) in a string,
        not the number of bytes. It correctly handles multi-byte UTF-8 sequences.

        UTF-8 encoding rules:
        - 0xxxxxxx: 1-byte character (ASCII)
        - 110xxxxx 10xxxxxx: 2-byte character
        - 1110xxxx 10xxxxxx 10xxxxxx: 3-byte character
        - 11110xxx 10xxxxxx 10xxxxxx 10xxxxxx: 4-byte character
        - 10xxxxxx: continuation byte (not counted as a character start)

        Creates the utf8_char_count(int8*) -> i32 function.
        """
        # Check if function already exists
        existing = self.codegen.module.globals.get("utf8_char_count")
        if isinstance(existing, ir.Function):
            self.utf8_char_count = existing
            return

        # Declare function: i32 utf8_char_count(i8* str)
        fn_ty = ir.FunctionType(self.codegen.i32, [self.codegen.i8.as_pointer()])
        func = ir.Function(self.codegen.module, fn_ty, name="utf8_char_count")
        self.utf8_char_count = func

        # Define function body
        entry_block = func.append_basic_block("entry")
        loop_head = func.append_basic_block("loop_head")
        loop_body = func.append_basic_block("loop_body")
        loop_exit = func.append_basic_block("loop_exit")

        # Save current builder state
        saved_builder = self.codegen.builder
        saved_block = saved_builder.block if saved_builder else None

        # Create temporary builder for this function
        builder = ir.IRBuilder(entry_block)

        # Entry block: Initialize counter and index
        str_param = func.args[0]
        str_param.name = "str"

        # Allocate local variables
        count_ptr = builder.alloca(self.codegen.i32, name="count")
        index_ptr = builder.alloca(self.codegen.i32, name="index")

        builder.store(ir.Constant(self.codegen.i32, 0), count_ptr)
        builder.store(ir.Constant(self.codegen.i32, 0), index_ptr)
        builder.branch(loop_head)

        # Loop head: Check if we've reached null terminator
        builder.position_at_end(loop_head)
        current_index = builder.load(index_ptr)
        char_ptr = builder.gep(str_param, [current_index])
        current_byte = builder.load(char_ptr)

        # Check if current byte is null terminator
        zero_i8 = ir.Constant(self.codegen.i8, 0)
        is_not_null = builder.icmp_signed('!=', current_byte, zero_i8)
        builder.cbranch(is_not_null, loop_body, loop_exit)

        # Loop body: Count UTF-8 characters
        builder.position_at_end(loop_body)
        current_byte_loaded = builder.load(char_ptr)
        byte_as_u8 = builder.zext(current_byte_loaded, self.codegen.i32)

        # Check if this is NOT a continuation byte (10xxxxxx)
        # Continuation bytes have pattern 10xxxxxx (0x80-0xBF)
        # We check if (byte & 0xC0) != 0x80
        mask = ir.Constant(self.codegen.i32, 0xC0)  # 11000000
        continuation_pattern = ir.Constant(self.codegen.i32, 0x80)  # 10000000

        masked_byte = builder.and_(byte_as_u8, mask)
        is_not_continuation = builder.icmp_signed('!=', masked_byte, continuation_pattern)

        # Increment count if this is a character start (not a continuation byte)
        current_count = builder.load(count_ptr)
        new_count = builder.select(
            is_not_continuation,
            builder.add(current_count, ir.Constant(self.codegen.i32, 1)),
            current_count
        )
        builder.store(new_count, count_ptr)

        # Increment index
        next_index = builder.add(current_index, ir.Constant(self.codegen.i32, 1))
        builder.store(next_index, index_ptr)
        builder.branch(loop_head)

        # Loop exit: Return count
        builder.position_at_end(loop_exit)
        final_count = builder.load(count_ptr)
        builder.ret(final_count)

        # Restore original builder state
        if saved_builder and saved_block:
            saved_builder.position_at_end(saved_block)


# Module-level utility functions for stdlib inline emitters
def emit_utf8_count(builder: ir.IRBuilder, module: ir.Module, string_ptr: ir.Value) -> ir.Value:
    """Emit inline UTF-8 character count for stdlib fallback.

    This is a simplified interface for stdlib inline emitters that don't have
    access to the full codegen context. It calls the utf8_char_count function
    that should already be declared in the module.

    Args:
        builder: The IR builder to use for emitting the call.
        module: The LLVM module containing the utf8_char_count function.
        string_ptr: Pointer to null-terminated UTF-8 string.

    Returns:
        Number of UTF-8 characters (codepoints) in the string as i32.

    Raises:
        KeyError: If utf8_char_count function is not declared in the module.
    """
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = ir.IntType(INT8_BIT_WIDTH).as_pointer()

    # Get or declare utf8_char_count function
    if "utf8_char_count" in module.globals:
        utf8_char_count_fn = module.globals["utf8_char_count"]
    else:
        # Declare the function if not already present
        fn_ty = ir.FunctionType(i32, [i8_ptr])
        utf8_char_count_fn = ir.Function(module, fn_ty, name="utf8_char_count")

    # Call the function
    return builder.call(utf8_char_count_fn, [string_ptr])
