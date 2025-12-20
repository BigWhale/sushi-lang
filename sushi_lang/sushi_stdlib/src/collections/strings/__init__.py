"""
String Library Main Coordinator

This module coordinates the generation of all string operations as a single LLVM module.
It orchestrates intrinsics and string methods, generating precompiled .bc files.

Architecture:
1. Intrinsics layer: Low-level LLVM IR building blocks
2. Method layer: Higher-level string operations using intrinsics
3. Module generation: Combines all into stdlib/dist/collections/strings.bc

String representation: { i8* data, i32 size } (fat pointer)
"""

from typing import Any
from dataclasses import dataclass
import llvmlite.ir as ir
from llvmlite import binding as llvm

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.internals import errors as er

# Import intrinsic implementations (for .bc compilation)
from .intrinsics.utf8_count import emit_utf8_count_intrinsic
from .intrinsics.utf8_byte_offset import emit_utf8_byte_offset_intrinsic
from .intrinsics.char_ops import (
    emit_toupper_intrinsic,
    emit_tolower_intrinsic,
    emit_isspace_intrinsic,
)

# Import method implementations (for .bc compilation)
from .methods.basic import (
    emit_string_size,
    emit_string_len,
    emit_string_concat,
)
from .methods.convert import (
    emit_string_to_bytes,
    emit_string_split,
    emit_string_join,
)
from .methods.case import (
    emit_string_upper,
    emit_string_lower,
    emit_string_cap,
)
from .methods.search import (
    emit_string_starts_with,
    emit_string_ends_with,
    emit_string_contains,
    emit_string_find,
    emit_string_find_last,
    emit_string_count,
)
from .methods.trim import (
    emit_string_trim,
    emit_string_tleft,
    emit_string_tright,
)
from .methods.slice import (
    emit_string_ss,
    emit_string_sleft,
    emit_string_sright,
    emit_string_char_at,
    emit_string_s,
)
from .methods.modify import (
    emit_string_replace,
    emit_string_reverse,
    emit_string_repeat,
    emit_string_pad_left,
    emit_string_pad_right,
    emit_string_strip_prefix,
    emit_string_strip_suffix,
)
from .methods.parse import (
    emit_string_to_i32,
    emit_string_to_i64,
    emit_string_to_f64,
)


# ==============================================================================
# Semantic Validation (Pass 2)
# ==============================================================================

@dataclass
class MethodSpec:
    """Specification for a string method's signature."""
    name: str
    arg_count: int
    arg_types: list[BuiltinType]  # Expected types for each argument


# Method specification registry - single source of truth for all string method signatures
# Note: is_empty is NOT included here as it's an inline intrinsic, not a stdlib method
METHOD_SPECS = {
    # No-argument methods
    "len": MethodSpec("string.len", 0, []),
    "size": MethodSpec("string.size", 0, []),
    "upper": MethodSpec("string.upper", 0, []),
    "lower": MethodSpec("string.lower", 0, []),
    "cap": MethodSpec("string.cap", 0, []),
    "trim": MethodSpec("string.trim", 0, []),
    "tleft": MethodSpec("string.tleft", 0, []),
    "tright": MethodSpec("string.tright", 0, []),
    "to_bytes": MethodSpec("string.to_bytes", 0, []),
    "reverse": MethodSpec("string.reverse", 0, []),

    # Single string argument methods
    "concat": MethodSpec("string.concat", 1, [BuiltinType.STRING]),
    "contains": MethodSpec("string.contains", 1, [BuiltinType.STRING]),
    "find": MethodSpec("string.find", 1, [BuiltinType.STRING]),
    "find_last": MethodSpec("string.find_last", 1, [BuiltinType.STRING]),
    "count": MethodSpec("string.count", 1, [BuiltinType.STRING]),
    "starts_with": MethodSpec("string.starts_with", 1, [BuiltinType.STRING]),
    "ends_with": MethodSpec("string.ends_with", 1, [BuiltinType.STRING]),
    "strip_prefix": MethodSpec("string.strip_prefix", 1, [BuiltinType.STRING]),
    "strip_suffix": MethodSpec("string.strip_suffix", 1, [BuiltinType.STRING]),

    # Single int argument methods
    "sleft": MethodSpec("string.sleft", 1, [BuiltinType.I32]),
    "sright": MethodSpec("string.sright", 1, [BuiltinType.I32]),
    "char_at": MethodSpec("string.char_at", 1, [BuiltinType.I32]),
    "repeat": MethodSpec("string.repeat", 1, [BuiltinType.I32]),

    # Two int arguments methods
    "s": MethodSpec("string.s", 2, [BuiltinType.I32, BuiltinType.I32]),
    "ss": MethodSpec("string.ss", 2, [BuiltinType.I32, BuiltinType.I32]),

    # String splitting and joining
    "split": MethodSpec("string.split", 1, [BuiltinType.STRING]),
    "join": MethodSpec("string.join", 1, []),

    # String modification
    "replace": MethodSpec("string.replace", 2, [BuiltinType.STRING, BuiltinType.STRING]),
    "pad_left": MethodSpec("string.pad_left", 2, [BuiltinType.I32, BuiltinType.STRING]),
    "pad_right": MethodSpec("string.pad_right", 2, [BuiltinType.I32, BuiltinType.STRING]),

    # String parsing (return Maybe<T>)
    "to_i32": MethodSpec("string.to_i32", 0, []),
    "to_i64": MethodSpec("string.to_i64", 0, []),
    "to_f64": MethodSpec("string.to_f64", 0, []),
}


def _validate_method_signature(call: MethodCall, spec: MethodSpec, reporter: Any, validator: Any = None) -> None:
    """Generic validation for string method signatures."""
    # Validate argument count
    if len(call.args) != spec.arg_count:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=spec.name, expected=spec.arg_count, got=len(call.args))
        return

    # Validate argument types if validator is available
    if validator:
        for i, (arg, expected_type) in enumerate(zip(call.args, spec.arg_types)):
            validator.validate_expression(arg)
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and arg_type != expected_type:
                expected_name = "string" if expected_type == BuiltinType.STRING else "int"
                er.emit(reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=expected_name, got=str(arg_type))


def is_builtin_string_method(method_name: str) -> bool:
    """Check if a method name is a built-in string method.

    Note: Includes both stdlib methods (in METHOD_SPECS) and inline intrinsics (is_empty).
    """
    return method_name in METHOD_SPECS or method_name == "is_empty"


def validate_builtin_string_method_with_validator(call: MethodCall, string_type: BuiltinType, reporter: Any, validator: Any) -> None:
    """Validate built-in string method calls with access to the validator for type checking."""
    method_name = call.method

    # Handle inline intrinsic is_empty (not in METHOD_SPECS)
    if method_name == "is_empty":
        if len(call.args) != 0:
            er.emit(reporter, er.ERR.CE2009, call.loc,
                   name="string.is_empty", expected=0, got=len(call.args))
        return

    spec = METHOD_SPECS.get(method_name)
    if spec:
        _validate_method_signature(call, spec, reporter, validator)


def get_builtin_string_method_return_type(method_name: str, string_type: BuiltinType) -> Type | None:
    """Get the return type of a built-in string method.

    Note: is_empty is included here even though it's an inline intrinsic,
    because type checking happens before code generation.
    """
    from sushi_lang.semantics.typesys import DynamicArrayType, EnumType
    from sushi_lang.backend.generics.maybe import ensure_maybe_type_in_table
    # Methods returning int
    if method_name in {"len", "size"}:
        return BuiltinType.I32
    # Methods returning bool (includes inline intrinsic is_empty)
    elif method_name in {"is_empty", "contains", "starts_with", "ends_with"}:
        return BuiltinType.BOOL
    # Methods returning string
    elif method_name in {"concat", "s", "sleft", "sright", "char_at", "ss",
                         "upper", "lower", "cap", "trim", "tleft", "tright", "replace",
                         "join", "pad_left", "pad_right", "strip_prefix", "strip_suffix"}:
        return BuiltinType.STRING
    # Methods returning u8[]
    elif method_name == "to_bytes":
        return DynamicArrayType(BuiltinType.U8)
    # Methods returning string[]
    elif method_name == "split":
        return DynamicArrayType(BuiltinType.STRING)
    # Methods returning Maybe<T> (parsing methods)
    elif method_name == "to_i32":
        # Return Maybe<i32> - note we need access to enum_table for this
        # This will be handled specially in type_visitor.py
        return None  # Special case, handled in type_visitor
    elif method_name == "to_i64":
        return None  # Special case, handled in type_visitor
    elif method_name == "to_f64":
        return None  # Special case, handled in type_visitor
    # Note: .find() method is handled specially in type_visitor.py
    return None


# ==============================================================================
# LLVM IR Generation
# ==============================================================================


def generate_module_ir() -> ir.Module:
    """Generate complete strings module as LLVM IR module.

    This function emits all intrinsics and string methods into a single module.
    This is the interface called by stdlib/build.py.

    Returns:
        LLVM IR Module containing all string method implementations.
    """
    # Create module
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
    module = create_stdlib_module("collections.strings")

    # === Emit intrinsics ===
    emit_utf8_count_intrinsic(module)
    emit_utf8_byte_offset_intrinsic(module)
    emit_toupper_intrinsic(module)
    emit_tolower_intrinsic(module)
    emit_isspace_intrinsic(module)

    # === Emit basic methods ===
    emit_string_size(module)
    emit_string_len(module)
    # Note: is_empty is NOT included - it's an inline intrinsic in compiler/is_empty.py
    emit_string_concat(module)

    # === Emit conversion methods ===
    emit_string_to_bytes(module)
    emit_string_split(module)
    emit_string_join(module)

    # === Emit case conversion methods ===
    emit_string_upper(module)
    emit_string_lower(module)
    emit_string_cap(module)

    # === Emit search methods ===
    emit_string_starts_with(module)
    emit_string_ends_with(module)
    emit_string_contains(module)
    emit_string_find(module)
    emit_string_find_last(module)
    emit_string_count(module)

    # === Emit trim methods ===
    emit_string_trim(module)
    emit_string_tleft(module)
    emit_string_tright(module)

    # === Emit slice methods ===
    emit_string_ss(module)
    emit_string_sleft(module)
    emit_string_sright(module)
    emit_string_char_at(module)
    emit_string_s(module)

    # === Emit modification methods ===
    emit_string_replace(module)
    emit_string_reverse(module)
    emit_string_repeat(module)
    emit_string_pad_left(module)
    emit_string_pad_right(module)
    emit_string_strip_prefix(module)
    emit_string_strip_suffix(module)

    # === Emit parsing methods ===
    emit_string_to_i32(module)
    emit_string_to_i64(module)
    emit_string_to_f64(module)

    return module


def generate_strings_module() -> str:
    """Generate complete strings module as LLVM IR string.

    Convenience wrapper for debugging and direct IR inspection.

    Returns:
        LLVM IR string for the complete strings module.
    """
    return str(generate_module_ir())


def compile_to_bitcode(output_path: str = "stdlib/dist/collections/strings.bc"):
    """Compile the strings module to LLVM bitcode.

    Args:
        output_path: Path to write the .bc file (default: stdlib/dist/collections/strings.bc)
    """
    # Generate IR
    ir_code = generate_strings_module()

    # Parse IR and create module
    llvm_module = llvm.parse_assembly(ir_code)
    llvm_module.verify()

    # Write bitcode
    with open(output_path, "wb") as f:
        f.write(llvm_module.as_bitcode())

    print(f"Compiled strings module to {output_path}")


if __name__ == "__main__":
    # Generate and print IR for debugging
    ir_code = generate_strings_module()
    print(ir_code)
    print("\n" + "=" * 80 + "\n")

    # Compile to bitcode
    compile_to_bitcode()
