"""The stop code of the new pipeline, CE0155 (docs/design/ir.md 11.1).

From the switch to SHIR, a program that the semantic passes accept can reach a construct
that a stage does not handle yet. A stop names the stage and the construct. A stop of the typecheck pass, of the lift pass, of the borrow pass or
of the SLIR lowering ends the work on its body, and the analysis goes on with the next
body and the next unit: so every unit is lowered for `--dump-shir` and `--dump-slir`, and
every error that comes before the walk is found. `Stops` records what the analysis met,
and the pipeline reports ONE stop after the dumps, only when the analysis reported no
error (`Stops.first`). When no stage stopped, every body that reached the SLIR lowering
finished, and the pipeline goes on to the backend: the emitter of a `SlirFunction` gives
the stop of the stage `emit` (`backend/slir/`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.errors import REGISTRY


@dataclass(slots=True)
class Stops:
    """The first stop of the typecheck pass, of the lift pass, of the borrow pass and of
    the SLIR lowering."""
    typecheck: Optional[StageStop] = None
    lift: Optional[StageStop] = None
    borrow: Optional[StageStop] = None
    slir: Optional[StageStop] = None

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

    def first(self, hole: Optional[StageStop]) -> Optional[StageStop]:
        """The stop that the pipeline reports: a stop of the typecheck pass, of the lift
        pass, of the borrow pass, an error hole whose codes no error offered (`hole`), then
        a stop of the SLIR lowering. None when nothing stopped: the program goes on to the
        backend."""
        for stop in (self.typecheck, self.lift, self.borrow, hole, self.slir):
            if stop is not None:
                return stop
        return None


def parse_stop(output: str) -> Optional[Tuple[str, str]]:
    """The (stage, construct) of the first CE0155 line in compiler output, or None.

    The pattern comes from the registry text of CE0155, so the message has one home.
    """
    text = re.escape(REGISTRY[StageStop.code].text)
    for name in ("stage", "construct"):
        text = text.replace(re.escape("{" + name + "}"), rf"(?P<{name}>\w+)")
    found = re.search(text, output)
    return (found["stage"], found["construct"]) if found else None
