"""The typecheck half of the borrowed return `peek T` (#1229, docs/design/borrow-model.md S10h).

The faults of a declaration (no source, an `E` that owns), a function value of a callable
with a borrowed return, and the stamp on each call: where the sources stand in it. The
source set itself is `semantics/borrowed_return.py`'s, and the borrow pass reads it too.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.borrowed_return import (
    borrowed_return_of, error_owns, source_positions, sources_in)

if TYPE_CHECKING:
    from . import TypeValidator


def stamp_call_sources(validator: 'TypeValidator', call: Any, callee: Any) -> None:
    """Stamp where the sources of `callee` stand in `call` (`callee_borrows`).

    `callee` is the declaration or the signature the call resolved to. A callee with an
    owned return leaves the stamp None.
    """
    record = borrowed_return_of(callee)
    if record is None:
        return
    names = [param.name for param in getattr(callee, "params", None) or ()]
    call.callee_borrows = source_positions(record, names, validator.tables)


def reject_borrowed_return_faults(validator: 'TypeValidator', node: Any) -> None:
    """CE2438 for an empty source set, CE2442 for an `E` that owns a resource.

    Judged on the declaration as written, so a template says each fault once.
    """
    record = borrowed_return_of(node)
    if record is None or record.refused:
        return
    if not sources_in(record, validator.tables):
        validator.err.emit_with(er.ERR.CE2438, record.span, name=node.name) \
            .help("take the value that the answer views as a borrowed parameter, or "
                  "drop `peek` and return an owned value").emit()
    err_type = getattr(node, "err_type", None)
    if error_owns(record, err_type, validator.tables):
        from sushi_lang.semantics.generics.type_display import display_type
        span: Optional[Span] = getattr(node, "err_span", None) or record.span
        validator.err.emit_with(er.ERR.CE2442, span, err=display_type(err_type)) \
            .help("give the error a plain payload, such as a code or an offset").emit()


def reject_borrowed_function_value(validator: 'TypeValidator', sig: Any, name: str,
                                   loc: Optional[Span]) -> bool:
    """CE2441 when `sig` has a borrowed return and is named as a value. True when refused."""
    if borrowed_return_of(sig) is None:
        return False
    validator.err.emit_with(er.ERR.CE2441, loc, name=name) \
        .help(f"call '{name}' directly, or wrap the call in a lambda that returns an "
              f"owned value").emit()
    return True
