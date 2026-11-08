"""A call of a name behind a dot that the namespace does not hold: `geo.twce(42)`.

The seam that says WHICH namespace a receiver names is `semantics/namespaces.py`. The
arms of the typecheck pass over SHIR (`visit/results.py`, `visit/function_values.py`)
share this refusal.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def reject_unknown_namespace_call(validator: 'TypeValidator', written: str,
                                  namespace: Optional[str], name: str, loc) -> None:
    """CE2008 for `written.name(...)`, a call of a name that the namespace does not hold,
    with the closest name that it holds as a help."""
    from sushi_lang.semantics.namespaces import suggest_member
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2008, loc, name=f"{written}.{name}")
    closest = (suggest_member(validator.namespaces.members(namespace), name)
               if namespace else None)
    # A suggestion equal to what the user wrote helps nobody: the member exists
    # and is not what this position takes.
    if closest is not None and closest != name:
        diagnostic = diagnostic.help(f"did you mean '{namespace}.{closest}'?")
    diagnostic.emit()
