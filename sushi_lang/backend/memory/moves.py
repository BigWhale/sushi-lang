"""Unified move tracking for RAII cleanup."""

from __future__ import annotations

from typing import Set, TYPE_CHECKING

if TYPE_CHECKING:
    from llvmlite import ir


class MoveTracker:
    """Tracks bindings whose ownership has been moved (skip RAII cleanup)."""

    def __init__(self) -> None:
        self._moved: Set['ir.Instruction'] = set()

    def mark(self, slot: 'ir.Instruction') -> None:
        """Mark a binding (by its slot) as moved; RAII cleanup skips it."""
        self._moved.add(slot)

    def is_moved(self, slot: 'ir.Instruction') -> bool:
        """Return True if the binding backed by this slot has been moved."""
        return slot in self._moved

    def unmark(self, slot: 'ir.Instruction') -> None:
        """Clear a binding's moved flag: a rebind RE-INITIALIZES it (F5, 2026-08-14)."""
        self._moved.discard(slot)

    def reset(self) -> None:
        """Clear all move state (called at each function boundary)."""
        self._moved.clear()
