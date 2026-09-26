"""RAII-style dynamic array and Own<T> memory management."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Optional, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import DynamicArrayType, Type, StructType
from sushi_lang.backend.constants import INT32_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import ZERO_I32, make_i32_const
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import gep_utils
from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


@dataclass
class DynamicArrayDescriptor:
    """Runtime descriptor for a dynamic array instance."""
    name: str                    # Variable name
    element_type: Type           # Element type (int, bool, string)
    llvm_alloca: ir.Instruction  # LLVM alloca for the struct
    depth: int = -1              # Scope depth at registration (shadow disambiguation)
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
    depth: int = -1              # Scope depth at registration (shadow disambiguation)
    destroyed: bool = False      # Explicitly .destroy()/.free()'d


class DynamicArrayManager:
    """RAII-style memory manager for dynamic arrays and Own<T>."""

    def __init__(self, builder: ir.IRBuilder, codegen: 'LLVMCodegen') -> None:
        """Initialize the dynamic array manager."""
        self.builder = builder
        self.codegen = codegen
        # A per-name STACK of descriptors, innermost last, each with the depth of its
        # scope, so a nested shadow does not overwrite the outer one. The ORDER of a
        # scope's names lives in `ScopeManager`, which walks every registry in one pass.
        self.arrays: Dict[str, List[DynamicArrayDescriptor]] = {}
        self.owned_pointers: Dict[str, List[OwnDescriptor]] = {}
        self.lists: Dict[str, List[ListDescriptor]] = {}

    def _array(self, name: str) -> Optional[DynamicArrayDescriptor]:
        """Innermost live dynamic-array descriptor for `name`, or None."""
        stack = self.arrays.get(name)
        return stack[-1] if stack else None

    def _list(self, name: str) -> Optional[ListDescriptor]:
        """Innermost live List<T> descriptor for `name`, or None."""
        stack = self.lists.get(name)
        return stack[-1] if stack else None

    def _own(self, name: str) -> Optional[OwnDescriptor]:
        """Innermost live Own<T> descriptor for `name`, or None."""
        stack = self.owned_pointers.get(name)
        return stack[-1] if stack else None

    @staticmethod
    def _at_depth(reg: Dict[str, List], name: str, depth: int):
        """The descriptor `name` registered at scope `depth` in a stacked registry, or None."""
        for descriptor in reversed(reg.get(name, ())):
            if descriptor.depth == depth:
                return descriptor
        return None

    def _enter(self, reg: Dict[str, List], name: str, descriptor) -> None:
        """Push a descriptor at the current depth; one at the same depth is replaced."""
        descriptor.depth = self.codegen.memory.depth
        stack = reg.setdefault(name, [])
        if stack and stack[-1].depth == descriptor.depth:
            stack[-1] = descriptor
        else:
            stack.append(descriptor)
        self.codegen.memory.declare_scope_name(name)

    def exit_actions(self, name: str, depth: int
                     ) -> Iterator[tuple[ir.Instruction, Callable[[], None]]]:
        """(slot, emit destructor) for `name`'s live array, List or Own at `depth`.

        The caller gates each on the move state. An explicit `.destroy()` sets
        `destroyed`, a state that no exit path changes, so such an entry has no action.
        """
        array = self._at_depth(self.arrays, name, depth)
        if array is not None and not array.destroyed:
            yield array.llvm_alloca, lambda: self._destroy_array(array)
        lst = self._at_depth(self.lists, name, depth)
        if lst is not None and not lst.destroyed:
            yield lst.llvm_alloca, lambda: self._destroy_list(lst)
        own = self._at_depth(self.owned_pointers, name, depth)
        if own is not None and not own.destroyed:
            yield own.slot, lambda: self._destroy_own(own)

    def drain(self, name: str, depth: int) -> None:
        """Pop `name`'s descriptors registered at `depth`; an outer namesake is live again."""
        for reg in (self.arrays, self.lists, self.owned_pointers):
            stack = reg.get(name)
            while stack and stack[-1].depth == depth:
                stack.pop()
            if stack is not None and not stack:
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

        alloca = entry_alloca(self.builder, struct_type, name=f"{name}_struct")

        null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)

        len_ptr = gep_utils.gep_dynamic_array_len(self.codegen, alloca, builder=self.builder)
        cap_ptr = gep_utils.gep_dynamic_array_cap(self.codegen, alloca, builder=self.builder)
        data_ptr = gep_utils.gep_dynamic_array_data(self.codegen, alloca, builder=self.builder)

        self.builder.store(ZERO_I32, len_ptr)
        self.builder.store(ZERO_I32, cap_ptr)
        self.builder.store(null_ptr, data_ptr)

        descriptor = DynamicArrayDescriptor(
            name=name,
            element_type=element_type,  # Use resolved type
            llvm_alloca=alloca
        )
        self._enter(self.arrays, name, descriptor)
        self.codegen.moves.arm_if_conditional(name, alloca)

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
        self._enter(self.arrays, name, descriptor)
        self.codegen.moves.arm_if_conditional(name, slot)

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

    def mark_as_moved(self, name: str) -> None:
        """Mark a dynamic array as moved (ownership transferred)."""
        descriptor = self._array(name)
        if descriptor is not None:
            self.codegen.moves.mark(descriptor.llvm_alloca)

    def is_list_type(self, ty: Type) -> bool:
        """Check if a type is List<T>."""
        return isinstance(ty, StructType) and is_instance_of(ty, "List")

    def register_list(self, var_name: str, list_type: StructType, slot: ir.Instruction) -> None:
        """Register a local List<T> variable for automatic RAII cleanup (#61)."""
        self._enter(self.lists, var_name,
                    ListDescriptor(name=var_name, list_type=list_type, llvm_alloca=slot))
        self.codegen.moves.arm_if_conditional(var_name, slot)

    def _destroy_list(self, descriptor: ListDescriptor) -> None:
        """Emit the destructor of a local List<T>, with no move gate."""
        from sushi_lang.backend.generics.list.methods_destroy import emit_list_destroy
        emit_list_destroy(self.codegen, descriptor.llvm_alloca, descriptor.list_type)

    def register_own(self, var_name: str, own_type: StructType, slot: ir.Instruction) -> None:
        """Register Own<T> variable for automatic RAII cleanup."""
        self._enter(self.owned_pointers, var_name,
                    OwnDescriptor(name=var_name, own_type=own_type, slot=slot))
        self.codegen.moves.arm_if_conditional(var_name, slot)

    def is_destroyed(self, var_name: str) -> bool:
        """Has `var_name` already been released by an explicit `.destroy()` / `.free()`?"""
        array = self._array(var_name)
        if array is not None and array.destroyed:
            return True
        lst = self._list(var_name)
        if lst is not None and lst.destroyed:
            return True
        own = self._own(var_name)
        return own is not None and own.destroyed

    def reset_destroyed_on_rebind(self, var_name: str) -> None:
        """Clear the destroyed flag after a rebind gives `var_name` a NEW value."""
        array = self._array(var_name)
        if array is not None:
            array.destroyed = False
        lst = self._list(var_name)
        if lst is not None:
            lst.destroyed = False
        own = self._own(var_name)
        if own is not None:
            own.destroyed = False

    def mark_own_destroyed(self, var_name: str) -> None:
        """Mark an Own<T> variable as explicitly destroyed."""
        own = self._own(var_name)
        if own is not None:
            own.destroyed = True

    def _destroy_own(self, descriptor: OwnDescriptor) -> None:
        """Emit the destructor of a local Own<T>, with no move gate."""
        from sushi_lang.backend.destructors import emit_value_destructor
        emit_value_destructor(self.codegen, descriptor.slot, descriptor.own_type)

    def _destroy_array(self, descriptor: DynamicArrayDescriptor) -> None:
        """Emit the destructor of a dynamic array, with no move gate."""
        from sushi_lang.backend.destructors import emit_value_destructor
        emit_value_destructor(self.codegen, descriptor.llvm_alloca,
                              DynamicArrayType(descriptor.element_type))

    def emit_array_destructor(self, name: str) -> None:
        """Emit the move-gated destructor of `name`'s innermost dynamic array."""
        descriptor = self._array(name)
        if descriptor is None or descriptor.destroyed:
            return
        self.codegen.moves.emit_free_unless_moved(
            descriptor.llvm_alloca, lambda: self._destroy_array(descriptor))

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

        len_ptr = gep_utils.gep_dynamic_array_len(self.codegen, descriptor.llvm_alloca, builder=self.builder)
        cap_ptr = gep_utils.gep_dynamic_array_cap(self.codegen, descriptor.llvm_alloca, builder=self.builder)
        data_ptr_ptr = gep_utils.gep_dynamic_array_data(self.codegen, descriptor.llvm_alloca, builder=self.builder)

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
