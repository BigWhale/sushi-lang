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

        # Struct cleanup tracking: variable name -> stack of (scope_level, StructType, alloca).
        # Stacked like _locals so a nested shadow of an owning struct does not overwrite the
        # outer entry: the inner binding pushes and its pop drains only the top-at-depth,
        # leaving the outer binding to be freed by the outer pop (a flat dict leaked it).
        # Only stores structs that need RAII cleanup.
        self._struct_cleanup: Dict[str, List[tuple[int, 'StructType', ir.AllocaInstr]]] = {}

        # Closure (function-value) cleanup tracking: variable name -> stack of
        # (scope_level, alloca) holding the {fn_ptr, env_ptr, drop_ptr} fat value. Stacked
        # like _locals for shadow-correctness (see _struct_cleanup). Every function-typed
        # `let` local is registered; the free is runtime-guarded by drop_ptr, so a
        # non-capturing value frees to a no-op (capture is erased from the `fn(...)` type).
        # Function PARAMETERS are deliberately NOT registered -- a passed closure is owned by
        # the caller's binding, not the callee (freeing it here would double-free).
        self._closure_cleanup: Dict[str, List[tuple[int, ir.AllocaInstr]]] = {}

        # Struct move tracking is delegated to the unified codegen.moves MoveTracker.

        # FFI no-leak registry: per-scope stack of marshalled C strings (i8*) that
        # must be freed at scope exit. Parallel to the dynamic-array scope stack.
        #
        # Discipline (mirrors basic-block mutual-exclusivity):
        # - register_cstr appends to the innermost scope's list.
        # - An early-exit path (return / ?? propagation) calls
        #   emit_cstr_cleanup_all(), which emits a free for every live cstr into
        #   the CURRENT (terminating) block WITHOUT mutating the registry. That
        #   block is mutually exclusive with all other exit blocks at runtime.
        # - The structural pop_scope() pops the innermost list and frees it into
        #   the fall-through block (also mutually exclusive with the early-exit
        #   blocks). Popping is the ONLY thing that removes entries.
        # Net effect: exactly one free executes per runtime path, no double free.
        self._cstr_cleanup: List[List[ir.Value]] = []

        # Inline-closure temp registry: per-scope stack of {fn,env,drop} fat VALUES for
        # capturing closures created inline as a call argument (#123). Such a closure is
        # never bound to a local, so it has no owner in _closure_cleanup; the caller's
        # scope owns it and frees its heap env at scope exit. Value-keyed (SSA fat value,
        # no name/slot) and freed via the runtime-guarded drop, mirroring _cstr_cleanup's
        # mutual-exclusion discipline: register appends; early-exit emits without mutating;
        # pop_scope drains exactly once on the fall-through. One free per runtime path.
        self._closure_temp_cleanup: List[List[ir.Value]] = []

        # String-value RAII (#145): local `string` bindings whose heap buffer is freed at
        # scope exit via the owned bit (a literal/borrow carries owned=0 -> the free is a
        # runtime no-op). Stacked like _closure_cleanup for shadow-correctness; move-tracked
        # via MoveTracker so a returned/aliased owning string is skipped (its new owner frees
        # it). Parameters are NOT registered -- a passed string is owned by the caller's binding.
        self._string_cleanup: Dict[str, List[tuple[int, ir.AllocaInstr]]] = {}

    @staticmethod
    def _stack_peek_slot(reg: Dict[str, List], name: str) -> Optional[ir.AllocaInstr]:
        """Return the innermost registered slot for `name` in a stacked cleanup registry.

        The slot is the LAST element of each entry tuple ((depth, slot) or
        (depth, type, slot)). Returns None if the name has no live registration.
        """
        entries = reg.get(name)
        if entries:
            return entries[-1][-1]
        return None

    @staticmethod
    def _stack_pop_at_depth(reg: Dict[str, List], name: str, depth: int) -> None:
        """Drop `name`'s top entry from a stacked cleanup registry if it is at `depth`."""
        entries = reg.get(name)
        if entries and entries[-1][0] == depth:
            entries.pop()
            if not entries:
                del reg[name]

    def push_scope(self) -> None:
        """Push a new lexical scope onto the scope stack.

        Creates a new scope level for variables declared in nested contexts
        like if statements, while loops, and function bodies. Also pushes
        a corresponding scope for dynamic array tracking.
        """
        self._scope_depth += 1
        self._scope_vars.append(set())
        self._cstr_cleanup.append([])
        self._closure_temp_cleanup.append([])

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

        # Clean up struct variables with dynamic array fields before popping scope.
        # This is the fall-through (normal) exit. If the block already terminated, an
        # early return/`??` inside this scope emitted the struct destructors on that path
        # already (emit_struct_cleanup); emitting again would append a stray free after
        # the terminator. Skip emission but still drain the tracking. Each runtime exit
        # path frees on its own mutually-exclusive block, so no double free (#59/#60).
        if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
            block = self.codegen.builder.block if self.codegen.builder is not None else None
            block_live = block is not None and not block.is_terminated
            for var_name in current_vars:
                struct_entries = self._struct_cleanup.get(var_name)
                if struct_entries and struct_entries[-1][0] == self._scope_depth:
                    _depth, struct_type, alloca = struct_entries[-1]
                    if block_live and not self.codegen.moves.is_moved(alloca):
                        self.codegen.dynamic_arrays.emit_struct_field_cleanup(var_name, struct_type, alloca)
                    # Remove this binding's entry when leaving its scope
                    self._stack_pop_at_depth(self._struct_cleanup, var_name, self._scope_depth)

        # Free function-value locals (closures) on the fall-through exit, runtime-guarded
        # by drop_ptr. Same mutual-exclusion discipline as structs (#59/#60): early-exit
        # paths emit their own guarded free via emit_closure_cleanup; a moved (escaped)
        # closure is skipped so its new owner frees it.
        if self._closure_cleanup:
            block = self.codegen.builder.block if self.codegen.builder is not None else None
            block_live = block is not None and not block.is_terminated
            for var_name in current_vars:
                closure_entries = self._closure_cleanup.get(var_name)
                if closure_entries and closure_entries[-1][0] == self._scope_depth:
                    slot = closure_entries[-1][-1]
                    if block_live and not self.codegen.moves.is_moved(slot):
                        self._emit_closure_free(slot)
                    self._stack_pop_at_depth(self._closure_cleanup, var_name, self._scope_depth)

        # Free string locals on the fall-through exit, runtime-guarded by the owned bit (#145).
        # Same mutual-exclusion discipline as structs/closures: early-exit paths emit their own
        # guarded free via emit_string_cleanup_all; a moved (returned/aliased) string is skipped
        # so its new owner frees it. A literal/borrow (owned=0) frees to a no-op.
        if self._string_cleanup:
            block = self.codegen.builder.block if self.codegen.builder is not None else None
            block_live = block is not None and not block.is_terminated
            for var_name in current_vars:
                string_entries = self._string_cleanup.get(var_name)
                if string_entries and string_entries[-1][0] == self._scope_depth:
                    slot = string_entries[-1][-1]
                    if block_live and not self.codegen.moves.is_moved(slot):
                        self._emit_string_free(slot)
                    self._stack_pop_at_depth(self._string_cleanup, var_name, self._scope_depth)

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

        # Free the marshalled C strings registered in this scope on the normal
        # (fall-through) block exit. This is the ONLY place the per-scope list is
        # removed. Early-exit paths (return / ??) emit their own frees into their
        # own terminating blocks via emit_cstr_cleanup_all() without popping, so
        # exactly one free runs per runtime path. _free_cstr_list is a no-op when
        # the current block is already terminated (e.g. the scope body ended in a
        # bare return), avoiding a stray free after the ret.
        if self._cstr_cleanup:
            self._free_cstr_list(self._cstr_cleanup.pop())

        # Free inline-closure argument temporaries registered in this scope on the
        # normal (fall-through) block exit -- the only place the per-scope list is
        # removed. Early-exit paths emit their own guarded drop via
        # emit_closure_temp_cleanup_all() without popping, so exactly one runs per path.
        if self._closure_temp_cleanup:
            self._free_closure_temp_list(self._closure_temp_cleanup.pop())

        self._scope_vars.pop()
        self._scope_depth -= 1

        # Also pop dynamic array scope if the manager is initialized
        if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
            self.codegen.dynamic_arrays.pop_scope()

    def register_cstr(self, c_str: 'ir.Value') -> None:
        """Register a marshalled C string (i8*) for freeing at scope exit."""
        if self._cstr_cleanup:
            self._cstr_cleanup[-1].append(c_str)

    def _free_cstr_list(self, ptrs: List['ir.Value']) -> None:
        """Emit free() calls for a list of C strings, if the block is live."""
        if not ptrs:
            return
        builder = self.codegen.builder
        if builder is None or builder.block is None or builder.block.is_terminated:
            return
        free_fn = self.codegen.get_free_func()
        for ptr in ptrs:
            builder.call(free_fn, [ptr])

    def emit_cstr_cleanup_all(self) -> None:
        """Emit a free for every live C string across all open scopes.

        Used on early-exit paths (return, ?? propagation): every still-open scope
        is abandoned on this path, so every live marshalled pointer must be freed
        here. The frees are emitted into the CURRENT block, which terminates
        immediately after (with a `ret`) and is therefore mutually exclusive with
        every other exit block at runtime.

        Crucially this does NOT mutate the registry: the lists stay intact so the
        structural pop_scope() calls on the fall-through path still emit their own
        frees into their own (mutually exclusive) blocks. Because at runtime
        exactly one of these blocks executes, each pointer is freed exactly once.
        """
        for scope_list in self._cstr_cleanup:
            self._free_cstr_list(scope_list)

    def register_closure_temp(self, fat_value: 'ir.Value') -> None:
        """Register an inline-closure argument temp ({fn,env,drop} value) for scope-exit free."""
        if self._closure_temp_cleanup:
            self._closure_temp_cleanup[-1].append(fat_value)

    def _free_closure_temp_list(self, fat_values: List['ir.Value']) -> None:
        """Emit the runtime-guarded env free for a list of closure temps, if the block is live."""
        if not fat_values:
            return
        builder = self.codegen.builder
        if builder is None or builder.block is None or builder.block.is_terminated:
            return
        from sushi_lang.backend.destructors import emit_function_value_destructor_from_value
        for fat in fat_values:
            emit_function_value_destructor_from_value(self.codegen, builder, fat)

    def emit_closure_temp_cleanup_all(self) -> None:
        """Emit the guarded env free for every live inline-closure temp across all open scopes.

        Used on early-exit paths (return, ?? propagation), mirroring
        emit_cstr_cleanup_all: the frees go into the CURRENT (terminating) block and the
        registry is NOT mutated, so the fall-through pop_scope still frees on its own
        mutually-exclusive block. Exactly one runs per runtime path -- no leak, no double free.
        """
        for scope_list in self._closure_temp_cleanup:
            self._free_closure_temp_list(scope_list)

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

    def set_semantic_type(self, name: str, semantic_ty: 'Type') -> None:
        """Register the semantic type of an already-declared local at the current scope.

        Only touches the semantic-type cache -- it deliberately does NOT register the
        name for RAII cleanup (unlike create_local). Used to attach `self`/parameter
        types in extension-method bodies, whose slots are created directly (fn_def=None)
        so begin_function never records their semantic types. A by-value `self` must
        NOT be freed by the callee, so the no-cleanup behaviour here is required.
        """
        if name not in self._types:
            self._types[name] = []
        self._types[name].append((self._scope_depth, semantic_ty))

    def create_local(self, name: str, ty: ir.Type, init: Optional[ir.Value] = None, semantic_ty: Optional['Type'] = None, register_cleanup: bool = True) -> ir.AllocaInstr:
        """Create local variable with optional initialization.

        Allocates space for a local variable in the function entry block
        and optionally initializes it with a value. Tracks struct variables
        with dynamic array fields for RAII cleanup.

        Args:
            name: The variable name.
            ty: The LLVM type for the variable.
            init: Optional initial value to store.
            semantic_ty: Optional semantic type for the variable.
            register_cleanup: When False, the local is NOT registered for scope-exit RAII
                (the semantic type is still recorded for method dispatch). Used for a
                borrow-like binding that aliases memory owned elsewhere -- e.g. a match
                variant-payload binding, which borrows the enum's payload; the enum (its
                owner) frees it, so registering the binding too would double-free (#139).

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

            # Track struct / enum variables that need cleanup.
            # Resolve a named / generic reference first -- UnknownType('Box'),
            # GenericTypeRef('List', (i32,)), GenericTypeRef('Result', (T, E)) -- to the concrete
            # struct/enum it names. The branches below dispatch on the resolved class, so an
            # unresolved local is registered in NO cleanup registry and its payload leaks (#179).
            from sushi_lang.semantics.typesys import StructType, EnumType, ArrayType, FunctionType, BuiltinType
            from sushi_lang.backend.destructors import resolve_named_type
            semantic_ty = resolve_named_type(self.codegen, semantic_ty)
            if not register_cleanup:
                pass  # borrow-like binding: aliases memory owned elsewhere, do not free
            elif isinstance(semantic_ty, (StructType, EnumType)):
                # An enum local whose active variant owns heap (a dynamic-array / string /
                # closure / owning-struct payload) is freed at scope exit like a struct
                # local, reusing the struct-cleanup registry so both the fall-through
                # (pop_scope) and early-exit (emit_struct_cleanup) paths free it through
                # the recursion-safe emit_value_destructor. #143 lifted CE2059 (enum may
                # hold T[]) without wiring this owner, so such enum locals leaked (#139).
                if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
                    if self.codegen.dynamic_arrays.struct_needs_cleanup(semantic_ty):
                        self._struct_cleanup.setdefault(name, []).append((self._scope_depth, semantic_ty, slot))
            # A fixed-size array local (`string[3]`, `Box[2]`) whose ELEMENTS own heap. It owns no
            # buffer of its own -- the storage is the alloca -- so it is not a dynamic array and
            # has no registry of its own; it reuses the owning-value registry, whose drain calls
            # the same recursion-safe emit_value_destructor. ArrayType matched NO branch in this
            # chain, so such a local was registered nowhere and no exit path could free it (#185).
            elif isinstance(semantic_ty, ArrayType):
                from sushi_lang.backend.destructors import needs_cleanup
                if needs_cleanup(semantic_ty):
                    self._struct_cleanup.setdefault(name, []).append((self._scope_depth, semantic_ty, slot))
            # Track function-value locals for runtime-guarded env free at scope exit.
            elif isinstance(semantic_ty, FunctionType):
                self._closure_cleanup.setdefault(name, []).append((self._scope_depth, slot))
            # Track string locals for owned-bit-guarded free at scope exit (#145).
            elif semantic_ty == BuiltinType.STRING:
                self._string_cleanup.setdefault(name, []).append((self._scope_depth, slot))

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

            # Track struct / enum variables that need cleanup.
            # Resolve a named / generic reference first -- UnknownType('Box'),
            # GenericTypeRef('List', (i32,)), GenericTypeRef('Result', (T, E)) -- to the concrete
            # struct/enum it names. The branches below dispatch on the resolved class, so an
            # unresolved local is registered in NO cleanup registry and its payload leaks (#179).
            from sushi_lang.semantics.typesys import StructType, EnumType, ArrayType, FunctionType, BuiltinType
            from sushi_lang.backend.destructors import resolve_named_type
            semantic_ty = resolve_named_type(self.codegen, semantic_ty)
            if isinstance(semantic_ty, (StructType, EnumType)):
                # An enum local whose active variant owns heap (a dynamic-array / string /
                # closure / owning-struct payload) is freed at scope exit like a struct
                # local, reusing the struct-cleanup registry so both the fall-through
                # (pop_scope) and early-exit (emit_struct_cleanup) paths free it through
                # the recursion-safe emit_value_destructor. #143 lifted CE2059 (enum may
                # hold T[]) without wiring this owner, so such enum locals leaked (#139).
                if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
                    if self.codegen.dynamic_arrays.struct_needs_cleanup(semantic_ty):
                        self._struct_cleanup.setdefault(name, []).append((self._scope_depth, semantic_ty, slot))
            # A fixed-size array local (`string[3]`, `Box[2]`) whose ELEMENTS own heap. It owns no
            # buffer of its own -- the storage is the alloca -- so it is not a dynamic array and
            # has no registry of its own; it reuses the owning-value registry, whose drain calls
            # the same recursion-safe emit_value_destructor. ArrayType matched NO branch in this
            # chain, so such a local was registered nowhere and no exit path could free it (#185).
            elif isinstance(semantic_ty, ArrayType):
                from sushi_lang.backend.destructors import needs_cleanup
                if needs_cleanup(semantic_ty):
                    self._struct_cleanup.setdefault(name, []).append((self._scope_depth, semantic_ty, slot))
            # Track function-value locals for runtime-guarded env free at scope exit.
            elif isinstance(semantic_ty, FunctionType):
                self._closure_cleanup.setdefault(name, []).append((self._scope_depth, slot))
            # Track string locals for owned-bit-guarded free at scope exit (#145).
            elif semantic_ty == BuiltinType.STRING:
                self._string_cleanup.setdefault(name, []).append((self._scope_depth, slot))

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

    def register_struct_cleanup(self, name: str, struct_type: 'StructType', slot: ir.AllocaInstr) -> None:
        """Register a struct variable for RAII cleanup of its dynamic-array fields.

        Used for by-value struct parameters that own heap memory: the callee owns its
        (deep-copied) copy and must free it at scope exit, exactly like a `let` local.
        The caller passes a resolved StructType so scope-exit cleanup can reach the fields.

        Args:
            name: The variable name (already entered into the current scope).
            struct_type: The resolved struct type whose array fields need freeing.
            slot: The alloca holding the struct value.
        """
        self._struct_cleanup.setdefault(name, []).append((self._scope_depth, struct_type, slot))

    def _emit_closure_free(self, slot: ir.AllocaInstr) -> None:
        """Emit the runtime-guarded env free for a function-value local (`if drop: drop(env)`)."""
        from sushi_lang.backend.destructors import emit_function_value_destructor
        emit_function_value_destructor(self.codegen, self.codegen.builder, slot)

    def _emit_string_free(self, slot: ir.AllocaInstr) -> None:
        """Emit the owned-bit-guarded free for a string local (`if owned: free(data)`) (#145)."""
        from sushi_lang.backend.destructors import emit_string_destructor
        emit_string_destructor(self.codegen, self.codegen.builder, slot)

    def emit_string_cleanup_all(self) -> None:
        """Emit the guarded free for every live string local across all open scopes.

        Used on early-exit paths (return / ?? propagation), mirroring the struct/closure
        early-exit emitters: the frees go into the CURRENT (terminating) block and the
        registry is NOT mutated, so the fall-through pop_scope still frees on its own
        mutually-exclusive block. A moved (returned/aliased) string is skipped so its new
        owner frees it; exactly one free runs per runtime path -- no leak, no double free.
        """
        builder = self.codegen.builder
        if builder is None or builder.block is None or builder.block.is_terminated:
            return
        for var_name, entries in self._string_cleanup.items():
            for _depth, slot in entries:
                if not self.codegen.moves.is_moved(slot):
                    self._emit_string_free(slot)

    def is_closure_registered(self, name: str) -> bool:
        """True if `name` is a registered function-value RAII owner in the current scope.

        Used to distinguish a rebind that MOVES from an owning closure local (source is
        registered) from one that borrows an env owned elsewhere (a param / container
        get-out / struct-field read, which is not registered).
        """
        return name in self._closure_cleanup

    def unregister_closure_cleanup(self, name: str) -> None:
        """Drop the innermost `name` entry from function-value RAII tracking (no-op if absent).

        Used when a function-value local is a NON-owning alias -- bound from a container
        get-out (`fns.get(i)??`) or a struct-field read (`s.handler`) whose env the
        container/struct still owns. Registering it as a second owner would double-free
        the shared environment (mirrors the Own<T>.get() non-owning-borrow guard). Only the
        just-registered (innermost) binding is removed, so an outer shadowed owner survives."""
        self._stack_pop_at_depth(self._closure_cleanup, name, self._scope_depth)

    def is_string_registered(self, name: str) -> bool:
        """True if `name` is a registered owning string local in the current scope (#145).

        Used to distinguish a rebind that MOVES from an owning string local (source is
        registered) from one that borrows a buffer owned elsewhere (a param / struct-field
        read / container get-out, which is not registered)."""
        return name in self._string_cleanup

    def is_struct_registered(self, name: str) -> bool:
        """True if `name` is a registered owning-struct local (a struct with heap-owning
        fields tracked for RAII cleanup). Used to decide whether handing the local to a
        container that stores it shallowly must MOVE it, so scope exit does not double-free
        the shared buffer (#140)."""
        return name in self._struct_cleanup

    def unregister_string_cleanup(self, name: str) -> None:
        """Drop the innermost `name` entry from string RAII tracking (no-op if absent) (#145).

        Used when a string local is a NON-owning alias -- bound from a struct-field read or
        a container get-out whose buffer the struct/container still owns. Registering it as a
        second owner would double-free (mirrors the closure/Own<T> non-owning-borrow guard).
        Only the just-registered (innermost) binding is removed, so an outer shadow survives."""
        self._stack_pop_at_depth(self._string_cleanup, name, self._scope_depth)

    def mark_struct_as_moved(self, var_name: str) -> None:
        """Mark a struct variable as moved (ownership transferred).

        Delegates to the unified MoveTracker. Moved structs are excluded from RAII
        cleanup, implementing move semantics for return values.

        Args:
            var_name: The variable name to mark as moved.
        """
        slot = self._slot_for_name(var_name)
        if slot is not None:
            self.codegen.moves.mark(slot)

    def _slot_for_name(self, name: str) -> Optional[ir.AllocaInstr]:
        """Resolve a name to its innermost binding slot, or None if it has no local slot."""
        if name in self._locals and self._locals[name]:
            return self._locals[name][-1][1]
        return None

    def is_struct_moved(self, var_name: str) -> bool:
        """Check if the innermost binding named `var_name` has been moved.

        Convenience for call sites that hold only a name and whose lookup is
        unambiguous (the innermost binding is the intended one). Cleanup walkers that
        hold the exact slot check `codegen.moves.is_moved(slot)` directly instead, so a
        shadowed outer binding is never confused with its inner namesake.

        Args:
            var_name: The variable name to check.

        Returns:
            True if the variable's innermost binding has been moved, False otherwise.
        """
        slot = self._slot_for_name(var_name)
        return slot is not None and self.codegen.moves.is_moved(slot)

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
        self._closure_cleanup.clear()
        self._string_cleanup.clear()
        self._cstr_cleanup = []
        self._closure_temp_cleanup = []

        # Clear moved tracking (function boundary)
        self.codegen.moves.reset()

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
        """Per-scope-level view of owning-struct locals (name -> (type, slot)).

        Rebuilt from the stacked _struct_cleanup so the early-exit cleanup walker
        (statements/utils.py) sees every live binding at its own scope level with its own
        slot -- an outer struct and its inner shadow land in different levels and are
        freed independently.
        """
        result: List[Dict[str, tuple['StructType', ir.AllocaInstr]]] = [
            {} for _ in range(self._scope_depth + 1)
        ]
        for name, entries in self._struct_cleanup.items():
            for depth, struct_type, slot in entries:
                if 0 <= depth < len(result):
                    result[depth][name] = (struct_type, slot)
        return result
