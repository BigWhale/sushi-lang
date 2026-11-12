"""The state of the emission of one `SlirFunction`."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn, Optional

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.report import Span
from sushi_lang.semantics.slir.function import SlirFunction
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

@dataclass(slots=True)
class FunctionState:
    """One function while the emitter fills it: the codegen, the LLVM function and its
    builder, the storage of each local (an entry-block slot, or the incoming address of a
    `peek` or a `poke` parameter), the slot that holds the address of each address local,
    the LLVM block of each SLIR block, and the span of the statement or the terminator that
    the emitter is on. Every function of the emitter after the stage entry takes the state,
    and reads the codegen here."""
    codegen: LLVMCodegen
    function: SlirFunction
    llvm_fn: ir.Function
    builder: ir.IRBuilder
    filename: Optional[str]
    span: Optional[Span] = None
    storage: dict[LocalId, ir.Value] = field(default_factory=dict)
    addresses: dict[LocalId, ir.Value] = field(default_factory=dict)
    blocks: dict[BlockId, ir.Block] = field(default_factory=dict)

    def unhandled(self, shape: object) -> NoReturn:
        """CE0000 for a SLIR shape that the emitter does not handle. A string is the name of
        a construct that the class does not tell apart."""
        name = shape if isinstance(shape, str) else type(shape).__name__
        self.fault(f"no arm for the SLIR shape '{name}'")

    def type_of(self, local: LocalId) -> Type:
        """The type of a local. The verifier makes sure that the id of a local is its
        position."""
        return self.function.locals[local.index].ty

    def storage_of(self, local: LocalId) -> ir.Value:
        """The address of the value of `local`. The storage of an address local is the
        address that its `Ref` wrote into its slot."""
        slot = self.addresses.get(local)
        if slot is not None:
            return self.builder.load(slot)
        address = self.storage.get(local)
        if address is None:
            self.fault(f"_{local.index} has no storage")
        return address

    def call(self, callee: ir.Value, args: list[ir.Value]) -> ir.Value:
        """The `call` instruction of a `Direct` callee: a function of the build or a C
        function. The emitters of an indirect call, a cursor step and an adapter call
        `builder.call` themselves."""
        return self.builder.call(callee, args)

    def block(self, block: BlockId) -> ir.Block:
        found = self.blocks.get(block)
        if found is None:
            self.fault(f"bb{block.index} is no block of the function")
        return found

    def fault(self, detail: str) -> NoReturn:
        raise_internal_error("CE0000", span=self.span, filename=self.filename,
                             detail=f"the emitter of '{self.function.name}': {detail}")
