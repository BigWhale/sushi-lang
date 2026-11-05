"""The SLIR unit: one function, its locals and its blocks, and the program (docs/design/ir.md 8.2).

`SlirProgram` keeps the outcome of the SLIR lowering of each SHIR body, keyed by the
`DeclId` of the body: the `SlirFunction`, or the construct at which the lowering stopped
(`SlirStopped`). The dump reads it in the order of the SHIR units and bodies. The SLIR
stage (`lower.py`) is the one writer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.ids import DeclId
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.slir.statements import SlirStatement
from sushi_lang.semantics.slir.terminators import SlirTerminator
from sushi_lang.semantics.typesys import Type

#: The name of this stage in the stop code.
STAGE = "slir"


@dataclass(frozen=True, slots=True)
class SlirLocal:
    """`name` is the source name of the local, for a diagnostic; None for a temporary.
    `view` marks a borrowed view: a `Copy` writes it, and it never drops or moves (ir.md
    8.2)."""
    id: LocalId
    ty: Type
    name: Optional[str]
    loc: Optional[Span]
    view: bool = False


@dataclass(frozen=True, slots=True)
class SlirBlock:
    id: BlockId
    statements: tuple[SlirStatement, ...]
    terminator: SlirTerminator


@dataclass(frozen=True, slots=True)
class SlirFunction:
    """`symbol` is the name in the module (monomorphic, ruling Q1), `name` the source name
    that the printer writes, and `unit` the declaring unit, whose module holds it.
    `locals[0]` is the return slot, and `params` names each parameter local with its
    convention."""
    symbol: str
    name: str
    unit: str
    params: tuple[tuple[LocalId, Convention], ...]
    locals: tuple[SlirLocal, ...]
    blocks: tuple[SlirBlock, ...]
    entry: BlockId


@dataclass(frozen=True, slots=True)
class SlirStopped:
    """A body whose SLIR lowering stopped at the construct `construct`."""
    name: str
    construct: str


SlirOutcome: TypeAlias = SlirFunction | SlirStopped


@dataclass(slots=True)
class SlirProgram:
    """The outcome of the SLIR lowering of each SHIR body that `borrow` finished."""
    _outcomes: dict[DeclId, SlirOutcome] = field(default_factory=dict)

    def record(self, body: DeclId, outcome: SlirOutcome) -> None:
        if body in self._outcomes:
            er.raise_internal_error("CE0000", detail="SLIR lowering lowered one body two times")
        self._outcomes[body] = outcome

    def outcome_of(self, body: DeclId) -> Optional[SlirOutcome]:
        """The outcome of the body `body`, or None when the body did not reach SLIR."""
        return self._outcomes.get(body)
