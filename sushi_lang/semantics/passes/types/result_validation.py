"""The `Result` constructor that a `return` of a body with a channel spells (docs/design/error-channel.md)."""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.results import result_ok_err
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.shir import exprs as e
from .compatibility import types_compatible

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.semantics.shir.exprs import ShirExpr
    from sushi_lang.semantics.typesys import Type


def spells_a_result(validator: 'TypeValidator', node: 'ShirExpr') -> bool:
    """Whether the expression is written `Result.Ok(...)` or `Result.Err(...)`.

    The CE2030 decision (#848). It reads the written expression and no type, so a
    template body takes it as written (#1070).
    """
    return (isinstance(node, e.EnumInit) and node.variant in ("Ok", "Err")
            and validator.decl_name(node.decl) == "Result")


def validate_result_pattern(validator: 'TypeValidator', node: e.EnumInit,
                            channel: 'Type') -> None:
    """Check the payload of a `Result.Ok(...)` / `Result.Err(...)` against the channel.

    The caller decided with `spells_a_result` that the node is one of the two, and checked
    it at the channel; this reads the type of its payload and compares it with the arm of
    the channel (CE2031, CE2039).
    """
    if not node.payload:
        return
    ok_type, err_type = result_ok_err(channel)
    expected = ok_type if node.variant == "Ok" else err_type
    got = validator.body_answers().type_of(node.payload[0].value.shir_id)
    if got is None or types_compatible(validator, got, expected):
        return
    if node.variant == "Ok":
        er.emit(validator.reporter, er.ERR.CE2031, node.loc,
                expected=display_type(expected), got=display_type(got))
    else:
        er.emit(validator.reporter, er.ERR.CE2039, node.loc,
                expected=display_type(expected), got=display_type(got))
