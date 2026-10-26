"""The dispatch of a pass over SHIR: one table of arms, keyed on the SHIR class (docs/design/ir.md 7.3, 7.9).

The typecheck pass and the borrow pass each walk a body through ONE table of arms, with a
row for each SHIR class that the pass reads. The two passes read the same dispatch here.
`arm_of` gives the arm of a node, and a class with no row is an internal error at the span
of the node: a pass never skips a node in silence. `stop` is the arm of a construct that a
pass does not handle yet: it ends the walk of the body with the stop code CE0155, which
names the stage and the construct, at the span of the node. The construct is the class of
the node. For a `Call`, it names the kind of the target too (`construct_of`).
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, NoReturn

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.shir import exprs as e


def arm_of(arms: Mapping[type, Callable[..., Any]], node: object,
           stage: str) -> Callable[..., Any]:
    """The arm of `node` in the table `arms` of the pass `stage`."""
    arm = arms.get(type(node))
    if arm is None:
        er.raise_internal_error(
            "CE0000", span=getattr(node, "loc", None),
            detail=f"the {stage} pass has no arm for the SHIR class '{type(node).__name__}'")
    return arm


def construct_of(node: Any) -> str:
    """The construct that a stop names: the class of `node`, and the target kind of a `Call`.

    The kind comes from the SHIR node alone, before `typecheck` resolves the callee. A
    call by a written name is `function`: the node does not tell a user function, a stdlib
    function, an extern and a qualified name apart.
    """
    if not isinstance(node, e.Call):
        return type(node).__name__
    target = node.target
    if isinstance(target, e.Dotted):
        kind = "method"
    elif isinstance(target, e.Static):
        kind = "static"
    elif isinstance(target, e.BuiltinFn):
        kind = "builtin"
    elif not isinstance(target.ref, e.GlobalRef):
        kind = "fn value"
    elif target.type_args:
        kind = "generic"
    else:
        kind = "function"
    return f"Call({kind})"


def stop(stage: str, node: Any) -> NoReturn:
    """The stop of the pass `stage` at a construct that it does not handle yet."""
    raise StageStop(stage, construct_of(node), node.loc)
