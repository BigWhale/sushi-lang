"""The hash of ONE held value, by its semantic type.

A struct field, an enum payload, an array element, a `List@(T)` element and an
`Own@(T)` payload all ask this one function, so a type hashes the same in every
position (#871). The order is the method-resolution order: a `Hashable`
implementation is the sanctioned override and wins, then the derived method, then
the primitive built-ins (a miss in the per-compilation table falls through to them).
An overridden type is terminal: its fields are not read.

A hash emitter takes the call node of a written `.hash()` call, and reads it for
nothing but the argument count. A held value has no call, so it gets None, and no
emitter reads a node that was never written (#855).
"""

from typing import Any, Optional

import llvmlite.ir as ir

from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType, Type


def reject_hash_arguments(call: Optional[Any]) -> None:
    """The backstop for the argument count of a hash emitter (CE0054)."""
    if call is not None and call.args:
        raise_internal_error("CE0054", got=len(call.args))


def hash_override(codegen: Any, semantic_type: Type) -> Optional[ir.Function]:
    """The function of the type's `Hashable` implementation, or None when it has none."""
    if codegen.perk_impl_table.get_method(semantic_type, "hash") is None:
        return None

    from sushi_lang.semantics.library_templates import impl_method_symbol
    llvm_fn = codegen.funcs.get(impl_method_symbol(str(semantic_type), "hash"))
    if llvm_fn is None:
        raise_internal_error("CE0024", method="hash", type=str(semantic_type))
    return llvm_fn


def emit_value_hash(codegen: Any, value: ir.Value, semantic_type: Type) -> ir.Value:
    """The u64 hash of `value`, a loaded value of `semantic_type`."""
    builder = require_builder(codegen)

    override = hash_override(codegen, semantic_type)
    if override is not None:
        param_type = list(override.args)[0].type
        if isinstance(param_type, ir.PointerType) and param_type.pointee == value.type:
            slot = entry_alloca(builder, value.type, name="hash_self")
            builder.store(value, slot)
            return builder.call(override, [slot])
        return builder.call(override, [codegen.utils.cast_for_param(value, param_type)])

    hash_method = _derived_hash(codegen, semantic_type)
    return hash_method.llvm_emitter(codegen, None, value, value.type, False)


def _derived_hash(codegen: Any, semantic_type: Type) -> Any:
    """The derived (or primitive built-in) hash method, registered on demand.

    The accepted set is the derive pass's (`hashability_of`). An array that only a
    container holds has no registration of its own yet, so it is registered here.
    """
    import sushi_lang.backend.types.primitives.hashing  # noqa: F401

    hash_method = codegen.derived_methods.get_method(semantic_type, "hash")
    if hash_method is not None:
        return hash_method

    from sushi_lang.semantics.generics.hashing import hashability_of, register_hash_if_hashable
    can_hash, _ = hashability_of(semantic_type)
    if not can_hash:
        raise_internal_error("CE0052", type=str(semantic_type))

    if not isinstance(semantic_type, BuiltinType):
        register_hash_if_hashable(semantic_type, codegen.derived_methods)

    hash_method = codegen.derived_methods.get_method(semantic_type, "hash")
    if hash_method is None:
        raise_internal_error("CE0051", type=str(semantic_type))
    return hash_method
