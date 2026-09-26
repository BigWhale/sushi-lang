"""CE2434 has ONE home: the AST builder, for the direct and the nested form (#791 row 4).

The builder refused `Own(nom x)` while it built the pattern, and the borrow pass refused
`Own(Inner.Word(nom s))` with a second emitter and a different help line. One rule, two
homes. The builder walks the whole pattern tree, so it refuses both forms before any
pass runs, and the borrow pass has no emitter for the code.
"""
from __future__ import annotations

from pathlib import Path



ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"







def test_one_emit_site():
    sites = []
    for path in sorted(ROOT.rglob("*.py")):
        if "internals/errors" in str(path):
            continue
        if "CE2434" in path.read_text():
            sites.append(str(path.relative_to(ROOT)))
    assert sites == ["semantics/ast_builder/statements/matching.py"], sites






