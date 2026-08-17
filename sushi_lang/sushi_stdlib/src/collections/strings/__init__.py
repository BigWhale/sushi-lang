"""String Library Main Coordinator"""

from typing import Any
from dataclasses import dataclass
import llvmlite.ir as ir
from llvmlite import binding as llvm

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.internals import errors as er

from .intrinsics.utf8_count import emit_utf8_count_intrinsic
from .intrinsics.utf8_byte_offset import emit_utf8_byte_offset_intrinsic
from .intrinsics.char_ops import (
    emit_toupper_intrinsic,
    emit_tolower_intrinsic,
    emit_isspace_intrinsic,
)

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
from sushi_lang.semantics.generics.type_display import display_type


@dataclass
class MethodSpec:
    """Specification for a string method's signature."""
    name: str
    arg_count: int
    arg_types: list[BuiltinType]  # Expected types for each argument


# Method specification registry - single source of truth for all string method signatures
# Note: is_empty is NOT included here as it's an inline intrinsic, not a stdlib method
METHOD_SPECS = {
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

    "concat": MethodSpec("string.concat", 1, [BuiltinType.STRING]),
    "contains": MethodSpec("string.contains", 1, [BuiltinType.STRING]),
    "find": MethodSpec("string.find", 1, [BuiltinType.STRING]),
    "find_last": MethodSpec("string.find_last", 1, [BuiltinType.STRING]),
    "count": MethodSpec("string.count", 1, [BuiltinType.STRING]),
    "starts_with": MethodSpec("string.starts_with", 1, [BuiltinType.STRING]),
    "ends_with": MethodSpec("string.ends_with", 1, [BuiltinType.STRING]),
    "strip_prefix": MethodSpec("string.strip_prefix", 1, [BuiltinType.STRING]),
    "strip_suffix": MethodSpec("string.strip_suffix", 1, [BuiltinType.STRING]),

    "sleft": MethodSpec("string.sleft", 1, [BuiltinType.I32]),
    "sright": MethodSpec("string.sright", 1, [BuiltinType.I32]),
    "char_at": MethodSpec("string.char_at", 1, [BuiltinType.I32]),
    "repeat": MethodSpec("string.repeat", 1, [BuiltinType.I32]),

    "s": MethodSpec("string.s", 2, [BuiltinType.I32, BuiltinType.I32]),
    "ss": MethodSpec("string.ss", 2, [BuiltinType.I32, BuiltinType.I32]),

    "split": MethodSpec("string.split", 1, [BuiltinType.STRING]),
    "join": MethodSpec("string.join", 1, []),

    "replace": MethodSpec("string.replace", 2, [BuiltinType.STRING, BuiltinType.STRING]),
    "pad_left": MethodSpec("string.pad_left", 2, [BuiltinType.I32, BuiltinType.STRING]),
    "pad_right": MethodSpec("string.pad_right", 2, [BuiltinType.I32, BuiltinType.STRING]),

    "to_i32": MethodSpec("string.to_i32", 0, []),
    "to_i64": MethodSpec("string.to_i64", 0, []),
    "to_f64": MethodSpec("string.to_f64", 0, []),
}


def _validate_method_signature(call: MethodCall, spec: MethodSpec, reporter: Any, validator: Any = None) -> None:
    """Generic validation for string method signatures."""
    if len(call.args) != spec.arg_count:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=spec.name, expected=spec.arg_count, got=len(call.args))
        return

    # Validate argument types if validator is available. strict=False is load-bearing:
    # a spec may list fewer arg_types than arg_count (join declares 1 arg, [] types --
    # its string[] argument has no entry), so this zip validates only the typed prefix.
    if validator:
        for i, (arg, expected_type) in enumerate(zip(call.args, spec.arg_types, strict=False)):
            validator.validate_expression(arg)
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and arg_type != expected_type:
                expected_name = "string" if expected_type == BuiltinType.STRING else "int"
                er.emit(reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=expected_name, got=display_type(arg_type))


def is_builtin_string_method(method_name: str) -> bool:
    """Check if a method name is a built-in string method."""
    return method_name in METHOD_SPECS or method_name in ("is_empty", "clone")


def validate_builtin_string_method_with_validator(call: MethodCall, string_type: BuiltinType, reporter: Any, validator: Any) -> None:
    """Validate built-in string method calls with access to the validator for type checking."""
    method_name = call.method

    # Inline intrinsics, not in METHOD_SPECS. `clone` is the explicit deep copy and the
    # escape from CE2411; it must not need `use <collections/strings>` (#242).
    if method_name in ("is_empty", "clone"):
        if len(call.args) != 0:
            er.emit(reporter, er.ERR.CE2009, call.loc,
                   name=f"string.{method_name}", expected=0, got=len(call.args))
        return

    spec = METHOD_SPECS.get(method_name)
    if spec:
        _validate_method_signature(call, spec, reporter, validator)


def get_builtin_string_method_return_type(method_name: str, string_type: BuiltinType) -> Type | None:
    """Get the return type of a built-in string method."""
    from sushi_lang.semantics.typesys import DynamicArrayType
    if method_name in {"len", "size"}:
        return BuiltinType.I32
    elif method_name in {"is_empty", "contains", "starts_with", "ends_with"}:
        return BuiltinType.BOOL
    elif method_name in {"clone", "concat", "s", "sleft", "sright", "char_at", "ss",
                         "upper", "lower", "cap", "trim", "tleft", "tright", "replace",
                         "join", "pad_left", "pad_right", "strip_prefix", "strip_suffix"}:
        return BuiltinType.STRING
    elif method_name == "to_bytes":
        return DynamicArrayType(BuiltinType.U8)
    elif method_name == "split":
        return DynamicArrayType(BuiltinType.STRING)
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


def generate_module_ir() -> ir.Module:
    """Generate complete strings module as LLVM IR module."""
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
    module = create_stdlib_module("collections.strings")

    emit_utf8_count_intrinsic(module)
    emit_utf8_byte_offset_intrinsic(module)
    emit_toupper_intrinsic(module)
    emit_tolower_intrinsic(module)
    emit_isspace_intrinsic(module)

    emit_string_size(module)
    emit_string_len(module)
    # Note: is_empty is NOT included - it's an inline intrinsic in compiler/is_empty.py
    emit_string_concat(module)

    emit_string_to_bytes(module)
    emit_string_split(module)
    emit_string_join(module)

    emit_string_upper(module)
    emit_string_lower(module)
    emit_string_cap(module)

    emit_string_starts_with(module)
    emit_string_ends_with(module)
    emit_string_contains(module)
    emit_string_find(module)
    emit_string_find_last(module)
    emit_string_count(module)

    emit_string_trim(module)
    emit_string_tleft(module)
    emit_string_tright(module)

    emit_string_ss(module)
    emit_string_sleft(module)
    emit_string_sright(module)
    emit_string_char_at(module)
    emit_string_s(module)

    emit_string_replace(module)
    emit_string_reverse(module)
    emit_string_repeat(module)
    emit_string_pad_left(module)
    emit_string_pad_right(module)
    emit_string_strip_prefix(module)
    emit_string_strip_suffix(module)

    emit_string_to_i32(module)
    emit_string_to_i64(module)
    emit_string_to_f64(module)

    return module


def generate_strings_module() -> str:
    """Generate complete strings module as LLVM IR string."""
    return str(generate_module_ir())


def compile_to_bitcode(output_path: str = "stdlib/dist/collections/strings.bc"):
    """Compile the strings module to LLVM bitcode."""
    ir_code = generate_strings_module()

    llvm_module = llvm.parse_assembly(ir_code)
    llvm_module.verify()

    with open(output_path, "wb") as f:
        f.write(llvm_module.as_bitcode())

    print(f"Compiled strings module to {output_path}")


if __name__ == "__main__":
    ir_code = generate_strings_module()
    print(ir_code)
    print("\n" + "=" * 80 + "\n")

    compile_to_bitcode()
