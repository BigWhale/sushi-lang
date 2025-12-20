"""
Variable scope management with O(1) lookup and RAII cleanup.

This module handles:
- Lexical scope stack for nested blocks
- Local variable allocation and tracking
- O(1) variable lookup via flat cache (primary storage)
- Struct variable tracking for automatic cleanup
- Move semantics tracking for ownership transfer

Architecture:
The flat caches are the primary storage for variable lookups.
Scope tracking uses lightweight sets of variable names per scope level,
avoiding duplicate storage of alloca/type information.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type, StructType


class ScopeManager:
    """Manages variable scoping and alloca tracking for LLVM code generation.

    Uses flat caches as primary storage for O(1) lookups while maintaining
    lightweight scope tracking for cleanup and shadowing support.
    """

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize scope manager with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and builders.
        """
        self.codegen = codegen

        # Current scope depth (starts at 0 when first scope is pushed)
        self._scope_depth: int = -1

        # Lightweight scope tracking: just variable names per scope level
        # Used for cleanup and popping - no duplicate storage of allocas
        self._scope_vars: List[Set[str]] = []

        # Primary storage: flat caches for O(1) lookup
        # Maps variable name -> stack of (scope_level, value) for shadowing support
        self._locals: Dict[str, List[tuple[int, ir.AllocaInstr]]] = {}
        self._types: Dict[str, List[tuple[int, 'Type']]] = {}

        # Struct cleanup tracking: variable name -> (StructType, alloca)
        # Only stores structs that need RAII cleanup (not duplicated per scope)
        self._struct_cleanup: Dict[str, tuple['StructType', ir.AllocaInstr]] = {}

        # Track which structs have already been cleaned up (to avoid double cleanup)
        self.cleaned_up_structs: Set[str] = set()

        # Track which struct variables have been moved (ownership transferred)
        self.moved_structs: Set[str] = set()

    def push_scope(self) -> None:
        """Push a new lexical scope onto the scope stack.

        Creates a new scope level for variables declared in nested contexts
        like if statements, while loops, and function bodies. Also pushes
        a corresponding scope for dynamic array tracking.
        """
        self._scope_depth += 1
        self._scope_vars.append(set())

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
        if self._scope_depth < 0:
            raise IndexError("No scopes to pop")

        # Get variables in current scope
        current_vars = self._scope_vars[self._scope_depth]

        # Clean up struct variables with dynamic array fields before popping scope
        if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
            for var_name in current_vars:
                if var_name in self._struct_cleanup:
                    # Skip cleanup if already cleaned or moved
                    if var_name not in self.cleaned_up_structs and not self.is_struct_moved(var_name):
                        struct_type, alloca = self._struct_cleanup[var_name]
                        self.codegen.dynamic_arrays.emit_struct_field_cleanup(var_name, struct_type, alloca)
                        self.cleaned_up_structs.add(var_name)
                    # Remove from cleanup tracking when leaving scope
                    del self._struct_cleanup[var_name]

        # Remove variables from flat caches
        for var_name in current_vars:
            # Remove from locals cache
            if var_name in self._locals and self._locals[var_name]:
                if self._locals[var_name][-1][0] == self._scope_depth:
                    self._locals[var_name].pop()
                    if not self._locals[var_name]:
                        del self._locals[var_name]

            # Remove from types cache
            if var_name in self._types and self._types[var_name]:
                if self._types[var_name][-1][0] == self._scope_depth:
                    self._types[var_name].pop()
                    if not self._types[var_name]:
                        del self._types[var_name]

        self._scope_vars.pop()
        self._scope_depth -= 1

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
        if name in self._locals and self._locals[name]:
            return self._locals[name][-1][1]
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
        if name in self._types and self._types[name]:
            return self._types[name][-1][1]
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

        # Track variable in current scope
        self._scope_vars[self._scope_depth].add(name)

        # Add to flat cache (primary storage)
        if name not in self._locals:
            self._locals[name] = []
        self._locals[name].append((self._scope_depth, slot))

        if semantic_ty is not None:
            if name not in self._types:
                self._types[name] = []
            self._types[name].append((self._scope_depth, semantic_ty))

            # Track struct variables that need cleanup
            from sushi_lang.semantics.typesys import StructType
            if isinstance(semantic_ty, StructType):
                if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
                    if self.codegen.dynamic_arrays.struct_needs_cleanup(semantic_ty):
                        self._struct_cleanup[name] = (semantic_ty, slot)

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
        # Check for duplicate in current scope
        if name in self._scope_vars[self._scope_depth]:
            raise KeyError(f"duplicate local in same scope: {name}")

        slot = self.entry_alloca(ty, name)

        # Track variable in current scope
        self._scope_vars[self._scope_depth].add(name)

        # Add to flat cache (primary storage)
        if name not in self._locals:
            self._locals[name] = []
        self._locals[name].append((self._scope_depth, slot))

        if semantic_ty is not None:
            if name not in self._types:
                self._types[name] = []
            self._types[name].append((self._scope_depth, semantic_ty))

            # Track struct variables that need cleanup
            from sushi_lang.semantics.typesys import StructType
            if isinstance(semantic_ty, StructType):
                if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
                    if self.codegen.dynamic_arrays.struct_needs_cleanup(semantic_ty):
                        self._struct_cleanup[name] = (semantic_ty, slot)

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
        while self._scope_depth >= 0:
            self.pop_scope()

        # Clear all state (should already be empty after popping)
        self._scope_vars = []
        self._scope_depth = -1
        self._locals.clear()
        self._types.clear()
        self._struct_cleanup.clear()

        # Clear cleanup tracking (function boundary)
        self.cleaned_up_structs.clear()

        # Clear moved tracking (function boundary)
        self.moved_structs.clear()

    def current_scope_size(self) -> int:
        """Get the current scope stack depth.

        Returns:
            The number of nested scopes currently active.
        """
        return self._scope_depth + 1

    def get_current_scope_vars(self) -> Dict[str, ir.AllocaInstr]:
        """Get variables in the current (innermost) scope.

        Returns:
            Dictionary mapping variable names to their alloca instructions
            in the current scope.

        Raises:
            IndexError: If no scopes are active.
        """
        if self._scope_depth < 0:
            raise IndexError("No active scopes")
        result = {}
        for name in self._scope_vars[self._scope_depth]:
            if name in self._locals and self._locals[name]:
                result[name] = self._locals[name][-1][1]
        return result

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
            scope_level = self._scope_depth + 1 + scope_level
        if scope_level < 0 or scope_level > self._scope_depth:
            raise IndexError(f"Invalid scope level: {scope_level}")
        return name in self._scope_vars[scope_level]

    # Backward compatibility properties
    @property
    def locals(self) -> List[Dict[str, ir.AllocaInstr]]:
        """Backward compatible access to locals (deprecated, use flat cache directly)."""
        # Reconstruct scope-based view for legacy code
        result = []
        for level, scope_vars in enumerate(self._scope_vars):
            scope_dict = {}
            for name in scope_vars:
                if name in self._locals:
                    for lvl, alloca in self._locals[name]:
                        if lvl == level:
                            scope_dict[name] = alloca
                            break
            result.append(scope_dict)
        return result

    @property
    def semantic_types(self) -> List[Dict[str, 'Type']]:
        """Backward compatible access to semantic types (deprecated, use flat cache directly)."""
        result = []
        for level, scope_vars in enumerate(self._scope_vars):
            scope_dict = {}
            for name in scope_vars:
                if name in self._types:
                    for lvl, ty in self._types[name]:
                        if lvl == level:
                            scope_dict[name] = ty
                            break
            result.append(scope_dict)
        return result

    @property
    def struct_variables(self) -> List[Dict[str, tuple['StructType', ir.AllocaInstr]]]:
        """Backward compatible access to struct variables (deprecated)."""
        # For backward compatibility, return single-level view
        result = [{}] * (self._scope_depth + 1) if self._scope_depth >= 0 else []
        if result:
            result[-1] = dict(self._struct_cleanup)
        return result
