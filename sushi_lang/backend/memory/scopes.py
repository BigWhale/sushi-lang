"""Variable scope management with O(1) lookup and RAII cleanup."""
from __future__ import annotations
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type, StructType


class ScopeManager:
    """Manages variable scoping and alloca tracking for LLVM code generation."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize scope manager with reference to main codegen instance."""
        self.codegen = codegen

        self._scope_depth: int = -1

        self._scope_vars: List[Set[str]] = []

        self._locals: Dict[str, List[tuple[int, ir.AllocaInstr]]] = {}
        self._types: Dict[str, List[tuple[int, 'Type']]] = {}

        # name -> stack of (scope_level, StructType, alloca). STACKED, not flat: a nested
        # shadow of an owning struct would overwrite the outer entry and leak it.
        self._struct_cleanup: Dict[str, List[tuple[int, 'StructType', ir.AllocaInstr]]] = {}

        # name -> stack of (scope_level, alloca) holding the fat value. Stacked for the
        # same reason as _struct_cleanup. The free is drop_ptr-guarded, so a non-capturing
        # value frees to a no-op -- capture is erased from the `fn(...)` type. An
        # extension/perk body (fn_def=None) registers nothing; its params stay borrows.
        self._closure_cleanup: Dict[str, List[tuple[int, ir.AllocaInstr]]] = {}

        # FFI no-leak registry: per-scope marshalled C strings to free at scope exit.
        #
        # The discipline is what makes it exactly one free per runtime path: an early-exit
        # path emits frees into its own terminating block WITHOUT mutating the registry, and
        # `pop_scope` is the ONLY thing that removes entries. Every exit block is mutually
        # exclusive at runtime, so no path frees twice.
        self._cstr_cleanup: List[List[ir.Value]] = []

        # A capturing closure created inline as a call argument is bound to no local, so it
        # has no owner in _closure_cleanup and the caller's scope frees its env (#123).
        # Value-keyed, and it follows _cstr_cleanup's mutual-exclusion discipline.
        self._closure_temp_cleanup: List[List[ir.Value]] = []

        # String-value RAII (#145): freed at scope exit through the owned bit, so a literal
        # (owned=0) frees to a no-op. Stacked, and move-tracked so a value with a new owner
        # is skipped. An extension/perk body registers nothing -- its string params stay
        # borrows with a cleared owned bit.
        self._string_cleanup: Dict[str, List[tuple[int, ir.AllocaInstr]]] = {}

    @staticmethod
    def _stack_peek_slot(reg: Dict[str, List], name: str) -> Optional[ir.AllocaInstr]:
        """Return the innermost registered slot for `name` in a stacked cleanup registry."""
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

    def _drain_registry_at_depth(self, registry: Dict[str, List], current_vars,
                                 emit_free) -> None:
        """Drain each current-scope binding's top registry entry on the fall-through exit."""
        if not registry:
            return
        block = self.codegen.builder.block if self.codegen.builder is not None else None
        block_live = block is not None and not block.is_terminated
        for var_name in current_vars:
            entries = registry.get(var_name)
            if entries and entries[-1][0] == self._scope_depth:
                entry = entries[-1]
                if block_live:
                    self.codegen.moves.emit_free_unless_moved(
                        entry[-1], lambda v=var_name, e=entry: emit_free(v, e))
                self._stack_pop_at_depth(registry, var_name, self._scope_depth)

    def push_scope(self) -> None:
        """Push a new lexical scope onto the scope stack."""
        self._scope_depth += 1
        self._scope_vars.append(set())
        self._cstr_cleanup.append([])
        self._closure_temp_cleanup.append([])

        if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
            self.codegen.dynamic_arrays.push_scope()

    def pop_scope(self) -> None:
        """Pop the current lexical scope from the scope stack."""
        if self._scope_depth < 0:
            raise IndexError("No scopes to pop")

        current_vars = self._scope_vars[self._scope_depth]

        # Drain the three stacked registries on the fall-through exit. If the block already
        # terminated, an early return emitted the frees on that path, so skip emission but
        # still drain the tracking -- emitting would append a stray free after the
        # terminator. Every exit path frees on its own mutually-exclusive block (#59/#60).
        if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
            self._drain_registry_at_depth(
                self._struct_cleanup, current_vars,
                lambda name, entry: self.codegen.dynamic_arrays.emit_struct_field_cleanup(
                    name, entry[1], entry[2]))
        self._drain_registry_at_depth(
            self._closure_cleanup, current_vars,
            lambda _name, entry: self._emit_closure_free(entry[-1]))
        self._drain_registry_at_depth(
            self._string_cleanup, current_vars,
            lambda _name, entry: self._emit_string_free(entry[-1]))

        for var_name in current_vars:
            if var_name in self._locals and self._locals[var_name]:
                if self._locals[var_name][-1][0] == self._scope_depth:
                    self._locals[var_name].pop()
                    if not self._locals[var_name]:
                        del self._locals[var_name]

            if var_name in self._types and self._types[var_name]:
                if self._types[var_name][-1][0] == self._scope_depth:
                    self._types[var_name].pop()
                    if not self._types[var_name]:
                        del self._types[var_name]

        # The ONLY place the per-scope cstr list is removed. An early exit emits its own
        # frees without popping, so exactly one free runs per runtime path.
        if self._cstr_cleanup:
            self._free_cstr_list(self._cstr_cleanup.pop())

        # The only place the per-scope closure-temp list is removed; an early exit emits its
        # own guarded drop without popping.
        if self._closure_temp_cleanup:
            self._free_closure_temp_list(self._closure_temp_cleanup.pop())

        self._scope_vars.pop()
        self._scope_depth -= 1

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
        """Emit a free for every live C string across all open scopes."""
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
            emit_function_value_destructor_from_value(self.codegen, fat)

    def emit_closure_temp_cleanup_all(self) -> None:
        """Emit the guarded env free for every live inline-closure temp across all open scopes.
        """
        for scope_list in self._closure_temp_cleanup:
            self._free_closure_temp_list(scope_list)

    def try_find_local_slot(self, name: str) -> Optional[ir.AllocaInstr]:
        """Local variable slot for `name`, or None if it is not a local at all."""
        if name in self._locals and self._locals[name]:
            return self._locals[name][-1][1]
        return None

    def find_local_slot(self, name: str) -> ir.AllocaInstr:
        """Find local variable slot by name in scope stack (O(1) lookup)."""
        slot = self.try_find_local_slot(name)
        if slot is not None:
            return slot
        raise_internal_error("CE0055", name=name)

    def find_semantic_type(self, name: str) -> Optional['Type']:
        """Find semantic type for a variable by name in scope stack (O(1) lookup)."""
        if name in self._types and self._types[name]:
            return self._types[name][-1][1]
        return None

    def set_semantic_type(self, name: str, semantic_ty: 'Type') -> None:
        """Register the semantic type of an already-declared local at the current scope."""
        if name not in self._types:
            self._types[name] = []
        self._types[name].append((self._scope_depth, semantic_ty))

    def _enter_local(self, name: str, ty: ir.Type, semantic_ty: Optional['Type'],
                     register_cleanup: bool) -> ir.AllocaInstr:
        """Allocate and track a local: the shared body of create_local and create_local_nostore
        (they used to hold 38 verbatim-duplicated lines; 11b).
        """
        slot = self.entry_alloca(ty, name)

        self._scope_vars[self._scope_depth].add(name)

        if name not in self._locals:
            self._locals[name] = []
        self._locals[name].append((self._scope_depth, slot))

        if semantic_ty is not None:
            if name not in self._types:
                self._types[name] = []
            self._types[name].append((self._scope_depth, semantic_ty))
            if register_cleanup:
                self.register_local_cleanup(name, semantic_ty, slot)
        return slot

    def create_local(self, name: str, ty: ir.Type, init: Optional[ir.Value] = None, semantic_ty: Optional['Type'] = None, register_cleanup: bool = True) -> ir.AllocaInstr:
        """Create local variable with optional initialization."""
        slot = self._enter_local(name, ty, semantic_ty, register_cleanup)
        if init is not None:
            if self.codegen.builder is None:
                raise_internal_error("CE0009")
            self.codegen.builder.store(init, slot)
        return slot

    def register_local_cleanup(self, name: str, semantic_ty: 'Type',
                               slot: ir.AllocaInstr) -> None:
        """Register a local in the cleanup registry its type belongs to."""
        from sushi_lang.semantics.typesys import StructType, EnumType, ArrayType, FunctionType, BuiltinType
        from sushi_lang.backend.destructors import resolve_named_type
        self.codegen.moves.arm_if_conditional(name, slot)
        semantic_ty = resolve_named_type(self.codegen, semantic_ty)
        if isinstance(semantic_ty, (StructType, EnumType)):
            # An enum local whose active variant owns heap reuses the struct-cleanup
            # registry, so both exit paths free it through emit_value_destructor. Lifting
            # CE2059 without this owner leaked every such local (#139).
            if hasattr(self.codegen, 'dynamic_arrays') and self.codegen.dynamic_arrays is not None:
                if self.codegen.dynamic_arrays.struct_needs_cleanup(semantic_ty):
                    self._struct_cleanup.setdefault(name, []).append((self._scope_depth, semantic_ty, slot))
        # A fixed array whose ELEMENTS own heap. Its storage is the alloca, so it is no
        # dynamic array and reuses the owning-value registry. ArrayType matched NO branch
        # here, so such a local was registered nowhere and never freed (#185).
        elif isinstance(semantic_ty, ArrayType):
            from sushi_lang.backend.destructors import needs_cleanup
            if needs_cleanup(semantic_ty):
                self._struct_cleanup.setdefault(name, []).append((self._scope_depth, semantic_ty, slot))
        elif isinstance(semantic_ty, FunctionType):
            self._closure_cleanup.setdefault(name, []).append((self._scope_depth, slot))
        # Track string locals for owned-bit-guarded free at scope exit (#145).
        elif semantic_ty == BuiltinType.STRING:
            self._string_cleanup.setdefault(name, []).append((self._scope_depth, slot))

    def register_owning_value(self, name: str, semantic_ty: 'Type',
                              slot: ir.AllocaInstr) -> None:
        """Give a slot that OWNS its value the registry that will free it, for any type."""
        from sushi_lang.semantics.typesys import DynamicArrayType, StructType
        from sushi_lang.backend.destructors import resolve_named_type

        resolved = resolve_named_type(self.codegen, semantic_ty)
        arrays = getattr(self.codegen, "dynamic_arrays", None)

        if isinstance(resolved, DynamicArrayType) and arrays is not None:
            arrays.register_param_array(name, resolved.base_type, slot)
            return

        self.register_local_cleanup(name, resolved, slot)

        if isinstance(resolved, StructType) and arrays is not None:
            if arrays.is_own_type(resolved):
                arrays.register_own(name, resolved, slot)
            elif arrays.is_list_type(resolved):
                arrays.register_list(name, resolved, slot)

    def create_local_nostore(self, name: str, ty: ir.Type, semantic_ty: Optional['Type'] = None,
                             register_cleanup: bool = True) -> ir.AllocaInstr:
        """Create local variable without initialization."""
        if name in self._scope_vars[self._scope_depth]:
            raise KeyError(f"duplicate local in same scope: {name}")

        return self._enter_local(name, ty, semantic_ty, register_cleanup)

    def entry_alloca(self, ty: ir.Type, name: str) -> ir.AllocaInstr:
        """Create alloca instruction in function entry block."""
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
        """Register a struct variable for RAII cleanup of its dynamic-array fields."""
        self._struct_cleanup.setdefault(name, []).append((self._scope_depth, struct_type, slot))

    def _emit_closure_free(self, slot: ir.AllocaInstr) -> None:
        """Emit the runtime-guarded env free for a function-value local (`if drop: drop(env)`)."""
        from sushi_lang.backend.destructors import emit_function_value_destructor
        emit_function_value_destructor(self.codegen, slot)

    def _emit_string_free(self, slot: ir.AllocaInstr) -> None:
        """Emit the owned-bit-guarded free for a string local (`if owned: free(data)`) (#145)."""
        from sushi_lang.backend.destructors import emit_string_destructor
        emit_string_destructor(self.codegen, slot)

    def emit_string_cleanup_all(self) -> None:
        """Emit the guarded free for every live string local across all open scopes."""
        builder = self.codegen.builder
        if builder is None or builder.block is None or builder.block.is_terminated:
            return
        for _var_name, entries in self._string_cleanup.items():
            for _depth, slot in entries:
                self.codegen.moves.emit_free_unless_moved(
                    slot, lambda s=slot: self._emit_string_free(s))

    def is_closure_registered(self, name: str) -> bool:
        """True if `name` is a registered function-value RAII owner in the current scope."""
        return name in self._closure_cleanup

    def unregister_closure_cleanup(self, name: str) -> None:
        """Drop the innermost `name` entry from function-value RAII tracking (no-op if absent).
        """
        self._stack_pop_at_depth(self._closure_cleanup, name, self._scope_depth)

    def is_string_registered(self, name: str) -> bool:
        """True if `name` is a registered owning string local in the current scope (#145)."""
        return name in self._string_cleanup

    def is_struct_registered(self, name: str) -> bool:
        """True if `name` is a registered owning-struct local (a struct with heap-owning fields
        tracked for RAII cleanup). Used to decide whether handing the local to a container that
        stores it shallowly must MOVE it, so scope exit does not double-free the shared buffer
        (#140).
        """
        return name in self._struct_cleanup

    def is_owned_local(self, name: str) -> bool:
        """True if `name` is a registered RAII owner in some current scope -- an owning
        struct/enum/fixed-array, string, closure, dynamic array, List, or Own.
        """
        if (name in self._struct_cleanup or name in self._string_cleanup
                or name in self._closure_cleanup):
            return True
        da = getattr(self.codegen, 'dynamic_arrays', None)
        if da is not None and (name in da.arrays or name in da.lists or name in da.owned_pointers):
            return True
        return False

    def unregister_string_cleanup(self, name: str) -> None:
        """Drop the innermost `name` entry from string RAII tracking (no-op if absent) (#145).
        """
        self._stack_pop_at_depth(self._string_cleanup, name, self._scope_depth)

    def mark_struct_as_moved(self, var_name: str) -> None:
        """Mark a struct variable as moved (ownership transferred)."""
        slot = self._slot_for_name(var_name)
        if slot is not None:
            self.codegen.moves.mark(slot)

    def _slot_for_name(self, name: str) -> Optional[ir.AllocaInstr]:
        """Resolve a name to its innermost binding slot, or None if it has no local slot."""
        if name in self._locals and self._locals[name]:
            return self._locals[name][-1][1]
        return None

    def is_struct_moved(self, var_name: str) -> bool:
        """Check if the innermost binding named `var_name` has been moved."""
        slot = self._slot_for_name(var_name)
        return slot is not None and self.codegen.moves.is_moved(slot)

    def reset_scope_stack(self) -> None:
        """Reset the scope stack to empty state."""
        while self._scope_depth >= 0:
            self.pop_scope()

        self._scope_vars = []
        self._scope_depth = -1
        self._locals.clear()
        self._types.clear()
        self._struct_cleanup.clear()
        self._closure_cleanup.clear()
        self._string_cleanup.clear()
        self._cstr_cleanup = []
        self._closure_temp_cleanup = []

        self.codegen.moves.reset()

    def current_scope_size(self) -> int:
        """Get the current scope stack depth."""
        return self._scope_depth + 1

    def get_current_scope_vars(self) -> Dict[str, ir.AllocaInstr]:
        """Get variables in the current (innermost) scope."""
        if self._scope_depth < 0:
            raise IndexError("No active scopes")
        result = {}
        for name in self._scope_vars[self._scope_depth]:
            if name in self._locals and self._locals[name]:
                result[name] = self._locals[name][-1][1]
        return result

    def has_variable_in_scope(self, name: str, scope_level: int = -1) -> bool:
        """Check if a variable exists in a specific scope level."""
        if scope_level < 0:
            scope_level = self._scope_depth + 1 + scope_level
        if scope_level < 0 or scope_level > self._scope_depth:
            raise IndexError(f"Invalid scope level: {scope_level}")
        return name in self._scope_vars[scope_level]

    @property
    def locals(self) -> List[Dict[str, ir.AllocaInstr]]:
        """Backward compatible access to locals (deprecated, use flat cache directly)."""
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
        """Per-scope-level view of owning-struct locals (name -> (type, slot))."""
        result: List[Dict[str, tuple['StructType', ir.AllocaInstr]]] = [
            {} for _ in range(self._scope_depth + 1)
        ]
        for name, entries in self._struct_cleanup.items():
            for depth, struct_type, slot in entries:
                if 0 <= depth < len(result):
                    result[depth][name] = (struct_type, slot)
        return result
