"""
RAII-style dynamic array and Own<T> memory management.

This module handles:
- Dynamic array lifetime and scope tracking
- Exponential growth strategy (2x like Rust Vec)
- Automatic cleanup at scope boundaries
- Own<T> heap allocation tracking
- Struct field cleanup for nested dynamic arrays
"""
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
    """Runtime descriptor for a dynamic array instance.

    Maps to LLVM struct: {i32 len, i32 cap, T* data}
    """
    name: str                    # Variable name
    element_type: Type           # Element type (int, bool, string)
    llvm_alloca: ir.Instruction  # LLVM alloca for the struct
    destroyed: bool = False      # Track if explicitly destroyed
    # Move state lives in the unified codegen.moves MoveTracker (keyed by name).


@dataclass
class OwnDescriptor:
    """Runtime descriptor for an Own<T> instance.

    Tracks ownership and destruction state for RAII cleanup.
    """
    name: str                    # Variable name
    own_type: StructType         # Own<T> struct type
    slot: ir.Instruction         # Alloca holding the Own<T> struct (move key + destructor target)
    depth: int = -1              # Scope depth at registration (shadow disambiguation)
    destroyed: bool = False      # Track if explicitly destroyed via .destroy()


@dataclass
class ListDescriptor:
    """Runtime descriptor for a local List<T> instance.

    A List<T> owns a heap buffer (its `data` pointer). Tracked parallel to dynamic
    arrays so it is freed on every exit path via RAII (#61).
    """
    name: str                    # Variable name
    list_type: StructType        # List<T> struct type
    llvm_alloca: ir.Instruction  # LLVM alloca for the List<T> struct
    destroyed: bool = False      # Explicitly .destroy()/.free()'d
    # Move state lives in the unified codegen.moves MoveTracker (keyed by name).


class DynamicArrayManager:
    """RAII-style memory manager for dynamic arrays and Own<T>.

    Responsibilities:
    - Track dynamic array lifetime and scope
    - Generate malloc/free calls with error handling
    - Implement exponential growth strategy
    - Automatic cleanup at scope boundaries
    - Own<T> lifetime management
    - Struct field cleanup (recursive)
    """

    def __init__(self, builder: ir.IRBuilder, codegen: 'LLVMCodegen') -> None:
        """Initialize the dynamic array manager.

        Args:
            builder: The LLVM IR builder.
            codegen: The main codegen instance.
        """
        self.builder = builder
        self.codegen = codegen
        # Stack of scopes, each containing dynamic arrays in that scope
        self.scope_stack: List[Set[str]] = []
        # Track dynamic arrays by name as a per-name STACK of descriptors (innermost last),
        # so a nested shadow of an owning array does not overwrite the outer descriptor:
        # the inner binding pushes, its scope pop frees it and pops it, and the outer
        # binding's descriptor is restored as the top. A flat dict lost the outer descriptor
        # and the outer pop then double-freed the inner array (CE0015 dominance ICE).
        self.arrays: Dict[str, List[DynamicArrayDescriptor]] = {}
        # Track Own<T> variables for RAII cleanup. Flat by name: Own cleanup is driven by a
        # wholesale iteration (emit_own_cleanup) at function/scope exit, not scope-popped like
        # arrays, so a per-name stack would never drain. The descriptor carries its slot for
        # slot-keyed move checks.
        self.owned_pointers: Dict[str, OwnDescriptor] = {}
        # Track local List<T> variables for RAII cleanup (stacked, parallel to dynamic arrays).
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
        """Exit current scope and automatically destroy all dynamic arrays
        declared in this scope (if not already destroyed or moved).

        Move semantics: Arrays marked as 'moved' are not cleaned up since
        ownership has been transferred to another variable.

        Raises:
            RuntimeError: If attempting to pop from an empty scope stack (CE0016).
        """
        if not self.scope_stack:
            from sushi_lang.internals.errors import raise_internal_error
            raise_internal_error("CE0016")

        current_scope = self.scope_stack.pop()
        current_lists = self.list_scope_stack.pop() if self.list_scope_stack else set()

        # Popping the per-name descriptor stacks below is the drain: these arrays / lists are
        # now out of scope and their outer namesake (if any) is restored as the top. If the
        # current block already terminated, an early `return`/`??` inside this scope already
        # emitted the destructors on this path -- emitting again would append a stray free
        # after the terminator. Skip EMISSION but still drain the stacks so shadowing stays
        # consistent. (Mirrors the cstr cleanup discipline; see #59.)
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
        """Declare a new dynamic array variable and allocate its struct on stack.

        Returns the alloca instruction for the array struct.
        The struct layout is: {i32 len, i32 cap, T* data}
        Initially: len=0, cap=0, data=null

        Args:
            name: The variable name.
            array_type: The dynamic array type.

        Returns:
            The alloca instruction for the array struct.
        """
        # Resolve UnknownType to StructType/EnumType if needed
        from sushi_lang.semantics.typesys import UnknownType
        element_type = array_type.base_type
        if isinstance(element_type, UnknownType):
            # Resolve to a concrete struct or enum from the symbol tables
            if element_type.name in self.codegen.struct_table.by_name:
                element_type = self.codegen.struct_table.by_name[element_type.name]
            elif element_type.name in self.codegen.enum_table.by_name:
                element_type = self.codegen.enum_table.by_name[element_type.name]
            else:
                from sushi_lang.internals.errors import raise_internal_error
                raise_internal_error("CE0020", type=element_type.name)

        # Create LLVM struct type for the dynamic array
        element_llvm_type = self._get_llvm_type_for_element(element_type)
        struct_type = ir.LiteralStructType([
            ir.IntType(INT32_BIT_WIDTH),                 # len
            ir.IntType(INT32_BIT_WIDTH),                 # cap
            ir.PointerType(element_llvm_type)            # data*
        ])

        # Allocate struct on stack
        alloca = self.builder.alloca(struct_type, name=f"{name}_struct")

        # Initialize to zero (len=0, cap=0, data=null)
        null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)

        # Store initial values using helper methods
        len_ptr = self.codegen.types.get_dynamic_array_len_ptr(self.builder, alloca)
        cap_ptr = self.codegen.types.get_dynamic_array_cap_ptr(self.builder, alloca)
        data_ptr = self.codegen.types.get_dynamic_array_data_ptr(self.builder, alloca)

        self.builder.store(ZERO_I32, len_ptr)
        self.builder.store(ZERO_I32, cap_ptr)
        self.builder.store(null_ptr, data_ptr)

        # Track the array (with resolved element type)
        descriptor = DynamicArrayDescriptor(
            name=name,
            element_type=element_type,  # Use resolved type
            llvm_alloca=alloca
        )
        self.arrays.setdefault(name, []).append(descriptor)

        # Add to current scope
        if self.scope_stack:
            self.scope_stack[-1].add(name)

        return alloca

    def register_param_array(self, name: str, element_type: Type, slot: ir.Instruction) -> None:
        """Register an incoming dynamic-array parameter for RAII cleanup.

        Unlike `declare_dynamic_array`, this does NOT allocate or zero-initialise a
        new struct: it adopts the existing parameter slot (which already holds the
        caller-synthesised array struct) into the destruction tracking so the array
        is freed at scope exit. Used for native variadic '...T' parameters, where the
        callee owns the collected T[] (the caller has moved it).

        Args:
            name: The parameter variable name.
            element_type: The array element type T.
            slot: The alloca holding the array struct (the parameter slot).
        """
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
        if self.scope_stack:
            self.scope_stack[-1].add(name)

    def emit_array_constructor_new(self, name: str) -> None:
        """Emit code for new() constructor - array is already initialized to empty.

        This is a no-op since declare_dynamic_array already initializes to empty.
        """
        pass  # new() constructor creates empty array - already done in declare

    def emit_array_constructor_from(self, name: str, elements: List[ir.Value]) -> None:
        """Emit code for from(array_literal) constructor.

        Allocates initial capacity and copies elements.

        Args:
            name: The array variable name.
            elements: The LLVM values for initial elements.
        """
        descriptor = self._array(name)
        if descriptor is None:
            raise_internal_error("CE0057", name=name)
        if descriptor.destroyed:
            raise_internal_error("CE0058", name=name)

        # Determine initial capacity (at least len, but use power of 2)
        initial_len = len(elements)
        if initial_len == 0:
            return  # Empty array, already initialized

        initial_capacity = self._next_power_of_2(initial_len)

        # Allocate memory
        element_size = self._get_element_size_bytes(descriptor.element_type)
        capacity_val = make_i32_const(initial_capacity)
        total_bytes = self.builder.mul(capacity_val, element_size, name="total_bytes")

        data_ptr = emit_malloc(self.codegen, self.builder, total_bytes)

        # Cast void* to element_type*
        element_llvm_type = self._get_llvm_type_for_element(descriptor.element_type)
        typed_data_ptr = self.builder.bitcast(data_ptr, ir.PointerType(element_llvm_type), name="typed_data_ptr")

        # Copy elements to allocated memory
        for i, element_value in enumerate(elements):
            element_ptr = self.builder.gep(typed_data_ptr, [make_i32_const(i)])
            # Cast element to the target type if needed (e.g., i32 -> i8 for u8[] arrays)
            casted_element = self.codegen.utils.cast_for_param(element_value, element_llvm_type)
            self.builder.store(casted_element, element_ptr)

        # Update struct fields with typed pointer
        self._update_array_fields(name, initial_len, initial_capacity, typed_data_ptr)

    def emit_destroy_call(self, name: str) -> None:
        """Emit explicit .destroy() method call.

        Frees memory and marks array as destroyed.

        Args:
            name: The array variable name.
        """
        self._emit_array_destructor(name)
        descriptor = self._array(name)
        if descriptor is not None:
            descriptor.destroyed = True

    def mark_as_moved(self, name: str) -> None:
        """Mark a dynamic array as moved (ownership transferred).

        Delegates to the unified MoveTracker, keyed by the array's slot so a sibling or
        shadowing binding of the same name is not poisoned. Moved arrays are excluded from
        RAII cleanup, implementing move semantics for return values.

        Args:
            name: The variable name to mark as moved.
        """
        descriptor = self._array(name)
        if descriptor is not None:
            self.codegen.moves.mark(descriptor.llvm_alloca)

    def is_list_type(self, ty: Type) -> bool:
        """Check if a type is List<T>.

        Args:
            ty: The type to check.

        Returns:
            True if the type is a List<T> instantiation, False otherwise.
        """
        return isinstance(ty, StructType) and ty.name.startswith("List<")

    def register_list(self, var_name: str, list_type: StructType, slot: ir.Instruction) -> None:
        """Register a local List<T> variable for automatic RAII cleanup (#61).

        The list owns a heap buffer; it is destroyed at scope exit on every path, like a
        dynamic array, unless it is moved (returned) or explicitly .free()/.destroy()'d.

        Args:
            var_name: The name of the variable holding a List<T> value.
            list_type: The List<T> struct type.
            slot: The alloca holding the List<T> struct.
        """
        self.lists.setdefault(var_name, []).append(
            ListDescriptor(name=var_name, list_type=list_type, llvm_alloca=slot))
        if self.list_scope_stack:
            self.list_scope_stack[-1].add(var_name)

    def mark_list_moved(self, var_name: str) -> None:
        """Mark a List<T> as moved (ownership transferred to the caller); skip cleanup."""
        descriptor = self._list(var_name)
        if descriptor is not None:
            self.codegen.moves.mark(descriptor.llvm_alloca)

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
        if descriptor.destroyed or self.codegen.moves.is_moved(descriptor.llvm_alloca):
            return
        from sushi_lang.backend.generics.list.methods_destroy import emit_list_destroy
        emit_list_destroy(self.codegen, descriptor.llvm_alloca, descriptor.list_type)

    def is_own_type(self, ty: Type) -> bool:
        """Check if a type is Own<T>.

        Args:
            ty: The type to check.

        Returns:
            True if the type is an Own<T> instantiation, False otherwise.
        """
        if isinstance(ty, StructType):
            # Check if struct name starts with "Own<" (e.g., "Own<i32>", "Own<string>")
            return ty.name.startswith("Own<")
        return False

    def register_own(self, var_name: str, own_type: StructType, slot: ir.Instruction) -> None:
        """Register Own<T> variable for automatic RAII cleanup.

        Args:
            var_name: The name of the variable holding an Own<T> value.
            own_type: The Own<T> struct type (e.g., Own<i32>).
            slot: The alloca holding the Own<T> struct (move key + destructor target).
        """
        depth = self.codegen.memory._scope_depth
        self.owned_pointers[var_name] = OwnDescriptor(
            name=var_name, own_type=own_type, slot=slot, depth=depth, destroyed=False)

    def mark_own_moved(self, var_name: str) -> None:
        """Mark an Own<T> as moved (ownership transferred, e.g. into another Own or a
        struct field); the unified MoveTracker excludes it from RAII cleanup."""
        descriptor = self.owned_pointers.get(var_name)
        if descriptor is not None:
            self.codegen.moves.mark(descriptor.slot)

    def mark_own_destroyed(self, var_name: str) -> None:
        """Mark an Own<T> variable as explicitly destroyed.

        This prevents RAII from attempting to destroy it again at scope exit.

        Args:
            var_name: The name of the Own<T> variable that was manually destroyed.
        """
        if var_name in self.owned_pointers:
            self.owned_pointers[var_name].destroyed = True

    def emit_own_cleanup(self) -> None:
        """Emit cleanup code for all Own<T> variables in current scope.

        Generates Own<T>.destroy() calls for all registered Own<T> variables
        that have not been explicitly destroyed.
        This should be called at scope boundaries (function exit, before returns, etc.).
        """
        for var_name, descriptor in self.owned_pointers.items():
            if not descriptor.destroyed and not self.codegen.moves.is_moved(descriptor.slot):
                self._emit_own_destructor(var_name, descriptor.own_type)

    def _emit_own_destructor(self, var_name: str, own_type: StructType) -> None:
        """Emit destructor code for a single Own<T> variable.

        Uses the general recursive destructor (destructors.emit_value_destructor),
        which descends into the owned payload before freeing the pointer. This is the
        SAME deep teardown used for Own<T> stored in struct fields / array elements, so
        a nested Own<Own<T>> frees every level exactly once regardless of storage.

        Args:
            var_name: The variable name.
            own_type: The Own<T> struct type.
        """
        from sushi_lang.backend.destructors import emit_value_destructor

        # Pass the variable slot (address of the Own<T> struct); the recursive
        # destructor geps field 0, recurses into the payload, then frees.
        own_slot = self.codegen.memory.find_local_slot(var_name)
        emit_value_destructor(self.codegen, self.builder, own_slot, own_type)

    def _emit_array_destructor(self, name: str) -> None:
        """Generate destructor code for a dynamic array.

        Skips cleanup for arrays that have been moved (ownership transferred).

        Args:
            name: The array variable name.
        """
        descriptor = self._array(name)
        if descriptor is None:
            return
        if descriptor.destroyed or self.codegen.moves.is_moved(descriptor.llvm_alloca):
            return

        # Use the general destructor for the array struct
        from sushi_lang.backend.destructors import emit_value_destructor
        emit_value_destructor(self.codegen, self.builder, descriptor.llvm_alloca, DynamicArrayType(descriptor.element_type))

    def _update_array_fields(self, name: str, length: int, capacity: int, data_ptr: ir.Value) -> None:
        """Update the len, cap, and data fields of a dynamic array struct.

        Args:
            name: The array variable name.
            length: The new length value.
            capacity: The new capacity value.
            data_ptr: The new data pointer.
        """
        descriptor = self._array(name)
        if descriptor is None:
            raise_internal_error("CE0057", name=name)

        # Get pointers to struct fields using helper methods
        len_ptr = self.codegen.types.get_dynamic_array_len_ptr(self.builder, descriptor.llvm_alloca)
        cap_ptr = self.codegen.types.get_dynamic_array_cap_ptr(self.builder, descriptor.llvm_alloca)
        data_ptr_ptr = self.codegen.types.get_dynamic_array_data_ptr(self.builder, descriptor.llvm_alloca)

        # Store new values
        self.builder.store(make_i32_const(length), len_ptr)
        self.builder.store(make_i32_const(capacity), cap_ptr)
        self.builder.store(data_ptr, data_ptr_ptr)

    def _get_llvm_type_for_element(self, element_type: Type) -> ir.Type:
        """Convert Sushi element type to LLVM type.

        Args:
            element_type: The semantic element type.

        Returns:
            The corresponding LLVM type.
        """
        # Use the main type system for consistent mapping
        return self.codegen.types.ll_type(element_type)

    def _get_element_size_bytes(self, element_type: Type) -> ir.Value:
        """Get the per-element allocation stride in bytes as an LLVM i32 constant.

        Uses the LLVM ABI alloc size of the element's LLVM type, which is the
        stride getelementptr uses to index the element pointer. This can exceed
        the semantic data size for padded types -- a string fat pointer {i8*, i32}
        has a 12-byte data size but a 16-byte alloc size -- and allocating with the
        smaller data size while GEP strides by the alloc size corrupts the heap
        past element 0 (issues #24 / #29).

        Args:
            element_type: The Sushi language type of array elements.

        Returns:
            LLVM i32 constant representing the allocation stride in bytes.
        """
        from sushi_lang.backend.expressions import memory
        element_llvm_type = self._get_llvm_type_for_element(element_type)
        return memory.get_element_size_constant(self.codegen, element_llvm_type)

    def _next_power_of_2(self, n: int) -> int:
        """Return the next power of 2 >= n. Used for capacity growth.

        Args:
            n: The input value.

        Returns:
            The next power of 2.
        """
        if n <= 1:
            return 1
        return 1 << (n - 1).bit_length()

    def struct_needs_cleanup(self, struct_type: StructType) -> bool:
        """Check if a struct (or enum) type owns heap that needs scope-exit cleanup.

        Accepts an EnumType at the top level too, so enum locals can reuse this gate
        (an enum owns heap when its active variant carries a dynamic-array / string /
        closure / owning-struct / Own / List payload).

        Args:
            struct_type: The struct (or enum) type to analyze.

        Returns:
            True if the type owns heap requiring cleanup, False otherwise.
        """
        from sushi_lang.semantics.typesys import EnumType
        if isinstance(struct_type, EnumType):
            return self._enum_needs_cleanup(struct_type)

        for field_name, field_type in struct_type.fields:
            if self._payload_needs_cleanup(field_type):
                return True
        return False

    def _payload_needs_cleanup(self, ty: Type) -> bool:
        """Whether a single field / variant-payload type owns heap needing cleanup.

        Cycle-safe: recursion through a heap-indirected payload (dynamic array / Own /
        List) short-circuits to True before descending into the element/payload type,
        so a self-referential type (e.g. `enum MsgValue: Arr(MsgValue[])`,
        `enum Tree: Node(Own<Tree>)`) terminates instead of looping.
        """
        from sushi_lang.semantics.typesys import (
            UnknownType, FunctionType, BuiltinType, StructType, EnumType)
        from sushi_lang.backend.destructors import result_ok_err
        # Resolve a named type to its concrete struct/enum definition.
        if isinstance(ty, UnknownType):
            if ty.name in self.codegen.struct_table.by_name:
                ty = self.codegen.struct_table.by_name[ty.name]
            elif ty.name in self.codegen.enum_table.by_name:
                ty = self.codegen.enum_table.by_name[ty.name]

        # A Result<T, E> field/payload is an enum carrying T and E, but it reaches us as a
        # ResultType / GenericTypeRef, which match no branch below -- so a struct or enum
        # owning a Result was not registered for cleanup at all (#179).
        result_args = result_ok_err(ty)
        if result_args is not None:
            return (self._payload_needs_cleanup(result_args[0])
                    or self._payload_needs_cleanup(result_args[1]))

        if isinstance(ty, DynamicArrayType):
            return True
        # A function-value (closure) owns a heap environment freed through its
        # runtime-guarded drop pointer (a no-op for a non-capturing value).
        if isinstance(ty, FunctionType):
            return True
        # A `string` owns a heap buffer when its runtime `owned` bit is set (#147);
        # scope-exit RAII frees it (guarded on the bit, so a literal/borrow is a no-op).
        if ty == BuiltinType.STRING:
            return True
        if isinstance(ty, StructType):
            # Own<T> / List<T> always own a heap allocation; other structs are checked
            # field-by-field. Named-prefix check short-circuits the self-referential
            # Own<Tree> / List<Node> cycle without recursing into the payload type.
            if ty.name.startswith("Own<") or ty.name.startswith("List<"):
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
        """Emit scope-exit cleanup for a struct local's owning fields.

        Delegates to the unified recursive value destructor (`emit_value_destructor`),
        which frees dynamic-array, string (#147), function-value (closure), nested-struct,
        enum, List and Own fields through a single code path -- the owned bit / drop pointer
        make borrowed or non-owning fields runtime no-ops. This replaces the former
        array-only field-walk (`_get_cleanup_fields` / `_emit_struct_field_*`), consolidating
        onto the same destructor used for structs nested in arrays/List/Own/enum/HashMap.

        Args:
            var_name: The struct variable name (unused; kept for call-site compatibility).
            struct_type: The struct type containing fields to clean up.
            struct_alloca: The alloca (pointer) holding the struct value.
        """
        from sushi_lang.backend.destructors import emit_value_destructor
        emit_value_destructor(self.codegen, self.builder, struct_alloca, struct_type)
