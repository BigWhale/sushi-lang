"""Name (variable reference) emission for the Sushi language compiler."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import Name
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def resolve_name_slot(codegen: 'LLVMCodegen', name: str) -> Optional[ir.Value]:
    """Return the address of `name`: its local alloca, else its global constant."""
    slot = codegen.memory.try_find_local_slot(name)
    if slot is not None:
        return slot
    return codegen.constants.lookup(name, codegen.emitting_unit, codegen.scope)


def resolve_name_semantic_type(codegen: 'LLVMCodegen', name: str) -> Optional['Type']:
    """Return the Sushi type of `name`, whether it is a local or a constant."""
    semantic_ty = codegen.memory.find_semantic_type(name)
    if semantic_ty is not None:
        return semantic_ty
    semantic_ty = codegen.variable_types.get(name)
    if semantic_ty is not None:
        return semantic_ty
    const_sig = codegen.const_table.lookup(name, codegen.emitting_unit, codegen.scope)
    return const_sig.const_type if const_sig is not None else None


def namespaced_storage(codegen: 'LLVMCodegen', expr) -> Optional[tuple]:
    """`(name, global, type)` when `expr` is `alias.name` reaching a UNIT VARIABLE, else None.

    The alias fold keeps a constant as a `MemberAccess` with a `namespace_ref`; a
    constant behind it is read by value, and a variable behind it is STORAGE the writers
    reach -- a rebind, a `poke`, a field write, a mutating method (unit-storage.md).
    """
    ref = getattr(expr, "namespace_ref", None)
    if ref is None or ref.kind != "constant":
        return None
    sig = codegen.const_table.lookup(ref.name, ref.origin)
    if sig is None or not sig.is_var:
        return None
    slot = codegen.constants.lookup(ref.name, ref.origin)
    if slot is None:
        raise_internal_error("CE0055", name=ref.name)
    return ref.name, slot, sig.const_type


def emit_stdlib_constant(codegen: 'LLVMCodegen', record, to_i1: bool) -> ir.Value:
    """A registry constant as the immediate its record carries. The ONE emitter."""
    value = ir.Constant(codegen.types.ll_type(record.get_return_type()), record.value)
    return codegen.utils.as_i1(value) if to_i1 else value


def emit_name(codegen: 'LLVMCodegen', expr: Name, to_i1: bool) -> ir.Value:
    """Emit variable or constant reference: section 8's ladder, as the front end walked it."""
    slot = resolve_name_slot(codegen, expr.id)
    if slot is not None:
        # load_with_reference_handling dereferences a peek/poke parameter, whose slot
        # holds a pointer to the borrowed variable rather than the value itself. A
        # constant is never a reference parameter, so it takes the plain-load path.
        from sushi_lang.backend.expressions import type_utils
        v = type_utils.load_with_reference_handling(codegen, expr.id, slot)
        return codegen.utils.as_i1(v) if to_i1 else v

    # Below every local and every declared constant, and only in a unit whose scope
    # holds the module: the rung the typecheck pass typed the name at (#560).
    from sushi_lang.semantics.stdlib_registry import lookup_stdlib_constant
    stdlib_const = lookup_stdlib_constant(expr.id, codegen.scope)
    if stdlib_const is not None:
        return emit_stdlib_constant(codegen, stdlib_const, to_i1)

    # Neither a local nor a constant: a bare reference to a top-level function is a
    # first-class function value -> a non-capturing fat pointer {thunk, null, null}.
    # The thunk bridges the bare fn into the uniform env-passing indirect ABI.
    llvm_fn = codegen.funcs.lookup(expr.id, codegen.emitting_unit, codegen.scope)
    if llvm_fn is not None:
        from sushi_lang.backend.runtime import closures
        return closures.materialize_function_ref(codegen, llvm_fn)
    raise_internal_error("CE0055", name=expr.id)



def emit_namespaced_value(codegen: 'LLVMCodegen', ref, to_i1: bool) -> ir.Value:
    """Read `<namespace>.<name>` as a value. Under this epic that is a constant.

    The unit or module the typecheck pass resolved answers, never the emitting one:
    two units may each declare `SCRATCH`, and the alias says which was meant.
    """
    origin, name = ref.origin, ref.name
    if ref.producer == "stdlib":
        from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
        module = get_stdlib_registry().get_module(origin)
        record = module.constants.get(name) if module is not None else None
        if record is None:
            raise_internal_error("CE0055", name=f"{origin}.{name}")
        return emit_stdlib_constant(codegen, record, to_i1)

    slot = codegen.constants.lookup(name, origin)
    if slot is None:
        raise_internal_error("CE0055", name=f"{origin}.{name}")
    value = codegen.builder.load(slot, name=name)
    return codegen.utils.as_i1(value) if to_i1 else value
