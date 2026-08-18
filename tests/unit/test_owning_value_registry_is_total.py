"""`register_owning_value` must answer for every kind of owner a local can hold.

There are two registration entry points and they are easy to mistake for each other.
`register_local_cleanup` knows the kinds whose storage IS the alloca -- a struct with
owning fields, a fixed array of owning elements, a closure, a string. `register_owning_value`
is the complete router: it adds the three kinds that keep their heap behind a descriptor,
a dynamic array, a `List@(T)` and an `Own@(T)`, each of which has its own registry.

#382 was a site that reached for the narrower one. A dynamic-array, `List@(T)` or `Own@(T)`
temporary parked in a slot was registered NOWHERE, so nothing freed it -- silently, because
a leak needs the gate to be looking. `HashMap@(K, V)` was unaffected, which is exactly what
made the defect look narrower than it was.

This gate asks the question directly: for every owner kind, does registering a slot put it
in a registry that scope exit reads?
"""
from __future__ import annotations

import pytest
from llvmlite import ir

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, FunctionType, StructType, UnknownType,
)


@pytest.fixture
def codegen():
    """A codegen positioned inside a function, so locals can be created."""
    from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager

    cg = LLVMCodegen()
    fn = ir.Function(cg.module, ir.FunctionType(ir.VoidType(), []), name="_registry_probe")
    block = fn.append_basic_block("entry")
    cg.builder = ir.IRBuilder(block)
    cg.entry_block = block
    cg.alloca_builder = ir.IRBuilder(block)
    cg.func = fn
    cg.dynamic_arrays = DynamicArrayManager(cg.builder, cg)
    return cg


def _owning_struct() -> StructType:
    return StructType(name="Holder",
                      fields=(("items", DynamicArrayType(base_type=BuiltinType.I32)),))


def _list_type() -> StructType:
    return StructType(name="List<i32>", fields=())


def _own_type() -> StructType:
    return StructType(name="Own<i32>", fields=())


def _registered_names(codegen: LLVMCodegen) -> set[str]:
    """Every name any scope-exit registry would free."""
    arrays = codegen.dynamic_arrays
    memory = codegen.memory
    return (set(arrays.arrays)
            | set(arrays.lists)
            | set(arrays.owned_pointers)
            | set(memory._struct_cleanup)
            | set(memory._string_cleanup)
            | set(memory._closure_cleanup))


# Every kind of owner a local slot can hold, with the semantic type that describes it.
OWNER_KINDS = {
    "dynamic_array": DynamicArrayType(base_type=BuiltinType.I32),
    "list": _list_type(),
    "own": _own_type(),
    "owning_struct": _owning_struct(),
    "string": BuiltinType.STRING,
    "fixed_array_of_strings": ArrayType(base_type=BuiltinType.STRING, size=2),
    "closure": FunctionType(param_types=(BuiltinType.I32,), ok_type=BuiltinType.I32,
                            err_type=UnknownType("StdError")),
}


@pytest.mark.parametrize("kind", sorted(OWNER_KINDS))
def test_every_owner_kind_reaches_a_registry(codegen, kind):
    semantic_type = OWNER_KINDS[kind]
    codegen.struct_table.by_name.setdefault("Holder", _owning_struct())
    codegen.struct_table.by_name.setdefault("List<i32>", _list_type())
    codegen.struct_table.by_name.setdefault("Own<i32>", _own_type())

    name = f"__probe_{kind}"
    slot = codegen.memory.entry_alloca(ir.IntType(64), name)
    codegen.memory.register_owning_value(name, semantic_type, slot)

    assert name in _registered_names(codegen), (
        f"{kind} ({semantic_type}) was registered in no cleanup registry, so nothing "
        f"would free it at scope exit"
    )


@pytest.mark.parametrize("kind", ["dynamic_array", "list", "own"])
def test_the_narrow_router_does_not_cover_the_descriptor_kinds(codegen, kind):
    """Why the two entry points are not interchangeable -- the #382 trap, pinned.

    If this ever starts passing, `register_local_cleanup` has grown these kinds and the
    two routers have converged; merge them rather than leaving a second way to be wrong.
    """
    semantic_type = OWNER_KINDS[kind]
    codegen.struct_table.by_name.setdefault("List<i32>", _list_type())
    codegen.struct_table.by_name.setdefault("Own<i32>", _own_type())

    name = f"__narrow_{kind}"
    slot = codegen.memory.entry_alloca(ir.IntType(64), name)
    codegen.memory.register_local_cleanup(name, semantic_type, slot)

    assert name not in _registered_names(codegen)
