"""
AST Visitor Pattern implementation for the Sushi language compiler.

This module provides base visitor classes for traversing and processing AST nodes
using the Visitor Pattern, eliminating the need for large match/isinstance chains.

Usage:
    1. Subclass NodeVisitor[T] for visitors that return values
    2. Subclass RecursiveVisitor for analysis passes (void return)
    3. Subclass NodeTransformer for AST transformations

Example:
    class MyAnalyzer(RecursiveVisitor):
        def visit_let(self, node: Let) -> None:
            # Custom handling for let statements
            print(f"Found let statement: {node.name}")
            super().visit_let(node)  # Continue traversal

    analyzer = MyAnalyzer()
    analyzer.visit(ast_node)
"""
from __future__ import annotations
from abc import ABC
from typing import Any, Optional, TypeVar, Generic, TYPE_CHECKING

from semantics.ast import Node, Stmt, Block
from semantics.ast import (
    # Statements
    Let, Rebind, ExprStmt, Return, Print, PrintLn, If, While, Foreach, Match, MatchArm, Break, Continue,
    # Expressions
    Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess,
    UnaryOp, BinaryOp, Call, MethodCall, DotCall, MemberAccess, StructConstructor, EnumConstructor,
    DynamicArrayNew, DynamicArrayFrom, CastExpr, Borrow, TryExpr, RangeExpr
)

if TYPE_CHECKING:
    from semantics.ast import Program, FuncDef, ExtendDef

T = TypeVar('T')

class NodeVisitor(ABC, Generic[T]):
    """
    Abstract base class for AST node visitors.

    Uses the Visitor Pattern with dynamic dispatch to eliminate large
    match/isinstance chains. Subclasses implement visit_* methods for
    each node type they need to handle.
    """

    def visit(self, node: Node) -> T:
        """
        Main entry point for visiting a node.

        Uses dynamic method dispatch to automatically route to the
        appropriate visit_* method based on the node's actual type.
        """
        method_name = f'visit_{type(node).__name__.lower()}'
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node: Node) -> T:
        """
        Default visitor that raises an error.
        Subclasses should either implement specific visit_* methods
        or override this to provide default behavior.
        """
        raise NotImplementedError(
            f"Visitor {self.__class__.__name__} doesn't handle {type(node).__name__}"
        )


class NodeTransformer(NodeVisitor[Node]):
    """
    Base class for visitors that transform AST nodes.

    Returns modified versions of nodes, useful for AST transformations
    and optimizations.
    """

    def generic_visit(self, node: Node) -> Node:
        """Default behavior: return node unchanged."""
        return node


class RecursiveVisitor(NodeVisitor[None]):
    """
    Base class for visitors that recursively traverse the entire AST.

    Automatically visits all child nodes unless a specific visit_* method
    provides different behavior. Useful for analysis passes.

    Subclasses should override the visit_* methods they care about.
    The default implementations handle recursive traversal.
    """

    def generic_visit(self, node: Node) -> None:
        """Default behavior: no action for unknown nodes."""
        pass

    # === Statement visitors ===

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
        """Visit a match statement. Default: visit scrutinee and arms."""
        self.visit(node.scrutinee)
        for arm in node.arms:
            # Note: We don't visit pattern bindings, just the body
            if hasattr(arm, 'body'):
                if isinstance(arm.body, Block):
                    self.visit(arm.body)
                else:  # Expression body
                    self.visit(arm.body)

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

    # === Expression visitors ===

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
        """Visit an array literal. Default: visit all elements."""
        for element in node.elements:
            self.visit(element)

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

    def visit_structconstructor(self, node: StructConstructor) -> None:
        """Visit a struct constructor. Default: visit all field values."""
        for value in node.values:
            self.visit(value)

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


class ASTPassRunner:
    """
    Utility class for running visitor passes over entire programs.

    Handles the common pattern of iterating over all top-level definitions
    (functions, extensions, constants) and applying a visitor to each.

    Usage:
        class MyPass(RecursiveVisitor):
            def visit_let(self, node):
                # handle let statements

        runner = ASTPassRunner(MyPass())
        runner.run(program)
    """

    def __init__(self, visitor: RecursiveVisitor) -> None:
        """Initialize with a visitor instance."""
        self.visitor = visitor

    def run(self, program: 'Program') -> None:
        """Run the visitor over all top-level definitions."""
        self.run_functions(program)
        self.run_extensions(program)

    def run_functions(self, program: 'Program') -> None:
        """Run visitor over all function bodies."""
        for func in program.functions:
            if hasattr(func, 'body') and func.body:
                self.visitor.visit(func.body)

    def run_extensions(self, program: 'Program') -> None:
        """Run visitor over all extension method bodies."""
        for ext in program.extensions:
            if hasattr(ext, 'body') and ext.body:
                self.visitor.visit(ext.body)

    def run_function(self, func: 'FuncDef') -> None:
        """Run visitor over a single function body."""
        if hasattr(func, 'body') and func.body:
            self.visitor.visit(func.body)

    def run_block(self, block: Block) -> None:
        """Run visitor over a single block."""
        self.visitor.visit(block)
