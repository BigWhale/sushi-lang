"""Formatting operations for LLVM code generation."""
from __future__ import annotations

import typing

from llvmlite import ir

from sushi_lang.backend.constants.llvm_values import make_i64_const
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.backend.runtime.constants import FORMAT_STRINGS, STDOUT_FD
from sushi_lang.internals.errors import raise_internal_error

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class FormattingOperations:
    """Manages formatting operations and type conversions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance."""
        self.codegen = codegen

        self.fmt_i32: ir.GlobalVariable | None = None
        self.fmt_i64: ir.GlobalVariable | None = None
        self.fmt_u32: ir.GlobalVariable | None = None
        self.fmt_u64: ir.GlobalVariable | None = None
        self.fmt_str: ir.GlobalVariable | None = None
        self.fmt_f32: ir.GlobalVariable | None = None
        self.fmt_f64: ir.GlobalVariable | None = None
        self.fmt_bool_true: ir.GlobalVariable | None = None
        self.fmt_bool_false: ir.GlobalVariable | None = None

    def declare_format_strings(self) -> None:
        """Declare global format string constants for printf operations."""
        for name in ["i32", "str", "f32", "f64"]:
            attr_name = f"fmt_{name}"
            if getattr(self, attr_name, None) is None:
                global_str = self._create_format_string(name, FORMAT_STRINGS[name])
                setattr(self, attr_name, global_str)

    def emit_console_write(self, data_ptr: ir.Value, length: ir.Value,
                           fd: int = STDOUT_FD, builder: ir.IRBuilder | None = None) -> None:
        """The ONE route from a print statement to a descriptor.

        `print`, `println` and every value kind between them reach the console here and
        nowhere else. The route used to be libc stdio, which BUFFERS: when the stream is
        a pipe rather than a terminal, stdio holds every byte until exit, while a
        descriptor write reaches the kernel at once. The two orders then disagree, and
        the captured output is not the written one (HANDLES.md, Phase 5).

        `length` is a byte count of any integer width; it is widened here so that no
        caller has to remember which width `write(2)` takes. `builder` names the builder
        to emit into, for a generated method body that has one of its own.
        """
        builder = builder if builder is not None else self.codegen.builder
        # A byte count is never negative, so the widening is a zext at every width.
        count = length if length.type == self.codegen.types.i64 \
            else builder.zext(length, self.codegen.types.i64, name="write_count")
        builder.call(self.codegen.runtime.libc_stdio.fd_write,
                     [ir.Constant(self.codegen.i32, fd), data_ptr, count])

    def _emit_formatted_write(self, fmt_ptr: ir.Value, value: ir.Value,
                              is_line: bool, size: int = 64) -> None:
        """Format one scalar into a frame buffer and write it, newline included.

        `sprintf` answers the byte count, so the newline is appended to the buffer
        rather than written separately -- one call to the kernel for the whole line.
        The buffer is an ENTRY alloca, so a print inside a loop does not grow the frame.
        """
        builder = self.codegen.builder
        i8 = self.codegen.i8
        slot = self.codegen.memory.entry_alloca(ir.ArrayType(i8, size + 1), "print_buf")
        buf = builder.bitcast(slot, i8.as_pointer(), name="print_buf_ptr")
        written = builder.call(self.codegen.runtime.libc_strings.sprintf,
                               [buf, fmt_ptr, value], name="print_len")
        if is_line:
            end = builder.gep(buf, [written], name="print_end")
            builder.store(ir.Constant(i8, ord("\n")), end)
            written = builder.add(written, ir.Constant(self.codegen.i32, 1))
        self.emit_console_write(buf, written)

    def emit_print_value(self, v: ir.Value, is_line: bool = False,
                         semantic_type=None) -> None:
        """Write one value to the console, with the newline when `is_line` asks."""
        assert (
            self.codegen.builder is not None
            and self.codegen.runtime.libc_strings.sprintf is not None
            and self.fmt_i32 is not None
            and self.fmt_str is not None
            and self.fmt_f32 is not None
            and self.fmt_f64 is not None
        )

        if self.codegen.types.is_string_type(v.type):
            # The fat pointer carries its own byte count, so the string is written in
            # place: no format pass, and no heap C-string copy (#141). A string of any
            # length goes out whole, which a frame buffer could not promise.
            data_ptr = self.codegen.builder.extract_value(v, 0)
            size = self.codegen.builder.extract_value(v, 1)
            self.emit_console_write(data_ptr, size)
            if is_line:
                self._emit_newline()
            return

        if isinstance(v.type, ir.FloatType):
            fmt_ptr = self.codegen.utils.cstr_ptr(self.fmt_f32)
            f64_val = self.codegen.builder.fpext(v, self.codegen.types.f64)
            self._emit_formatted_write(fmt_ptr, f64_val, is_line)
            return

        if isinstance(v.type, ir.DoubleType):
            fmt_ptr = self.codegen.utils.cstr_ptr(self.fmt_f64)
            self._emit_formatted_write(fmt_ptr, v, is_line)
            return

        self._emit_print_integer(v, semantic_type, is_line)

    def _emit_newline(self) -> None:
        """Write the one byte a `println` adds after a string."""
        newline = self._get_format_string("newline", "\n")
        self.emit_console_write(newline, ir.Constant(self.codegen.i32, 1))

    def _emit_print_integer(self, v: ir.Value, semantic_type=None,
                            is_line: bool = False) -> None:
        """Print an integer at its own width with signedness-aware formatting."""
        from sushi_lang.semantics.typesys import BuiltinType
        from sushi_lang.backend.expressions.type_utils import is_unsigned_type

        builder = self.codegen.builder

        width = v.type.width if isinstance(v.type, ir.IntType) else 32
        is_bool = (width == 1 or semantic_type == BuiltinType.BOOL)
        is_signed = not is_unsigned_type(semantic_type)

        if is_bool:
            self._emit_print_bool(v, is_line)
            return

        if not isinstance(v.type, ir.IntType):
            fmt_ptr = self._get_format_string("i32", FORMAT_STRINGS["i32"])
            self._emit_formatted_write(fmt_ptr, self.codegen.utils.as_i32(v), is_line)
            return

        if width < 32:
            value = builder.sext(v, self.codegen.i32) if is_signed \
                else builder.zext(v, self.codegen.i32)
            name = "i32" if is_signed else "u32"
        elif width == 32:
            value = v
            name = "i32" if is_signed else "u32"
        else:
            value = v
            name = "i64" if is_signed else "u64"

        fmt_ptr = self._get_format_string(name, FORMAT_STRINGS[name])
        self._emit_formatted_write(fmt_ptr, value, is_line)

    def _emit_print_bool(self, v: ir.Value, is_line: bool = False) -> None:
        """Print a bool as the word it is: true or false, never 1 or 0 (#523).

        #516 moved the interpolation hole to the words and left the print statements
        on %d, so `println(flag)` and `println("{flag}")` disagreed about one value.
        Both spellings go through emit_bool_to_string now.
        """
        self.emit_print_value(self.emit_bool_to_string(v), is_line=is_line)

    def emit_integer_to_string(self, int_value: ir.Value, is_signed: bool, bit_width: int) -> ir.Value:
        """Generate integer to string conversion using sprintf."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        if self.codegen.runtime.libc_strings.sprintf is None:
            raise_internal_error("CE0013", name="sprintf")
        if bit_width <= 32:
            if is_signed:
                fmt_str = self._get_format_string("i32", FORMAT_STRINGS["i32"])
            else:
                fmt_str = self._get_format_string("u32", FORMAT_STRINGS["u32"])
        else:  # 64-bit
            if is_signed:
                fmt_str = self._get_format_string("i64", FORMAT_STRINGS["i64"])
            else:
                fmt_str = self._get_format_string("u64", FORMAT_STRINGS["u64"])

        buffer = self._allocate_conversion_buffer(32)

        converted_value = self._prepare_integer_for_sprintf(int_value, is_signed, bit_width)

        self.codegen.builder.call(self.codegen.runtime.libc_strings.sprintf, [buffer, fmt_str, converted_value])

        return self.codegen.runtime.strings.emit_cstr_to_fat_pointer(buffer, owned=1)

    def emit_float_to_string(self, float_value: ir.Value, is_double: bool) -> ir.Value:
        """Generate float to string conversion using sprintf."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        if self.codegen.runtime.libc_strings.sprintf is None:
            raise_internal_error("CE0013", name="sprintf")
        if is_double:
            fmt_str = self._get_format_string("f64", "%.6f")
        else:
            fmt_str = self._get_format_string("f32", "%.6f")
            float_value = self.codegen.builder.fpext(float_value, self.codegen.types.f64)

        buffer = self._allocate_conversion_buffer(64)

        self.codegen.builder.call(self.codegen.runtime.libc_strings.sprintf, [buffer, fmt_str, float_value])

        return self.codegen.runtime.strings.emit_cstr_to_fat_pointer(buffer, owned=1)

    def emit_bool_to_string(self, bool_value: ir.Value) -> ir.Value:
        """Generate bool to string conversion."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        true_str = self.codegen.runtime.strings.emit_string_literal(FORMAT_STRINGS["bool_true"])
        false_str = self.codegen.runtime.strings.emit_string_literal(FORMAT_STRINGS["bool_false"])

        if bool_value.type != self.codegen.i1:
            bool_value = self.codegen.utils.as_i1(bool_value)

        return self.codegen.builder.select(bool_value, true_str, false_str)

    def emit_character_case_conversion(self, char_value: ir.Value, to_upper: bool) -> ir.Value:
        """Generate toupper/tolower call for character case conversion."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        if to_upper:
            if self.codegen.runtime.libc_ctype.toupper is None:
                raise_internal_error("CE0013", name="toupper")
            return self.codegen.builder.call(self.codegen.runtime.libc_ctype.toupper, [char_value])
        else:
            if self.codegen.runtime.libc_ctype.tolower is None:
                raise_internal_error("CE0013", name="tolower")
            return self.codegen.builder.call(self.codegen.runtime.libc_ctype.tolower, [char_value])

    def emit_character_classification(self, char_value: ir.Value, classification: str) -> ir.Value:
        """Generate character classification call (isspace, isdigit, isalpha, isalnum)."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        classification_funcs = {
            "space": self.codegen.runtime.libc_ctype.isspace,
            "digit": self.codegen.runtime.libc_ctype.isdigit,
            "alpha": self.codegen.runtime.libc_ctype.isalpha,
            "alnum": self.codegen.runtime.libc_ctype.isalnum,
        }

        if classification not in classification_funcs:
            raise_internal_error("CE0096", operation=classification)

        func = classification_funcs[classification]
        if func is None:
            raise_internal_error("CE0013", name=classification)
        return self.codegen.builder.call(func, [char_value])

    def _create_format_string(self, name: str, format_str: str) -> ir.GlobalVariable:
        """Generic method to create a global format string constant."""
        data = format_str.encode('utf-8') + b'\0'
        arr_ty = ir.ArrayType(self.codegen.i8, len(data))
        gv = ir.GlobalVariable(self.codegen.module, arr_ty, name=f".fmt.{name}")
        gv.linkage = "private"
        gv.global_constant = True
        gv.unnamed_addr = "unnamed_addr"
        gv.initializer = ir.Constant(arr_ty, bytearray(data))
        return gv

    def _get_format_string(self, name: str, format_str: str) -> ir.Value:
        """Get or create a global format string constant."""
        attr_name = f"fmt_{name}"
        existing = getattr(self, attr_name, None)

        if existing is None:
            global_str = self._create_format_string(name, format_str)
            setattr(self, attr_name, global_str)
            existing = global_str

        zero = ir.Constant(self.codegen.i32, 0)
        return self.codegen.builder.gep(existing, [zero, zero])

    def _allocate_conversion_buffer(self, size: int) -> ir.Value:
        """Allocate a buffer for type-to-string conversion."""
        buffer_size = make_i64_const(size)
        buffer = emit_malloc(self.codegen, self.codegen.builder, buffer_size)
        # If emitted inside a print/println argument, this to-string buffer is a temporary
        # to free after output (#141). No-op elsewhere.
        self.codegen.register_string_temp(buffer)
        return buffer

    def _prepare_integer_for_sprintf(
        self, int_value: ir.Value, is_signed: bool, bit_width: int
    ) -> ir.Value:
        """Prepare integer value for sprintf by converting to appropriate type."""
        if bit_width < 32:
            if is_signed:
                return self.codegen.builder.sext(int_value, self.codegen.i32)
            else:
                return self.codegen.builder.zext(int_value, self.codegen.i32)
        else:
            return int_value
