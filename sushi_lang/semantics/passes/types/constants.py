"""Constant definition validation for type validation (the typecheck pass)."""
from __future__ import annotations

from typing import Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import ConstDef, VarDef
from sushi_lang.semantics.namespaces import in_body_scope
from sushi_lang.semantics.type_predicates import holds_dynamic_array
from sushi_lang.semantics.typesys import BuiltinType

from .utils import validate_type_name


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
    """Validate the declared type of a constant, then walk its initializer, in the scope its
    initializer resolves in."""
    from .signatures import unit_tables
    with in_body_scope(self, "namespaces", const, unit_tables(self)):
        _validate_constant(self, const)


def _validate_constant(self, const: ConstDef) -> None:
    validate_type_name(self, const.ty, const.type_span)

    # Blank type cannot be used for constants
    if const.ty == BuiltinType.BLANK:
        self.err.emit(er.ERR.CE2032, const.type_span)
        return

    is_var = isinstance(const, VarDef)
    if not is_var and holds_dynamic_array(const.ty):
        self.err.emit(er.ERR.CE2015, const.type_span, name=const.name)
        return

    # The declaration takes the type a `let` would: a `List@(T)` or a `Maybe@(T)`
    # spelling is interned here, and the record every reader of the name consults
    # follows the declaration. A generic enum's variant is evaluated against it (#551).
    from .resolution import resolve_variable_type
    resolved = resolve_variable_type(self, const.ty, const.type_span)
    if resolved is not None and resolved != const.ty:
        const.ty = resolved
        sig = self.const_sig(const.name)
        if sig is not None:
            sig.const_type = resolved

    # The initializer is the SHIR body of the constant (docs/design/ir.md 6.3).
    self.check_body(const)
