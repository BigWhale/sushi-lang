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
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from llvmlite import ir
from semantics.typesys import DynamicArrayType, Type, StructType
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH
from backend.llvm_constants import ZERO_I32, make_i32_const
from backend.memory.heap import emit_malloc, emit_free

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


@dataclass
class DynamicArrayDescriptor:
    """Runtime descriptor for a dynamic array instance.

    Maps to LLVM struct: {i32 len, i32 cap, T* data}
    """
    name: str                    # Variable name
    element_type: Type           # Element type (int, bool, string)
    llvm_alloca: ir.Instruction  # LLVM alloca for the struct
    destroyed: bool = False      # Track if explicitly destroyed
    moved: bool = False          # Track if ownership has been moved (Rust-style move semantics)


@dataclass
class OwnDescriptor:
    """Runtime descriptor for an Own<T> instance.

    Tracks ownership and destruction state for RAII cleanup.
    """
    name: str                    # Variable name
    own_type: StructType         # Own<T> struct type
    destroyed: bool = False      # Track if explicitly destroyed via .destroy()


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
        # Track all dynamic arrays by name
        self.arrays: Dict[str, DynamicArrayDescriptor] = {}
        # Track Own<T> variables for RAII cleanup
        self.owned_pointers: Dict[str, OwnDescriptor] = {}

    def push_scope(self) -> None:
        """Enter a new scope for dynamic array tracking."""
        self.scope_stack.append(set())

    def pop_scope(self) -> None:
        """Exit current scope and automatically destroy all dynamic arrays
        declared in this scope (if not already destroyed or moved).

        Move semantics: Arrays marked as 'moved' are not cleaned up since
        ownership has been transferred to another variable.

        Raises:
            RuntimeError: If attempting to pop from an empty scope stack (CE0016).
        """
        if not self.scope_stack:
            from internals.errors import raise_internal_error
            raise_internal_error("CE0016")

        current_scope = self.scope_stack.pop()

        # Generate destructor calls for all arrays in this scope
        # Skip arrays that have been moved (ownership transferred)
        for array_name in current_scope:
            if array_name in self.arrays:
                descriptor = self.arrays[array_name]
                if not descriptor.destroyed and not descriptor.moved:
                    self._emit_array_destructor(array_name)
                    descriptor.destroyed = True

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
        # Resolve UnknownType to StructType if needed
        from semantics.typesys import UnknownType
        element_type = array_type.base_type
        if isinstance(element_type, UnknownType):
            # Resolve to StructType from struct table
            if element_type.name in self.codegen.struct_table.by_name:
                element_type = self.codegen.struct_table.by_name[element_type.name]
            else:
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
        self.arrays[name] = descriptor

        # Add to current scope
        if self.scope_stack:
            self.scope_stack[-1].add(name)

        return alloca

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
        if name not in self.arrays:
            raise_internal_error("CE0057", name=name)

        descriptor = self.arrays[name]
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
        if name in self.arrays:
            self.arrays[name].destroyed = True

    def mark_as_moved(self, name: str) -> None:
        """Mark a dynamic array as moved (ownership transferred).

        Moved arrays are excluded from RAII cleanup. This implements move
        semantics for return values, allowing ownership transfer without cleanup.

        Args:
            name: The variable name to mark as moved.
        """
        if name in self.arrays:
            self.arrays[name].moved = True

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

    def register_own(self, var_name: str, own_type: StructType) -> None:
        """Register Own<T> variable for automatic RAII cleanup.

        Args:
            var_name: The name of the variable holding an Own<T> value.
            own_type: The Own<T> struct type (e.g., Own<i32>).
        """
        descriptor = OwnDescriptor(name=var_name, own_type=own_type, destroyed=False)
        self.owned_pointers[var_name] = descriptor

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
            if not descriptor.destroyed:
                self._emit_own_destructor(var_name, descriptor.own_type)

    def _emit_own_destructor(self, var_name: str, own_type: StructType) -> None:
        """Emit destructor code for a single Own<T> variable.

        Args:
            var_name: The variable name.
            own_type: The Own<T> struct type.
        """
        from backend.generics import own

        # Load Own<T> value from variable slot
        own_slot = self.codegen.memory.find_local_slot(var_name)
        own_value = self.builder.load(own_slot, name=f"{var_name}_own_value")

        # Call Own<T>.destroy()
        own.emit_own_destroy(self.codegen, own_value)

    def _emit_array_destructor(self, name: str) -> None:
        """Generate destructor code for a dynamic array.

        Skips cleanup for arrays that have been moved (ownership transferred).

        Args:
            name: The array variable name.
        """
        if name not in self.arrays:
            return

        descriptor = self.arrays[name]
        if descriptor.destroyed or descriptor.moved:
            return

        # Use the general destructor for the array struct
        from backend.destructors import emit_value_destructor
        emit_value_destructor(self.codegen, self.builder, descriptor.llvm_alloca, DynamicArrayType(descriptor.element_type))

    def _update_array_fields(self, name: str, length: int, capacity: int, data_ptr: ir.Value) -> None:
        """Update the len, cap, and data fields of a dynamic array struct.

        Args:
            name: The array variable name.
            length: The new length value.
            capacity: The new capacity value.
            data_ptr: The new data pointer.
        """
        if name not in self.arrays:
            raise_internal_error("CE0057", name=name)

        descriptor = self.arrays[name]

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
        """Get the size in bytes of an element type as an LLVM i32 constant.

        Delegates to the centralized type size calculation in LLVMTypeSystem.

        Args:
            element_type: The Sushi language type of array elements.

        Returns:
            LLVM i32 constant representing the size in bytes.
        """
        # Delegate to centralized size calculation (single source of truth)
        return self.codegen.types.get_type_size_constant(element_type)

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
        """Check if a struct type contains any dynamic array fields that need cleanup.

        Args:
            struct_type: The struct type to analyze.

        Returns:
            True if the struct contains dynamic array fields, False otherwise.
        """
        from semantics.typesys import UnknownType
        for field_name, field_type in struct_type.fields:
            # Resolve UnknownType to StructType if needed
            if isinstance(field_type, UnknownType):
                if field_type.name in self.codegen.struct_table.by_name:
                    field_type = self.codegen.struct_table.by_name[field_type.name]

            if isinstance(field_type, DynamicArrayType):
                return True
            # Check nested structs recursively
            if isinstance(field_type, StructType):
                if self.struct_needs_cleanup(field_type):
                    return True
        return False

    def _get_cleanup_fields(self, struct_type: StructType, base_indices: Optional[List[int]] = None) -> List[Tuple[str, List[int], DynamicArrayType]]:
        """Get list of dynamic array fields that need cleanup from a struct.

        Returns a list of tuples: (field_path, gep_indices, field_type)
        where gep_indices is the list of indices to reach the field via GEP.

        Args:
            struct_type: The struct type to analyze.
            base_indices: The GEP indices accumulated from parent structs (for nested structs).

        Returns:
            List of (field_path, gep_indices, dynamic_array_type) tuples.

        Example:
            For: struct Container { i32[] numbers, string name }
            Returns: [("numbers", [0, 0], DynamicArrayType(I32))]

            For: struct Outer { Inner data, string name }
                 struct Inner { i32[] values }
            Returns: [("data.values", [0, 0, 0], DynamicArrayType(I32))]
        """
        if base_indices is None:
            base_indices = [0]  # First index is always 0 for GEP into struct pointer

        cleanup_fields = []
        from semantics.typesys import UnknownType

        for field_index, (field_name, field_type) in enumerate(struct_type.fields):
            field_gep_indices = base_indices + [field_index]

            # Resolve UnknownType to StructType if needed
            if isinstance(field_type, UnknownType):
                if field_type.name in self.codegen.struct_table.by_name:
                    field_type = self.codegen.struct_table.by_name[field_type.name]

            if isinstance(field_type, DynamicArrayType):
                # Found a dynamic array field - add it to cleanup list
                cleanup_fields.append((field_name, field_gep_indices, field_type))

            elif isinstance(field_type, StructType):
                # Nested struct - recursively scan its fields
                nested_fields = self._get_cleanup_fields(field_type, field_gep_indices)
                # Prepend parent field name to nested field paths
                for nested_name, nested_indices, nested_type in nested_fields:
                    full_path = f"{field_name}.{nested_name}"
                    cleanup_fields.append((full_path, nested_indices, nested_type))

        return cleanup_fields

    def emit_struct_field_cleanup(self, var_name: str, struct_type: StructType, struct_alloca: ir.Value) -> None:
        """Emit cleanup code for all dynamic array fields in a struct variable.

        Generates LLVM IR to free memory for each dynamic array field at scope exit.

        Args:
            var_name: The struct variable name (for debugging).
            struct_type: The struct type containing fields to clean up.
            struct_alloca: The alloca instruction for the struct variable.
        """
        cleanup_fields = self._get_cleanup_fields(struct_type)

        if not cleanup_fields:
            return  # No cleanup needed

        # For each dynamic array field, emit destructor code
        for field_path, gep_indices, array_type in cleanup_fields:
            self._emit_struct_field_destructor(var_name, field_path, struct_alloca, gep_indices, array_type)

    def _emit_struct_field_destructor(self, var_name: str, field_path: str,
                                       struct_alloca: ir.Value, gep_indices: List[int],
                                       array_type: DynamicArrayType) -> None:
        """Emit destructor code for a single dynamic array field in a struct.

        Args:
            var_name: The struct variable name.
            field_path: The field path (e.g., "numbers" or "data.values").
            struct_alloca: The alloca instruction for the struct variable.
            gep_indices: The GEP indices to reach the field.
            array_type: The dynamic array type of the field.
        """
        # Get pointer to the dynamic array field using GEP
        # Convert Python ints to LLVM constants
        gep_index_values = [make_i32_const(idx) for idx in gep_indices]
        field_name_safe = field_path.replace('.', '_')
        field_ptr = self.builder.gep(
            struct_alloca, gep_index_values,
            name=f"{var_name}_{field_name_safe}_ptr"
        )

        # Load the dynamic array struct {i32 len, i32 cap, T* data}
        field_value = self.builder.load(field_ptr, name=f"{var_name}_{field_name_safe}")

        # Emit destructor logic - same as _emit_array_destructor
        # but operating on field_value instead of a top-level array

        # The field_value is the array struct by value, we need its address to GEP into it
        # Allocate a temporary slot for the array struct so we can GEP into it
        array_struct_slot = self.builder.alloca(
            field_value.type,
            name=f"{var_name}_{field_name_safe}_slot"
        )
        self.builder.store(field_value, array_struct_slot)

        # Get pointer to data pointer using helper method
        data_ptr_ptr = self.codegen.types.get_dynamic_array_data_ptr(self.builder, array_struct_slot)
        data_ptr = self.builder.load(data_ptr_ptr, name=f"{var_name}_{field_name_safe}_data")

        # Check if data is not null before freeing
        null_ptr = ir.Constant(data_ptr.type, None)
        is_not_null = self.builder.icmp_unsigned("!=", data_ptr, null_ptr)

        with self.builder.if_then(is_not_null):
            # Cast typed pointer back to void* for free()
            void_ptr_type = ir.PointerType(ir.IntType(INT8_BIT_WIDTH))
            void_ptr = self.builder.bitcast(
                data_ptr, void_ptr_type,
                name=f"{var_name}_{field_name_safe}_void_ptr"
            )
            emit_free(self.builder, self.codegen, void_ptr)
