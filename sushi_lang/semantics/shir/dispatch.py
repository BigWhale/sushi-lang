"""The dispatch of a pass over SHIR: one table of arms, keyed on the SHIR class (docs/design/ir.md 7.3, 7.9).

The typecheck pass and the borrow pass each walk a body through ONE table of arms, with a
row for each SHIR class that the pass reads. The two passes read the same dispatch here.
`arm_of` gives the arm of a node, and a class with no row is an internal error at the span
of the node: a pass never skips a node in silence. `stop` is the arm of a construct that a
pass does not handle yet: it ends the walk of the body with the stop code CE0155, which
names the stage and the construct, at the span of the node. The construct is the class of
the node. For a `Call`, it names the kind of the target too (`construct_of`), or the
family of a method call that the ladder resolved and whose arm the pass does not have yet.
A stop at a node in a state that the pass cannot handle yet names the state after the class
(`ConstRef with no value`).
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, NoReturn, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.call_facts import CallFacts
from sushi_lang.semantics.statics import is_builtin_static
from sushi_lang.semantics.stdlib_registry import is_source_stdlib_module


def arm_of(arms: Mapping[type, Callable[..., Any]], node: object,
           stage: str) -> Callable[..., Any]:
    """The arm of `node` in the table `arms` of the pass `stage`."""
    arm = arms.get(type(node))
    if arm is None:
        er.raise_internal_error(
            "CE0000", span=getattr(node, "loc", None),
            detail=f"the {stage} pass has no arm for the SHIR class '{type(node).__name__}'")
    return arm


def construct_of(node: Any, facts: Optional[CallFacts] = None, *,
                 family: Optional[str] = None) -> str:
    """The construct that a stop names: the class of `node`, and the target kind of a `Call`.

    A `Call` needs `facts`, and a stage that stops on one with no facts is an internal
    error: no stage prints a label that another stage would print in another way. A method
    call whose family the ladder found names the family instead, and needs no facts.
    """
    if not isinstance(node, e.Call):
        return type(node).__name__
    if family is not None:
        if not isinstance(node.target, e.Dotted):
            er.raise_internal_error("CE0000", span=node.loc,
                                    detail="only a method call stops at a method family")
        return f"Call(method {family})"
    if facts is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a stop at a call needs the call facts of its stage")
    return f"Call({_target_kind(node.target, facts)})"


def _is_stdlib_unit(unit: Optional[str]) -> bool:
    return unit is not None and is_source_stdlib_module(unit)


def _target_kind(target: e.CallTarget, facts: CallFacts) -> str:
    if isinstance(target, e.Dotted):
        units = facts.declaring_units(target.name)
        if not units:
            return "method"
        return "extension" if any(not _is_stdlib_unit(unit) for unit in units) else "stdlib method"
    if isinstance(target, e.Static):
        if is_builtin_static(str(target.ty).rpartition(".")[2], target.name):
            return "builtin static"
        return "static"
    if isinstance(target, e.BuiltinFn):
        return "builtin"
    if not isinstance(target.ref, e.GlobalRef):
        return "fn value"
    if target.type_args:
        return "generic"
    alias, dot, name = facts.written_name(target.ref.decl).rpartition(".")
    if dot:
        kind = facts.namespace_kind(alias, name)
        return kind if kind in ("extern", "stdlib") else "alias"
    if facts.is_generic_function(name):
        return "generic"
    if facts.is_extern_function(name):
        return "extern"
    return "stdlib" if facts.is_stdlib_function(name) else "function"


def stop(stage: str, node: Any, facts: Optional[CallFacts] = None, *,
         family: Optional[str] = None, state: Optional[str] = None) -> NoReturn:
    """The stop of the pass `stage` at a construct that it does not handle yet, or at a
    node in the `state` that it does not handle yet."""
    construct = construct_of(node, facts, family=family)
    raise StageStop(stage, construct if state is None else f"{construct} {state}", node.loc)


def stop_at_family(stage: str, node: Any, family: str) -> NoReturn:
    """The stop of the pass `stage` at a method call whose built-in family the ladder
    found, where the pass does not have the arm of the family yet
    (`<stage>, Call(method <family>)`)."""
    stop(stage, node, family=family)


def stop_at_perk_method(stage: str, node: Any) -> NoReturn:
    """The stop of the pass `stage` at a call of a method of a perk implementation
    (`<stage>, Call(perk method)`)."""
    raise StageStop(stage, "Call(perk method)", node.loc)
