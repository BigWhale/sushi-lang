"""What the typecheck pass keeps while it validates one outermost expression.

The time to check a nest of operators must be near linear in its depth. Two things
make this true, and both are here:

- The pass keeps the TYPE of each operator node that it validated, until the
  outermost expression is done. The node above reads the kept type. It does not infer
  the whole operand again. A propagation can change the type of a validated node (for
  example, a literal under a negation gets the type of its sibling). Thus a
  propagation removes the kept type of each node under the value that it stamps.
- The OVERFLOW check of a nest runs one time, at its top. An overflow-checked node
  does the check for each operand that has the type of the node. A node whose check is
  done above does the same for its own operands. The top folds the nest one time and
  reports each operation of the nest that leaves its type. The pass never keeps a
  fold, because a fold stamps the AST. Only the reference to a named constant can be
  memoized. The check runs in `ExpressionValidator.visit`, because each validated
  expression goes through it: from `validate_expression`, and from an arm that visits
  its child (a tuple element, an interpolation hole, a field receiver).
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Optional, Set, Tuple, TypeGuard, Union

from sushi_lang.semantics.ast import BinaryOp, UnaryOp
from sushi_lang.semantics.ast_walk import walk_nodes
from .expressions import ARITHMETIC_OPS
from .propagation import same_type_operands

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Expr
    from sushi_lang.semantics.typesys import Type


def is_overflow_checked(node: 'Expr') -> TypeGuard[Union[BinaryOp, UnaryOp]]:
    """Whether the node is an operation that CE2077 checks."""
    if isinstance(node, BinaryOp):
        return node.op in ARITHMETIC_OPS
    return isinstance(node, UnaryOp) and node.op == "neg"


def nest_under(top: 'Expr') -> Set[int]:
    """The identity of each node whose overflow check the top of a nest does."""
    found: Set[int] = set()
    pending = [top]
    while pending:
        node = pending.pop()
        found.add(id(node))
        pending.extend(same_type_operands(node))
    return found


class OperatorNests:
    """The kept types and the handed checks of one outermost expression."""

    def __init__(self) -> None:
        self._depth = 0
        self._types: Dict[int, Tuple['Expr', Optional['Type']]] = {}
        self._handed: Dict[int, 'Expr'] = {}

    def hold(self) -> None:
        """Start one validation. The kept types live until the outermost one ends."""
        self._depth += 1

    def enter(self, expr: 'Expr') -> bool:
        """Start the visit of `expr`. Answer True when a node above does its overflow check."""
        self.hold()
        self._types.pop(id(expr), None)
        handed = self._handed.pop(id(expr), None) is expr
        if handed or is_overflow_checked(expr):
            for operand in same_type_operands(expr):
                self._handed[id(operand)] = operand
        return handed

    def leave(self) -> None:
        """End one validation or one visit. The outermost one forgets everything."""
        self._depth -= 1
        if self._depth == 0:
            self._types.clear()
            self._handed.clear()

    def keep(self, expr: 'Expr', expr_type: Optional['Type']) -> None:
        """Keep the type of a validated operator node."""
        if isinstance(expr, (BinaryOp, UnaryOp)):
            self._types[id(expr)] = (expr, expr_type)

    def kept(self, expr: 'Expr') -> Tuple[bool, Optional['Type']]:
        """The kept type of `expr`, and whether there is one."""
        entry = self._types.get(id(expr))
        if entry is None or entry[0] is not expr:
            return False, None
        return True, entry[1]

    def forget_types_under(self, root: 'Expr') -> None:
        """A propagation stamps `root`. A kept type of a node under it can be wrong now."""
        if not self._types:
            return
        types = self._types

        def forget(node) -> bool:
            types.pop(id(node), None)
            return True

        walk_nodes(root, forget)
