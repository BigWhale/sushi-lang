"""Statement validation for the typecheck pass."""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Expr
    from sushi_lang.semantics.passes.types import TypeValidator
from sushi_lang.semantics.visitors import RecursiveVisitor
from sushi_lang.semantics.ast import (
    Let, Rebind, ExprStmt, Return, Print, PrintLn, If, While, Foreach, Match, Break, Continue
)


class StatementValidator(RecursiveVisitor):
    """Visitor for validating statements using the Visitor Pattern."""

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    def visit_if(self, node: If) -> None:
        """Validate if statement conditions and branches."""
        for cond, block in node.arms:
            # Validate condition is boolean (CE2005)
            self.type_validator._validate_boolean_condition(cond, "if")
            self.type_validator._validate_block(block)

        if node.else_block:
            self.type_validator._validate_block(node.else_block)

    def visit_while(self, node: While) -> None:
        """Validate while statement condition and body."""
        # Validate condition is boolean (CE2005)
        self.type_validator._validate_boolean_condition(node.cond, "while")
        self.type_validator._validate_block(node.body)

    def visit_foreach(self, node: Foreach) -> None:
        """Validate foreach statement iterator type and body."""
        self.type_validator._validate_foreach_statement(node)

    def visit_expand(self, node) -> None:
        """Reject an Expand that survived to the typecheck pass (CE0119)."""
        er.emit(self.type_validator.reporter, er.ERR.CE0119, node.loc,
                message="expand(...) is only valid inside a function with a ...Ts type-pack parameter")

    def visit_match(self, node: Match) -> None:
        """Validate match statement with exhaustiveness checking."""
        self.type_validator._validate_match_statement(node)

    def visit_let(self, node: Let) -> None:
        """Validate let statement."""
        self.type_validator._validate_let_statement(node)

    def visit_return(self, node: Return) -> None:
        """Validate return statement."""
        self.type_validator._validate_return_statement(node)

    def visit_exprstmt(self, node: ExprStmt) -> None:
        """Validate expression statement and warn if Result<T> is unused."""
        self.type_validator.validate_expression(node.expr)

        expr_type = self.type_validator.infer_expression_type(node.expr)
        if expr_type is not None:
            from sushi_lang.semantics.typesys import EnumType, BuiltinType

            if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                ok_variant = expr_type.get_variant("Ok")
                if ok_variant and ok_variant.associated_types:
                    t_type = ok_variant.associated_types[0]

                    # Skip warning if T is blank type (~)
                    # Blank functions have no meaningful return value to handle
                    if t_type == BuiltinType.BLANK:
                        return

                # Emit warning for unused Result<T, E> (where T is not blank)
                er.emit(self.type_validator.reporter, er.ERR.CW2001, node.expr.loc)

    def visit_print(self, node: Print) -> None:
        """Validate print statement."""
        self._validate_printed_value(node.value)

    def visit_println(self, node: PrintLn) -> None:
        """Validate println statement."""
        self._validate_printed_value(node.value)

    def _validate_printed_value(self, value: 'Expr') -> None:
        """What `print` and `println` both ask of the value they take.

        An unhandled `Result@(T, E)` has no printable form, so it is CE2037 in either.
        """
        from sushi_lang.semantics.typesys import EnumType

        self.type_validator.validate_expression(value)

        expr_type = self.type_validator.infer_expression_type(value)
        if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
            er.emit(self.type_validator.reporter, er.ERR.CE2037, value.loc)

    def visit_rebind(self, node: Rebind) -> None:
        """Validate rebind statement."""
        self.type_validator._validate_rebind_statement(node)

    def visit_break(self, node: Break) -> None:
        """Break statements don't need type validation."""
        pass

    def visit_continue(self, node: Continue) -> None:
        """Continue statements don't need type validation."""
        pass

