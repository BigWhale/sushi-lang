"""Variable scope management with O(1) lookup and RAII cleanup."""
from __future__ import annotations
from typing import Callable, Dict, Iterator, List, Optional, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.memory import allocas
from sushi_lang.semantics.ownership import is_own_type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type, StructType


# The two container shapes that carry their own scope-exit registry. `DynamicArrayManager`
# destroys a List through `lists` and an Own through `owned_pointers`, so the general
# struct registry must leave them alone or each is destroyed twice. A `HashMap` is absent
# on purpose: it has no registry of its own and IS destroyed through the struct registry.
_DEDICATED_REGISTRY_BASES = ("List", "Own")


def _has_dedicated_registry(ty: 'Type') -> bool:
    """Does this type already have a scope-exit registry that is not the struct one?"""
    return is_instance_of(ty, *_DEDICATED_REGISTRY_BASES)


class ScopeManager:
    """Manages variable scoping and alloca tracking for LLVM code generation."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize scope manager with reference to main codegen instance."""
        self.codegen = codegen

        self._scope_depth: int = -1

        # An ORDERED set of the names declared at each depth: a dict keeps insertion
        # order, which is declaration order, and `pop_scope` destroys the reverse of it.
        # A plain `set` here made the destruction order Python's hash order.
        self._scope_vars: List[Dict[str, None]] = []

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
    def _stack_pop_at_depth(reg: Dict[str, List], name: str, depth: int) -> None:
        """Drop `name`'s top entry from a stacked cleanup registry if it is at `depth`."""
        entries = reg.get(name)
        if entries and entries[-1][0] == depth:
            entries.pop()
            if not entries:
                del reg[name]

    @staticmethod
    def _stack_entry_at_depth(reg: Dict[str, List], name: str, depth: int):
        """`name`'s entry registered at `depth` in a stacked cleanup registry, or None."""
        for entry in reversed(reg.get(name, ())):
            if entry[0] == depth:
                return entry
        return None

    @property
    def depth(self) -> int:
        """The depth of the innermost open scope; -1 when no scope is open."""
        return self._scope_depth

    def declare_scope_name(self, name: str) -> None:
        """Record `name` in the innermost scope, in declaration order, if it is new there."""
        if self._scope_depth >= 0:
            self._scope_vars[self._scope_depth].setdefault(name)

    def _block_live(self) -> bool:
        """Is there an open, unterminated block to emit a destructor into?"""
        builder = self.codegen.builder
        return builder is not None and builder.block is not None and not builder.block.is_terminated

    def _exit_actions(self, name: str, depth: int
                      ) -> Iterator[tuple[ir.AllocaInstr, Callable[[], None]]]:
        """(slot, emit destructor) for each registry that holds `name` at `depth`."""
        from sushi_lang.backend.destructors import emit_value_destructor
        struct = self._stack_entry_at_depth(self._struct_cleanup, name, depth)
        if struct is not None:
            _depth, ty, slot = struct
            yield slot, lambda: emit_value_destructor(self.codegen, slot, ty)
        closure = self._stack_entry_at_depth(self._closure_cleanup, name, depth)
        if closure is not None:
            yield closure[-1], lambda: self._emit_closure_free(closure[-1])
        string = self._stack_entry_at_depth(self._string_cleanup, name, depth)
        if string is not None:
            yield string[-1], lambda: self._emit_string_free(string[-1])
        arrays = getattr(self.codegen, "dynamic_arrays", None)
        if arrays is not None:
            yield from arrays.exit_actions(name, depth)

    def _emit_scope_exit(self, depth: int) -> None:
        """Emit the destructors of the scope at `depth`, and drain nothing.

        One walk over the scope's names, NEWEST FIRST, and each name goes to the registry
        that holds it. The order a scope destroys in is a property of the SCOPE; a walk
        per registry made it a property of which registry a type happens to land in.
        Reverse declaration order is the RAII rule: the last binding opened is the first
        closed, so a `BufWriter` flushes into a `File` that is still open.
        """
        for name in reversed(self._scope_vars[depth]):
            for slot, emit_free in self._exit_actions(name, depth):
                self.codegen.moves.emit_free_unless_moved(slot, emit_free)
        self._free_cstr_list(self._cstr_cleanup[depth])
        self._free_closure_temp_list(self._closure_temp_cleanup[depth])

    def emit_exit_cleanup(self, lowest_depth: int) -> None:
        """Emit the destructors of every scope from the innermost down to `lowest_depth`.

        The one sweep for the early exits: `lowest_depth` 0 for a `return` or a `??`, the
        loop's first scope for a `break` or a `continue`. It emits and does not drain:
        every exit path frees on its own block, and `pop_scope` alone removes the entries.
        """
        if not self._block_live():
            return
        for depth in range(self._scope_depth, lowest_depth - 1, -1):
            self._emit_scope_exit(depth)

    def push_scope(self) -> None:
        """Push a new lexical scope onto the scope stack."""
        self._scope_depth += 1
        self._scope_vars.append({})
        self._cstr_cleanup.append([])
        self._closure_temp_cleanup.append([])

    def pop_scope(self) -> None:
        """Destroy the innermost scope's locals on the fall-through path, then drain it.

        If the block already terminated, an early exit emitted the frees on that path, so
        emit nothing but still drain the tracking (#59/#60).
        """
        if self._scope_depth < 0:
            raise_internal_error("CE0016")

        depth = self._scope_depth
        current_vars = self._scope_vars[depth]
        if self._block_live():
            self._emit_scope_exit(depth)

        arrays = getattr(self.codegen, "dynamic_arrays", None)
        for var_name in current_vars:
            for registry in (self._struct_cleanup, self._closure_cleanup, self._string_cleanup,
                             self._locals, self._types):
                self._stack_pop_at_depth(registry, var_name, depth)
            if arrays is not None:
                arrays.drain(var_name, depth)

        self._cstr_cleanup.pop()
        self._closure_temp_cleanup.pop()
        self._scope_vars.pop()
        self._scope_depth -= 1

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
        self.track_local(name, slot, semantic_ty)
        if semantic_ty is not None and register_cleanup:
            self.register_local_cleanup(name, semantic_ty, slot)
        return slot

    def track_local(self, name: str, slot: ir.Instruction,
                    semantic_ty: Optional['Type']) -> None:
        """Track an EXISTING slot as the local `name` of the innermost scope.

        The tracking half of `_enter_local`, for a slot made elsewhere: a parameter slot,
        and the descriptor slot of a dynamic-array local. It registers no cleanup.
        """
        self._scope_vars[self._scope_depth].setdefault(name)
        self._locals.setdefault(name, []).append((self._scope_depth, slot))
        if semantic_ty is not None:
            self._types.setdefault(name, []).append((self._scope_depth, semantic_ty))

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
        from sushi_lang.backend.destructors import needs_cleanup, resolve_named_type
        self.codegen.moves.arm_if_conditional(name, slot)
        semantic_ty = resolve_named_type(self.codegen, semantic_ty)
        if isinstance(semantic_ty, (StructType, EnumType)):
            # An enum local whose active variant owns heap reuses the struct-cleanup
            # registry, so both exit paths free it through emit_value_destructor. Lifting
            # CE2059 without this owner leaked every such local (#139).
            #
            # A `List@(T)` and an `Own@(T)` are the exception, and they are why this asks
            # two questions rather than one: each has a REGISTRY OF ITS OWN
            # (`dynamic_arrays.lists`, `.owned_pointers`) that already destroys it at
            # scope exit. Registering one here as well destroys it twice. A `HashMap` has
            # no registry of its own and belongs here, which is why the test is the two
            # names and not `CONTAINER_BASES`.
            if not _has_dedicated_registry(semantic_ty) and needs_cleanup(self.codegen, semantic_ty):
                self._struct_cleanup.setdefault(name, []).append((self._scope_depth, semantic_ty, slot))
        # A fixed array whose ELEMENTS own heap. Its storage is the alloca, so it is no
        # dynamic array and reuses the owning-value registry. ArrayType matched NO branch
        # here, so such a local was registered nowhere and never freed (#185).
        elif isinstance(semantic_ty, ArrayType):
            if needs_cleanup(self.codegen, semantic_ty):
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
            if is_own_type(resolved):
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
        """Make a stack slot in the entry block of the function being emitted."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        return allocas.entry_alloca(self.codegen.builder, ty, name)

    def _emit_closure_free(self, slot: ir.AllocaInstr) -> None:
        """Emit the runtime-guarded env free for a function-value local (`if drop: drop(env)`)."""
        from sushi_lang.backend.destructors import emit_function_value_destructor
        emit_function_value_destructor(self.codegen, slot)

    def _emit_string_free(self, slot: ir.AllocaInstr) -> None:
        """Emit the owned-bit-guarded free for a string local (`if owned: free(data)`) (#145)."""
        from sushi_lang.backend.destructors import emit_string_destructor
        emit_string_destructor(self.codegen, slot)

    def mark_struct_as_moved(self, var_name: str) -> None:
        """Mark a struct variable as moved (ownership transferred)."""
        slot = self.try_find_local_slot(var_name)
        if slot is not None:
            self.codegen.moves.mark(slot)

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
