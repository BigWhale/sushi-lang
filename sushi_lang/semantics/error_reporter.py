"""Error emission helper for semantic passes."""

from typing import Optional
from sushi_lang.internals.report import Span, Reporter, DiagnosticBuilder
from sushi_lang.internals import errors as er


class PassErrorReporter:
    """Thin wrapper for error emission in semantic passes."""

    def __init__(self, reporter: Reporter):
        """Initialize with a reporter instance."""
        self.reporter = reporter
        # When True, emit() / emit_with() are no-ops. Used by a pass that needs a
        # DRY analysis run whose diagnostics must not reach the user -- e.g. the borrow
        # checker's first (fixed-point discovery) pass over a loop body.
        self.suppressed = False

    def emit(self, error_msg: er.ErrorMessage, span: Optional[Span], **kwargs) -> None:
        """Emit an error or warning."""
        if self.suppressed:
            return
        er.emit(self.reporter, error_msg, span, **kwargs)

    def emit_with(self, error_msg: er.ErrorMessage, span: Optional[Span], **kwargs) -> DiagnosticBuilder:
        """Emit an error or warning and return a builder for attaching notes/help."""
        if self.suppressed:
            return _NullDiagnosticBuilder()  # type: ignore[return-value]  # no-op stand-in
        return er.emit_with(self.reporter, error_msg, span, **kwargs)


class _NullDiagnosticBuilder:
    """No-op stand-in returned by a suppressed emit_with(); swallows .note()/.emit()."""

    def note(self, *args, **kwargs) -> "_NullDiagnosticBuilder":
        return self

    def help(self, *args, **kwargs) -> "_NullDiagnosticBuilder":
        return self

    def emit(self, *args, **kwargs) -> None:
        return None
