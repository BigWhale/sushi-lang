"""The marked field take: `nom s.field` hands one field out of a value the body owns.

A take is a `Take` in a `let` or a `return`, or a `nom` argument on a field (an `Arg` with
an `Owned` marker), docs/design/ir.md 7.4. Anything else that carries the marker is the
value as written: the marker is inert there.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.semantics.ownership import Ownership, Provenance, TypeClass
from sushi_lang.semantics.shir import exprs as e

from .reads import read_type

if TYPE_CHECKING:
    from . import BorrowChecker


def field_take(checker: 'BorrowChecker', place: e.ShirExpr) -> Optional[e.LocalRef]:
    """The local that a marked take of `place` spends, or None when it is no take.

    Three conditions, and each is what keeps the take sound. The access is ONE step off a
    bare NAME, so there is a local to spend and an intermediate field cannot be read
    through. The name is a local this function OWNS, so no second owner appears behind
    the caller's back -- a borrow falls through to CE2411 like any other read through a
    live owner. And the field OWNS something: a take of a field that owns nothing has
    nothing to hand over, so the marker is an ordinary copy there.
    """
    from .consume import name_provenance
    if not (isinstance(place, e.Field) and isinstance(place.base, e.LocalRef)):
        return None
    root = place.base
    if checker.borrow_state.get(root.local) is None:
        return None
    if name_provenance(checker, root.local) is not Provenance.OWNED:
        return None
    if checker.types.type_class(read_type(checker, place)) is not TypeClass.MOVE:
        return None
    return root


def take(checker: 'BorrowChecker', node: e.ShirExpr, root: e.LocalRef,
         place: e.ShirExpr) -> Ownership:
    """The take `node` of the field `place` of `root`: a `move` row, and the spend of the
    WHOLE receiver, which is CE2433's rule read on a struct.

    What suppresses the receiver's own free is the whole value and not one field, so a
    take leaves every other owning field with no owner at all. Spending the receiver is
    what gives them one: SLIR lowering drops them at the take. The spend is a consuming
    use of the local, so the take answers CE2401 for a live borrow and arms a drop flag
    inside a branch exactly as `f(nom s)` does.
    """
    from .consume import consume_named, decide
    decision = decide(checker, node, Provenance.OWNED, TypeClass.MOVE)
    consume_named(checker, root, Provenance.OWNED, place.loc, write=False)
    return decision
