"""A type name written behind an alias: `geo.Vec`, `geo.Box@(i32)`, `@(T: geo.Loud)`.

The seam that says WHICH namespace a qualifier names is `semantics/namespaces.py`. This
module is the typecheck pass's half for the positions that hold a TYPE, exactly as
`calls/namespaced.py` is its half for the positions that hold a CALL. Both ask the seam
first and the visibility table second, which is the two-seam rule of
`docs/design/unit-namespaces.md` section 3.

Under phase 1 the qualifier picks which declaration is meant and the table key stays the
bare name (Ruling 6), so nothing below resolves anything: it refuses a qualifier that
names no namespace, a name the namespace does not hold, and a name this unit may not
have. What survives goes on to the ordinary rules unchanged.
"""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.namespaces import suggest_member

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from . import TypeValidator


def written_name(namespace: Optional[str], name: str) -> str:
    """The name as the user wrote it, for a diagnostic to quote."""
    return name if namespace is None else f"{namespace}.{name}"


def reject_qualified_type(validator: 'TypeValidator', type_obj: Optional['Type'],
                          span: Optional[Span]) -> bool:
    """Check the qualifier on a written type. True when it was refused."""
    namespace = getattr(type_obj, "namespace", None)
    if namespace is None:
        return False
    name = getattr(type_obj, "name", None) or getattr(type_obj, "base_name", "")
    return reject_qualified_name(validator, namespace, name, span, kind="type")


def check_qualified_constraints(validator: 'TypeValidator', program: Any) -> None:
    """Check the qualifier on every perk constraint this unit writes.

    Driven by `ast_walk.declarations()`, the one walk over a unit's declarations, so a
    kind that gains type parameters is covered by having been added there. The bare
    constraint's own rules -- the perk exists, and its contract is not private
    (CE4011) -- are the collect pass's and are untouched: this reads the qualifier and
    nothing else.
    """
    from sushi_lang.semantics.ast_walk import declarations

    for _kind, decl in declarations(program):
        type_params = getattr(decl, "type_params", None)
        if not type_params:
            continue
        fallback = getattr(decl, "name_span", None) or getattr(decl, "loc", None)
        for param in type_params:
            namespaces = getattr(param, "constraint_namespaces", None) or ()
            for name, namespace in zip(getattr(param, "constraints", None) or (),
                                       namespaces, strict=False):
                if namespace is not None:
                    reject_qualified_name(validator, namespace, name,
                                          getattr(param, "loc", None) or fallback,
                                          kind="perk")


def reject_qualified_name(validator: 'TypeValidator', namespace: str, name: str,
                          span: Optional[Span], *, kind: str) -> bool:
    """The whole rule for one qualified name in a type position.

    `CE2001` carries both misses. It is the code for a name written where a type
    belongs that names no type, and a qualifier that reaches nothing is that same
    mistake with one more segment to say it in.
    """
    written = written_name(namespace, name)

    if not validator.namespaces.is_namespace(namespace):
        er.emit_with(validator.reporter, er.ERR.CE2001, span, name=written) \
            .help(f"no namespace '{namespace}' is bound in this unit; "
                  f"bind one with `use \"...\" as {namespace}`") \
            .emit()
        return True

    if validator.namespaces.lookup(namespace, name) is None:
        _reject_unknown_member(validator, namespace, name, span, written)
        return True

    if kind == "perk":
        # A perk's CONTRACT is what privacy hides, and `reject_private_perk_contract`
        # already owns that rule for a bare constraint (CE4011). The qualifier changes
        # where the name may be written and never who may promise it.
        return False

    from .visibility import reject_private_type
    return reject_private_type(validator, name, span)


def _reject_unknown_member(validator: 'TypeValidator', namespace: str, name: str,
                           span: Optional[Span], written: str) -> None:
    """The namespace is bound and does not hold this name."""
    origin = validator.namespaces.origin(namespace) or namespace
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2001, span, name=written)
    closest = suggest_member(validator.namespaces.members(namespace), name)
    if closest is not None:
        diagnostic = diagnostic.help(f"did you mean '{namespace}.{closest}'?")
    else:
        diagnostic = diagnostic.help(f"'{origin}' declares no '{name}'")
    diagnostic.emit()
