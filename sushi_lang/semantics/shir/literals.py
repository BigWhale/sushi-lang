"""A negated integer literal is one value with its sign (docs/design/ir.md 8.5)."""
from __future__ import annotations

from typing import Optional

from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.operators import NEG_OP


def negated_int_literal(node: object) -> Optional[e.Lit]:
    """The integer literal of `-N`, or None for any other node."""
    if (isinstance(node, e.Unary) and node.op == NEG_OP and isinstance(node.operand, e.Lit)
            and isinstance(node.operand.value, e.IntConst)):
        return node.operand
    return None
