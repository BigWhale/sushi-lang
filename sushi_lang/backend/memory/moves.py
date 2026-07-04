"""Unified move tracking for RAII cleanup.

A single source of truth for "which named variables have had their ownership
moved away" and must therefore be skipped by scope-exit destructors. This
replaces the three formerly parallel mechanisms (per-descriptor `moved` flags on
dynamic arrays and lists, and `ScopeManager.moved_structs`), so every owning type
- dynamic arrays, List<T>, structs with owned fields, and Own<T> - shares one
consistent notion of a move.

Semantics are flat/by-name and per-function: the tracker is reset at each function
boundary (see `ScopeManager.reset_scope_stack`). Name shadowing across nested
scopes is not distinguished - a pre-existing limitation inherited from the
mechanisms this consolidates.
"""

from __future__ import annotations

from typing import Set


class MoveTracker:
    """Tracks variables whose ownership has been moved (skip RAII cleanup)."""

    def __init__(self) -> None:
        self._moved: Set[str] = set()

    def mark(self, name: str) -> None:
        """Mark a variable as moved (ownership transferred); RAII cleanup skips it."""
        self._moved.add(name)

    def is_moved(self, name: str) -> bool:
        """Return True if the variable has been moved."""
        return name in self._moved

    def reset(self) -> None:
        """Clear all move state (called at each function boundary)."""
        self._moved.clear()
