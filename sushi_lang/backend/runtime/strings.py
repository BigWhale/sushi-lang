"""String operations and UTF-8 support for LLVM code generation."""
from __future__ import annotations

import typing

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import FALSE_I1
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.internals.errors import raise_internal_error

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class StringOperations:
    """Manages string operations and UTF-8 support."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance."""
        self.codegen = codegen

        self.utf8_char_count: ir.Function

    def declare_utf8_functions(self) -> None:
        """Declare UTF-8 support functions."""
        self._declare_and_define_utf8_char_count()

    def emit_string_literal(self, string_value: str) -> ir.Value:
        """Generate a global string constant and return a fat pointer struct."""
        global_str = self.codegen.string_manager.get_or_create_raw(string_value)

        zero = ir.Constant(self.codegen.i32, 0)
        data_ptr = self.codegen.builder.gep(global_str, [zero, zero])

        # Build fat pointer struct: {i8* data, i32 size, i8 owned}
        # Literals are backed by a deduplicated global -> owned=0 (RAII must NEVER free it).
        # The owned field must be set concretely (not left undef): on ARM64 `size` and
        # `owned` share one by-value argument register, so an undef owned poisons `size`.
        string_struct_type = self.codegen.types.string_struct
        size = len(string_value.encode('utf-8'))
        size_value = ir.Constant(self.codegen.i32, size)

        undef_struct = ir.Constant(string_struct_type, ir.Undefined)
        struct_with_data = self.codegen.builder.insert_value(undef_struct, data_ptr, 0)
        struct_with_size = self.codegen.builder.insert_value(struct_with_data, size_value, 1)
        struct_complete = self.codegen.builder.insert_value(
            struct_with_size, ir.Constant(self.codegen.i8, 0), 2)

        return struct_complete

    def emit_string_comparison(self, op: str, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        """Generate string comparison for fat pointer structs."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        lhs_data = self.codegen.builder.extract_value(lhs, 0)
        lhs_size = self.codegen.builder.extract_value(lhs, 1)
        rhs_data = self.codegen.builder.extract_value(rhs, 0)
        rhs_size = self.codegen.builder.extract_value(rhs, 1)

        sizes_equal = self.codegen.builder.icmp_signed('==', lhs_size, rhs_size)

        check_data_block = self.codegen.builder.append_basic_block(name="check_data")
        merge_block = self.codegen.builder.append_basic_block(name="merge")

        entry_block = self.codegen.builder.block

        self.codegen.builder.cbranch(sizes_equal, check_data_block, merge_block)

        self.codegen.builder.position_at_end(check_data_block)
        # Use memcmp to compare data. memcmp's n is size_t (i64); zero-extend the
        # i32 string size so the full 64-bit length register is defined (issue #149).
        lhs_size_n = self.codegen.builder.zext(lhs_size, self.codegen.types.i64)
        memcmp_result = self.codegen.builder.call(self.codegen.runtime.libc_strings.memcmp, [lhs_data, rhs_data, lhs_size_n])
        data_equal = self.codegen.builder.icmp_signed('==', memcmp_result, ir.Constant(self.codegen.i32, 0))
        self.codegen.builder.branch(merge_block)

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
        """Add null terminator to string at specified offset."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        null_pos_ptr = self.codegen.builder.gep(string_ptr, [offset])

        null_char = ir.Constant(self.codegen.i8, 0)
        self.codegen.builder.store(null_char, null_pos_ptr)

    def emit_string_concat(self, str1: ir.Value, str2: ir.Value) -> ir.Value:
        """Generate string concatenation by allocating new memory and copying both strings."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        data1 = self.codegen.builder.extract_value(str1, 0)
        size1 = self.codegen.builder.extract_value(str1, 1)
        data2 = self.codegen.builder.extract_value(str2, 0)
        size2 = self.codegen.builder.extract_value(str2, 1)

        total_size = self.codegen.builder.add(size1, size2)

        total_size_i64 = self.codegen.builder.zext(total_size, ir.IntType(INT64_BIT_WIDTH))
        new_data = emit_malloc(self.codegen, self.codegen.builder, total_size_i64)
        # If emitted inside a print/println argument, this concat buffer is a temporary
        # to free after output (#141). No-op elsewhere.
        self.codegen.register_string_temp(new_data)

        # Copy first string using llvm.memcpy intrinsic. Use the i64-length form and
        # zero-extend the i32 string size: the fat-pointer size field sits next to the
        # `owned` byte and padding, and passing the raw i32 lets those adjacent bytes
        # leak into the 64-bit length register that glibc's memcpy reads, giving a
        # garbage huge length and an out-of-bounds read on x86-64 (issue #149).
        memcpy_fn = self.codegen.module.declare_intrinsic(
            'llvm.memcpy',
            [ir.PointerType(self.codegen.i8), ir.PointerType(self.codegen.i8), ir.IntType(INT64_BIT_WIDTH)]
        )
        is_volatile = FALSE_I1
        size1_i64 = self.codegen.builder.zext(size1, ir.IntType(INT64_BIT_WIDTH))
        self.codegen.builder.call(memcpy_fn, [new_data, data1, size1_i64, is_volatile])

        offset_ptr = self.codegen.builder.gep(new_data, [size1])
        size2_i64 = self.codegen.builder.zext(size2, ir.IntType(INT64_BIT_WIDTH))
        self.codegen.builder.call(memcpy_fn, [offset_ptr, data2, size2_i64, is_volatile])

        string_struct_type = self.codegen.types.string_struct
        undef_struct = ir.Constant(string_struct_type, ir.Undefined)
        struct_with_data = self.codegen.builder.insert_value(undef_struct, new_data, 0)
        struct_with_size = self.codegen.builder.insert_value(struct_with_data, total_size, 1)
        struct_complete = self.codegen.builder.insert_value(
            struct_with_size, ir.Constant(self.codegen.i8, 1), 2)

        return struct_complete

    def emit_to_cstr(self, string_struct: ir.Value) -> ir.Value:
        """Convert fat pointer string to null-terminated C string."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        data_ptr = self.codegen.builder.extract_value(string_struct, 0)
        size = self.codegen.builder.extract_value(string_struct, 1)

        size_plus_one = self.codegen.builder.add(size, ir.Constant(self.codegen.i32, 1))
        size_i64 = self.codegen.builder.zext(size_plus_one, ir.IntType(INT64_BIT_WIDTH))
        c_str = emit_malloc(self.codegen, self.codegen.builder, size_i64)

        # Copy string data using llvm.memcpy intrinsic. i64-length form + zero-extended
        # size so adjacent fat-pointer bytes cannot leak into the length register (#149).
        memcpy_fn = self.codegen.module.declare_intrinsic(
            'llvm.memcpy',
            [ir.PointerType(self.codegen.i8), ir.PointerType(self.codegen.i8), ir.IntType(INT64_BIT_WIDTH)]
        )
        is_volatile = FALSE_I1
        size_copy_i64 = self.codegen.builder.zext(size, ir.IntType(INT64_BIT_WIDTH))
        self.codegen.builder.call(memcpy_fn, [c_str, data_ptr, size_copy_i64, is_volatile])

        null_ptr = self.codegen.builder.gep(c_str, [size])
        self.codegen.builder.store(ir.Constant(self.codegen.i8, 0), null_ptr)

        return c_str

    def emit_cstr_to_fat_pointer(self, c_str: ir.Value, owned: int) -> ir.Value:
        """Convert null-terminated C string to fat pointer struct."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        size = self.codegen.builder.call(self.codegen.runtime.libc_strings.strlen, [c_str])

        string_struct_type = self.codegen.types.string_struct
        undef_struct = ir.Constant(string_struct_type, ir.Undefined)
        struct_with_data = self.codegen.builder.insert_value(undef_struct, c_str, 0)
        struct_with_size = self.codegen.builder.insert_value(struct_with_data, size, 1)
        struct_complete = self.codegen.builder.insert_value(
            struct_with_size, ir.Constant(self.codegen.i8, 1 if owned else 0), 2)

        return struct_complete

    def emit_cstr_to_owned_fat_pointer(self, c_str: ir.Value) -> ir.Value:
        """Copy a foreign C `char*` into a fresh Sushi-owned buffer (owned=1)."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        b = self.codegen.builder

        size = b.call(self.codegen.runtime.libc_strings.strlen, [c_str])  # i32 byte count
        size_i64 = b.zext(size, ir.IntType(INT64_BIT_WIDTH))

        new_data = emit_malloc(self.codegen, b, size_i64)

        # i64-length memcpy with the zero-extended size (never the raw i32, see #149).
        memcpy_fn = self.codegen.module.declare_intrinsic(
            'llvm.memcpy',
            [ir.PointerType(self.codegen.i8), ir.PointerType(self.codegen.i8), ir.IntType(INT64_BIT_WIDTH)]
        )
        b.call(memcpy_fn, [new_data, c_str, size_i64, FALSE_I1])

        string_struct_type = self.codegen.types.string_struct
        s = ir.Constant(string_struct_type, ir.Undefined)
        s = b.insert_value(s, new_data, 0)
        s = b.insert_value(s, size, 1)
        s = b.insert_value(s, ir.Constant(self.codegen.i8, 1), 2)
        return s

    def emit_string_byte_count(self, string_ptr: ir.Value) -> ir.Value:
        """Generate call to strlen for string BYTE count (not character count)."""
        return self.codegen.builder.call(self.codegen.runtime.libc_strings.strlen, [string_ptr])

    def emit_string_length(self, string_ptr: ir.Value) -> ir.Value:
        """DEPRECATED: Use emit_string_byte_count() for clarity."""
        return self.emit_string_byte_count(string_ptr)

    def emit_string_allocation(self, size: ir.Value) -> ir.Value:
        """Generate call to malloc for string allocation."""
        if isinstance(size.type, ir.IntType) and size.type.width == 32:
            size_i64 = self.codegen.builder.zext(size, ir.IntType(INT64_BIT_WIDTH))
        else:
            size_i64 = size
        return emit_malloc(self.codegen, self.codegen.builder, size_i64)

    def emit_string_char_count(self, string_ptr: ir.Value) -> ir.Value:
        """Generate call to utf8_char_count for Unicode-aware CHARACTER counting."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        return self.codegen.builder.call(self.utf8_char_count, [string_ptr])

    def _declare_and_define_utf8_char_count(self) -> None:
        """Declare and define the utf8_char_count function for Unicode-aware string length."""
        existing = self.codegen.module.globals.get("utf8_char_count")
        if isinstance(existing, ir.Function):
            self.utf8_char_count = existing
            return

        fn_ty = ir.FunctionType(self.codegen.i32, [self.codegen.i8.as_pointer()])
        func = ir.Function(self.codegen.module, fn_ty, name="utf8_char_count")
        self.utf8_char_count = func

        entry_block = func.append_basic_block("entry")
        loop_head = func.append_basic_block("loop_head")
        loop_body = func.append_basic_block("loop_body")
        loop_exit = func.append_basic_block("loop_exit")

        saved_builder = self.codegen.builder
        saved_block = saved_builder.block if saved_builder else None

        builder = ir.IRBuilder(entry_block)

        str_param = func.args[0]
        str_param.name = "str"

        count_ptr = builder.alloca(self.codegen.i32, name="count")
        index_ptr = builder.alloca(self.codegen.i32, name="index")

        builder.store(ir.Constant(self.codegen.i32, 0), count_ptr)
        builder.store(ir.Constant(self.codegen.i32, 0), index_ptr)
        builder.branch(loop_head)

        builder.position_at_end(loop_head)
        current_index = builder.load(index_ptr)
        char_ptr = builder.gep(str_param, [current_index])
        current_byte = builder.load(char_ptr)

        zero_i8 = ir.Constant(self.codegen.i8, 0)
        is_not_null = builder.icmp_signed('!=', current_byte, zero_i8)
        builder.cbranch(is_not_null, loop_body, loop_exit)

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

        current_count = builder.load(count_ptr)
        new_count = builder.select(
            is_not_continuation,
            builder.add(current_count, ir.Constant(self.codegen.i32, 1)),
            current_count
        )
        builder.store(new_count, count_ptr)

        next_index = builder.add(current_index, ir.Constant(self.codegen.i32, 1))
        builder.store(next_index, index_ptr)
        builder.branch(loop_head)

        builder.position_at_end(loop_exit)
        final_count = builder.load(count_ptr)
        builder.ret(final_count)

        if saved_builder and saved_block:
            saved_builder.position_at_end(saved_block)


def emit_utf8_count(builder: ir.IRBuilder, module: ir.Module, string_ptr: ir.Value) -> ir.Value:
    """Emit inline UTF-8 character count for stdlib fallback."""
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = ir.IntType(INT8_BIT_WIDTH).as_pointer()

    if "utf8_char_count" in module.globals:
        utf8_char_count_fn = module.globals["utf8_char_count"]
    else:
        fn_ty = ir.FunctionType(i32, [i8_ptr])
        utf8_char_count_fn = ir.Function(module, fn_ty, name="utf8_char_count")

    return builder.call(utf8_char_count_fn, [string_ptr])
