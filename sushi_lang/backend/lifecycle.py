"""One identity, one symbol, one out-of-line emitter for a composite type's lifecycle.

A type's deep clone must duplicate exactly the heap its destructor frees, so both halves
live here. `composite_type_key`'s ArrayType arm is load-bearing: without it `Buffer[2]`
and `Buffer[3]` collapse onto one linkonce_odr body and the linker keeps whichever came
first. See docs/design/ownership-conventions.md section 7.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict

import llvmlite.ir as ir

from sushi_lang.semantics.typesys import (
    Type, ArrayType, DynamicArrayType, StructType, EnumType,
)
from sushi_lang.backend.constants import INT8_BIT_WIDTH

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


# ---------------------------------------------------------------------------
# Identity and symbols
# ---------------------------------------------------------------------------

def composite_type_key(value_type: Type) -> str:
    """A stable identity key for a composite type's lifecycle bodies."""
    if isinstance(value_type, DynamicArrayType):
        return "[]" + composite_type_key(value_type.base_type)
    if isinstance(value_type, ArrayType):
        return f"[{value_type.size}]" + composite_type_key(value_type.base_type)
    if isinstance(value_type, (StructType, EnumType)):
        return value_type.name
    return getattr(value_type, "name", type(value_type).__name__)


_SYMBOL_CHAR_MAP = {"<": "_L", ">": "_G", ",": "_C", "[": "_A", "]": "_E", " ": ""}


def lifecycle_symbol(prefix: str, value_type: Type) -> str:
    """Deterministic, symbol-safe name for a per-type lifecycle function."""
    out = []
    for ch in composite_type_key(value_type):
        out.append(ch if (ch.isalnum() or ch == "_") else _SYMBOL_CHAR_MAP.get(ch, "_"))
    return prefix + "".join(out)


# ---------------------------------------------------------------------------
# The handler table
# ---------------------------------------------------------------------------

_KINDS = ("dynamic_array", "fixed_array", "struct", "enum")

# kind -> {"destroy": emitter, "clone": emitter}. destroy is
# (codegen, value_ptr, value_type) -> None; clone is
# (codegen, value, value_type) -> ir.Value.
_HANDLERS: Dict[str, Dict[str, Callable]] = {}


def register_lifecycle(kind: str, *, destroy: Callable | None = None,
                       clone: Callable | None = None) -> None:
    """Register one half (or both) of a composite kind's lifecycle handler."""
    assert kind in _KINDS, f"unknown lifecycle kind: {kind}"
    entry = _HANDLERS.setdefault(kind, {})
    if destroy is not None:
        entry["destroy"] = destroy
    if clone is not None:
        entry["clone"] = clone


def kind_of(value_type: Type) -> str:
    """The handler-table kind for a composite type."""
    if isinstance(value_type, DynamicArrayType):
        return "dynamic_array"
    if isinstance(value_type, ArrayType):
        return "fixed_array"
    if isinstance(value_type, StructType):
        return "struct"
    if isinstance(value_type, EnumType):
        return "enum"
    raise AssertionError(f"not a composite lifecycle type: {value_type!r}")


def _handler(value_type: Type, half: str) -> Callable:
    kind = kind_of(value_type)
    entry = _HANDLERS.get(kind, {})
    if half not in entry:
        raise KeyError(
            f"no {half} emitter registered for lifecycle kind '{kind}' "
            f"(type {value_type!r}); the clone and destroy halves must be "
            "registered together -- a one-sided handler is a double free or a "
            "leak by construction"
        )
    return entry[half]


def inline_destroy(codegen: 'LLVMCodegen', value_ptr: ir.Value, value_type: Type) -> None:
    """Dispatch a composite type's inline destructor through the handler table."""
    _handler(value_type, "destroy")(codegen, value_ptr, value_type)


def inline_clone(codegen: 'LLVMCodegen', value: ir.Value, value_type: Type) -> ir.Value:
    """Dispatch a composite type's inline deep clone through the handler table."""
    return _handler(value_type, "clone")(codegen, value, value_type)


def registered_halves() -> Dict[str, tuple]:
    """kind -> sorted halves present. For the totality test."""
    return {kind: tuple(sorted(entry)) for kind, entry in _HANDLERS.items()}


# ---------------------------------------------------------------------------
# The one out-of-line emitter (for self-referential types)
# ---------------------------------------------------------------------------

def get_or_emit_lifecycle_func(codegen: 'LLVMCodegen', value_type: Type,
                               half: str) -> ir.Function:
    """Get (or lazily emit) the out-of-line lifecycle function for a recursive type.

    half == "destroy": `void __sushi_dtor_<mangled>(i8* value_ptr)`.
    half == "clone":   `<T> __sushi_clone_<mangled>(<T> value)`.

    The function is inserted into its cache BEFORE the body is emitted, so a
    self-referential position inside the body resolves to a call to this same
    function (terminating the emission). The body is built with a fresh in-progress
    stack seeded with this type's key, so the recursion point becomes a self-call
    while unrelated nested types still inline.

    The body is emitted lazily, mid-emission of another function, and the emitters it calls
    reach for the ambient state. So BOTH `codegen.builder` and `codegen.func` are swapped
    and restored -- swapping only the builder puts the element loop in the caller (#257).
    """
    if half == "destroy":
        caches = codegen._dtor_funcs
        symbol = lifecycle_symbol("__sushi_dtor_", value_type)
    elif half == "clone":
        caches = getattr(codegen, "_clone_funcs", None)
        if caches is None:
            caches = {}
            codegen._clone_funcs = caches
        symbol = lifecycle_symbol("__sushi_clone_", value_type)
    else:
        raise AssertionError(f"unknown lifecycle half: {half}")

    key = composite_type_key(value_type)
    if key in caches:
        return caches[key]

    if half == "destroy":
        i8_ptr_ty = ir.PointerType(ir.IntType(INT8_BIT_WIDTH))
        fn_ty = ir.FunctionType(ir.VoidType(), [i8_ptr_ty])
    else:
        lltype = codegen.types.ll_type(value_type)
        fn_ty = ir.FunctionType(lltype, [lltype])

    fn = ir.Function(codegen.module, fn_ty, name=symbol)
    fn.linkage = "linkonce_odr"
    caches[key] = fn

    entry = fn.append_basic_block(name="entry")
    fb = ir.IRBuilder(entry)

    saved_builder, saved_func = codegen.builder, codegen.func
    if half == "destroy":
        saved_stack = codegen._dtor_inprogress
        codegen._dtor_inprogress = [key]
    else:
        saved_stack = getattr(codegen, "_clone_inprogress", None)
        codegen._clone_inprogress = [key]
    codegen.builder, codegen.func = fb, fn
    try:
        if half == "destroy":
            typed_ptr = fb.bitcast(
                fn.args[0],
                ir.PointerType(codegen.types.ll_type(value_type)),
                name="self_ptr",
            )
            inline_destroy(codegen, typed_ptr, value_type)
        else:
            result = inline_clone(codegen, fn.args[0], value_type)
            codegen.builder.ret(result)
    finally:
        codegen.builder, codegen.func = saved_builder, saved_func
        if half == "destroy":
            codegen._dtor_inprogress = saved_stack
        else:
            codegen._clone_inprogress = saved_stack if saved_stack is not None else []
    if half == "destroy":
        fb.ret_void()
    return fn
