"""The statement arms of the typecheck pass over SHIR (docs/design/ir.md 7.5).

`check_block` checks the statements of one block in the written order, each through the
table of the pass (`visit.ARMS`). The body walk starts here, and an arm that holds a block
calls it again. `check_params` gives each parameter its type before the walk: the written
type, resolved, with no `peek` or `poke` (the convention holds the mode, ir.md 4.1).

An `Assign` of a local is a `let` (`is_init`) or a rebind; the value is checked at the type
of the local. A `let` writes the type of its local, and a `let` that writes none is CE2007,
except for a hidden local, which takes the type of its value. A `let` whose value does not
fit keeps its written type, and the pass records the local as refused: a method call on it
reports nothing more. A fixed literal of another size than the declared `T[N]` of a `let`
or a constant is CE2011. An `Assign` of a `Field` is a field write, and an `Assign` of an
`Index` an element write: the value is checked at the type of the field or the element. A
write into a field or an element of a constant is CE2096, and a write into a byte of a
string is CE2113. An `Assign` of any other place is not typed yet. A reference `let`
gives its local the type of its place, and refuses a value that is no place (CE2404). An expression statement drops its
value, and a dropped `Result` of a value is CW2001. A `Return` of a body with a channel
spells `Result.Ok(...)` or `Result.Err(...)` (CE2030), checked at the `Result` of the
channel (CE2031, CE2039). A `Return` of a bare body checks its value at the declared return
type, and refuses a `Result` constructor (CE2091). A `Return` of an initializer checks its
value at the declared type of the constant or `var` (CE2002 at `name = value`), and writes
the value that the constant evaluator gave it (`passes/types/constants.py`). An initializer
that has no value, and is no empty container, stops the walk.

An `If` and a `Loop` with a `Cond` source check their condition (`check_condition`) and
their blocks; an `elif` is an `If` in the `else` block. The kind of a `while` loop is
`While` (ir.md 7.7). A loop over a range with a bare binder is `CountedRange`: its range is
checked by `ranges.check_range_iterable`, and the binder is an i32. A direct walk is in
`visit/walks.py`. A loop over any other iterable stops the walk. A `break` and a `continue`
have no answer: the scope pass refuses one outside a loop.

A `Match` checks its scrutinee (CE2048, `matching.matchable_type`) and writes its
`MatchFacts`. Then it checks the pattern of each arm, gives the arm its row for the one
checker, and walks the body of the arm: a literal arm of a match on an integer or a string
(`matching.check_literal_arm`), or a pattern arm of a match on an enum or a tuple, which
also gives each binder its type (`matching.check_pattern_arm`). An arm of an enum or a
tuple match whose pattern has an error does not walk its body. After the last arm, the one
checker reads the rows (`matching.check_match_exhaustiveness`). A scrutinee with an error,
or whose enum name the unit lost, has no arm to read: the match has no facts, the walk goes
on after it, and the body does not go on to the next stage.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arrays import reject_write_into_constant
from sushi_lang.semantics.passes.types.compatibility import (
    reject_mismatch, reject_return_mismatch, reject_unhandled_result)
from sushi_lang.semantics.passes.types.constants import assignment_span
from sushi_lang.semantics.passes.types.matching import (
    ArmRows, check_literal_arm, check_match_exhaustiveness, check_pattern_arm, literal_kind,
    matchable_type, reject_poke_binding_into_a_constant)
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.passes.types.result_validation import (
    spells_a_result, validate_result_pattern)
from sushi_lang.semantics.passes.types.statements import reject_unspelled_result
from sushi_lang.semantics.passes.types.utils import (
    names_no_type, resolve_declared_type, validate_type_name)
from sushi_lang.semantics.passes.types.visit.arrays import reject_wrong_size
from sushi_lang.semantics.passes.types.visit.operators import check_condition
from sushi_lang.semantics.passes.types.visit.ranges import check_range_iterable
from sushi_lang.semantics.passes.types.visit.walks import (
    check_direct_walk, unwraps_a_try_binder)
from sushi_lang.semantics.ownership import is_own_type
from sushi_lang.semantics.places import Step, is_get_call, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer, ShirBody
from sushi_lang.semantics.shir.loop_kind import CountedRange, While
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.typeck import MatchFacts
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, EnumType, ReferenceType, Type, deref_type)

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_params(validator: TypeValidator, body: ShirBody) -> None:
    """The type of each parameter: its written type, resolved. A value pack is no value, so
    it has no type: a use of it is CE0144 (`expressions.check_local_ref`)."""
    answers = validator.body_answers()
    for param in body.params:
        if param.pack:
            continue
        ty = validator.written_type(param.ty)
        answers.record_local(param.local, resolve_variable_type(validator, ty, param.loc))


def check_block(validator: TypeValidator, block: ShirBlock) -> None:
    """Check each statement of `block`, in the written order."""
    for statement in block.statements:
        visit.check(validator, statement)


def check_expand(validator: TypeValidator, node: s.Expand) -> None:
    """`expand(a in args):` of a template check, one time for every element of the pack: the
    binder has the opaque element type of this `expand` (ir.md 4.5). The pack is no value, so
    it is not checked as an expression. No other body holds an `Expand`."""
    elements = validator.pack_elements
    if elements is None:
        er.raise_internal_error("CE0015", message="an `expand` reached the typecheck pass "
                                                  "outside a template check")
    local = validator.body_locals[node.binder]
    element = elements.element_of(node.shir_id, local.name, local.loc)
    validator.body_answers().record_local(node.binder, element)
    check_block(validator, node.body)


def check_assign(validator: TypeValidator, node: s.Assign) -> None:
    """A `let`, a rebind of a local or of a `var`, a field write or an element write. A
    write into a constant is CE2096, and a write into a byte of a string CE2113; the value
    is then checked at no type."""
    target = node.target
    if node.is_init and isinstance(target, e.LocalRef):
        _check_let(validator, node, target)
        return
    if node.is_init or not isinstance(target, (e.LocalRef, e.StorageRef, e.Field, e.Index)):
        visit.stop_at(validator, node)
    ty = visit.check_expr(validator, target)
    if reject_unwritable_place(validator, target, node.loc):
        visit.check_expr(validator, node.value)
        return
    got = visit.check_expr(validator, node.value, ty)
    if ty is not None:
        reject_mismatch(validator, got, ty, node.loc, None)


def reject_unwritable_place(validator: TypeValidator, place: e.ShirExpr, loc: Span) -> bool:
    """CE2113 for a byte of a string (at the place), and CE2096 for a field or an element
    of a constant (at `loc`). The caller checks the place first. Answers whether a
    diagnostic refused it."""
    if isinstance(place, e.Index):
        base = validator.body_answers().type_of(place.base.shir_id)
        if deref_type(base) == BuiltinType.STRING:
            er.emit(validator.reporter, er.ERR.CE2113, place.loc)
            return True
        return reject_write_into_constant(validator, place, "assign to an element of", loc)
    return isinstance(place, e.Field) and reject_write_into_constant(
        validator, place, "assign to a field of", loc)


def _check_let(validator: TypeValidator, node: s.Assign, target: e.LocalRef) -> None:
    """`let T x = v`: the written type is the type of the local, and `v` is checked at it."""
    local = validator.body_locals[target.local]
    declared = None if local.declared is None else validator.written_type(local.declared)
    if isinstance(declared, ReferenceType):
        _check_let_reference(validator, node, target, declared)
        return
    answers = validator.body_answers()
    if declared is None:
        if local.name is not None and not unwraps_a_try_binder(validator, node):
            er.emit(validator.reporter, er.ERR.CE2007, local.loc, name=local.name)
            return
        inferred = visit.check_expr(validator, node.value)
        if inferred is not None:
            answers.record_local(local.id, inferred)
            visit.check_expr(validator, target)
        return
    refused = validate_type_name(validator, declared, local.declared_loc)
    if declared == BuiltinType.BLANK:
        er.emit(validator.reporter, er.ERR.CE2032, local.declared_loc)
        return
    ty = resolve_variable_type(validator, declared, local.declared_loc)
    answers.record_local(local.id, ty)
    visit.check_expr(validator, target)
    got = visit.check_expr(validator, node.value, ty)
    if refused:
        return
    if (reject_unhandled_result(validator, got, ty, node.value.loc)
            or _reject_literal_size(validator, node.value, ty, node.value.loc)
            or reject_mismatch(validator, got, ty, node.value.loc, local.declared_loc)):
        validator.refused_locals.add(local.id)


def _check_let_reference(validator: TypeValidator, node: s.Assign, target: e.LocalRef,
                         declared: ReferenceType) -> None:
    """`let peek T x = <place>` and `let poke T x = <place>` (#409): the local has the type
    of its place (ir.md 8.2), and the value is a `Borrow` of the place, checked at `T`. A
    value that is no place (a call, a literal, a slice) is CE2404. What the mode may do to
    the place (a constant, the owner) is the rule of `borrow`."""
    local = validator.body_locals[target.local]
    refused = validate_type_name(validator, declared.referenced_type, local.declared_loc)
    ty = resolve_variable_type(validator, declared.referenced_type, local.declared_loc)
    validator.body_answers().record_local(local.id, ty)
    visit.check_expr(validator, target)
    value = node.value
    if not isinstance(value, e.Borrow) or not _names_a_place(value.place):
        _reject_no_place(validator, value, declared, local.name)
        return
    got = visit.check_expr(validator, value, ty)
    if refused or got is None:
        return
    if not _reaches_storage(validator, value.place):
        _reject_no_place(validator, value, declared, local.name)
        return
    reject_mismatch(validator, deref_type(got), ty, value.loc, local.declared_loc)


def _reject_no_place(validator: TypeValidator, value: e.ShirExpr, declared: ReferenceType,
                     name: Optional[str]) -> None:
    """CE2404: the value of a reference `let` names no storage."""
    er.emit_with(validator.reporter, er.ERR.CE2404, value.loc, expr=name) \
        .help(f"a `let {declared.mutability.value}` binds a PLACE: a local, a field, an "
              f"element, or an Own's payload (`o.get()`); bind a call result by value, with "
              f"`let T {name} = ...`") \
        .emit()


def _names_a_place(expr: e.ShirExpr) -> bool:
    """Whether `expr` is written as a place: a name, or a field, an element or a `get()`
    chain off one. A slice is a new descriptor and no place (docs/design/slices.md)."""
    if isinstance(expr, e.Slice):
        return False
    walked = walk_place(expr, Step.MEMBER | Step.INDEX | Step.CALL, crosses_call=is_get_call)
    return isinstance(walked.node, (e.LocalRef, e.StorageRef, e.ConstRef, e.GlobalRef))


def _reaches_storage(validator: TypeValidator, expr: e.ShirExpr) -> bool:
    """Whether each `get()` of the place `expr` reads the payload of an `Own@(T)`: a heap
    cell that the `Own` keeps. A `get()` of any other type answers a temporary."""
    answers = validator.body_answers()
    walked = walk_place(expr, Step.MEMBER | Step.INDEX | Step.CALL, crosses_call=is_get_call)
    return all(is_own_type(deref_type(answers.type_of(step.target.recv.shir_id)))
               for step in walked.path
               if isinstance(step, e.Call) and isinstance(step.target, e.Dotted))


def _reject_literal_size(validator: TypeValidator, value: e.ShirExpr, declared: Type,
                         span: Span) -> bool:
    """CE2011 at `span` for a fixed literal that a `let` or a constant declares with
    another size."""
    return (isinstance(value, e.ArrayInit) and isinstance(value.kind, e.Fixed)
            and isinstance(declared, ArrayType)
            and reject_wrong_size(validator, value, declared, span))


def check_expr_stmt(validator: TypeValidator, node: s.ExprStmt) -> None:
    """A value that the statement drops. A `Result` of a value loses its error: CW2001."""
    ty = visit.check_expr(validator, node.value)
    if not (isinstance(ty, EnumType) and is_instance_of(ty, "Result")):
        return
    ok = ty.get_variant("Ok")
    if ok is not None and ok.associated_types and ok.associated_types[0] == BuiltinType.BLANK:
        return
    er.emit(validator.reporter, er.ERR.CW2001, node.value.loc, ty=display_type(ty))


def check_return(validator: TypeValidator, node: s.Return) -> None:
    """A `return`. A body with a channel spells a `Result` constructor, and a bare body
    returns the value: a `Result` constructor is CE2091 there, and a `~` return type takes
    any value."""
    body = validator.body
    if body is not None and isinstance(body.origin, Initializer):
        _check_initializer(validator, node)
        return
    value = node.value
    channel = validator.channel_result
    if channel is not None:
        _check_channel_return(validator, value, channel)
        return
    if spells_a_result(validator, value):
        er.emit(validator.reporter, er.ERR.CE2091, value.loc,
                callable=validator.body_name or "this body")
        return
    declared = validator.body_return_type
    if declared is None or declared == BuiltinType.BLANK:
        visit.check_expr(validator, node.value)
        return
    expected = resolve_declared_type(validator, declared)
    got = visit.check_expr(validator, node.value, expected)
    if expected is not None:
        reject_return_mismatch(validator, got, expected, node.value.loc)


def _check_initializer(validator: TypeValidator, node: s.Return) -> None:
    """The `Return` of an initializer body: the value at the declared type, as a `let`
    checks it. An initializer that fits and has no value, and is no empty container, stops
    the walk. The pass writes no value row: the backend makes the global from the constant
    evaluator (docs/design/ir.md 7.3, 8.2)."""
    facts = validator.initializer
    if facts is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="an initializer body with no declaration")
    const = facts.decl
    got = visit.check_expr(validator, node.value, facts.ty)
    span = assignment_span(const)
    if (_reject_literal_size(validator, node.value, facts.ty, span)
            or reject_mismatch(validator, got, facts.ty, span, const.type_span)):
        return
    if got is None or facts.empty:
        return
    if facts.value is None:
        visit.stop_with_no_value(node)


def _check_channel_return(validator: TypeValidator, value: e.ShirExpr, channel: Type) -> None:
    """A `return` of a body with a channel spells `Result.Ok(...)` or `Result.Err(...)`,
    which is checked at the `Result` of the channel. Any other value is CE2030, because
    nothing wraps a bare value (#848); a copy of a template took that rule on the
    template. A `Static` call on `Result` is checked at the channel too, so that its
    CE2045 names the instance."""
    if isinstance(value, e.EnumInit) and spells_a_result(validator, value):
        visit.check_expr(validator, value, channel)
        validate_result_pattern(validator, value, channel)
        return
    on_result = (isinstance(value, e.Call) and isinstance(value.target, e.Static)
                 and str(value.target.ty) == "Result")
    visit.check_expr(validator, value, channel if on_result else None)
    if not validator.in_template_copy:
        reject_unspelled_result(validator, value)


def check_if(validator: TypeValidator, node: s.If) -> None:
    check_condition(validator, node.cond)
    check_block(validator, node.then)
    if node.otherwise is not None:
        check_block(validator, node.otherwise)


def check_loop(validator: TypeValidator, node: s.Loop) -> None:
    """A `while` loop, a counted loop or a direct walk (`visit/walks.py`): its source, its
    kind and its body. A counted loop walks a range with a bare binder, which is an i32; `_`
    binds nothing. A range makes its values and has no storage for them, so a `peek` or a
    `poke` binder over one is CE2423 at the marker, and the body is not checked."""
    source = node.source
    answers = validator.body_answers()
    if isinstance(source, s.Cond):
        check_condition(validator, source.cond)
        answers.record_loop_kind(node.shir_id, While())
    elif isinstance(source.iterable, e.Range) and isinstance(source.by, s.Ref):
        check_range_iterable(validator, source.iterable)
        er.emit(validator.reporter, er.ERR.CE2423, source.by.loc or node.loc)
        return
    elif isinstance(source.iterable, e.Range) and isinstance(source.by, s.Bare):
        check_range_iterable(validator, source.iterable)
        if not isinstance(source.binder, s.Discard):
            answers.record_local(source.binder, BuiltinType.I32)
        answers.record_loop_kind(node.shir_id, CountedRange())
    elif not check_direct_walk(validator, node, source):
        return
    check_block(validator, node.body)


def check_assert(validator: TypeValidator, node: s.Assert) -> None:
    """An `assert`: the condition is a `bool` (CE2005, CE2516), the message a `string`
    (CE2116)."""
    check_condition(validator, node.cond)
    if node.message is None:
        return
    ty = visit.check_expr(validator, node.message, BuiltinType.STRING)
    if (ty is None or deref_type(ty) == BuiltinType.STRING
            or names_no_type(validator, ty)):
        return
    er.emit(validator.reporter, er.ERR.CE2116, node.message.loc, got=display_type(ty))


def check_match(validator: TypeValidator, node: s.Match) -> None:
    """A `match`: its facts, then the pattern and the body of each arm in the written
    order, then the coverage of the arms. An arm of an enum or a tuple match whose pattern
    has an error does not walk its body. After an error of the scrutinee, no arm is read."""
    ty = matchable_type(validator, node, visit.check_expr(validator, node.scrutinee))
    if ty is None:
        validator.refused_before = True
        return
    kind = literal_kind(ty)
    validator.body_answers().record_match(
        node.shir_id, MatchFacts(ty, ty if kind == "integer" else None))
    if kind is None:
        reject_poke_binding_into_a_constant(validator, node)
    rows = ArmRows()
    for index, arm in enumerate(node.arms):
        if kind is not None:
            check_literal_arm(validator, rows, node, index, ty)
        elif not check_pattern_arm(validator, rows, node, index, ty):
            continue
        check_block(validator, arm.body)
    check_match_exhaustiveness(validator, node, ty, rows)


def check_jump(_validator: TypeValidator, _node: s.Break | s.Continue) -> None:
    """A `break` or a `continue`: no type and no answer."""
