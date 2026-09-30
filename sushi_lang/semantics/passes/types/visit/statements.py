"""Statement validation for the typecheck pass."""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator
from sushi_lang.semantics.passes.types.expressions import validate_boolean_condition
from sushi_lang.semantics.passes.types.matching import validate_match_statement
from sushi_lang.semantics.passes.types.statements import (
    validate_foreach_statement, validate_let_statement, validate_rebind_statement,
    validate_return_statement)
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
            validate_boolean_condition(self.type_validator, cond, "if")
            self.type_validator._validate_block(block)

        if node.else_block:
            self.type_validator._validate_block(node.else_block)

    def visit_while(self, node: While) -> None:
        """Validate while statement condition and body."""
        # Validate condition is boolean (CE2005)
        validate_boolean_condition(self.type_validator, node.cond, "while")
        self.type_validator._validate_block(node.body)

    def visit_foreach(self, node: Foreach) -> None:
        """Validate foreach statement iterator type and body."""
        validate_foreach_statement(self.type_validator, node)

    def visit_expand(self, node) -> None:
        """Reject an Expand that survived to the typecheck pass (CE0119)."""
        er.emit(self.type_validator.reporter, er.ERR.CE0119, node.loc,
                message="expand(...) is only valid inside a function with a ...Ts type-pack parameter")

    def visit_match(self, node: Match) -> None:
        """Validate match statement with exhaustiveness checking."""
        validate_match_statement(self.type_validator, node)

    def visit_let(self, node: Let) -> None:
        """Validate let statement."""
        validate_let_statement(self.type_validator, node)

    def visit_return(self, node: Return) -> None:
        """Validate return statement."""
        validate_return_statement(self.type_validator, node)

    def visit_exprstmt(self, node: ExprStmt) -> None:
        """Validate expression statement and warn if Result<T> is unused."""
        self.type_validator.validate_expression(node.expr)

        expr_type = self.type_validator.infer_expression_type(node.expr)
        if expr_type is not None:
            from sushi_lang.semantics.typesys import EnumType, BuiltinType

            if isinstance(expr_type, EnumType) and is_instance_of(expr_type, "Result"):
                ok_variant = expr_type.get_variant("Ok")
                if ok_variant and ok_variant.associated_types:
                    t_type = ok_variant.associated_types[0]

                    # Skip warning if T is blank type (~)
                    # Blank functions have no meaningful return value to handle
                    if t_type == BuiltinType.BLANK:
                        return

                # Emit warning for unused Result<T, E> (where T is not blank)
                er.emit(self.type_validator.reporter, er.ERR.CW2001, node.expr.loc,
                        ty=display_type(expr_type))

    def visit_print(self, node: Print) -> None:
        """Validate print statement."""
        self._validate_printed_value(node)

    def visit_println(self, node: PrintLn) -> None:
        """Validate println statement."""
        self._validate_printed_value(node)

    def _validate_printed_value(self, node: 'Print | PrintLn') -> None:
        """What `print` and `println` both ask of the value they take.

        An unhandled `Result@(T, E)` has no printable form, so it is CE2037 in either.
        Any other value prints by the rule an interpolation hole reads: a primitive, or a
        struct or an enum through `Display`. Everything else is CE2115, and a type that
        holds something with no string form says which field it is.
        """
        from sushi_lang.semantics.generics.contracts import DISPLAY
        from sushi_lang.semantics.passes.types.expressions import top_level_contract
        from sushi_lang.semantics.type_predicates import is_string_convertible
        from sushi_lang.semantics.passes.types.utils import names_no_type
        from sushi_lang.semantics.typesys import BuiltinType, EnumType, deref_type

        value = node.value
        self.type_validator.validate_expression(value)

        expr_type = self.type_validator.infer_expression_type(value)
        if expr_type is None:
            return
        if isinstance(expr_type, EnumType) and is_instance_of(expr_type, "Result"):
            er.emit(self.type_validator.reporter, er.ERR.CE2037, value.loc)
            return
        # `~` prints as it always has, and a type that did not resolve is reported where
        # it is written.
        shown = deref_type(expr_type)
        if (is_string_convertible(shown) or shown == BuiltinType.BLANK
                or names_no_type(self.type_validator, shown)):
            return
        printable, reason = top_level_contract(self.type_validator, expr_type, DISPLAY)
        if printable:
            node.display_type = deref_type(expr_type)
            return
        report = er.emit_with(self.type_validator.reporter, er.ERR.CE2115, value.loc,
                              type=display_type(expr_type))
        if reason is not None:
            report = report.note(f"no derived Display: {reason}")
        report.emit()

    def visit_rebind(self, node: Rebind) -> None:
        """Validate rebind statement."""
        validate_rebind_statement(self.type_validator, node)

    def visit_break(self, node: Break) -> None:
        """Break statements don't need type validation."""
        pass

    def visit_continue(self, node: Continue) -> None:
        """Continue statements don't need type validation."""
        pass

