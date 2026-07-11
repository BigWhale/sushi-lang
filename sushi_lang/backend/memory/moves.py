"""Unified move tracking for RAII cleanup.

A single source of truth for "which owning bindings have had their ownership
moved away" and must therefore be skipped by scope-exit destructors. This
replaces the three formerly parallel mechanisms (per-descriptor `moved` flags on
dynamic arrays and lists, and `ScopeManager.moved_structs`), so every owning type
- dynamic arrays, List<T>, structs with owned fields, and Own<T> - shares one
consistent notion of a move.

Keyed by SLOT IDENTITY (the `ir.AllocaInstr` backing a binding), not by name.
Every owning binding has exactly one canonical slot (registered in
`ScopeManager._locals`), so two same-named bindings in nested or sibling scopes -
a shadow, or a name reused after an earlier binding was moved - are distinct keys.
A flat name-keyed set could not tell them apart and would poison the second
binding with the first's move (see tests/memory/test_run_move_then_reuse_name).
The tracker is still reset at each function boundary (see
`ScopeManager.reset_scope_stack`); slots from a finished function never recur.
"""

from __future__ import annotations

from typing import Set, TYPE_CHECKING

if TYPE_CHECKING:
    from llvmlite import ir


class MoveTracker:
    """Tracks bindings whose ownership has been moved (skip RAII cleanup).

    Keyed by the binding's slot (`ir.AllocaInstr`); callers resolve a name to its
    slot before marking or checking.
    """

    def __init__(self) -> None:
        self._moved: Set['ir.Instruction'] = set()

    def mark(self, slot: 'ir.Instruction') -> None:
        """Mark a binding (by its slot) as moved; RAII cleanup skips it."""
        self._moved.add(slot)

    def is_moved(self, slot: 'ir.Instruction') -> bool:
        """Return True if the binding backed by this slot has been moved."""
        return slot in self._moved

    def reset(self) -> None:
        """Clear all move state (called at each function boundary)."""
        self._moved.clear()
