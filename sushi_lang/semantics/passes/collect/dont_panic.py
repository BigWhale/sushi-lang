"""The `dont_panic` gate and the inert-marker warning (docs/design/dont-panic.md, s6, s8).

Read once per declaration, at the marker, in the collect pass: one CE0152 for each
declaration that the unit may not mark, else one CW0004 for each marked body that holds
no index to uncheck. The hybrid templates of a library meet the same gate in
`library_registration.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import ExtendDef
from sushi_lang.semantics.unchecked_index import holds_an_unchecked_index

if TYPE_CHECKING:
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.units import Unit


DONT_PANIC_FLAG = "--dont-panic"


@dataclass(frozen=True)
class MarkerGate:
    """What one unit may do with the marker.

    `allowed`: the unit is a bundled stdlib unit, or the build passes the flag (D7, D8).
    `library`: the source library of the unit, which the error names (D9).
    `warns_inert`: the unit is its author's, so an inert marker is a warning (D12).
    `counts`: a marker here is a use of the flag (D13). A bundled stdlib marker is not.
    """
    allowed: bool
    library: Optional[str] = None
    warns_inert: bool = False
    counts: bool = False

    @classmethod
    def for_unit(cls, unit: 'Unit', flag: bool) -> 'MarkerGate':
        stdlib = unit.is_bundled_stdlib
        return cls(allowed=stdlib or flag, library=unit.library_name,
                   warns_inert=unit.provenance is None or stdlib, counts=not stdlib)

    @classmethod
    def for_library_template(cls, library: str, flag: bool) -> 'MarkerGate':
        """A template of a library, parsed again in the consumer's build."""
        return cls(allowed=flag, library=library, counts=True)


def _subject(decl: Any, library: Optional[str]) -> str:
    if isinstance(decl, ExtendDef) and decl.is_conversion:
        from sushi_lang.semantics.generics.type_display import display_type
        name = (f"the conversion '{display_type(decl.target_type)} "
                f"as {display_type(decl.ret)}'")
    else:
        name = f"'{decl.name}'"
    return name if library is None else f"{name} of the library '{library}'"


def judge_marker(reporter: 'Reporter', gate: MarkerGate, decl: Any,
                 filename: Optional[str]) -> bool:
    """Judge the marker of one declaration. The answer is True when the marker is a use
    of the flag."""
    if decl.dont_panic is None:
        return False
    span = decl.dont_panic_span or decl.name_span
    subject = _subject(decl, gate.library)
    if not gate.allowed:
        help_text = (f"the library '{gate.library}' asks for unchecked indexes; "
                     f"pass {DONT_PANIC_FLAG} to consent"
                     if gate.library is not None else
                     f"pass {DONT_PANIC_FLAG} to allow the unchecked indexes of this "
                     f"build, or remove the marker")
        er.emit_with(reporter, er.ERR.CE0152, span, filename=filename,
                     subject=subject).help(help_text).emit()
        return False
    if gate.warns_inert and not holds_an_unchecked_index(decl.body):
        er.emit_with(reporter, er.ERR.CW0004, span, filename=filename,
                     subject=subject) \
            .help("remove the marker; an index in a lambda keeps its check").emit()
    return gate.counts


def check_dont_panic(collector: Any, decl: Any) -> None:
    """The gate for one declaration of the unit that `collector` collects."""
    gate: Optional[MarkerGate] = collector.marker_gate
    if gate is not None and judge_marker(collector.r, gate, decl,
                                         collector.current_unit_file):
        collector.marked.append(decl.name)
