"""String Library Main Coordinator"""

from typing import Any
from dataclasses import dataclass
import llvmlite.ir as ir
from llvmlite import binding as llvm

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import Type, BuiltinType, DynamicArrayType
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


@dataclass(frozen=True)
class MethodSpec:
    """One string method's row: the arguments after the receiver, and the return (#828).

    The return-type reader and the back end both read this row, and the back end turns
    each Sushi type into its LLVM type through `llvm_value_type`.
    """
    name: str
    arg_types: tuple
    returns: Any

    @property
    def arg_count(self) -> int:
        return len(self.arg_types)


_S, _I32, _BOOL = BuiltinType.STRING, BuiltinType.I32, BuiltinType.BOOL


def _maybe(payload: BuiltinType) -> GenericTypeRef:
    return GenericTypeRef(base_name="Maybe", type_args=(payload,))


def _spec(name: str, args: tuple, returns: Any) -> MethodSpec:
    return MethodSpec(f"string.{name}", args, returns)


# The ONE spelling of every string method's signature.
# Note: is_empty and clone are NOT here: they are inline intrinsics, not stdlib methods.
METHOD_SPECS = {name: _spec(name, args, returns) for name, args, returns in (
    ("len", (), _I32),
    ("size", (), _I32),
    ("upper", (), _S),
    ("lower", (), _S),
    ("cap", (), _S),
    ("trim", (), _S),
    ("tleft", (), _S),
    ("tright", (), _S),
    ("to_bytes", (), DynamicArrayType(BuiltinType.U8)),
    ("reverse", (), _S),

    ("concat", (_S,), _S),
    ("contains", (_S,), _BOOL),
    ("find", (_S,), _maybe(_I32)),
    ("find_last", (_S,), _maybe(_I32)),
    ("count", (_S,), _I32),
    ("starts_with", (_S,), _BOOL),
    ("ends_with", (_S,), _BOOL),
    ("strip_prefix", (_S,), _S),
    ("strip_suffix", (_S,), _S),

    ("sleft", (_I32,), _S),
    ("sright", (_I32,), _S),
    ("char_at", (_I32,), _S),
    ("repeat", (_I32,), _S),

    ("s", (_I32, _I32), _S),
    ("ss", (_I32, _I32), _S),

    ("split", (_S,), DynamicArrayType(_S)),
    ("join", (DynamicArrayType(_S),), _S),

    ("replace", (_S, _S), _S),
    ("pad_left", (_I32, _S), _S),
    ("pad_right", (_I32, _S), _S),

    ("to_i32", (), _maybe(_I32)),
    ("to_i64", (), _maybe(BuiltinType.I64)),
    ("to_f64", (), _maybe(BuiltinType.F64)),
)}

# The inline intrinsics answer here, beside the rows.
_INLINE_RETURNS = {"is_empty": _BOOL, "clone": _S}


def _validate_method_signature(call: MethodCall, spec: MethodSpec, reporter: Any, validator: Any = None) -> None:
    """Generic validation for string method signatures."""
    if len(call.args) != spec.arg_count:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=spec.name, expected=spec.arg_count, got=len(call.args))
        return

    # Validate argument types if validator is available. Only a scalar parameter is
    # checked here: `join`'s string[] argument is not, as before the row held it.
    if validator:
        for i, (arg, expected_type) in enumerate(zip(call.args, spec.arg_types, strict=True)):
            if not isinstance(expected_type, BuiltinType):
                continue
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
    """Get the return type of a built-in string method, from its row.

    Total over every string method: a Maybe-returning one answers a GenericTypeRef
    spelling, which a caller with an enum table interns (the table has none) (#269).
    """
    spec = METHOD_SPECS.get(method_name)
    if spec is not None:
        return spec.returns
    return _INLINE_RETURNS.get(method_name)


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
