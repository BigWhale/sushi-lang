"""One rule decides CE2510, and it covers the bitwise operators too.

`& | ^` used to accept two widths where `+` refused them, and the backend then
widened or truncated the right operand on its own -- so `low | wide` (u8, u32)
compiled clean and printed a value with the high bits cut off (#438). The rule now
lives in one function that arithmetic, comparison and the bitwise operators all
call. These cases hold the collapse in place: the bitwise half must stay, the
arithmetic half must not have moved, and a shift count must stay free.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]






def test_ce2510_is_emitted_from_one_place():
    """The rule is one function. A second emitter is a second rule that will drift."""
    sites = subprocess.run(
        ["grep", "-rn", "ERR.CE2510", "sushi_lang"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    assert len(sites) == 1, "\n".join(sites)
    assert "passes/types/expressions.py" in sites[0], sites[0]












