"""The name of a local that the compiler makes and the user never writes (#1001).

`NAME` in the grammar is `CNAME`, `("_"|LETTER) ("_"|LETTER|DIGIT)*`, so a name that
starts with `__` is a legal identifier. A hidden local must contain a character that
`CNAME` refuses, or a user variable of the same spelling collides with it. `HIDDEN_MARK`
is that character, and every hidden local name starts with it. LLVM quotes such a name.
"""
from __future__ import annotations

from typing import Optional

HIDDEN_MARK = "#"


def hidden_name(kind: str, n: Optional[int] = None) -> str:
    """`#<kind><n>`, or `#<kind>` when the name needs no number."""
    return f"{HIDDEN_MARK}{kind}" if n is None else f"{HIDDEN_MARK}{kind}{n}"
