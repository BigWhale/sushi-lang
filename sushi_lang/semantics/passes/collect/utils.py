# semantics/passes/collect/utils.py
"""Shared utilities for collection passes."""

from __future__ import annotations
from typing import Any, List, Optional

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.generics.types import TypeParameter
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


def format_location(reporter: Reporter, span: Optional[Span]) -> str:
    """Format span information for error messages.

    Args:
        reporter: Reporter instance with filename
        span: Optional span information

    Returns:
        Formatted location string (e.g., "file.sushi:10:5")
    """
    if not span:
        return reporter.filename
    return f"{reporter.filename}:{span.line}:{span.col}"


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
    )


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
