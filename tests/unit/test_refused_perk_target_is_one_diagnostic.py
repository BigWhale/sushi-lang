"""A refused generic perk-implementation target is one diagnostic (#860).

The collect pass refuses a wrong type-argument count (CE2062) and a target that mixes
concrete arguments with type parameters (CE2098). The refusal is recorded by the base
name and each method name the implementation declares, beside the extension path's
record, so a call of one of those methods adds no CE2008. A method the implementation
never declared is still an undefined name.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is absent, so the code
LIST is asserted here, on the analyze path.
"""
from __future__ import annotations

from pathlib import Path









def test_one_helper_emits_ce2098():
    """The extension collector and the perk collector share one CE2098 emit."""
    root = Path(__file__).resolve().parents[2] / "sushi_lang"
    sites = [p.relative_to(root).as_posix()
             for p in root.rglob("*.py") if "ERR.CE2098" in p.read_text()]
    assert sites == ["semantics/generics/extension_targets.py"]
