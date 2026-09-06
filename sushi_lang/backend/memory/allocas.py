"""The one place a stack slot is made.

An `alloca` belongs in the ENTRY block of its function and nowhere else. A slot made at
the current position is made again on every pass of a loop that reaches it, and LLVM
releases the frame only at the return, so the stack grows without a limit until the
program walks off the guard page (#589). It is not an optimizer question either:
`mem2reg` and SROA promote a slot in the entry block alone, so a slot anywhere else stays
in memory at every `--opt` level.

The function comes from the BUILDER, not from a field on the codegen. An out-of-line
destructor or clone body is emitted with `codegen.builder` moved to the new function
while the caller's own emission context stands, and a slot placed by that context would
land in the wrong frame.
"""
from __future__ import annotations

from llvmlite import ir


def entry_alloca(builder: ir.IRBuilder, typ: ir.Type, name: str = "") -> ir.AllocaInstr:
    """Make a stack slot in the entry block of the function `builder` writes into.

    The caller's own builder makes the move and takes its position back, because a
    second builder over one block leaves the first one's insertion anchor pointing at
    the wrong instruction.
    """
    with builder.goto_entry_block():
        return builder.alloca(typ, name=name)
