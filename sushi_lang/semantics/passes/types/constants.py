# semantics/passes/types/constants.py
"""
Constant definition validation for type validation (Pass 2).

Validates constant declarations: type annotation, blank/dynamic-array
restrictions, compile-time evaluation, and value/type compatibility.

Functions receive the TypeValidator instance (``self``) and mutate it in
place, matching the delegation pattern used across this package.
"""
from __future__ import annotations

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import ConstDef
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType

from .utils import validate_type_name
from .compatibility import validate_assignment_compatibility
from .propagation import propagate_types_to_value


def validate_constant(self, const: ConstDef) -> None:
    """Validate a constant definition."""
    # Validate the constant's type annotation
    validate_type_name(self, const.ty, const.type_span)

    # Blank type cannot be used for constants
    if const.ty == BuiltinType.BLANK:
        self.err.emit(er.ERR.CE2032, const.type_span)
        return

    # Constants should not use dynamic arrays (they don't make sense for compile-time values)
    if isinstance(const.ty, DynamicArrayType):
        self.err.emit(er.ERR.CE2015, const.type_span, name=const.name)
        return

    # Evaluate constant expression at compile-time
    from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
    evaluator = ConstantEvaluator(self.reporter, self.const_table, self.ast_constants)
    const_value = evaluator.evaluate(const.value, const.ty, const.loc)

    if const_value is None:
        # Error already emitted by evaluator
        return

    # Context-type any bare numeric literal to the declared const type before the
    # compatibility check (e.g. const u8 MAX = 200).
    propagate_types_to_value(self, const.value, const.ty)

    # Validate value type matches declared type
    validate_assignment_compatibility(self, const.ty, const.value, const.type_span, const.loc)
