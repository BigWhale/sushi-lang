"""Unified move tracking for RAII cleanup."""

from __future__ import annotations

from typing import Dict, Set, TYPE_CHECKING

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class MoveTracker:
    """Tracks bindings whose ownership has been moved (skip RAII cleanup).

    Two regimes (#414). A binding whose every move dominates its scope exit is
    tracked STATICALLY: `mark` records the slot and every free site skips it. A
    binding the borrow pass stamped as CONDITIONALLY moved carries a runtime i1 drop
    flag instead: `arm` allocates it in the entry block and stores true at the
    declaration (re-arming per loop iteration), `mark` stores false at the move
    site, and `emit_free_unless_moved` wraps the free in an `if (flag)`.
    """

    def __init__(self, codegen: 'LLVMCodegen' = None) -> None:
        self.codegen = codegen
        self._moved: Set['ir.Instruction'] = set()
        self._flags: Dict['ir.Instruction', 'ir.AllocaInstr'] = {}

    def arm_if_conditional(self, name: str, slot: 'ir.Instruction') -> None:
        """Arm a drop flag iff the borrow pass stamped `name` as conditionally moved (#414)."""
        if name in getattr(self.codegen, 'current_conditional_moves', ()):
            self.arm(slot)

    def arm(self, slot: 'ir.Instruction') -> None:
        """Give `slot` a drop flag (entry-block alloca) and set it live, at the
        current builder position -- the binding's declaration."""
        flag = self._flags.get(slot)
        if flag is None:
            flag = self.codegen.alloca_builder.alloca(ir.IntType(1), name="drop_flag")
            self._flags[slot] = flag
        self.codegen.builder.store(ir.Constant(ir.IntType(1), 1), flag)

    def mark(self, slot: 'ir.Instruction') -> None:
        """Mark a binding (by its slot) as moved; RAII cleanup skips it."""
        flag = self._flags.get(slot)
        if flag is not None:
            self.codegen.builder.store(ir.Constant(ir.IntType(1), 0), flag)
            return
        self._moved.add(slot)

    def is_moved(self, slot: 'ir.Instruction') -> bool:
        """True if the binding is STATICALLY moved. A flagged binding answers False:
        whether it still owns its value is a runtime fact, and the free sites ask
        through `emit_free_unless_moved`."""
        return slot in self._moved

    def emit_free_unless_moved(self, slot: 'ir.Instruction', emit) -> None:
        """Emit `emit()` unless the binding no longer owns its value.

        THE free gate: statically moved -> nothing; flagged -> the free runs under
        `if (drop_flag)`; otherwise the free runs unconditionally.
        """
        if slot in self._moved:
            return
        flag = self._flags.get(slot)
        if flag is None:
            emit()
            return
        live = self.codegen.builder.load(flag, name="drop_live")
        with self.codegen.builder.if_then(live):
            emit()

    def unmark(self, slot: 'ir.Instruction') -> None:
        """Clear a binding's moved flag: a rebind RE-INITIALIZES it (F5, 2026-08-14)."""
        flag = self._flags.get(slot)
        if flag is not None:
            self.codegen.builder.store(ir.Constant(ir.IntType(1), 1), flag)
        self._moved.discard(slot)

    def reset(self) -> None:
        """Clear all move state (called at each function boundary)."""
        self._moved.clear()
        self._flags.clear()
