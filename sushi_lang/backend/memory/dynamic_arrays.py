"""RAII-style dynamic array and Own<T> memory management."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.typesys import DynamicArrayType, Type, StructType
from sushi_lang.backend.constants import INT32_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import ZERO_I32, make_i32_const
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


@dataclass
class DynamicArrayDescriptor:
    """Runtime descriptor for a dynamic array instance."""
    name: str                    # Variable name
    element_type: Type           # Element type (int, bool, string)
    llvm_alloca: ir.Instruction  # LLVM alloca for the struct
    destroyed: bool = False      # Track if explicitly destroyed


@dataclass
class OwnDescriptor:
    """Runtime descriptor for an Own<T> instance."""
    name: str                    # Variable name
    own_type: StructType         # Own<T> struct type
    slot: ir.Instruction         # Alloca holding the Own<T> struct (move key + destructor target)
    depth: int = -1              # Scope depth at registration (shadow disambiguation)
    destroyed: bool = False      # Track if explicitly destroyed via .destroy()


@dataclass
class ListDescriptor:
    """Runtime descriptor for a local List<T> instance."""
    name: str                    # Variable name
    list_type: StructType        # List<T> struct type
    llvm_alloca: ir.Instruction  # LLVM alloca for the List<T> struct
    destroyed: bool = False      # Explicitly .destroy()/.free()'d


class DynamicArrayManager:
    """RAII-style memory manager for dynamic arrays and Own<T>."""

    def __init__(self, builder: ir.IRBuilder, codegen: 'LLVMCodegen') -> None:
        """Initialize the dynamic array manager."""
        self.builder = builder
        self.codegen = codegen
        self.scope_stack: List[Set[str]] = []
        # A per-name STACK of descriptors, innermost last, so a nested shadow does not
        # overwrite the outer one. A flat dict lost the outer descriptor and the outer pop
        # then double-freed the inner array.
        self.arrays: Dict[str, List[DynamicArrayDescriptor]] = {}
        # Track Own<T> variables for RAII cleanup. Flat by name: Own cleanup is driven by a
        # wholesale iteration (emit_own_cleanup) at function/scope exit, not scope-popped like
        # arrays, so a per-name stack would never drain. The descriptor carries its slot for
        # slot-keyed move checks.
        self.owned_pointers: Dict[str, OwnDescriptor] = {}
        self.lists: Dict[str, List[ListDescriptor]] = {}
        self.list_scope_stack: List[Set[str]] = []

    def _array(self, name: str) -> Optional[DynamicArrayDescriptor]:
        """Innermost live dynamic-array descriptor for `name`, or None."""
        stack = self.arrays.get(name)
        return stack[-1] if stack else None

    def _list(self, name: str) -> Optional[ListDescriptor]:
        """Innermost live List<T> descriptor for `name`, or None."""
        stack = self.lists.get(name)
        return stack[-1] if stack else None

    def push_scope(self) -> None:
        """Enter a new scope for dynamic array and List<T> tracking."""
        self.scope_stack.append(set())
        self.list_scope_stack.append(set())

    def pop_scope(self) -> None:
        """Exit current scope and automatically destroy all dynamic arrays declared in this scope
        (if not already destroyed or moved).
        """
        if not self.scope_stack:
            from sushi_lang.internals.errors import raise_internal_error
            raise_internal_error("CE0016")

        current_scope = self.scope_stack.pop()
        current_lists = self.list_scope_stack.pop() if self.list_scope_stack else set()

        # Popping the stacks IS the drain, and it restores any outer namesake. If the block
        # already terminated, an early exit emitted the destructors on that path, so skip
        # EMISSION but still drain, or shadowing goes inconsistent (#59).
        block = self.builder.block
        emit = not (block is not None and block.is_terminated)

        # Generate destructor calls for all arrays / lists in this scope on the fall-through
        # path, then pop each binding's top descriptor. The destructors are no-ops for moved /
        # explicitly-destroyed values. Do NOT set `destroyed` here: it denotes an explicit
        # .destroy(), a cross-path state, and each runtime exit path frees on its own block.
        for array_name in current_scope:
            if emit and array_name in self.arrays:
                self._emit_array_destructor(array_name)
            self._pop_descriptor(self.arrays, array_name)
        for list_name in current_lists:
            if emit:
                self._emit_list_destructor(list_name)
            self._pop_descriptor(self.lists, list_name)

    @staticmethod
    def _pop_descriptor(reg: Dict[str, List], name: str) -> None:
        """Pop `name`'s innermost descriptor from a stacked registry (no-op if absent)."""
        stack = reg.get(name)
        if stack:
            stack.pop()
            if not stack:
                del reg[name]

    def declare_dynamic_array(self, name: str, array_type: DynamicArrayType) -> ir.Instruction:
        """Declare a new dynamic array variable and allocate its struct on stack."""
        from sushi_lang.semantics.typesys import UnknownType
        element_type = array_type.base_type
        if isinstance(element_type, UnknownType):
            if element_type.name in self.codegen.struct_table.by_name:
                element_type = self.codegen.struct_table.by_name[element_type.name]
            elif element_type.name in self.codegen.enum_table.by_name:
                element_type = self.codegen.enum_table.by_name[element_type.name]
            else:
                from sushi_lang.internals.errors import raise_internal_error
                raise_internal_error("CE0020", type=element_type.name)

        element_llvm_type = self._get_llvm_type_for_element(element_type)
        struct_type = ir.LiteralStructType([
            ir.IntType(INT32_BIT_WIDTH),                 # len
            ir.IntType(INT32_BIT_WIDTH),                 # cap
            ir.PointerType(element_llvm_type)            # data*
        ])

        alloca = self.builder.alloca(struct_type, name=f"{name}_struct")

        null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)

        len_ptr = self.codegen.types.get_dynamic_array_len_ptr(self.builder, alloca)
        cap_ptr = self.codegen.types.get_dynamic_array_cap_ptr(self.builder, alloca)
        data_ptr = self.codegen.types.get_dynamic_array_data_ptr(self.builder, alloca)

        self.builder.store(ZERO_I32, len_ptr)
        self.builder.store(ZERO_I32, cap_ptr)
        self.builder.store(null_ptr, data_ptr)

        descriptor = DynamicArrayDescriptor(
            name=name,
            element_type=element_type,  # Use resolved type
            llvm_alloca=alloca
        )
        self.arrays.setdefault(name, []).append(descriptor)
        self.codegen.moves.arm_if_conditional(name, alloca)

        if self.scope_stack:
            self.scope_stack[-1].add(name)

        return alloca

    def register_param_array(self, name: str, element_type: Type, slot: ir.Instruction) -> None:
        """Register an incoming dynamic-array parameter for RAII cleanup."""
        from sushi_lang.semantics.typesys import UnknownType
        if isinstance(element_type, UnknownType):
            if element_type.name in self.codegen.struct_table.by_name:
                element_type = self.codegen.struct_table.by_name[element_type.name]
            elif element_type.name in self.codegen.enum_table.by_name:
                element_type = self.codegen.enum_table.by_name[element_type.name]
            else:
                from sushi_lang.internals.errors import raise_internal_error
                raise_internal_error("CE0020", type=element_type.name)

        descriptor = DynamicArrayDescriptor(
            name=name,
            element_type=element_type,
            llvm_alloca=slot,
        )
        self.arrays.setdefault(name, []).append(descriptor)
        self.codegen.moves.arm_if_conditional(name, slot)
        if self.scope_stack:
            self.scope_stack[-1].add(name)

    def emit_array_constructor_new(self, name: str) -> None:
        """Emit code for new() constructor - array is already initialized to empty."""
        pass  # new() constructor creates empty array - already done in declare

    def emit_array_constructor_from(self, name: str, elements) -> None:
        """Emit code for from(array_literal) constructor, over emitted runs."""
        from sushi_lang.backend.types.arrays import runs

        descriptor = self._array(name)
        if descriptor is None:
            raise_internal_error("CE0057", name=name)
        if descriptor.destroyed:
            raise_internal_error("CE0058", name=name)

        element_llvm_type = self._get_llvm_type_for_element(descriptor.element_type)
        initial_len = runs.readable_total(elements)

        if initial_len is None:
            # A run-time length (#478). The capacity equals the length, and a run-time zero
            # is DATA rather than an error (Ruling 2), so there is no short circuit.
            from sushi_lang.backend.types.arrays.utils import emit_dynamic_array_of_length

            length = runs.emit_total_length(self.codegen, elements)
            _, typed_data_ptr = emit_dynamic_array_of_length(
                self.codegen, element_llvm_type, length)
            runs.fill_runs(self.codegen, typed_data_ptr, elements, element_llvm_type)
            self._update_array_fields_dynamic(name, length, length, typed_data_ptr)
            return

        if initial_len == 0:
            return  # Empty array, already initialized

        initial_capacity = self._next_power_of_2(initial_len)

        element_size = self._get_element_size_bytes(descriptor.element_type)
        capacity_val = make_i32_const(initial_capacity)
        total_bytes = self.builder.mul(capacity_val, element_size, name="total_bytes")

        data_ptr = emit_malloc(self.codegen, self.builder, total_bytes)

        typed_data_ptr = self.builder.bitcast(data_ptr, ir.PointerType(element_llvm_type), name="typed_data_ptr")

        runs.fill_runs(self.codegen, typed_data_ptr, elements, element_llvm_type)

        self._update_array_fields(name, initial_len, initial_capacity, typed_data_ptr)

    def emit_destroy_call(self, name: str) -> None:
        """Emit explicit .destroy() method call."""
        self._emit_array_destructor(name)
        descriptor = self._array(name)
        if descriptor is not None:
            descriptor.destroyed = True

    def mark_as_moved(self, name: str) -> None:
        """Mark a dynamic array as moved (ownership transferred)."""
        descriptor = self._array(name)
        if descriptor is not None:
            self.codegen.moves.mark(descriptor.llvm_alloca)

    def is_list_type(self, ty: Type) -> bool:
        """Check if a type is List<T>."""
        return isinstance(ty, StructType) and ty.name.startswith("List<")

    def register_list(self, var_name: str, list_type: StructType, slot: ir.Instruction) -> None:
        """Register a local List<T> variable for automatic RAII cleanup (#61)."""
        self.lists.setdefault(var_name, []).append(
            ListDescriptor(name=var_name, list_type=list_type, llvm_alloca=slot))
        self.codegen.moves.arm_if_conditional(var_name, slot)
        if self.list_scope_stack:
            self.list_scope_stack[-1].add(var_name)

    def mark_list_destroyed(self, var_name: str) -> None:
        """Mark a List<T> as explicitly destroyed/freed; skip redundant RAII cleanup."""
        descriptor = self._list(var_name)
        if descriptor is not None:
            descriptor.destroyed = True

    def _emit_list_destructor(self, name: str) -> None:
        """Emit destructor code for a local List<T> (no-op if moved / already destroyed)."""
        descriptor = self._list(name)
        if descriptor is None:
            return
        if descriptor.destroyed:
            return
        from sushi_lang.backend.generics.list.methods_destroy import emit_list_destroy
        self.codegen.moves.emit_free_unless_moved(
            descriptor.llvm_alloca,
            lambda: emit_list_destroy(self.codegen, descriptor.llvm_alloca, descriptor.list_type))

    def is_own_type(self, ty: Type) -> bool:
        """Check if a type is Own<T>."""
        if isinstance(ty, StructType):
            return ty.name.startswith("Own<")
        return False

    def register_own(self, var_name: str, own_type: StructType, slot: ir.Instruction) -> None:
        """Register Own<T> variable for automatic RAII cleanup."""
        depth = self.codegen.memory._scope_depth
        self.owned_pointers[var_name] = OwnDescriptor(
            name=var_name, own_type=own_type, slot=slot, depth=depth, destroyed=False)
        self.codegen.moves.arm_if_conditional(var_name, slot)

    def is_destroyed(self, var_name: str) -> bool:
        """Has `var_name` already been released by an explicit `.destroy()` / `.free()`?"""
        array = self._array(var_name)
        if array is not None and array.destroyed:
            return True
        lst = self._list(var_name)
        if lst is not None and lst.destroyed:
            return True
        own = self.owned_pointers.get(var_name)
        return own is not None and own.destroyed

    def reset_destroyed_on_rebind(self, var_name: str) -> None:
        """Clear the destroyed flag after a rebind gives `var_name` a NEW value."""
        array = self._array(var_name)
        if array is not None:
            array.destroyed = False
        lst = self._list(var_name)
        if lst is not None:
            lst.destroyed = False
        own = self.owned_pointers.get(var_name)
        if own is not None:
            own.destroyed = False

    def mark_own_destroyed(self, var_name: str) -> None:
        """Mark an Own<T> variable as explicitly destroyed."""
        if var_name in self.owned_pointers:
            self.owned_pointers[var_name].destroyed = True

    def emit_own_cleanup(self) -> None:
        """Emit cleanup code for all Own<T> variables in current scope."""
        for var_name, descriptor in self.owned_pointers.items():
            if not descriptor.destroyed and not self.codegen.moves.is_moved(descriptor.slot):
                self._emit_own_destructor(var_name, descriptor.own_type)

    def _emit_own_destructor(self, var_name: str, own_type: StructType) -> None:
        """Emit destructor code for a single Own<T> variable."""
        from sushi_lang.backend.destructors import emit_value_destructor

        own_slot = self.codegen.memory.find_local_slot(var_name)
        descriptor = self.owned_pointers.get(var_name)
        gate_slot = descriptor.slot if descriptor is not None else own_slot
        self.codegen.moves.emit_free_unless_moved(
            gate_slot,
            lambda: emit_value_destructor(self.codegen, own_slot, own_type))

    def _emit_array_destructor(self, name: str) -> None:
        """Generate destructor code for a dynamic array."""
        descriptor = self._array(name)
        if descriptor is None:
            return
        if descriptor.destroyed:
            return

        from sushi_lang.backend.destructors import emit_value_destructor
        self.codegen.moves.emit_free_unless_moved(
            descriptor.llvm_alloca,
            lambda: emit_value_destructor(self.codegen, descriptor.llvm_alloca,
                                          DynamicArrayType(descriptor.element_type)))

    def _update_array_fields(self, name: str, length: int, capacity: int, data_ptr: ir.Value) -> None:
        """Update the len, cap, and data fields of a dynamic array struct."""
        self._update_array_fields_dynamic(name, make_i32_const(length),
                                          make_i32_const(capacity), data_ptr)

    def _update_array_fields_dynamic(self, name: str, length: ir.Value, capacity: ir.Value,
                                     data_ptr: ir.Value) -> None:
        """The same, for a length only known at run time (#478)."""
        descriptor = self._array(name)
        if descriptor is None:
            raise_internal_error("CE0057", name=name)

        len_ptr = self.codegen.types.get_dynamic_array_len_ptr(self.builder, descriptor.llvm_alloca)
        cap_ptr = self.codegen.types.get_dynamic_array_cap_ptr(self.builder, descriptor.llvm_alloca)
        data_ptr_ptr = self.codegen.types.get_dynamic_array_data_ptr(self.builder, descriptor.llvm_alloca)

        self.builder.store(length, len_ptr)
        self.builder.store(capacity, cap_ptr)
        self.builder.store(data_ptr, data_ptr_ptr)

    def _get_llvm_type_for_element(self, element_type: Type) -> ir.Type:
        """Convert Sushi element type to LLVM type."""
        return self.codegen.types.ll_type(element_type)

    def _get_element_size_bytes(self, element_type: Type) -> ir.Value:
        """Get the per-element allocation stride in bytes as an LLVM i32 constant."""
        from sushi_lang.backend.expressions import memory
        element_llvm_type = self._get_llvm_type_for_element(element_type)
        return memory.get_element_size_constant(self.codegen, element_llvm_type)

    def _next_power_of_2(self, n: int) -> int:
        """Return the next power of 2 >= n. Used for capacity growth."""
        if n <= 1:
            return 1
        return 1 << (n - 1).bit_length()

    def struct_needs_cleanup(self, struct_type: StructType) -> bool:
        """Check if a struct (or enum) type owns heap that needs scope-exit cleanup."""
        from sushi_lang.semantics.typesys import EnumType
        if isinstance(struct_type, EnumType):
            return self._enum_needs_cleanup(struct_type)

        for _field_name, field_type in struct_type.fields:
            if self._payload_needs_cleanup(field_type):
                return True
        return False

    def _payload_needs_cleanup(self, ty: Type) -> bool:
        """Whether a single field / variant-payload type owns heap needing cleanup."""
        from sushi_lang.semantics.typesys import (
            ArrayType, FunctionType, BuiltinType, StructType, EnumType)
        from sushi_lang.backend.destructors import resolve_named_type

        # Resolve a named type -- UnknownType('Box') or GenericTypeRef('List', (i32,)), the
        # latter being how the Ok payload of Result<List<i32>, E> arrives -- to its concrete
        # struct/enum definition. An unresolved reference matches no branch below and is
        # silently reported as owning nothing (#183).
        ty = resolve_named_type(self.codegen, ty)

        if isinstance(ty, DynamicArrayType):
            return True
        # A fixed array `T[N]` holds its elements inline and owns no buffer of its own, but the
        # ELEMENTS can own heap -- a `string[3]` field makes its struct heap-owning (#185). Must
        # agree with destructors.needs_cleanup: this predicate gates REGISTRATION and that one
        # gates RECURSION, and the two disagreeing is exactly what #162/#183 were.
        if isinstance(ty, ArrayType):
            return self._payload_needs_cleanup(ty.base_type)
        if isinstance(ty, FunctionType):
            return True
        # A `string` owns a heap buffer when its runtime `owned` bit is set (#147);
        # scope-exit RAII frees it (guarded on the bit, so a literal/borrow is a no-op).
        if ty == BuiltinType.STRING:
            return True
        if isinstance(ty, StructType):
            # The containers always own heap; other structs are checked field-by-field. The
            # prefix check short-circuits a self-referential `Own<Tree>` without recursing.
            # Keyed on the shared CONTAINER_PREFIXES, so the set is spelled once (#181).
            from sushi_lang.semantics.generics.cloning import CONTAINER_PREFIXES
            if ty.name.startswith(CONTAINER_PREFIXES):
                return True
            return self.struct_needs_cleanup(ty)
        if isinstance(ty, EnumType):
            return self._enum_needs_cleanup(ty)
        return False

    def _enum_needs_cleanup(self, enum_type: 'StructType') -> bool:
        """Whether any variant of an enum carries a heap-owning payload."""
        for variant in enum_type.variants:
            for assoc_type in variant.associated_types:
                if self._payload_needs_cleanup(assoc_type):
                    return True
        return False

    def emit_struct_field_cleanup(self, var_name: str, struct_type: StructType, struct_alloca: ir.Value) -> None:
        """Emit scope-exit cleanup for a struct local's owning fields."""
        from sushi_lang.backend.destructors import emit_value_destructor
        emit_value_destructor(self.codegen, struct_alloca, struct_type)
