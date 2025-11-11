"""Custom exceptions for AST building errors."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from internals.report import Span


class BlankReturnSyntaxError(Exception):
    """Exception raised when Ok() is used without a value (likely needs Ok(~) for blank type)."""
    def __init__(self, message: str, span: Optional['Span'] = None):
        super().__init__(message)
        self.span = span


class UnterminatedInterpolationError(Exception):
    """Exception raised when string interpolation braces are not properly closed."""
    def __init__(self, message: str, span: Optional['Span'] = None):
        super().__init__(message)
        self.span = span


class EmptyInterpolationError(Exception):
    """Exception raised when string interpolation braces contain no expression."""
    def __init__(self, message: str, span: Optional['Span'] = None):
        super().__init__(message)
        self.span = span


class CStyleOctalError(Exception):
    """Exception raised when C-style octal literal (leading zero) is detected."""
    def __init__(self, literal: str, span: Optional['Span'] = None):
        message = f"C-style octal literal '{literal}' is not supported. Use '0o' prefix instead."
        super().__init__(message)
        self.literal = literal
        self.span = span
