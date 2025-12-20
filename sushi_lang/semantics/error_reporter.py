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
from sushi_lang.internals.report import Span, Reporter
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
        er.emit(self.reporter, error_msg, span, **kwargs)
