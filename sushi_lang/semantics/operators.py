"""The written operators of an expression. The AST and SHIR both name them."""
from __future__ import annotations

from typing import Final, Literal, TypeAlias

UnOp: TypeAlias = Literal["neg", "not", "~"]
BinOp: TypeAlias = Literal["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=",
                           "and", "or", "xor", "&", "|", "^", "<<", ">>"]

NEG_OP: Final = "neg"
NOT_OP: Final = "not"
COMPLEMENT_OP: Final = "~"
ADD_OP: Final = "+"
DIV_OP: Final = "/"
EQ_OP: Final = "=="
NE_OP: Final = "!="
AND_OP: Final = "and"
XOR_OP: Final = "xor"
SHL_OP: Final = "<<"
SHR_OP: Final = ">>"

#: The operators that read `Eq`.
EQUALITY_OPS = ("==", "!=")
# Arithmetic accepts no non-numeric type at all, so its set needs no name. The
# operators it covers do: the unary minus arrives as '-' and not as 'neg'.
ARITHMETIC_OPS = ("+", "-", "*", "/", "%")
DIVISION_OPS = ("/", "%")
#: The comparison operators that order their operands.
ORDERING_OPS = ("<", "<=", ">", ">=")
COMPARISON_OPS = EQUALITY_OPS + ORDERING_OPS
#: The operators over two `bool` operands; `and` and `or` are control flow (a short circuit).
LOGICAL_OPS = ("and", "or", "xor")
SHORT_CIRCUIT_OPS = ("and", "or")
#: The bitwise operators whose two operands have the type of the operation.
BITWISE_OPS = ("&", "|", "^")
#: A shift: the count has a type of its own.
SHIFT_OPS = ("<<", ">>")
#: The unary operators whose operand has the type of the operation (#448).
TYPE_KEEPING_UNARY = ("neg", "~")
