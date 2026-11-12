"""The LLVM signature of a function of the program, the one rule for both ends of a call.

A value that moves by address (`moves_by_address`: a `T[N]` of more than 64 bytes) is
never a first-class value at a call boundary. As a parameter it crosses as a pointer, and
the callee copies the bytes into its own slot. As the answer it goes into a slot that the
caller gives: the LAST parameter, and the function answers `void`. The last position keeps
the environment of a function value first, so a thunk forwards its parameters unchanged.
A C function keeps the C signature (`unsafe external`), and so does `main`
(docs/design/array-representation.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from llvmlite import ir

from sushi_lang.backend.types.arrays.runs import moves_by_address


@dataclass(frozen=True)
class CallSignature:
    """The LLVM type of a function, which parameter crosses by address, and whether the
    answer goes into the slot that the caller gives as the last parameter."""
    function_type: ir.FunctionType
    by_address: tuple[bool, ...]
    answer_by_address: bool
    answer: ir.Type


def call_signature(answer: ir.Type, params: Sequence[ir.Type]) -> CallSignature:
    """The signature of a function that answers `answer` and takes `params`. Each type is
    the LLVM type of the convention: a `peek` or a `poke` parameter is already a pointer."""
    by_address = tuple(moves_by_address(param) for param in params)
    ll_params = [param.as_pointer() if moved else param
                 for param, moved in zip(params, by_address, strict=True)]
    if moves_by_address(answer):
        return CallSignature(ir.FunctionType(ir.VoidType(), ll_params + [answer.as_pointer()]),
                             by_address, True, answer)
    return CallSignature(ir.FunctionType(answer, ll_params), by_address, False, answer)
