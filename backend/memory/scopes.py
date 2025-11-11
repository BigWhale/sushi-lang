"""
Variable scope management with O(1) lookup and RAII cleanup.

This module handles:
- Lexical scope stack for nested blocks
- Local variable allocation and tracking
- O(1) variable lookup via flat cache
- Struct variable tracking for automatic cleanup
- Move semantics tracking for ownership transfer
"""
from __future__ import annotations
from typing import Dict, List, Optional, TYPE_CHECKING

from llvmlite import ir
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.typesys import Type, StructType


class ScopeManager:
    """Manages variable scoping and alloca tracking for LLVM code generation."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize scope manager with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and builders.
        """
        self.codegen = codegen

        # Scope stack for nested variable visibility
        self.locals: List[Dict[str, ir.AllocaInstr]] = []

        # Parallel scope stack for semantic type information
        self.semantic_types: List[Dict[str, 'Type']] = []

        # Parallel scope stack for tracking struct variables that need cleanup
        # Maps variable_name -> (StructType, alloca_instruction)
        self.struct_variables: List[Dict[str, tuple['StructType', ir.AllocaInstr]]] = []

        # Track which structs have already been cleaned up (to avoid double cleanup)
        self.cleaned_up_structs: set[str] = set()

        # Track which struct variables have been moved (ownership transferred)
        # Moved structs are excluded from RAII cleanup
        self.moved_structs: set[str] = set()

        # O(1) lookup cache: maps variable name to stack of (scope_level, alloca/type)
        # Each variable name has a stack to handle shadowing - innermost scope is last
        self._flat_locals_cache: Dict[str, List[tuple[int, ir.AllocaInstr]]] = {}
        self._flat_types_cache: Dict[str, List[tuple[int, 'Type']]] = {}

    def push_scope(self) -> None:
        """Push a new lexical scope onto the scope stack.

        Creates a new scope level for variables declared in nested contexts
        like if statements, while loops, and function bodies. Also pushes
        a corresponding scope for dynamic array tracking.
        """
        self.locals.append({})
        self.semantic_types.append({})
        self.struct_variables.append({})

        # Also push dynamic array scope if the manager is initialized
        if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
            self.codegen.dynamic_arrays.push_scope()

    def pop_scope(self) -> None:
        """Pop the current lexical scope from the scope stack.

        Removes the innermost scope, making variables declared in that
        scope no longer accessible. Also triggers cleanup of dynamic
        arrays and struct fields with dynamic arrays declared in this scope.

        Move semantics: Skips cleanup for variables marked as moved.

        Raises:
            IndexError: If there are no scopes to pop.
        """
        # Clean up struct variables with dynamic array fields before popping scope
        if self.struct_variables and hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
            current_struct_scope = self.struct_variables[-1]
            for var_name, (struct_type, alloca) in current_struct_scope.items():
                # Skip cleanup if:
                # 1. Already cleaned (e.g., by early return)
                # 2. Marked as moved (ownership transferred via return)
                if var_name not in self.cleaned_up_structs and not self.is_struct_moved(var_name):
                    self.codegen.dynamic_arrays.emit_struct_field_cleanup(var_name, struct_type, alloca)
                    self.cleaned_up_structs.add(var_name)

        # Remove variables from flat cache before popping
        current_scope_level = len(self.locals) - 1
        current_locals = self.locals[-1]
        current_types = self.semantic_types[-1]

        # Remove entries from flat locals cache
        for var_name in current_locals.keys():
            if var_name in self._flat_locals_cache:
                # Remove the last entry (should match current scope level)
                if self._flat_locals_cache[var_name] and self._flat_locals_cache[var_name][-1][0] == current_scope_level:
                    self._flat_locals_cache[var_name].pop()
                    # Clean up empty lists
                    if not self._flat_locals_cache[var_name]:
                        del self._flat_locals_cache[var_name]

        # Remove entries from flat types cache
        for var_name in current_types.keys():
            if var_name in self._flat_types_cache:
                # Remove the last entry (should match current scope level)
                if self._flat_types_cache[var_name] and self._flat_types_cache[var_name][-1][0] == current_scope_level:
                    self._flat_types_cache[var_name].pop()
                    # Clean up empty lists
                    if not self._flat_types_cache[var_name]:
                        del self._flat_types_cache[var_name]

        self.locals.pop()
        self.semantic_types.pop()
        self.struct_variables.pop()

        # Also pop dynamic array scope if the manager is initialized
        if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
            self.codegen.dynamic_arrays.pop_scope()

    def find_local_slot(self, name: str) -> ir.AllocaInstr:
        """Find local variable slot by name in scope stack (O(1) lookup).

        Uses flat cache for O(1) lookup time instead of O(n) scope traversal.
        Correctly handles variable shadowing by returning the most recent
        (innermost scope) declaration.

        Args:
            name: The variable name to search for.

        Returns:
            The alloca instruction for the variable.

        Raises:
            KeyError: If the variable is not found in any scope.
        """
        # O(1) lookup via flat cache
        if name in self._flat_locals_cache and self._flat_locals_cache[name]:
            # Return the most recent entry (innermost scope)
            return self._flat_locals_cache[name][-1][1]
        raise KeyError(f"undefined name: {name}")

    def find_semantic_type(self, name: str) -> Optional['Type']:
        """Find semantic type for a variable by name in scope stack (O(1) lookup).

        Uses flat cache for O(1) lookup time instead of O(n) scope traversal.
        Correctly handles variable shadowing by returning the most recent type.

        Args:
            name: The variable name to search for.

        Returns:
            The semantic type of the variable, or None if not found.
        """
        # O(1) lookup via flat cache
        if name in self._flat_types_cache and self._flat_types_cache[name]:
            # Return the most recent entry (innermost scope)
            return self._flat_types_cache[name][-1][1]
        return None

    def create_local(self, name: str, ty: ir.Type, init: Optional[ir.Value] = None, semantic_ty: Optional['Type'] = None) -> ir.AllocaInstr:
        """Create local variable with optional initialization.

        Allocates space for a local variable in the function entry block
        and optionally initializes it with a value. Tracks struct variables
        with dynamic array fields for RAII cleanup.

        Args:
            name: The variable name.
            ty: The LLVM type for the variable.
            init: Optional initial value to store.
            semantic_ty: Optional semantic type for the variable.

        Returns:
            The alloca instruction for the variable.
        """
        slot = self.entry_alloca(ty, name)
        current_scope_level = len(self.locals) - 1

        # Add to scope stack
        self.locals[-1][name] = slot

        # Update flat cache for O(1) lookup
        if name not in self._flat_locals_cache:
            self._flat_locals_cache[name] = []
        self._flat_locals_cache[name].append((current_scope_level, slot))

        if semantic_ty is not None:
            self.semantic_types[-1][name] = semantic_ty

            # Update flat cache for semantic types
            if name not in self._flat_types_cache:
                self._flat_types_cache[name] = []
            self._flat_types_cache[name].append((current_scope_level, semantic_ty))

            # Track struct variables that need cleanup
            from semantics.typesys import StructType
            if isinstance(semantic_ty, StructType):
                if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
                    if self.codegen.dynamic_arrays.struct_needs_cleanup(semantic_ty):
                        self.struct_variables[-1][name] = (semantic_ty, slot)

        if init is not None:
            if self.codegen.builder is None:
                raise_internal_error("CE0009")
            self.codegen.builder.store(init, slot)
        return slot

    def create_local_nostore(self, name: str, ty: ir.Type, semantic_ty: Optional['Type'] = None) -> ir.AllocaInstr:
        """Create local variable without initialization.

        Allocates space for a local variable but does not store any
        initial value. Used when the initialization will be done separately.
        Tracks struct variables with dynamic array fields for RAII cleanup.

        Args:
            name: The variable name.
            ty: The LLVM type for the variable.
            semantic_ty: Optional semantic type for the variable.

        Returns:
            The alloca instruction for the variable.

        Raises:
            KeyError: If a variable with the same name already exists in current scope.
        """
        if name in self.locals[-1]:
            raise KeyError(f"duplicate local in same scope: {name}")

        slot = self.entry_alloca(ty, name)
        current_scope_level = len(self.locals) - 1

        # Add to scope stack
        self.locals[-1][name] = slot

        # Update flat cache for O(1) lookup
        if name not in self._flat_locals_cache:
            self._flat_locals_cache[name] = []
        self._flat_locals_cache[name].append((current_scope_level, slot))

        if semantic_ty is not None:
            self.semantic_types[-1][name] = semantic_ty

            # Update flat cache for semantic types
            if name not in self._flat_types_cache:
                self._flat_types_cache[name] = []
            self._flat_types_cache[name].append((current_scope_level, semantic_ty))

            # Track struct variables that need cleanup
            from semantics.typesys import StructType
            if isinstance(semantic_ty, StructType):
                if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
                    needs_cleanup = self.codegen.dynamic_arrays.struct_needs_cleanup(semantic_ty)
                    if needs_cleanup:
                        self.struct_variables[-1][name] = (semantic_ty, slot)

        return slot

    def entry_alloca(self, ty: ir.Type, name: str) -> ir.AllocaInstr:
        """Create alloca instruction in function entry block.

        Places the allocation instruction at the beginning of the function's
        entry block, before any other instructions, following LLVM best
        practices for SSA form and optimization.

        Args:
            ty: The LLVM type to allocate.
            name: The variable name for debugging.

        Returns:
            The alloca instruction.

        Raises:
            AssertionError: If entry block or alloca builder is not available.
        """
        if self.codegen.entry_block is None:
            raise_internal_error("CE0011")
        if self.codegen.alloca_builder is None:
            raise_internal_error("CE0012")
        if hasattr(self.codegen, "entry_branch") and self.codegen.entry_branch is not None:
            self.codegen.alloca_builder.position_before(self.codegen.entry_branch)
        else:
            self.codegen.alloca_builder.position_at_start(self.codegen.entry_block)
        return self.codegen.alloca_builder.alloca(ty, name=name)

    def mark_struct_as_moved(self, var_name: str) -> None:
        """Mark a struct variable as moved (ownership transferred).

        Moved structs are excluded from RAII cleanup. This implements move
        semantics for return values, allowing ownership transfer without cleanup.

        Args:
            var_name: The variable name to mark as moved.
        """
        self.moved_structs.add(var_name)

    def is_struct_moved(self, var_name: str) -> bool:
        """Check if a struct variable has been moved.

        Args:
            var_name: The variable name to check.

        Returns:
            True if the variable has been moved, False otherwise.
        """
        return var_name in self.moved_structs

    def reset_scope_stack(self) -> None:
        """Reset the scope stack to empty state.

        Pops all remaining scopes to trigger cleanup, then clears all state.
        Typically used when ending function processing or resetting the memory manager state.
        """
        # Pop all scopes to trigger cleanup
        while len(self.locals) > 0:
            self.pop_scope()

        # Clear all stacks (should already be empty after popping)
        self.locals = []
        self.semantic_types = []
        self.struct_variables = []

        # Clear flat caches (should already be empty after popping)
        self._flat_locals_cache.clear()
        self._flat_types_cache.clear()

        # Clear cleanup tracking (function boundary)
        self.cleaned_up_structs.clear()

        # Clear moved tracking (function boundary)
        self.moved_structs.clear()

    def current_scope_size(self) -> int:
        """Get the current scope stack depth.

        Returns:
            The number of nested scopes currently active.
        """
        return len(self.locals)

    def get_current_scope_vars(self) -> Dict[str, ir.AllocaInstr]:
        """Get variables in the current (innermost) scope.

        Returns:
            Dictionary mapping variable names to their alloca instructions
            in the current scope.

        Raises:
            IndexError: If no scopes are active.
        """
        if not self.locals:
            raise IndexError("No active scopes")
        return self.locals[-1].copy()

    def has_variable_in_scope(self, name: str, scope_level: int = -1) -> bool:
        """Check if a variable exists in a specific scope level.

        Args:
            name: The variable name to check.
            scope_level: The scope level to check (-1 for current scope).

        Returns:
            True if the variable exists in the specified scope.

        Raises:
            IndexError: If the scope level is invalid.
        """
        if scope_level < 0:
            scope_level = len(self.locals) + scope_level
        if scope_level < 0 or scope_level >= len(self.locals):
            raise IndexError(f"Invalid scope level: {scope_level}")
        return name in self.locals[scope_level]
