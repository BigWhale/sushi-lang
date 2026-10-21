"""The written operators of an expression. The AST and SHIR both name them."""
from __future__ import annotations

from typing import Literal, TypeAlias

UnOp: TypeAlias = Literal["neg", "not", "~"]
BinOp: TypeAlias = Literal["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=",
                           "and", "or", "xor", "&", "|", "^", "<<", ">>"]

#: The operators that read `Eq`.
EQUALITY_OPS = ("==", "!=")
# Arithmetic accepts no non-numeric type at all, so its set needs no name. The
# operators it covers do: the unary minus arrives as '-' and not as 'neg'.
ARITHMETIC_OPS = ("+", "-", "*", "/", "%")
DIVISION_OPS = ("/", "%")
COMPARISON_OPS = EQUALITY_OPS + ("<", "<=", ">", ">=")
#: The bitwise operators whose two operands have the type of the operation.
BITWISE_OPS = ("&", "|", "^")
#: A shift: the count has a type of its own.
SHIFT_OPS = ("<<", ">>")
