"""SHIR to SLIR: the arm of `??` (docs/design/ir.md 8.9).

The arm is a free function that takes the `Lowering` of `lower.py` first; the value table of
`lower.py` names it. This module reads `lower.py` for its types only, so no import cycle
exists at run time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.generics.results import result_ok_err
from sushi_lang.semantics.shir.callee import Conversion
from sushi_lang.semantics.shir.typeck import TryFacts
from sushi_lang.semantics.slir import lower_views as views
from sushi_lang.semantics.slir.lower_conversions import convert
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering
    from sushi_lang.semantics.slir.lower_values import Value


def try_(low: Lowering, node: e.Try) -> Value:
    """`??` (ir.md 8.9): the operand into a temporary, a switch on its discriminant with
    no other value, an `Err` block that builds the `Err` into `_0`, leaves every scope
    and returns, and the `Ok` block, where the payload moves into the place that takes
    the value; each store moves the temporary. A `??` across two error types calls the
    declared conversion in the `Err` block, with the move of the error, and the `Err` takes
    its answer. A `??` over a borrowed owning wrapper (a `reject` row at its
    operand) takes nothing: it switches on the wrapper where it is, the `Err` block copies the
    error, which is plain, and the payload is a view of the wrapper. A `??` over
    `m.or_err(nom e)` with a borrowed `Maybe` (a `reject` row at the receiver) is ONE shape
    with no call and no `Result` value: it switches on the `Maybe` where it is, the `None`
    block builds `Err(e)` with the move of `e`, and the payload is a view of the `Maybe`
    (ir.md 8.9)."""
    facts = low.typeck.try_facts_of(node.shir_id)
    choice = low.typeck.conversion_of(node.shir_id)
    if facts is None or choice is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a ?? with no facts reached SLIR")
    ty = low.type_of(node.inner)
    (ok_tag, ok), (err_tag, err) = _arms(ty, facts.success_tag, node.loc)
    error_variant = err
    through = views.read_through(low, node.inner)
    if through is not None:
        wrapper, error = through
        (ok_tag, ok), (err_tag, _none) = _maybe_arms(low.type_of(views.receiver_of(
            low, node.inner)), node.loc)
        borrowed = True
    else:
        wrapper, borrowed = views.wrapper(low, node.inner)
        error = (p.Copy if borrowed else p.Move)(p.Field(p.Downcast(wrapper, err), 0))
    take = p.Copy if borrowed else p.Move
    tag = low.new_local(BuiltinType.I32, None, None)
    low.emit([st.Assign(p.Local(tag), r.Discriminant(wrapper), node.loc)])
    ok_block, err_block = low.new_block(), low.new_block()
    targets = tuple(sorted([(ok_tag, ok_block.id), (err_tag, err_block.id)]))
    low.jump(tm.SwitchInt(p.Copy(p.Local(tag)), targets, None, node.loc))
    at_switch = low.placement.state()
    low.block = err_block
    if isinstance(choice, Conversion):
        low.placement.open_statement()
        error = _converted(low, choice, error, facts, node.loc)
    low.finish(r.Aggregate(r.AggVariant(facts.returns, error_variant), (error,)), node.loc)
    if isinstance(choice, Conversion):
        low.placement.leave_scope()
    low.placement.restore(at_switch)
    low.block = ok_block
    if through is not None and isinstance(error, p.Move) and isinstance(error.place, p.Local):
        low.emit(low.placement.drop_on_path(error.place.id, node.loc))
    value = r.Use(take(p.Field(p.Downcast(wrapper, ok), 0)))
    return views.view_of(value, value.operand) if borrowed else value


def _converted(low: Lowering, choice: Conversion, error: p.Operand, facts: TryFacts,
               span: Span) -> p.Operand:
    """The `Err` path of a `??` across two error types: the error goes into the call of the
    conversion, and its answer into a temporary of a scope of the `Err` block alone, which
    the `Err` of the channel takes."""
    target = result_ok_err(cast(EnumType, facts.returns))[1]
    answer = low.temporary(convert(low, choice, error, facts.err, target), target, span)
    return p.Move(p.Local(answer))


def _maybe_arms(ty: Type, span: Span) -> tuple[tuple[int, str], tuple[int, str]]:
    """The tag and the name of the `Some` variant and of the `None` variant of a `Maybe`."""
    maybe = deref_type(ty)
    some = maybe.get_variant_index("Some") if isinstance(maybe, EnumType) else None
    if not isinstance(maybe, EnumType) or some is None or len(maybe.variants) != 2:
        er.raise_internal_error("CE0000", span=span,
                                detail="a read-through or_err over no Maybe reached SLIR")
    return (some, "Some"), (1 - some, maybe.variants[1 - some].name)


def _arms(ty: Type, success: int, span: Span) -> tuple[tuple[int, str], tuple[int, str]]:
    """The tag and the name of the success variant and of the error variant of a `Result`."""
    if not isinstance(ty, EnumType) or len(ty.variants) != 2:
        er.raise_internal_error("CE0000", span=span, detail="a ?? over no Result reached SLIR")
    return (success, ty.variants[success].name), (1 - success, ty.variants[1 - success].name)
