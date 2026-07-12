# semantics/passes/collect/utils.py
"""Shared utilities for collection passes."""

from __future__ import annotations
from typing import Any, List, Optional

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.typesys import Type


def extract_type_param_names(type_params_raw: Optional[List]) -> Optional[List[str]]:
    """Extract type parameter names from AST type_params.

    Handles both legacy List[str] and new List[BoundedTypeParam] formats.

    Args:
        type_params_raw: Raw type_params from AST (may be None, List[str], or List[BoundedTypeParam])

    Returns:
        List of parameter names as strings, or None if no parameters
    """
    if type_params_raw is None:
        return None

    if not isinstance(type_params_raw, list) or len(type_params_raw) == 0:
        return None

    names = []
    for tp in type_params_raw:
        if isinstance(tp, str):
            # Legacy format: direct string
            names.append(tp)
        elif isinstance(tp, BoundedTypeParam):
            # New format: BoundedTypeParam with .name attribute
            names.append(tp.name)
        else:
            # Unknown format - skip
            continue

    return names if names else None


def param_from_node(p: Any, idx: int) -> 'Param':
    """Convert AST parameter node to Param dataclass.

    Args:
        p: AST parameter node
        idx: Parameter index (for fallback naming)

    Returns:
        Param dataclass instance
    """
    from .functions import Param  # Import here to avoid circular dependency

    # Expect object-style params with .name/.ty and optional spans
    pname = getattr(p, "name", None)
    pty: Optional[Type] = getattr(p, "ty", None)
    pname_span: Optional[Span] = getattr(p, "name_span", None)
    ptype_span: Optional[Span] = getattr(p, "type_span", None)

    # Defensive fallbacks
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
    )


def note_first_declaration(builder: Any, spans: dict, name: str,
                           what: str = "first defined here") -> Any:
    """Attach the ORIGINAL declaration's location to a duplicate-declaration error.

    A redeclaration is relational: it is only an error because of the first one, and
    the first one is the half the user cannot see. If the name was predefined by the
    compiler there is no span to point at, so say that instead.
    """
    prev = spans.get(name)
    if prev is not None:
        return builder.note(what, prev)
    return builder.note("defined by the compiler")


def get_span(node: Any, *attrs: str) -> Optional[Span]:
    """Get first non-None span from node attributes.

    Args:
        node: AST node
        *attrs: Attribute names to check in order

    Returns:
        First non-None span, or None if all are None
    """
    for attr in attrs:
        span = getattr(node, attr, None)
        if span is not None:
            return span
    return None
