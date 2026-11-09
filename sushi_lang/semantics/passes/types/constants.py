"""Constant definition validation for type validation (the typecheck pass).

The constant evaluator reads the initializer at the declared type first, with the reporter
of the pass (docs/design/ir.md 7.3, Q5). After an error of the evaluator, the initializer
body is not walked. Else the walk checks the body (`visit/statements.py`), and the value
that the evaluator gave is the value row of its `Return`. An empty container allocates
nothing, so the evaluator does not read it, and it has no value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import ConstDef, VarDef
from sushi_lang.semantics.const_eval import allocates_nothing
from sushi_lang.semantics.namespaces import in_body_scope
from sushi_lang.semantics.type_predicates import holds_dynamic_array
from sushi_lang.semantics.typesys import BuiltinType, Type

from .utils import validate_type_name

if TYPE_CHECKING:
    from sushi_lang.semantics.const_eval import ConstantValue


@dataclass(frozen=True)
class InitializerFacts:
    """What the `Return` of an initializer body reads: the declaration, its resolved type,
    the value that the constant evaluator gave it, and whether it is an empty container,
    which has no value."""
    decl: ConstDef
    ty: Type
    value: Optional[ConstantValue]
    empty: bool


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
    # The initializer of a compiled library's constant names what its library unit may
    # name, a private function of the library included.
    outer = self.in_library_body
    self.in_library_body = self.in_library_unit or const.scope_unit is not None
    try:
        with in_body_scope(self, "namespaces", const, unit_tables(self)):
            _validate_constant(self, const)
    finally:
        self.in_library_body = outer


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
    # spelling is interned here, into the record that every reader of the declared type
    # reads. A generic enum's variant is evaluated against it (#551).
    from .resolution import resolve_variable_type
    resolved = resolve_variable_type(self, const.ty, const.type_span)
    sig = self.const_table.record_of(const, self.current_unit_name)
    if resolved is not None and sig is not None:
        sig.const_type = resolved
    ty = resolved if resolved is not None else const.ty

    value = None
    empty = is_var and allocates_nothing(const.value)
    if not empty:
        offered = self.errors_offered()
        value = self.constant_evaluator(self.reporter).evaluate(const.value, ty, const.loc)
        if self.errors_offered() != offered:
            return

    # The initializer is the SHIR body of the constant (docs/design/ir.md 6.3).
    self.initializer = InitializerFacts(const, ty, value, empty)
    try:
        self.check_body(const)
    finally:
        self.initializer = None
