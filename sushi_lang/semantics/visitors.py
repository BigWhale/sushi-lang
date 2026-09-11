"""AST Visitor Pattern implementation for the Sushi language compiler."""
from __future__ import annotations
from abc import ABC
from typing import TypeVar, Generic

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import Node, Block
from sushi_lang.semantics.ast import (
    Let, Rebind, ExprStmt, Return, Print, PrintLn, If, While, Foreach, Match, Break, Continue,
    Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess,
    UnaryOp, BinaryOp, Call, MethodCall, DotCall, MemberAccess, EnumConstructor,
    DynamicArrayNew, DynamicArrayFrom, CastExpr, Borrow, TryExpr, RangeExpr, Spread,
    Expand, Lambda
)

T = TypeVar('T')

# A node kind a PARENT arm reads inside itself and never hands to `visit()`. Named so the
# gate can tell a deliberate leaf from a forgotten arm, the way `ast_walk.TERMINAL_NODES`
# does for the type walk. A member gets no `visit_` arm and never reaches the backstop.
WALKED_IN_PARENT = frozenset({
    "ArrayElement",      # `visit_arrayliteral` reads .value and .count
    "MatchArm",          # `visit_match` hands the arm's BODY over; the pattern binds names
    "Pattern", "LiteralPattern", "WildcardPattern",
    "OwnPattern", "RefBinding", "NomBinding",
})

class NodeVisitor(ABC, Generic[T]):
    """Abstract base class for AST node visitors."""

    def visit(self, node: Node) -> T:
        """Main entry point for visiting a node."""
        method_name = f'visit_{type(node).__name__.lower()}'
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node: Node) -> T:
        """Default visitor that raises an error. Subclasses should either implement specific
        visit_* methods or override this to provide default behavior.
        """
        raise NotImplementedError(
            f"Visitor {self.__class__.__name__} doesn't handle {type(node).__name__}"
        )


class RecursiveVisitor(NodeVisitor[None]):
    """Base class for visitors that recursively traverse the entire AST."""

    def generic_visit(self, node: Node) -> None:
        """A node kind with no arm. NOT a silent skip (#639).

        A miss got NO analysis from any consumer, and said nothing. `WALKED_IN_PARENT`
        names the kinds a parent arm reads itself, so none of them arrives here. The CI
        gate is tests/unit/test_visitor_dispatch_is_total.py; this is the backstop.
        """
        er.raise_internal_error("CE0136", span=getattr(node, "loc", None),
                                node=type(node).__name__)

    def visit_let(self, node: Let) -> None:
        """Visit a let statement. Default: visit the value expression."""
        self.visit(node.value)

    def visit_rebind(self, node: Rebind) -> None:
        """Visit a rebind statement. Default: visit the value expression."""
        self.visit(node.value)

    def visit_exprstmt(self, node: ExprStmt) -> None:
        """Visit an expression statement. Default: visit the expression."""
        self.visit(node.expr)

    def visit_return(self, node: Return) -> None:
        """Visit a return statement. Default: visit the value expression."""
        self.visit(node.value)

    def visit_print(self, node: Print) -> None:
        """Visit a print statement. Default: visit the value expression."""
        self.visit(node.value)

    def visit_println(self, node: PrintLn) -> None:
        """Visit a println statement. Default: visit the value expression."""
        self.visit(node.value)

    def visit_if(self, node: If) -> None:
        """Visit an if statement. Default: visit conditions and blocks."""
        for cond, block in node.arms:
            self.visit(cond)
            self.visit(block)
        if node.else_block:
            self.visit(node.else_block)

    def visit_while(self, node: While) -> None:
        """Visit a while statement. Default: visit condition and body."""
        self.visit(node.cond)
        self.visit(node.body)

    def visit_foreach(self, node: 'Foreach') -> None:
        """Visit a foreach statement. Default: visit iterable and body."""
        self.visit(node.iterable)
        self.visit(node.body)

    def visit_match(self, node: 'Match') -> None:
        """Visit a match statement. Default: visit the scrutinee and each arm's body.

        A body is a block or a bare expression, and `visit` takes either. The PATTERN is
        not walked: it binds names and holds no expression.
        """
        self.visit(node.scrutinee)
        for arm in node.arms:
            self.visit(arm.body)

    def visit_expand(self, node: Expand) -> None:
        """Visit an expand statement. Default: visit the value pack and the body."""
        self.visit(node.iterable)
        self.visit(node.body)

    def visit_break(self, node: Break) -> None:
        """Visit a break statement. Default: no action."""
        pass

    def visit_continue(self, node: Continue) -> None:
        """Visit a continue statement. Default: no action."""
        pass

    def visit_block(self, node: Block) -> None:
        """Visit a block. Default: visit all statements."""
        for stmt in node.statements:
            self.visit(stmt)

    def visit_name(self, node: Name) -> None:
        """Visit a name expression. Default: no action."""
        pass

    def visit_intlit(self, node: IntLit) -> None:
        """Visit an integer literal. Default: no action."""
        pass

    def visit_floatlit(self, node: FloatLit) -> None:
        """Visit a float literal. Default: no action."""
        pass

    def visit_boollit(self, node: BoolLit) -> None:
        """Visit a boolean literal. Default: no action."""
        pass

    def visit_stringlit(self, node: StringLit) -> None:
        """Visit a string literal. Default: no action."""
        pass

    def visit_interpolatedstring(self, node: InterpolatedString) -> None:
        """Visit an interpolated string. Default: visit all expressions."""
        for part in node.parts:
            if not isinstance(part, str):  # part is an Expr
                self.visit(part)

    def visit_arrayliteral(self, node: ArrayLiteral) -> None:
        """Visit an array literal. Default: visit every value, and every repeat count."""
        for element in node.elements:
            self.visit(element.value)
            if element.count is not None:
                self.visit(element.count)

    def visit_indexaccess(self, node: IndexAccess) -> None:
        """Visit an index access. Default: visit array and index."""
        self.visit(node.array)
        self.visit(node.index)

    def visit_unaryop(self, node: UnaryOp) -> None:
        """Visit a unary operation. Default: visit the operand."""
        self.visit(node.expr)

    def visit_binaryop(self, node: BinaryOp) -> None:
        """Visit a binary operation. Default: visit both operands."""
        self.visit(node.left)
        self.visit(node.right)

    def visit_call(self, node: Call) -> None:
        """Visit a function call. Default: visit all arguments."""
        for arg in node.args:
            self.visit(arg)

    def visit_methodcall(self, node: MethodCall) -> None:
        """Visit a method call. Default: visit receiver and arguments."""
        self.visit(node.receiver)
        for arg in node.args:
            self.visit(arg)

    def visit_dynamicarraynew(self, node: DynamicArrayNew) -> None:
        """Visit a dynamic array new expression. Default: no action."""
        pass

    def visit_dynamicarrayfrom(self, node: DynamicArrayFrom) -> None:
        """Visit a dynamic array from expression. Default: visit elements."""
        self.visit(node.elements)

    def visit_castexpr(self, node: CastExpr) -> None:
        """Visit a cast expression. Default: visit the source expression."""
        self.visit(node.expr)

    def visit_dotcall(self, node: DotCall) -> None:
        """Visit a dot call (method-like). Default: visit receiver and arguments."""
        self.visit(node.receiver)
        for arg in node.args:
            self.visit(arg)

    def visit_memberaccess(self, node: MemberAccess) -> None:
        """Visit a member access (obj.field). Default: visit the receiver."""
        self.visit(node.receiver)

    def visit_enumconstructor(self, node: EnumConstructor) -> None:
        """Visit an enum constructor. Default: visit all arguments."""
        for arg in node.args:
            self.visit(arg)

    def visit_borrow(self, node: Borrow) -> None:
        """Visit a borrow expression. Default: visit the borrowed expression."""
        self.visit(node.expr)

    def visit_tryexpr(self, node: TryExpr) -> None:
        """Visit a try expression (??). Default: visit the inner expression."""
        self.visit(node.expr)

    def visit_rangeexpr(self, node: RangeExpr) -> None:
        """Visit a range expression. Default: visit start and end."""
        self.visit(node.start)
        self.visit(node.end)

    def visit_blanklit(self, node: BlankLit) -> None:
        """Visit a blank literal (~). Default: no action."""
        pass

    def visit_spread(self, node: Spread) -> None:
        """Visit a spread argument (arr...). Default: visit the bloomed expression."""
        self.visit(node.value)

    def visit_lambda(self, node: Lambda) -> None:
        """Visit a lambda literal. Default: visit the body, a block or a bare expression.

        A parameter carries a name and a type, never an expression, so `params` and
        `captures` hold nothing for the walk.
        """
        self.visit(node.body)
