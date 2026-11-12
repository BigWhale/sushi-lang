"""The dispatch of a pass over SHIR: one table of arms, keyed on the SHIR class (docs/design/ir.md 7.3, 7.9).

The typecheck pass and the borrow pass each walk a body through ONE table of arms, with a
row for each SHIR class that the pass reads. The two passes read the same dispatch here.
`arm_of` gives the arm of a node, and a class with no row is an internal error at the span
of the node: a pass never skips a node in silence. `unhandled` is the internal error of a
node that a stage has a row for but cannot handle in the shape or the state that it has.
In a body that has an error, such a node is the trace of that error: the walk of the body
ends there (`AfterAnError`), and the error is the diagnostic (docs/design/ir.md 7.9).
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, NoReturn, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import AfterAnError


def arm_of(arms: Mapping[type, Callable[..., Any]], node: object,
           stage: str) -> Callable[..., Any]:
    """The arm of `node` in the table `arms` of the pass `stage`."""
    arm = arms.get(type(node))
    if arm is None:
        er.raise_internal_error(
            "CE0000", span=getattr(node, "loc", None),
            detail=f"the {stage} pass has no arm for the SHIR class '{type(node).__name__}'")
    return arm


def unhandled(stage: str, node: Any, *, state: Optional[str] = None,
              after_an_error: bool = False) -> NoReturn:
    """CE0000 at `node`: the stage `stage` does not handle the construct, or the construct
    in the `state` that it has. In a body that has an error (`after_an_error`), the walk of
    the body ends instead. The callers count an error of ANY unit of the program as such an
    error (`program_failed`), so a construct that a stage does not handle shows no CE0000 in
    a program that fails for another reason: the internal fault stays hidden."""
    if after_an_error:
        raise AfterAnError
    construct = type(node).__name__ if state is None else f"{type(node).__name__} {state}"
    er.raise_internal_error("CE0000", span=getattr(node, "loc", None),
                            detail=f"the {stage} stage does not handle the construct "
                                   f"'{construct}'")
