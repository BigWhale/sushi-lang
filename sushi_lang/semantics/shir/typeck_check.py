"""The completeness check of `TypeckResults` (docs/design/ir.md 7.3, ruling S5).

When the `typecheck` pass ends on a body with no error, each node of the body has each
answer that it needs, and a gap is the internal error CE0156. The check walks the body
once (`walk.py`) and asks the table:

- each local and each parameter has a type, and each expression node has a type;
- a `Call` has a callee and a convention for each argument, and a call of `print` or
  `println` a display type for each argument;
- a `StructInit`, an `EnumInit` and a `TupleInit` have an instance type;
- a `Field` has an index, a `Loop` a kind, a `Range` the answer whether it is a value;
- a `Cast` has its conversion, and a `Try` its conversion and its facts;
- each hole of an `Interp` has a display type;
- a `Match` has the type of its scrutinee;
- each target of a `Destructure`, at each level, has an element type;
- an integer literal and a `ConstRef` have a value, and a `StorageRef` the symbol of its
  global.

A `ConstRef` of a constant with no value stops the walk (`ConstRef with no value`), so its
body does not reach this check. An initializer needs no value row: a `var` of an empty
container has no value, and any other initializer with no value stops the walk.

The captures of a lambda are in `Bindings` and not in the body, so the check cannot count
them: the type of a capture has no check here.
"""
from __future__ import annotations

from typing import Any, Callable, Iterator, Optional, Sequence, TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ShirBody
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId
from sushi_lang.semantics.shir.nodes import ShirNode
from sushi_lang.semantics.shir.side_table import MissingAnswer, raise_first_gap
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.shir.walk import walk


Needs: TypeAlias = Callable[[TypeckResults, Any], Iterator[str]]


def _call(answers: TypeckResults, call: e.Call) -> Iterator[str]:
    if answers.callee_of(call.shir_id) is None:
        yield "callee"
    for position in range(len(call.args)):
        if answers.convention_of(call.shir_id, position) is None:
            yield f"convention of argument {position}"
    if isinstance(call.target, e.BuiltinFn):
        for position in range(len(call.args)):
            if answers.display_type_of(call.shir_id, position) is None:
                yield f"display type of argument {position}"


def _instance(answers: TypeckResults,
              init: e.StructInit | e.EnumInit | e.TupleInit) -> Iterator[str]:
    if answers.instance_of(init.shir_id) is None:
        yield "instance type"


def _field(answers: TypeckResults, node: e.Field) -> Iterator[str]:
    if answers.field_index_of(node.shir_id) is None:
        yield "field index"


def _range(answers: TypeckResults, node: e.Range) -> Iterator[str]:
    if answers.range_value_of(node.shir_id) is None:
        yield "range value"


def _cast(answers: TypeckResults, node: e.Cast | e.Try) -> Iterator[str]:
    if answers.conversion_of(node.shir_id) is None:
        yield "conversion"


def _try(answers: TypeckResults, node: e.Try) -> Iterator[str]:
    yield from _cast(answers, node)
    if answers.try_facts_of(node.shir_id) is None:
        yield "try facts"


def _interp(answers: TypeckResults, node: e.Interp) -> Iterator[str]:
    for position, part in enumerate(node.parts):
        if not isinstance(part, str) and answers.display_type_of(node.shir_id, position) is None:
            yield f"display type of part {position}"


def _lit(answers: TypeckResults, node: e.Lit) -> Iterator[str]:
    if isinstance(node.value, e.IntConst) and answers.value_of(node.shir_id) is None:
        yield "value"


def _const_ref(answers: TypeckResults, node: e.ConstRef) -> Iterator[str]:
    if answers.value_of(node.shir_id) is None:
        yield "value"


def _storage_ref(answers: TypeckResults, node: e.StorageRef) -> Iterator[str]:
    if answers.global_of(node.shir_id) is None:
        yield "global"


def _loop(answers: TypeckResults, node: s.Loop) -> Iterator[str]:
    if answers.loop_kind_of(node.shir_id) is None:
        yield "loop kind"


def _match(answers: TypeckResults, node: s.Match) -> Iterator[str]:
    if answers.match_facts_of(node.shir_id) is None:
        yield "scrutinee type"


def _destructure(answers: TypeckResults, node: s.Destructure) -> Iterator[str]:
    for path in _target_paths(node.targets, ()):
        if answers.element_type_of(node.shir_id, path) is None:
            yield f"element type of target {'.'.join(str(step) for step in path)}"


def _target_paths(targets: Sequence[s.DestructureTarget],
                  prefix: tuple[int, ...]) -> Iterator[tuple[int, ...]]:
    """The path of each target, a `Nested` target before the targets that it holds."""
    for index, target in enumerate(targets):
        path = (*prefix, index)
        yield path
        if isinstance(target, s.Nested):
            yield from _target_paths(target.targets, path)


#: The answers that a node of each class needs beside its type.
NEEDS: dict[type, Needs] = {
    e.Call: _call,
    e.StructInit: _instance,
    e.EnumInit: _instance,
    e.TupleInit: _instance,
    e.Field: _field,
    e.Range: _range,
    e.Cast: _cast,
    e.Try: _try,
    e.Interp: _interp,
    e.Lit: _lit,
    e.ConstRef: _const_ref,
    e.StorageRef: _storage_ref,
    s.Loop: _loop,
    s.Match: _match,
    s.Destructure: _destructure,
}


def missing_answers(body: ShirBody, answers: TypeckResults) -> list[MissingAnswer]:
    """Each answer that `body` needs and that `answers` does not hold: the locals first,
    the parameters before the others, then the nodes in the order of the walk."""
    gaps = [MissingAnswer("type", name, loc) for local, name, loc in _locals(body)
            if answers.local_type(local) is None]
    for _path, value in walk(body.block, "block"):
        if not isinstance(value, ShirNode):
            continue
        node = type(value).__name__
        if isinstance(value, e.ExprNode) and answers.type_of(value.shir_id) is None:
            gaps.append(MissingAnswer("type", node, value.loc))
        needs = NEEDS.get(type(value))
        if needs is not None:
            gaps.extend(MissingAnswer(answer, node, value.loc) for answer in needs(answers, value))
    return gaps


def _locals(body: ShirBody) -> Iterator[tuple[LocalId, str, Optional[Span]]]:
    """Each local of the body, by its id, with its text and its span: a parameter first."""
    by_id = {local.id: local for local in body.locals}
    params = {param.local: param for param in body.params}
    for ident in [*params, *sorted(set(by_id) - set(params))]:
        local = by_id.get(ident)
        name = None if local is None else local.name
        text = f"local _{ident.index}" if name is None else f'local _{ident.index} "{name}"'
        loc = None if local is None else local.loc
        if loc is None and ident in params:
            loc = params[ident].loc
        yield ident, text, loc


def check_answers(body: ShirBody, answers: TypeckResults, decls: DeclNames) -> None:
    """CE0156 at the first answer that `body` needs and that `answers` does not hold."""
    raise_first_gap(er.ERR.CE0156, missing_answers(body, answers), decls.name_of(body.fn_id))
