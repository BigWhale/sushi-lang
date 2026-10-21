"""The state of the emission of one `SlirFunction`, and the stop of the stage `emit`."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn, Optional

from llvmlite import ir

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.report import Span
from sushi_lang.semantics.slir.function import SlirFunction
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

#: The name of this stage in the stop code.
STAGE = "emit"


@dataclass(slots=True)
class FunctionState:
    """One function while the emitter fills it: the codegen, the LLVM function and its
    builder, the storage of each local (an entry-block slot, or the incoming address of a
    `peek` or a `poke` parameter), the LLVM block of each SLIR block, and the span of the
    statement or the terminator that the emitter is on. A row of a dispatch table that
    takes one shape (the state and the shape) reads the codegen here."""
    codegen: LLVMCodegen
    function: SlirFunction
    llvm_fn: ir.Function
    builder: ir.IRBuilder
    filename: Optional[str]
    span: Optional[Span] = None
    storage: dict[LocalId, ir.Value] = field(default_factory=dict)
    blocks: dict[BlockId, ir.Block] = field(default_factory=dict)

    def stop(self, shape: object) -> NoReturn:
        """CE0155 `emit, <class>` for a SLIR shape that the emitter does not handle yet."""
        raise StageStop(STAGE, type(shape).__name__, self.span, self.filename)

    def type_of(self, local: LocalId) -> Type:
        """The type of a local. The verifier makes sure that the id of a local is its
        position."""
        return self.function.locals[local.index].ty

    def storage_of(self, local: LocalId) -> ir.Value:
        """The address of the value of `local`."""
        address = self.storage.get(local)
        if address is None:
            self.fault(f"_{local.index} has no storage")
        return address

    def block(self, block: BlockId) -> ir.Block:
        found = self.blocks.get(block)
        if found is None:
            self.fault(f"bb{block.index} is no block of the function")
        return found

    def fault(self, detail: str) -> NoReturn:
        raise_internal_error("CE0000", span=self.span, filename=self.filename,
                             detail=f"the emitter of '{self.function.name}': {detail}")
