"""Statement validation for type validation."""
from __future__ import annotations
from itertools import count
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, EnumType, IteratorType
from sushi_lang.semantics.ast import Let, Return, Rebind, If, While, Foreach, EnumConstructor, DotCall, MethodCall, Name, MemberAccess, IndexAccess
from sushi_lang.semantics.param_modes import ParamMode, receiver_mode
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

    from sushi_lang.semantics.typesys import ReferenceType
    if isinstance(stmt.ty, ReferenceType):
        validate_let_reference(validator, stmt)
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

    # Validate Result@(T) handling
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


def _is_own_type(validator: 'TypeValidator', ty) -> bool:
    """Is `ty` an `Own@(T)`, in either spelling the passes keep?"""
    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(ty, GenericTypeRef):
        return ty.base_name == "Own"
    name = getattr(ty, "name", None)
    return isinstance(name, str) and name.startswith("Own<")


def place_root(validator: 'TypeValidator', expr) -> Optional[Name]:
    """The name at the root of a PLACE, or None when `expr` names no storage.

    A `let poke` / `let peek` binds a pointer, so its initializer must have an address a
    frame keeps: a bare name, a member or index chain off one, or an `Own@(T).get()` on
    one (the payload's heap cell). A call result, a `??`, a literal or a constructor is a
    temporary, and binding a pointer into one is CE2404 -- the rule a `poke self` call
    answers to as well.
    """
    while True:
        if isinstance(expr, Name):
            return expr
        if isinstance(expr, MemberAccess):
            expr = expr.receiver
            continue
        if isinstance(expr, IndexAccess):
            expr = expr.array
            continue
        if (isinstance(expr, (MethodCall, DotCall)) and expr.method == "get"
                and not expr.args
                and _is_own_type(validator, validator.infer_expression_type(expr.receiver))):
            expr = expr.receiver
            continue
        return None


def validate_let_reference(validator: 'TypeValidator', stmt: Let) -> None:
    """`let poke T x = <place>` / `let peek T x = <place>`: a checked borrow binding (#409).

    The declared type is the reference, the initializer is the place it points into, and
    the binding is recorded with its full `ReferenceType` so that every later reader -- the
    borrow pass's write gates, the backend's dereference -- answers by construction. The
    owner's freeze, the one-`poke` rule and the consuming-use refusal are the borrow
    pass's; here the place is checked to HAVE an address, and a constant is refused
    because it has none (CE2400) where a unit variable has one.
    """
    from sushi_lang.semantics.typesys import ReferenceType
    from .resolution import resolve_variable_type
    from .propagation import propagate_types_to_value

    mode = "poke" if stmt.ty.is_poke() else "peek"
    root = place_root(validator, stmt.value)
    if root is None:
        er.emit_with(validator.reporter, er.ERR.CE2404, stmt.value.loc, expr=stmt.name) \
            .help(f"a `let {mode}` binds a PLACE: a local, a field, an element, or an "
                  f"Own's payload (`o.get()`); bind a call result by value, with "
                  f"`let T {stmt.name} = ...`") \
            .emit()
        return
    if root.id not in validator.variable_types:
        sig = validator.const_sig(root.id)
        if sig is not None and not sig.is_var:
            er.emit(validator.reporter, er.ERR.CE2400, root.loc, name=root.id)
            return

    referent = resolve_variable_type(validator, stmt.ty.referenced_type, stmt.type_span)
    ref_type = ReferenceType(referenced_type=referent, mutability=stmt.ty.mutability)
    stmt.ty = ref_type
    validator.variable_types[stmt.name] = ref_type

    propagate_types_to_value(validator, stmt.value, referent)
    validate_assignment_compatibility(validator, referent, stmt.value, stmt.type_span,
                                      stmt.value.loc)


def validate_return_statement(validator: 'TypeValidator', stmt: Return) -> None:
    """Validate return statement type compatibility."""
    if not validator.current_function:
        # An extension or perk-impl body has no current_function -- it returns a BARE
        # value. The expression must still be WALKED, or a generic call in it is never
        # rewritten to its monomorphized name and reaches the backend as a CE0000 (#212).
        if getattr(validator, "in_extension_context", False) and stmt.value is not None:
            value = stmt.value
            channel = getattr(validator, "extension_channel_result", None)
            if (isinstance(value, DotCall)
                    and isinstance(value.receiver, Name)
                    and value.receiver.id == "Result"
                    and value.method in ("Ok", "Err")):
                if channel is not None and value.method == "Err":
                    # Ruling 6: the error is the ONE spelled constructor in a channel
                    # body. Propagate the interned Result into it (the stamp the
                    # backend constructs from) and check it as a function would.
                    from .propagation import propagate_declared_type_to_value
                    expected = propagate_declared_type_to_value(validator, value, channel)
                    from .compatibility import validate_return_compatibility
                    validate_return_compatibility(validator, expected, value, value.loc)
                    return
                # A bare method refuses both constructors; a channel method refuses
                # the Ok spelling -- its success returns bare (CE2091 either way).
                method_name = getattr(validator, "extension_method_name", None) or "<method>"
                refused = ("Result.Ok(...)" if channel is not None
                           else "Result.Ok(...) / Result.Err(...)")
                er.emit(validator.reporter, er.ERR.CE2091, value.loc,
                        name=method_name, refused=refused)
                return

            # PROPAGATE the declared return type into the value, then walk it and check
            # the bare value against that type. validate_return_compatibility does both
            # (and emits CE2003 on a mismatch). A blank (~) return type accepts anything,
            # so skip both. Propagation is what stamps `resolved_enum_type` on a generic
            # enum constructor: without it every generic enum in this position was a
            # CE0113 (#387), which is the same symptom the CE2091 guard above predicts.
            declared_type = getattr(validator, "extension_return_type", None)
            if declared_type is not None and declared_type != BuiltinType.BLANK:
                from .propagation import propagate_declared_type_to_value
                expected_type = propagate_declared_type_to_value(validator, value, declared_type)

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
            # A unit variable is rebindable storage; another unit's private one is
            # fenced here exactly as a read of it is (CE3005).
            sig = validator.const_sig(var_name)
            if sig is None or not sig.is_var:
                validator.validate_expression(stmt.value)
                return
            from .visibility import reject_private_name
            if reject_private_name(validator, "variable", sig, stmt.target.loc):
                validator.validate_expression(stmt.value)
                return
            var_type = sig.const_type
        else:
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

        # A field of a constant is .rodata like any other part of it (CE2096).
        from .arrays import reject_write_to_constant
        if reject_write_to_constant(stmt.target, "assign to a field of",
                                    stmt.loc, validator.reporter, validator):
            validator.validate_expression(stmt.value)
            return

        actual_type = validator.infer_expression_type(stmt.target)
        if actual_type is None:
            validator.validate_expression(stmt.value)
            return

    elif isinstance(stmt.target, IndexAccess):
        # `arr[i] := v`. The index and the compile-time bounds are the read side's
        # question, so validating the target answers both (CE2002, CE2012), and the
        # inference stamps `inferred_element_type` for the backend to read.
        validator.validate_expression(stmt.target)

        # A constant lives in .rodata: the store is undefined behaviour, not a
        # diagnostic, so it must never be emitted (CE2096).
        from .arrays import reject_write_to_constant
        if reject_write_to_constant(stmt.target.array, "assign to an element of",
                                    stmt.loc, validator.reporter, validator):
            validator.validate_expression(stmt.value)
            return

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

    if isinstance(iterable_type, IteratorType):
        element_type = iterable_type.element_type
    else:
        # Not an iterator: it may still carry `next()` (HANDLES.md ruling R21). The
        # protocol is asked SECOND, because `Iterator@(T)` is a cursor over contiguous
        # storage and its walk needs no call at all.
        element_type = resolve_protocol_iterator(validator, stmt, iterable_type)
        if element_type is None:
            er.emit(validator.reporter, er.ERR.CE2033, stmt.iterable.loc,
                    got=display_type(iterable_type))
            return

    # The `??` binder: the loop binds the raw item under the hidden name the AST builder
    # gave it, and the body opens with `let <T> <name> = <hidden>??`. Filling in that
    # `let`'s type is the ONE thing the parser could not do, so it is the only rule the
    # marker needs here -- the unwrap itself is the ordinary TryExpr.
    if stmt.item_try_let is not None:
        ok_payload = _result_ok_payload(element_type)
        if ok_payload is None:
            er.emit(validator.reporter, er.ERR.CE2517,
                    stmt.item_try_span or stmt.loc, ty=display_type(element_type))
            return
        # A declared type on a `??` binder names what the USER binds, which is the
        # unwrapped value -- so it belongs to the `let` and not to the loop's own slot.
        if stmt.item_type is not None:
            stmt.item_try_let.ty = stmt.item_type
            stmt.item_try_let.type_span = stmt.item_type_span
            stmt.item_type = None
            stmt.item_type_span = None
        else:
            stmt.item_try_let.ty = ok_payload

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
    #
    # A `next()` protocol iterator never has element storage: the item is the value the
    # CALL answered, held in the loop's own slot. The spelling of the iterable does not
    # change that, so the protocol is asked before the allowlist -- a user `iter()`
    # answering a protocol iterator would otherwise pass the name test and bind a
    # pointer into a temporary.
    if stmt.item_borrow is not None:
        if (stmt.protocol_next is not None
                or not _foreach_iterable_is_addressable(stmt.iterable)):
            er.emit(validator.reporter, er.ERR.CE2423,
                    stmt.item_borrow_span or stmt.loc)
            return

    # The item binding lives for the LOOP and no longer (#341), so whatever it shadows is
    # saved and restored. Without that, an outer local kept the ITEM's type.
    _MISSING = object()
    previous = validator.variable_types.get(stmt.item_name, _MISSING)
    if stmt.item_borrow is not None:
        # The binding's registered type is the REFERENCE, so every consumer that asks
        # "is this name a borrow?" (the borrow pass's rules, backend deref machinery) gets the
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


# One counter for the whole process, for the same reason the AST builder keeps one: the
# hidden local a protocol loop holds its iterator in must be a name no source can write,
# and a nested loop must not reuse its parent's.
_protocol_iter_ids = count()


def _result_ok_payload(ty):
    """The Ok payload of a `Result@(T, E)`, or None when this is not one."""
    if not isinstance(ty, EnumType) or not ty.name.startswith("Result<"):
        return None
    ok_variant = ty.get_variant("Ok")
    if ok_variant is None or len(ok_variant.associated_types) != 1:
        return None
    return ok_variant.associated_types[0]


def _maybe_some_payload(ty):
    """The Some payload of a `Maybe@(T)`, or None when this is not one."""
    if not isinstance(ty, EnumType) or not ty.name.startswith("Maybe<"):
        return None
    some_variant = ty.get_variant("Some")
    if some_variant is None or len(some_variant.associated_types) != 1:
        return None
    return some_variant.associated_types[0]


def resolve_protocol_iterator(validator: 'TypeValidator', stmt: Foreach, iterable_type):
    """The `next()` protocol: what this loop yields, or None when the type is not walkable.

    A type is walkable when it carries a nullary `next()` answering `Maybe@(T)`. That is
    the whole protocol -- no type to implement, no perk to name (HANDLES.md ruling R21) --
    and the element type is T.

    Resolution goes through `resolve_method`, the one ladder validation and inference
    already share, so a perk method named `next` and an extension method named `next`
    answer by the same rule as everywhere else. Three shapes are refused, and each for
    the same reason -- the loop has to be able to call it repeatedly and read a stop out
    of the answer. A `next()` taking ARGUMENTS has nothing to be handed. One declaring
    `| E` answers a Result rather than a Maybe: the protocol carries no error channel,
    and a fallible iterator says so in its ITEM instead. And a `nom self` receiver
    answers once and spends the iterator.

    The call the loop will make is built and VALIDATED here, against a hidden local
    holding the iterator. That is what stamps the receiver mode, the parameter modes and
    the return type, so the backend emits an ordinary method call and needs no second
    resolution of its own.
    """
    from sushi_lang.semantics.passes.types.calls.methods import (
        RESOLUTION_REPORTED, extension_call_result_type, resolve_method,
        validate_method_call)

    resolved = resolve_method(validator, iterable_type, "next", report=False)
    if resolved is None or resolved is RESOLUTION_REPORTED:
        return None
    if getattr(resolved.method, "params", None):
        return None
    # A CONSUMING receiver cannot answer twice: the first call would spend the loop's
    # own iterator and the second would read a value that has been given away. So
    # `nom self` on a `next()` makes a type unwalkable rather than walkable once.
    if receiver_mode(getattr(resolved.method, "self_mode", None)) is ParamMode.NOM:
        return None
    element_type = _maybe_some_payload(
        extension_call_result_type(validator, resolved.method))
    if element_type is None:
        return None

    if stmt.protocol_iter_name is None:
        stmt.protocol_iter_name = f"__fe_iter{next(_protocol_iter_ids)}"
    iter_name = stmt.protocol_iter_name

    call = MethodCall(receiver=Name(id=iter_name, loc=stmt.iterable.loc),
                      method="next", args=[], loc=stmt.iterable.loc)
    _MISSING = object()
    previous = validator.variable_types.get(iter_name, _MISSING)
    validator.variable_types[iter_name] = iterable_type
    try:
        validate_method_call(validator, call)
    finally:
        if previous is _MISSING:
            validator.variable_types.pop(iter_name, None)
        else:
            validator.variable_types[iter_name] = previous
    stmt.protocol_next = call
    return element_type


def _foreach_iterable_is_addressable(iterable) -> bool:
    """True when the iterable's elements live in addressable container storage."""
    from sushi_lang.semantics.ast import DotCall, MethodCall
    if isinstance(iterable, (MethodCall, DotCall)):
        return iterable.method in ("iter", "keys", "values")
    return False
