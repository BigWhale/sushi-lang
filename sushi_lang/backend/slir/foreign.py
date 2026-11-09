"""A C value as a Sushi value: the answer of a C function, the read of a C global, and the
methods of a `ptr` (docs/design/ir.md 8.4, 8.6).

A null is a state of the C boundary and never a Sushi value. A nullable pointer is a
`Maybe`: a null pointer is `None`. A plain `ptr` or `string` that is null ends the program
with RE2025. A `char*` becomes an owned copy. A C global is declared under its link name
in each module (`codegen.external_vars`), and a read loads it at the moment of the read.

A method of a `ptr` is a `gep` of the byte offset on the `i8*`, then the access, each one
`align 1`: a byte offset says nothing about the alignment. `offset` answers the address,
`to_string` an owned copy of the C string there, and `load_ptr` a `Maybe@(ptr)`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from llvmlite import ir

from sushi_lang.backend.generics.container_table import blank
from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.semantics.ffi_boundary import is_pointer_value, nullable_payload
from sushi_lang.semantics.foreign_memory import FOREIGN_PTR_WIDTHS
from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.externals import ExternalVarSig


def c_answer(state: FunctionState, raw: ir.Value, ty: Optional[Type]) -> ir.Value:
    """The Sushi value of a C value of the declared type `ty`."""
    payload = nullable_payload(ty)
    if payload is not None:
        return _nullable_answer(state, raw, payload)
    if is_pointer_value(ty):
        _end_on_null(state, raw)
        return _pointer_value(state, raw, ty)
    return raw


def extern_variable(state: FunctionState,
                    symbol: str) -> Optional[tuple[ir.Value, ExternalVarSig]]:
    """The declaration of the C global whose link name is `symbol`, or None."""
    for declared, var in state.codegen.external_vars.values():
        if var.link_name == symbol:
            return declared, var
    return None


def read_extern_variable(state: FunctionState, symbol: str) -> ir.Value:
    """A load of the C global `symbol`, as the Sushi value of its declared type."""
    found = extern_variable(state, symbol)
    if found is None:
        state.fault(f"the module declares no C global '{symbol}'")
    declared, var = found
    return c_answer(state, state.builder.load(declared), var.ty)


def emit_foreign_ptr_method(state: FunctionState, name: str, receiver: ir.Value,
                            args: list[ir.Value]) -> ir.Value:
    """The value of `p.<name>(args)`, from the value of each operand."""
    builder = state.builder
    if not args:
        state.fault(f"the call of 'ptr.{name}' has no offset")
    address = builder.gep(receiver, [args[0]])
    if name == "offset":
        return address
    if name == "to_string":
        return state.codegen.runtime.strings.emit_cstr_to_owned_fat_pointer(address)
    kind, _, suffix = name.partition("_")
    if suffix == "ptr":
        ll_type: ir.Type = ir.PointerType(ir.IntType(8))
    else:
        ll_type = state.codegen.types.ll_type(FOREIGN_PTR_WIDTHS[suffix])
    slot = builder.bitcast(address, ir.PointerType(ll_type))
    if kind == "store":
        if len(args) != 2 or args[1].type != ll_type:
            state.fault(f"the call of 'ptr.{name}' does not store a value of {ll_type}")
        builder.store(args[1], slot, align=1)
        return blank(state.codegen)
    loaded = builder.load(slot, align=1)
    if suffix == "ptr":
        return _nullable_answer(state, loaded, ForeignPtrType())
    return loaded


def _pointer_value(state: FunctionState, raw: ir.Value, ty: Optional[Type]) -> ir.Value:
    """A `char*` is an owned copy; a `ptr` is the pointer itself."""
    if ty == BuiltinType.STRING:
        return state.codegen.runtime.strings.emit_cstr_to_owned_fat_pointer(raw)
    return raw


def _end_on_null(state: FunctionState, raw: ir.Value) -> None:
    """RE2025: a declaration that says non-null got a null pointer."""
    builder = state.builder
    is_null = builder.icmp_unsigned("==", raw, ir.Constant(raw.type, None))
    trap_block = builder.append_basic_block("ffi_null_trap")
    ok_block = builder.append_basic_block("ffi_non_null")
    builder.cbranch(is_null, trap_block, ok_block)
    builder.position_at_end(trap_block)
    state.codegen.runtime.errors.emit_runtime_error("RE2025")
    builder.unreachable()
    builder.position_at_end(ok_block)


def _nullable_answer(state: FunctionState, raw: ir.Value, payload: Type) -> ir.Value:
    """A null pointer is `Maybe.None`; any other pointer is `Maybe.Some` of its value."""
    codegen = state.codegen
    builder = state.builder
    is_null = builder.icmp_unsigned("==", raw, ir.Constant(raw.type, None))
    none_block = builder.append_basic_block("ffi_ret_none")
    some_block = builder.append_basic_block("ffi_ret_some")
    merge_block = builder.append_basic_block("ffi_ret_merge")
    builder.cbranch(is_null, none_block, some_block)
    builder.position_at_end(none_block)
    none_value = emit_maybe_none(codegen, payload)
    none_end = builder.block
    builder.branch(merge_block)
    builder.position_at_end(some_block)
    some_value = emit_maybe_some(codegen, payload, _pointer_value(state, raw, payload))
    some_end = builder.block
    builder.branch(merge_block)
    builder.position_at_end(merge_block)
    result = builder.phi(none_value.type)
    result.add_incoming(none_value, none_end)
    result.add_incoming(some_value, some_end)
    return result
