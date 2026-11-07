"""Statement validation for type validation."""
from __future__ import annotations
from itertools import count
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.hidden_names import hidden_name
from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import EnumType, IteratorType
from sushi_lang.semantics.ast import Foreach, DotCall, MethodCall, Name, RangeExpr
from sushi_lang.semantics.param_modes import ParamMode, receiver_mode
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from .utils import validate_type_name
from .compatibility import types_compatible
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from . import TypeValidator


def reject_unspelled_result(validator: 'TypeValidator', value) -> None:
    """CE2030: a `return` in a channel body spells no `Result` constructor.

    Two callers, one decision (`spells_a_result`): the `Return` arm of a body as it is
    walked, and a template body as it is written (#1070).
    """
    er.emit_with(validator.reporter, er.ERR.CE2030, value.loc) \
        .help("wrap return value: return Result.Ok(value)").emit()


def validate_foreach_statement(validator: 'TypeValidator', stmt: Foreach) -> None:
    """Validate foreach statement: check iterator type and item variable. A range is
    checked by the typecheck pass over SHIR (`visit/ranges.py`)."""
    validator.validate_expression(stmt.iterable)
    iterable_type = validator.infer_expression_type(stmt.iterable)

    if iterable_type is None:
        # With an error on record the build stops before the backend; with none, a
        # missing type here is a gap in the compiler, and it is told at the iterable.
        if not validator.reporter.has_errors:
            er.emit(validator.reporter, er.ERR.CE0146, stmt.iterable.loc)
        return

    if isinstance(iterable_type, IteratorType):
        element_type = iterable_type.element_type
        _choose_the_walk(validator, stmt, iterable_type)
    else:
        # Not an iterator: it may still carry `next()` (HANDLES.md ruling R21). The
        # protocol is asked SECOND, because `Iterator@(T)` is a cursor over contiguous
        # storage and its walk needs no call at all.
        element_type = resolve_protocol_iterator(validator, stmt, iterable_type)
        if element_type is None:
            # A `next()` that a target bound refuses for this type is that fault, and
            # not a type with no `next()` (#1070): one fault, one diagnostic.
            from sushi_lang.semantics.passes.types.calls.methods import (
                RESOLUTION_REPORTED, _answer_from_template)
            if _answer_from_template(validator, iterable_type, "next", stmt.iterable.loc,
                                     report=True) is RESOLUTION_REPORTED:
                return
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

    item_type = stmt.item_type
    if stmt.item_borrow is not None:
        # The binding's registered type is the REFERENCE, so every consumer that asks
        # "is this name a borrow?" (the borrow pass's rules, backend deref machinery) gets the
        # truthful answer; expression inference auto-derefs a reference-typed name.
        from sushi_lang.semantics.param_modes import borrow_mode
        from sushi_lang.semantics.typesys import ReferenceType
        item_type = ReferenceType(stmt.item_type, borrow_mode(stmt.item_borrow))
    _validate_bound_body(validator, stmt.item_name, item_type, stmt.body)


def _validate_bound_body(validator: 'TypeValidator', name: str, ty: 'Optional[Type]',
                         body) -> None:
    """Validate a loop body with its binder bound to `ty`.

    The binding lives for the body and no longer (#341), so whatever it shadows is saved
    and restored. Without that, an outer local kept the binder's type.
    """
    saved = ({name: validator.variable_types[name]}
             if name in validator.variable_types else {})
    validator.variable_types[name] = ty
    try:
        validator._validate_block(body)
    finally:
        validator.variable_types.pop(name, None)
        validator.variable_types.update(saved)


# One counter for the whole process, for the same reason the AST builder keeps one: the
# hidden local a protocol loop holds its iterator in must be a name no source can write,
# and a nested loop must not reuse its parent's.
_protocol_iter_ids = count()


def _result_ok_payload(ty):
    """The Ok payload of a `Result@(T, E)`, or None when this is not one."""
    if not isinstance(ty, EnumType) or not is_instance_of(ty, "Result"):
        return None
    ok_variant = ty.get_variant("Ok")
    if ok_variant is None or len(ok_variant.associated_types) != 1:
        return None
    return ok_variant.associated_types[0]


def _maybe_some_payload(ty):
    """The Some payload of a `Maybe@(T)`, or None when this is not one."""
    if not isinstance(ty, EnumType) or not is_instance_of(ty, "Maybe"):
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
    if protocol_item_type(validator, iterable_type) is None:
        return None
    if stmt.protocol_iter_name is None:
        stmt.protocol_iter_name = hidden_name("fe_iter", next(_protocol_iter_ids))
    element_type, call = protocol_next_call(validator, iterable_type,
                                            stmt.protocol_iter_name, stmt.iterable.loc)
    stmt.protocol_next = call
    return element_type


def protocol_item_type(validator: 'TypeValidator', iterable_type) -> Optional[Type]:
    """The T of a nullary `next()` answering `Maybe@(T)` on this type, or None.

    The one rule of the protocol, for a `foreach` and for `Iterator.new(nom x)` alike.
    It reports nothing and stamps nothing.
    """
    from sushi_lang.semantics.passes.types.calls.methods import (
        RESOLUTION_REPORTED, extension_call_result_type, resolve_method)

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
    return _maybe_some_payload(extension_call_result_type(validator, resolved.method))


def protocol_next_call(validator: 'TypeValidator', iterable_type, iter_name: str,
                       loc) -> tuple[Optional[Type], MethodCall]:
    """Build and validate `<iter_name>.next()` on a walkable type, and answer its item.

    The call is VALIDATED against a hidden local that holds the value, which stamps the
    receiver mode, the symbol and the return type, so the backend needs no second
    resolution of its own.
    """
    from sushi_lang.semantics.passes.types.calls.methods import validate_method_call

    call = MethodCall(receiver=Name(id=iter_name, loc=loc), method="next", args=[], loc=loc)
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
    return protocol_item_type(validator, iterable_type), call


def _choose_the_walk(validator: 'TypeValidator', stmt: Foreach, iterable_type) -> None:
    """A walk of an `Iterator@(T)`: a direct walk, or a walk of a value (#1259).

    A range and a built-in producer written as the iterable are walked DIRECTLY: the loop
    reads the source with no call through `next_fn` and no allocation. Every other
    iterator is a VALUE, and the loop calls its `next_fn`. The borrow pass reads this
    choice: it stamps the producer of a direct walk, and it decides whether a value is
    walked in place (ruling S6, docs/design/ir.md).
    """
    from sushi_lang.semantics.generics.iterators import is_producer
    from .utils import settle_item_modes

    if isinstance(stmt.iterable, RangeExpr) or is_producer(stmt.iterable):
        return
    stmt.value_iterator = settle_item_modes(validator, iterable_type)


def _foreach_iterable_is_addressable(iterable) -> bool:
    """True when the iterable's elements live in addressable container storage."""
    from sushi_lang.semantics.ast import MethodCall
    if isinstance(iterable, (MethodCall, DotCall)):
        return iterable.method in ("iter", "keys", "values")
    return False
