"""What crosses the C boundary, and how it crosses (docs/design/ffi-memory.md).

One home for the rule, because four readers ask it: the collector interns what a
signature names, the `externs` pass refuses what cannot cross, the backend lowers and
marshals it, and the CW5001 notes describe it.
"""
from __future__ import annotations

from typing import Any, Optional

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.type_predicates import BUILTIN_NUMERIC_TYPES
from sushi_lang.semantics.typesys import BuiltinType, EnumType, ForeignPtrType, Type

# The C ABI's own allowlist. It is not the numeric set plus two: a `~` crosses the
# boundary as void, and nothing else here is a numeric rule.
C_ABI_BUILTINS = BUILTIN_NUMERIC_TYPES | {
    BuiltinType.BOOL, BuiltinType.STRING, BuiltinType.BLANK,
}


def is_pointer_value(ty: Optional[Type]) -> bool:
    """A value that crosses as a C pointer and that C may answer NULL for."""
    return isinstance(ty, ForeignPtrType) or ty == BuiltinType.STRING


def nullable_payload(ty: Optional[Type]) -> Optional[Type]:
    """The pointer a `Maybe@(string)` / `Maybe@(ptr)` wraps, else None (#1085).

    Written (`GenericTypeRef`) or interned (`EnumType`) alike, because the `externs` pass
    reads the declaration and the backend reads the collected signature.
    """
    if isinstance(ty, GenericTypeRef) and ty.base_name == "Maybe":
        args = ty.type_args
    elif isinstance(ty, EnumType) and ty.generic_base == "Maybe":
        args = ty.generic_args or ()
    else:
        return None
    if len(args) == 1 and is_pointer_value(args[0]):
        return args[0]
    return None


def is_c_abi_scalar(ty: Optional[Type]) -> bool:
    """A value C holds as itself: a number, `bool`, `string`, `ptr` or `~`.

    This is the whole rule for a trailing variadic argument (CE5005), which has no
    declared type to say how to marshal anything else.
    """
    if isinstance(ty, ForeignPtrType):
        return True
    return isinstance(ty, BuiltinType) and ty in C_ABI_BUILTINS


def is_c_abi_type(ty: Optional[Type]) -> bool:
    """Strict allowlist for a declared parameter or return."""
    return is_c_abi_scalar(ty) or nullable_payload(ty) is not None


def intern_boundary_type(ty: Optional[Type], enums: Any) -> Optional[Type]:
    """The type a collected signature holds: a nullable pointer is the interned Maybe."""
    payload = nullable_payload(ty)
    if payload is None or isinstance(ty, EnumType):
        return ty
    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
    return ensure_maybe_type_in_table(enums, payload) or ty


#: The built-in that reads the calling thread's `errno` (#1087). A declaration of the
#: same name wins over it, as a declaration wins over every stdlib row.
ERRNO_FUNCTION = "errno"


def unit_declares_external_block(external_table: Any, unit_name: Optional[str]) -> bool:
    """Does this unit declare an `unsafe external` block? The confinement of CE5009."""
    if external_table is None:
        return False
    return any(sig.unit_name == unit_name
               for decls in external_table.by_namespace.values()
               for sig in decls.values())
