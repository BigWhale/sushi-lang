"""Statement validation for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, EnumType, IteratorType
from sushi_lang.semantics.ast import Let, Return, Rebind, If, While, Foreach, EnumConstructor, DotCall, MethodCall, Name, MemberAccess
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from .utils import validate_type_name
from .compatibility import validate_assignment_compatibility, types_compatible
from .expressions import validate_boolean_condition
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from . import TypeValidator


def validate_let_statement(validator: 'TypeValidator', stmt: Let) -> None:
    """Validate let statement type annotations."""
    # Check if type annotation is missing (CE2007)
    if stmt.ty is None:
        er.emit(validator.reporter, er.ERR.CE2007, stmt.name_span, name=stmt.name)
        return  # Cannot continue without type info

    validate_type_name(validator, stmt.ty, stmt.type_span)

    # Blank type cannot be used for variables
    if stmt.ty == BuiltinType.BLANK:
        er.emit(validator.reporter, er.ERR.CE2032, stmt.type_span)
        return

    # A reference-typed `let` parses by grammar accident and would produce an
    # unchecked alias (issue #252) -- reject it until local borrow bindings are a
    # designed, checked feature.
    from sushi_lang.semantics.typesys import ReferenceType
    if isinstance(stmt.ty, ReferenceType):
        mode = "peek" if stmt.ty.is_peek() else "poke"
        er.emit(validator.reporter, er.ERR.CE2413, stmt.type_span,
                mode=mode, ty=display_type(stmt.ty.referenced_type))
        return

    from .resolution import resolve_variable_type
    from sushi_lang.semantics.generics.types import GenericTypeRef

    resolved_type = resolve_variable_type(validator, stmt.ty, stmt.type_span)

    validator.variable_types[stmt.name] = resolved_type

    if not (isinstance(stmt.ty, GenericTypeRef) and stmt.ty.base_name == "Result"):
        if resolved_type != stmt.ty:
            stmt.ty = resolved_type

    # A bare Result constructor infers as None, so the inference-based check below never
    # fires on it and it used to reach codegen as a CE0113 (#48). CE2505 here instead.
    if stmt.value is not None:
        is_result_ctor = (
            (isinstance(stmt.value, EnumConstructor) and stmt.value.enum_name == "Result")
            or (isinstance(stmt.value, DotCall)
                and isinstance(stmt.value.receiver, Name)
                and stmt.value.receiver.id == "Result")
        )
        lhs_is_result = (
            (isinstance(resolved_type, EnumType) and resolved_type.name.startswith("Result<"))
            or (isinstance(stmt.ty, GenericTypeRef) and stmt.ty.base_name == "Result")
            or (isinstance(stmt.ty, EnumType) and stmt.ty.name.startswith("Result<"))
        )
        if is_result_ctor and not lhs_is_result:
            er.emit(validator.reporter, er.ERR.CE2505, stmt.value.loc)
            return

    if stmt.value:
        from .propagation import propagate_types_to_value
        propagate_types_to_value(validator, stmt.value, resolved_type)

    # Validate assignment compatibility (CE2002)
    if stmt.value:
        validate_assignment_compatibility(validator, stmt.ty, stmt.value, stmt.type_span, stmt.value.loc)

    # Phase 4.2: Validate Result<T> handling
    # If RHS is a function call that returns Result<T>, LHS must also be Result<T>
    # (unless RHS is already .realise() or other handling method)
    if stmt.value:
        rhs_type = validator.infer_expression_type(stmt.value)

        # A declared `Result<T, E>` either resolves to its interned enum or stays a
        # GenericTypeRef, so both spellings are checked or CE2505 misfires.
        lhs_is_result = (
            (isinstance(resolved_type, EnumType) and resolved_type.name.startswith("Result<"))
            or (isinstance(stmt.ty, GenericTypeRef) and stmt.ty.base_name == "Result")
            or (isinstance(stmt.ty, EnumType) and stmt.ty.name.startswith("Result<"))
        )

        if (rhs_type is not None and
            isinstance(rhs_type, EnumType) and
            rhs_type.name.startswith("Result<") and
            not lhs_is_result):

            # Allow if RHS is already a method call (like .realise() or .clone())
            # because those methods return the unwrapped type
            if not isinstance(stmt.value, (MethodCall, DotCall)):
                er.emit(validator.reporter, er.ERR.CE2505, stmt.value.loc)


def validate_return_statement(validator: 'TypeValidator', stmt: Return) -> None:
    """Validate return statement type compatibility."""
    if not validator.current_function:
        # An extension or perk-impl body has no current_function -- it returns a BARE
        # value. The expression must still be WALKED, or a generic call in it is never
        # rewritten to its monomorphized name and reaches the backend as a CE0000 (#212).
        if getattr(validator, "in_extension_context", False) and stmt.value is not None:
            value = stmt.value
            # A Result.Ok(...)/Result.Err(...) wrapper is the anti-pattern that later
            # crashes codegen (CE0113); reject it cleanly here.
            if (isinstance(value, DotCall)
                    and isinstance(value.receiver, Name)
                    and value.receiver.id == "Result"
                    and value.method in ("Ok", "Err")):
                method_name = getattr(validator, "extension_method_name", None) or "<method>"
                er.emit(validator.reporter, er.ERR.CE2091, value.loc, name=method_name)
                return

            # Walk the return expression and check the bare value against the declared
            # return type. validate_return_compatibility does both (and emits CE2003 on
            # a mismatch). A blank (~) return type accepts anything, so skip the check.
            expected_type = getattr(validator, "extension_return_type", None)
            if expected_type is not None and expected_type != BuiltinType.BLANK:
                from .compatibility import validate_return_compatibility
                validate_return_compatibility(validator, expected_type, value, value.loc)
            else:
                validator.validate_expression(value)
        return

    expected_type = validator.current_function.ret
    if expected_type is None:
        return  # Functions without return type (shouldn't happen after CE0103)

    from .resolution import resolve_return_type_to_result
    expected_type = resolve_return_type_to_result(
        validator,
        expected_type,
        validator.current_function.err_type
    )

    if stmt.value:
        from .propagation import propagate_types_to_value
        propagate_types_to_value(validator, stmt.value, expected_type)

        validator.validate_expression(stmt.value)

        from .result_validation import validate_result_pattern

        if not validate_result_pattern(validator, stmt.value, expected_type):
            er.emit_with(validator.reporter, er.ERR.CE2030, stmt.value.loc) \
                .help("wrap return value: return Result.Ok(value)").emit()

        # Check for ?? in main() warning (CW2511)
        if validator.current_function.name == "main":
            from .expressions import check_propagation_in_expression
            if check_propagation_in_expression(stmt.value):
                er.emit(validator.reporter, er.ERR.CW2511, stmt.value.loc)
    else:
        er.emit_with(validator.reporter, er.ERR.CE2030, stmt.loc) \
            .help("wrap return value: return Result.Ok(value)").emit()


def validate_rebind_statement(validator: 'TypeValidator', stmt: Rebind) -> None:
    """Validate rebind statement type compatibility (CE2002)."""
    from sushi_lang.semantics.ast import Name

    actual_type = None

    if isinstance(stmt.target, Name):
        var_name = stmt.target.id
        if var_name not in validator.variable_types:
            validator.validate_expression(stmt.value)
            return

        var_type = validator.variable_types[var_name]

        # Unwrap reference types for validation
        # When rebinding through a reference parameter, we check compatibility
        # with the referenced type, not the reference wrapper
        from sushi_lang.semantics.typesys import ReferenceType
        actual_type = var_type
        if isinstance(var_type, ReferenceType):
            actual_type = var_type.referenced_type

    elif isinstance(stmt.target, MemberAccess):
        validator.validate_expression(stmt.target)

        actual_type = validator.infer_expression_type(stmt.target)
        if actual_type is None:
            validator.validate_expression(stmt.value)
            return

    else:
        validator.validate_expression(stmt.target)
        validator.validate_expression(stmt.value)
        return

    if stmt.value:
        from .propagation import propagate_types_to_value
        propagate_types_to_value(validator, stmt.value, actual_type)

    validator.validate_expression(stmt.value)

    expr_type = validator.infer_expression_type(stmt.value)

    if expr_type is None:
        return

    # `types_compatible` and NOT a bare `!=`: the two sides arrive at different
    # resolution depths, and comparing directly makes "how far resolved is it?" part of
    # type identity (#240). One type printed twice in a CE2002 is that failure's
    # signature (#288).
    from .compatibility import types_compatible
    if not types_compatible(validator, expr_type, actual_type):
        er.emit(validator.reporter, er.ERR.CE2002, stmt.loc,
               expected=display_type(actual_type), got=display_type(expr_type))


def validate_if_statement(validator: 'TypeValidator', stmt: If) -> None:
    """Validate if statement conditions and branches."""
    for cond, block in stmt.arms:
        # Validate condition is boolean (CE2005)
        validate_boolean_condition(validator, cond, "if")
        validator._validate_block(block)

    if stmt.else_block:
        validator._validate_block(stmt.else_block)


def validate_while_statement(validator: 'TypeValidator', stmt: While) -> None:
    """Validate while statement condition and body."""
    # Validate condition is boolean (CE2005)
    validate_boolean_condition(validator, stmt.cond, "while")

    validator._validate_block(stmt.body)


def validate_foreach_statement(validator: 'TypeValidator', stmt: Foreach) -> None:
    """Validate foreach statement: check iterator type and item variable."""
    validator.validate_expression(stmt.iterable)
    iterable_type = validator.infer_expression_type(stmt.iterable)

    if iterable_type is None:
        return  # Error already emitted during expression validation

    if not isinstance(iterable_type, IteratorType):
        er.emit(validator.reporter, er.ERR.CE2033, stmt.iterable.loc, got=display_type(iterable_type))
        return

    element_type = iterable_type.element_type

    if stmt.item_type is not None:
        validate_type_name(validator, stmt.item_type, stmt.item_type_span)

        declared_type = stmt.item_type
        from sushi_lang.semantics.typesys import UnknownType
        if isinstance(stmt.item_type, UnknownType):
            resolved_type = resolve_unknown_type(stmt.item_type, validator.struct_table.by_name, validator.enum_table.by_name)
            if resolved_type != stmt.item_type:
                declared_type = resolved_type

        if not types_compatible(validator, declared_type, element_type):
            er.emit(validator.reporter, er.ERR.CE2034, stmt.item_type_span,
                   expected=display_type(element_type), got=display_type(declared_type))
            return

        stmt.item_type = declared_type
    else:
        stmt.item_type = element_type

    # A reference binding points INTO the container's element storage, so the iterable
    # must have some: a range and `map.entries()` synthesize their values. The allowlist
    # is deliberate -- a new iterable kind must be PROVEN addressable first (#300).
    if stmt.item_borrow is not None:
        if not _foreach_iterable_is_addressable(stmt.iterable):
            er.emit(validator.reporter, er.ERR.CE2423,
                    stmt.item_borrow_span or stmt.loc)
            return

    # The item binding lives for the LOOP and no longer (#341), so whatever it shadows is
    # saved and restored. Without that, an outer local kept the ITEM's type.
    _MISSING = object()
    previous = validator.variable_types.get(stmt.item_name, _MISSING)
    if stmt.item_borrow is not None:
        # The binding's registered type is the REFERENCE, so every consumer that asks
        # "is this name a borrow?" (Pass 3 rules, backend deref machinery) gets the
        # truthful answer; expression inference auto-derefs a reference-typed name.
        from sushi_lang.semantics.typesys import BorrowMode, ReferenceType
        mode = BorrowMode.POKE if stmt.item_borrow == "poke" else BorrowMode.PEEK
        validator.variable_types[stmt.item_name] = ReferenceType(stmt.item_type, mode)
    else:
        validator.variable_types[stmt.item_name] = stmt.item_type

    try:
        validator._validate_block(stmt.body)
    finally:
        if previous is _MISSING:
            validator.variable_types.pop(stmt.item_name, None)
        else:
            validator.variable_types[stmt.item_name] = previous


def _foreach_iterable_is_addressable(iterable) -> bool:
    """True when the iterable's elements live in addressable container storage."""
    from sushi_lang.semantics.ast import DotCall, MethodCall
    if isinstance(iterable, (MethodCall, DotCall)):
        return iterable.method in ("iter", "keys", "values")
    return False
