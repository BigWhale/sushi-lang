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


def pack_element_name(pack_param: str, index: int) -> str:
    """The local of element `index` of the variadic pack parameter `pack_param` (#1015)."""
    return hidden_name(f"pack_{pack_param}_", index)


def expand_copy_local_name(local: str, copy_number: int) -> str:
    """The local `local` of the unrolled `expand` copy `copy_number` (#1015).

    `copy_number` is unique over the instance: nested and sibling copies draw from
    one counter (#1018).
    """
    return hidden_name(f"{local}_x", copy_number)
