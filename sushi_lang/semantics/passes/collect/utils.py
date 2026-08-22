"""Shared utilities for collection passes."""

from __future__ import annotations
from typing import Any, List, Optional

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import BoundedTypeParam, Param
from sushi_lang.semantics.typesys import Type


def extract_type_param_names(type_params_raw: Optional[List]) -> Optional[List[str]]:
    """Extract type parameter names from AST type_params."""
    if type_params_raw is None:
        return None

    if not isinstance(type_params_raw, list) or len(type_params_raw) == 0:
        return None

    names = []
    for tp in type_params_raw:
        if isinstance(tp, str):
            names.append(tp)
        elif isinstance(tp, BoundedTypeParam):
            names.append(tp.name)
        else:
            continue

    return names if names else None


def param_from_node(p: Any, idx: int) -> 'Param':
    """Convert AST parameter node to Param dataclass."""
    from .functions import Param  # Import here to avoid circular dependency

    pname = getattr(p, "name", None)
    pty: Optional[Type] = getattr(p, "ty", None)
    pname_span: Optional[Span] = getattr(p, "name_span", None)
    ptype_span: Optional[Span] = getattr(p, "type_span", None)

    if not isinstance(pname, str):
        pname = str(pname) if pname is not None else f"_p{idx}"

    return Param(
        name=pname,
        ty=pty,
        name_span=pname_span,
        type_span=ptype_span,
        index=idx,
        is_variadic=bool(getattr(p, "is_variadic", False)),
        is_pack=bool(getattr(p, "is_pack", False)),
        is_nom=bool(getattr(p, "is_nom", False)),
    )


def note_first_declaration(builder: Any, spans: dict, name: str,
                           what: str = "first defined here") -> Any:
    """Attach the ORIGINAL declaration's location to a duplicate-declaration error."""
    prev = spans.get(name)
    if prev is not None:
        return builder.note(what, prev)
    return builder.note("defined by the compiler")


def get_span(node: Any, *attrs: str) -> Optional[Span]:
    """Get first non-None span from node attributes."""
    for attr in attrs:
        span = getattr(node, attr, None)
        if span is not None:
            return span
    return None


def reject_reference_in(reporter, ty: Optional[Type], span: Optional[Span],
                        code) -> bool:
    """Reject a reference type in a position that has no semantics for one (R4)."""
    from sushi_lang.internals import errors as er
    from sushi_lang.semantics.generics.type_display import display_type
    from sushi_lang.semantics.type_predicates import contains_reference

    if not contains_reference(ty):
        return False
    er.emit(reporter, code, span, ty=display_type(ty))
    return True


def reject_try_in_body(reporter, body: Any, context: str) -> None:
    """Reject every `??` in an extension or perk method body (CE0131, #398).

    These bodies return a bare value (CE2091), so a `??` has nothing to
    propagate into. The rule is structural, so the collect pass owns it: the walk sees
    the DECLARATION, fires once per occurrence, and covers a template nobody
    instantiates. The walk SKIPS lambda subtrees: a lambda has
    its own Result channel, so a `??` inside one is legal (#399).
    """
    import dataclasses

    from sushi_lang.internals import errors as er
    from sushi_lang.semantics.ast import Lambda, Node, TryExpr

    def walk(node: Any) -> None:
        if isinstance(node, Lambda):
            return
        if isinstance(node, TryExpr):
            er.emit_with(reporter, er.ERR.CE0131,
                         getattr(node, "loc", None), context=context) \
                .help("handle the Result in the body: match on it, or use "
                      ".realise(default)").emit()
            walk(node.expr)
            return
        # `If.arms` holds plain (cond, Block) tuples, so tuples walk too.
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if isinstance(node, Node):
            for f in dataclasses.fields(node):
                walk(getattr(node, f.name))

    walk(body)
