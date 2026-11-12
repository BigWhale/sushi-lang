"""The SLIR verifier: the drop of a `Global` (docs/design/ir.md 8.4, 8.13).

A `Global` always holds a value and no exit drops it, so its one `Drop` is the drop of the
old value of a rebind, and the one `Drop` of a part of it is the drop of the old part of a
field write or an element write (8.10, rule 6). Each stands directly before an `Assign`
into the same place, of a type that owns. A `DropIfSet` of a `Global` is refused: a
`Global` has no flag. The dataflow does not read a `Global`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import SlirBlock
from sushi_lang.semantics.slir.printer import show
from sushi_lang.semantics.slir.verify_rules import GLOBAL_DROP, OWNING_DROP
from sushi_lang.semantics.typesys import owns_resource

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.verify import Check


def drops_a_global(stmt: st.SlirStatement) -> bool:
    """Whether `stmt` is a `Drop` or a `DropIfSet` of a `Global` or of a part of one."""
    return isinstance(stmt, (st.Drop, st.DropIfSet)) and isinstance(p.root_of(stmt.place),
                                                                   p.Global)


def global_drops(check: Check, block: SlirBlock) -> None:
    """Each drop of a `Global` in `block`: a `Drop` of an owning type, and an `Assign` into
    the same place as the next statement."""
    statements = block.statements
    for at, stmt in enumerate(statements):
        if not (isinstance(stmt, (st.Drop, st.DropIfSet)) and drops_a_global(stmt)):
            continue
        if isinstance(stmt, st.DropIfSet):
            check.fail(GLOBAL_DROP, f"{show(stmt.place)} is a global, which has no flag",
                       stmt.span)
        ty = check.operand_type(p.Copy(stmt.place), stmt.span)
        if ty is None or not owns_resource(ty, check.drops):
            check.fail(OWNING_DROP, f"{show(stmt.place)} has the type {display_type(ty)}, "
                                    f"which owns nothing", stmt.span)
        after = statements[at + 1] if at + 1 < len(statements) else None
        if not (isinstance(after, st.Assign) and after.place == stmt.place):
            check.fail(GLOBAL_DROP, f"{show(stmt.place)} drops, and the next statement does "
                                    f"not store into it", stmt.span)
