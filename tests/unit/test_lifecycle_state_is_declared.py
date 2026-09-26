"""The lifecycle state of the codegen is declared in its constructor, all four halves.

The destructor cache and stack were declared in `LLVMCodegen.__init__`, and the clone
twins were made at their first use. A reader of state that a first use makes must guard
the read, so four sites read the clone state through `getattr(codegen, ..., None)`, and
`build_module_single_unit` restored the clone stack to an empty list where it restored the
destructor stack from its saved value (#836).
"""
from __future__ import annotations

import re
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"





def test_no_backend_module_reads_the_lifecycle_state_through_getattr():
    pattern = re.compile(r"getattr\([^)]*['\"]_(clone|dtor)_(funcs|inprogress)['\"]")
    hits = [
        f"{path.relative_to(BACKEND)}:{lineno}"
        for path in sorted(BACKEND.rglob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []
