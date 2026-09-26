"""The answer to "is this name a reference" belongs to the open scope, not to the name (#941)."""
from __future__ import annotations

from pathlib import Path



_BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"












def test_the_name_readers_do_not_read_the_flat_table():
    """The two name readers ask the scope manager; the flat per-function table has no scope."""
    for rel in ("expressions/names.py", "expressions/type_utils.py"):
        source = (_BACKEND / rel).read_text()
        assert "variable_types" not in source, f"{rel} reads the flat `variable_types` table"
