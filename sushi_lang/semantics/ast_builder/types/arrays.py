"""Parser for array types (fixed and dynamic)."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of
from sushi_lang.semantics.ast_builder.utils.tree_navigation import unhandled
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, TYPE_NODE_NAMES

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_array_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[ArrayType]:
    """Parse fixed-size array type (array_t)."""
    base_type_node = None
    size_node = None

    for child in node.children:
        if isinstance(child, Tree) and child.data == "array_size":
            size_node = child
        elif isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t" or child.data == "reference_t"):
            base_type_node = child

    if base_type_node is None or size_node is None:
        return None

    base_type = ast_builder._parse_type(base_type_node)
    if base_type is None:
        return None

    size = _array_size(size_node, ast_builder)
    return ArrayType(base_type=base_type, size=size)


def _array_size(size_node: Tree, ast_builder: 'ASTBuilder') -> int:
    """The element count of a fixed array. Raises CE2099 when it is not one.

    An array size is the second consumer of a numeric token, so it goes through the
    same seam an expression does: every base, and the underscore rule (CE6006) and
    the C-octal rule (CE2071) with it. Reading `int(token.value)` here would let
    Python's own rules decide, and they are looser than ours.

    A NAME is a constant of THIS unit, read from the constants the builder has
    already built. The size has to be a number before the type exists, which is
    long before any pass has a program-wide constant table -- so a constant next
    door is reachable as a value and not as a size.
    """
    from sushi_lang.semantics.ast_builder.expressions.literals import expr_from_token
    from sushi_lang.semantics.ast import IntLit, Name

    token = next((child for child in size_node.children if isinstance(child, Token)), None)
    if token is None:
        unhandled(size_node)

    size_expr = expr_from_token(token, ast_builder)

    if isinstance(size_expr, Name):
        value = ast_builder.integer_constant(size_expr.id)
        if value is None:
            _reject(token, "no integer constant of this unit is named "
                           f"'{size_expr.id}'")
    elif isinstance(size_expr, IntLit):
        value = size_expr.value
    else:
        _reject(token, "a size is an integer")

    if value < 1:
        _reject(token, "an array holds at least one element")
    return value


def _reject(token: Token, reason: str) -> None:
    """Raise CE2099 for a size that cannot count elements."""
    raise SyntaxDiagnostic("CE2099", span=span_of(token),
                           size=str(token.value), reason=reason).help(
        "write a positive integer in any base (256, 0x100, 0b1_0000_0000) or the "
        "name of an integer constant declared in this unit")


def parse_dynamic_array_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[DynamicArrayType]:
    """Parse dynamic array type (dynamic_array_t)."""
    base_type_node = None

    for child in node.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t" or child.data == "reference_t"):
            base_type_node = child
            break

    if base_type_node is None:
        return None

    base_type = ast_builder._parse_type(base_type_node)
    if base_type is None:
        return None

    return DynamicArrayType(base_type=base_type)
