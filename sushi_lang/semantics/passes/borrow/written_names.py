"""The borrow pass's diagnostics name the written local of an `expand` copy (#1022).

The unroll gives each top-level `let` of an `expand` copy a hidden name, and every
copy of one written `let` carries one `WrittenLet` record. This reporter is the one
seam between the borrow pass and the diagnostic: a hidden name that is a copy local of
this body prints as its written name, and a fault that names a copy local is told once
per written statement. "The same fault" is the code, the record of each copy local it
names, and the written span (`written_span`) -- identities, never text or columns.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable, Dict, Optional, Set

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import DiagnosticBuilder, Reporter, Span
from sushi_lang.semantics.error_reporter import PassErrorReporter, _NullDiagnosticBuilder
from sushi_lang.semantics.generics.monomorphize.unroll import WrittenLet, written_span
from sushi_lang.semantics.hidden_names import HIDDEN_MARK

if TYPE_CHECKING:
    from .state import BorrowState

_HIDDEN_NAME = re.compile(re.escape(HIDDEN_MARK) + r"\w+")


class WrittenNameReporter(PassErrorReporter):
    """`PassErrorReporter` that prints the written name of each `expand` copy local."""

    def __init__(self, reporter: Reporter,
                 states: Callable[[], Dict[str, 'BorrowState']]) -> None:
        super().__init__(reporter)
        self._states = states
        self._told: Set[tuple] = set()

    def enter_body(self) -> None:
        """Forget the faults told in the previous body."""
        self._told = set()

    def emit(self, error_msg: er.ErrorMessage, span: Optional[Span], **kwargs) -> None:
        self.emit_with(error_msg, span, **kwargs).emit()

    def emit_with(self, error_msg: er.ErrorMessage, span: Optional[Span],
                  **kwargs) -> DiagnosticBuilder:
        if self.suppressed or self._told_before(error_msg.code, span, kwargs.values()):
            return _NullDiagnosticBuilder()  # type: ignore[return-value]
        shown = {key: self.shown(value) if isinstance(value, str) else value
                 for key, value in kwargs.items()}
        return _WrittenNameBuilder(super().emit_with(error_msg, span, **shown), self)

    def shown(self, text: str) -> str:
        """``text`` with each copy local of this body spelled as it is written."""
        return _HIDDEN_NAME.sub(lambda m: self._written_name(m.group(0)), text)

    def _written_name(self, name: str) -> str:
        record = self._record_of(name)
        return record.name if record is not None else name

    def _record_of(self, name: str) -> Optional[WrittenLet]:
        state = self._states().get(name)
        return state.written if state is not None else None

    def _told_before(self, code: str, span: Optional[Span], values) -> bool:
        written = written_span(span)
        if written is None:
            return False
        records = frozenset(
            record
            for value in values if isinstance(value, str)
            for name in {value, *_HIDDEN_NAME.findall(value)}
            if (record := self._record_of(name)) is not None
        )
        if not records:
            return False
        key = (code, id(written), records)
        if key in self._told:
            return True
        self._told.add(key)
        return False


class _WrittenNameBuilder(DiagnosticBuilder):
    """A builder whose notes and helps print the written name of each copy local."""

    def __init__(self, inner: DiagnosticBuilder, names: WrittenNameReporter) -> None:
        self._inner = inner
        self._names = names

    def note(self, message: str) -> DiagnosticBuilder:
        self._inner.note(self._names.shown(message))
        return self

    def note_at(self, message: str, span: Span,
                filename: Optional[str] = None) -> DiagnosticBuilder:
        self._inner.note_at(self._names.shown(message), span, filename)
        return self

    def help(self, message: str) -> DiagnosticBuilder:
        self._inner.help(self._names.shown(message))
        return self

    def location_only(self) -> DiagnosticBuilder:
        self._inner.location_only()
        return self

    def emit(self) -> None:
        self._inner.emit()
