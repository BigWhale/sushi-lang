"""Constant definition validation for type validation (the typecheck pass)."""
from __future__ import annotations

from typing import Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import ConstDef, VarDef
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType

from .utils import validate_type_name
from .compatibility import validate_assignment_compatibility
from .propagation import propagate_types_to_value


def assignment_span(const: ConstDef) -> Optional[Span]:
    """The `name = value` half of a declaration, which is what a mismatch is about.

    Not the declaration's own span: that reaches from `const` to the line after the
    statement, so it marks the declared type as well -- and the declared type already
    has a note of its own on the same diagnostic.
    """
    name = const.name_span
    value = const.value.loc if const.value is not None else None
    if name is None or value is None:
        return const.loc
    return Span(name.line, name.col, value.end_line, value.end_col)


def validate_constant(self, const: ConstDef) -> None:
    """Validate a constant definition."""
    validate_type_name(self, const.ty, const.type_span)

    # Blank type cannot be used for constants
    if const.ty == BuiltinType.BLANK:
        self.err.emit(er.ERR.CE2032, const.type_span)
        return

    is_var = isinstance(const, VarDef)
    if isinstance(const.ty, DynamicArrayType) and not is_var:
        self.err.emit(er.ERR.CE2015, const.type_span, name=const.name)
        return

    if is_var:
        # Storage takes the type a `let` would: a `List@(T)` spelling is interned here,
        # and the record every reader of the name consults follows the declaration.
        from .resolution import resolve_variable_type
        resolved = resolve_variable_type(self, const.ty, const.type_span)
        if resolved is not None and resolved != const.ty:
            const.ty = resolved
            sig = self.const_sig(const.name)
            if sig is not None:
                sig.const_type = resolved
        from sushi_lang.semantics.passes.const_eval import allocates_nothing
        if allocates_nothing(const.value):
            # An empty container is the descriptor `{0, 0, null}` -- no evaluation,
            # only the type stamp its position hands over (#544).
            propagate_types_to_value(self, const.value, const.ty)
            validate_assignment_compatibility(self, const.ty, const.value,
                                              const.type_span, assignment_span(const))
            return

    from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
    evaluator = ConstantEvaluator(self.reporter, self.const_table, self.ast_constants,
                                  self.current_unit_name, self.scope, self.struct_table)
    const_value = evaluator.evaluate(const.value, const.ty, const.loc)

    if const_value is None:
        return

    propagate_types_to_value(self, const.value, const.ty)

    validate_assignment_compatibility(self, const.ty, const.value, const.type_span,
                                      assignment_span(const))
