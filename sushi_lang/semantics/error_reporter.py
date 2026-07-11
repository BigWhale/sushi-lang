"""
Error emission helper for semantic passes.

Provides a thin wrapper around internals.errors.emit() to reduce boilerplate
in semantic analysis passes. Instead of repeatedly importing and calling:

    from sushi_lang.internals import errors as er
    er.emit(self.reporter, er.ERR.CE2001, span, name="foo")

Passes can use:

    from sushi_lang.semantics.error_reporter import PassErrorReporter
    self.err = PassErrorReporter(self.reporter)
    self.err.emit(er.ERR.CE2001, span, name="foo")

This eliminates the need to pass self.reporter on every error emission call.
"""

from typing import Optional
from sushi_lang.internals.report import Span, Reporter, DiagnosticBuilder
from sushi_lang.internals import errors as er


class PassErrorReporter:
    """Thin wrapper for error emission in semantic passes.

    Reduces boilerplate by binding the reporter instance, allowing
    direct error emission without passing self.reporter each time.

    Example:
        >>> class MyPass:
        >>>     def __init__(self, reporter: Reporter):
        >>>         self.reporter = reporter
        >>>         self.err = PassErrorReporter(reporter)
        >>>
        >>>     def validate(self, span):
        >>>         # Before: er.emit(self.reporter, er.ERR.CE2001, span, name="x")
        >>>         # After:
        >>>         self.err.emit(er.ERR.CE2001, span, name="x")
    """

    def __init__(self, reporter: Reporter):
        """Initialize with a reporter instance.

        Args:
            reporter: The Reporter instance to emit errors to.
        """
        self.reporter = reporter
        # When True, emit() / emit_with() are no-ops. Used by a pass that needs a
        # DRY analysis run whose diagnostics must not reach the user -- e.g. the borrow
        # checker's first (fixed-point discovery) pass over a loop body.
        self.suppressed = False

    def emit(self, error_msg: er.ErrorMessage, span: Optional[Span], **kwargs) -> None:
        """Emit an error or warning.

        Args:
            error_msg: The error message from er.ERR (e.g., er.ERR.CE2001)
            span: Source location span (can be None for some errors)
            **kwargs: Format parameters for the error message

        Example:
            >>> self.err.emit(er.ERR.CE1001, span, name="undefined_var")
            >>> self.err.emit(er.ERR.CE2002, span, expected="i32", got="string")
        """
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
