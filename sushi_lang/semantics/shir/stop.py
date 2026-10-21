"""The stop code of the new pipeline, CE0155 (docs/design/ir.md 11.1).

From the switch to SHIR, a program that the semantic passes accept reaches a stage that
is not built yet, or a construct that a stage does not handle yet. A stop names the stage
and the construct. A stop of the typecheck pass, of the lift pass, of the borrow pass or
of the SLIR lowering ends the work on its body, and the analysis goes on with the next
body and the next unit: so every unit is lowered for `--dump-shir` and `--dump-slir`, and
every error that comes before the walk is found. `Stops` records what the analysis met,
and the pipeline reports ONE stop after the dumps, only when the analysis reported no
error (`Stops.first`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span

#: The stage after the SLIR lowering, which is not built yet.
NEXT_STAGE = "emit"


@dataclass(slots=True)
class Stops:
    """The first stop of the typecheck pass, of the lift pass, of the borrow pass and of
    the SLIR lowering, and the stop of the first body that the SLIR lowering finished: that
    body meets the next stage."""
    typecheck: Optional[StageStop] = None
    lift: Optional[StageStop] = None
    borrow: Optional[StageStop] = None
    slir: Optional[StageStop] = None
    next_stage: Optional[StageStop] = None

    def stopped(self, stop: StageStop) -> None:
        if self.typecheck is None:
            self.typecheck = stop

    def lift_stopped(self, stop: StageStop) -> None:
        if self.lift is None:
            self.lift = stop

    def borrow_stopped(self, stop: StageStop) -> None:
        if self.borrow is None:
            self.borrow = stop

    def slir_stopped(self, stop: StageStop) -> None:
        if self.slir is None:
            self.slir = stop

    def finished(self, body: str, span: Optional[Span], filename: Optional[str]) -> None:
        if self.next_stage is None:
            self.next_stage = StageStop(NEXT_STAGE, body, span, filename)

    def first(self, hole: Optional[StageStop]) -> StageStop:
        """The stop that the pipeline reports: a stop of the typecheck pass, of the lift
        pass, of the borrow pass, an error hole whose codes no error offered (`hole`), a
        stop of the SLIR lowering, the first finished body, then the program when no body
        was lowered."""
        for stop in (self.typecheck, self.lift, self.borrow, hole, self.slir, self.next_stage):
            if stop is not None:
                return stop
        return StageStop(NEXT_STAGE, "the program")
