"""The overflow mode of a build (`--overflow`, docs/adr/0002-overflow-mode.md)."""
from __future__ import annotations

from enum import Enum


class OverflowMode(Enum):
    """What a checked integer operator does at run time with a result that its type cannot
    hold: `TRAP` stops the program, `WRAP` loses the bits that leave the type."""
    TRAP = "trap"
    WRAP = "wrap"
