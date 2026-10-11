"""The written operators of an expression. The AST and SHIR both name them."""
from __future__ import annotations

from typing import Literal, TypeAlias

UnOp: TypeAlias = Literal["neg", "not", "~"]
BinOp: TypeAlias = Literal["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=",
                           "and", "or", "xor", "&", "|", "^", "<<", ">>"]
