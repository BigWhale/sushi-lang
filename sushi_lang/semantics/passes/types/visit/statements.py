"""The statement walk of the typecheck pass over SHIR (docs/design/ir.md 7.5).

`check_block` checks the statements of one block in the written order, each through the
table of the pass (`visit.ARMS`). The body walk starts here, and an arm that holds a block
calls it again.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.semantics.passes.types.visit import check
from sushi_lang.semantics.shir.nodes import ShirBlock

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_block(validator: TypeValidator, block: ShirBlock) -> None:
    """Check each statement of `block`, in the written order."""
    for statement in block.statements:
        check(validator, statement)
