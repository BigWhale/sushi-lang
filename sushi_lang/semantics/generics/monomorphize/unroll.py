"""Compile-time unrolling of ``expand(...)`` over parameter packs."""
from __future__ import annotations

import copy
import dataclasses
import itertools
from typing import Dict, Iterator, List, Optional, Tuple, cast

from sushi_lang.semantics.ast import (
    Block, Expand, Name, Lambda, Let, Foreach, Stmt, Match, MatchArm, Pattern, OwnPattern,
    TuplePattern, destructure_binders, pattern_bindings,
)
from sushi_lang.semantics.hidden_names import expand_copy_local_name
from sushi_lang.internals.report import Span


_COPY_IDS = itertools.count(1)


@dataclasses.dataclass(eq=False)
class WrittenLet:
    """One binder as it is written in an `expand` body: a `let`, or another binder.

    Every copy of that binder, at every depth of nesting, points to one record, so a
    diagnostic about the declaration is told once and with the written name (#1019).
    The other binders are the loop variable of an `expand`, a `foreach` item, a pattern
    binding and a lambda parameter (#1031). The record is compared by identity.
    """
    name: str


# A copy `Let` -> its written declaration. The key is the copy's `id`; the value holds
# the copy, so the key is never used again for a different object.
_WRITTEN: Dict[int, Tuple[Let, WrittenLet]] = {}


def written_let(stmt: Let) -> Optional[WrittenLet]:
    """The written declaration of a `let` that the unroll copied, or None."""
    entry = _WRITTEN.get(id(stmt))
    if entry is None or entry[0] is not stmt:
        return None
    return entry[1]


# The nodes that bind a name without a `let`: an `Expand` (its loop variable), a
# `Foreach` (its item), a `Pattern` / `TuplePattern` / `OwnPattern` (its bindings) and a
# `Lambda` (its parameters). A node -> the records of its written names, one dict for the written
# node and all its copies. Same keying rule as `_WRITTEN`.
_BINDER_NODES = (Expand, Foreach, Pattern, TuplePattern, OwnPattern, Lambda)
_WRITTEN_BINDERS: Dict[int, Tuple[object, Dict[str, WrittenLet]]] = {}


def written_binder(node: object, name: str) -> Optional[WrittenLet]:
    """The record of the written name ``name`` that a copied binder node binds, or None."""
    entry = _WRITTEN_BINDERS.get(id(node))
    if entry is None or entry[0] is not node:
        return None
    return entry[1].setdefault(name, WrittenLet(name))


def _binders_of(node: object) -> Dict[str, WrittenLet]:
    """The records of a binder node, shared with the node it was copied from."""
    entry = _WRITTEN_BINDERS.get(id(node))
    if entry is not None and entry[0] is node:
        return entry[1]
    records: Dict[str, WrittenLet] = {}
    _WRITTEN_BINDERS[id(node)] = (node, records)
    return records


# A `Name` the unroll renamed from an `expand` loop variable to a pack element -> the
# record of the written loop variable. Keyed by the node, because one hidden pack
# element name stands for a different written variable in a sibling or a nested
# `expand`.
_WRITTEN_VARIABLES: Dict[int, Tuple[Name, WrittenLet]] = {}


def written_variable(name: Name) -> Optional[WrittenLet]:
    """The written `expand` loop variable a renamed ``name`` stands for, or None."""
    entry = _WRITTEN_VARIABLES.get(id(name))
    if entry is None or entry[0] is not name:
        return None
    return entry[1]


# A copy `Span` -> the span of the written `expand` body it was copied from, with the
# same keying rule as `_WRITTEN`. Every copy of one written node shares one written
# span, so a diagnostic about one written statement is told once (#1022).
_WRITTEN_SPANS: Dict[int, Tuple[Span, Span]] = {}


def written_span(span: Optional[Span]) -> Optional[Span]:
    """The written span of a span that the unroll copied, or None."""
    entry = _WRITTEN_SPANS.get(id(span))
    if entry is None or entry[0] is not span:
        return None
    return entry[1]


def _is_frozen_dataclass(obj) -> bool:
    """True if ``obj`` is a frozen ``@dataclass`` instance."""
    params = getattr(type(obj), "__dataclass_params__", None)
    return bool(params is not None and getattr(params, "frozen", False))


def _pattern_binding_names(pattern) -> set:
    """Collect the variable names bound by a match-arm pattern."""
    return {name for name, _owner, _span in pattern_bindings(pattern)}


def unroll_expands(
    body: Block, pack_param_fanout: Dict[str, List[str]]
) -> Block:
    """Rewrite every ``Expand`` in ``body`` into its unrolled ordinary statements.

    Every copy of every ``expand`` in the instance, at every depth of nesting and
    in every sibling ``expand``, draws its own number from ONE counter, so a local
    of a copy has a name that is unique over the instance (#1018).
    """
    body.statements = _unroll_stmt_list(
        body.statements, pack_param_fanout, itertools.count()
    )
    return body


def _unroll_stmt_list(
    statements: List[Stmt], pack_param_fanout: Dict[str, List[str]],
    copy_numbers: Iterator[int],
) -> List[Stmt]:
    """Unroll a flat statement list, splicing expanded copies in place."""
    result: List[Stmt] = []
    for stmt in statements:
        if isinstance(stmt, Expand):
            result.extend(_unroll_expand(stmt, pack_param_fanout, copy_numbers))
        else:
            result.append(
                _unroll_in_nested_blocks(stmt, pack_param_fanout, copy_numbers)
            )
    return result


def _unroll_expand(
    node: Expand, pack_param_fanout: Dict[str, List[str]],
    copy_numbers: Iterator[int],
) -> List[Stmt]:
    """Expand a single ``Expand`` node into its N unrolled body copies."""
    # The iterable must be a Name referencing a pack value-parameter. Anything
    # else is a malformed expand (CE0119) -- it used to fall through to an empty
    # fan-out and silently unroll to ZERO statements, vanishing the body from
    # the compiled program. Raised as a SushiError (no reporter is threaded
    # this deep); the top-level guard renders it as a normal diagnostic.
    from sushi_lang.internals.diagnostics import SushiError

    if not isinstance(node.iterable, Name):
        raise SushiError(
            "CE0119", span=node.loc,
            message="the iterable must be the ...Ts pack parameter name",
        )
    pack_name = node.iterable.id
    if pack_name not in pack_param_fanout:
        raise SushiError(
            "CE0119", span=node.loc,
            message=f"'{pack_name}' is not the function's ...Ts type-pack parameter",
        )
    fanout = pack_param_fanout[pack_name]

    out: List[Stmt] = []
    lets, binders, variables, spans = _written_nodes(node.body)
    written = [(let, written_let(let) or WrittenLet(let.name)) for let in lets]
    binder_origins = [(binder, _binders_of(binder)) for binder in binders]
    # A loop variable of an enclosing `expand` that this body names was renamed
    # already; each copy of that `Name` keeps its record.
    variable_origins = [(name, cast(WrittenLet, written_variable(name)))
                        for name in variables]
    span_origins = [(span, written_span(span) or span) for span in spans]
    variable = _binders_of(node).setdefault(node.var, WrittenLet(node.var))
    for elem_name in fanout:
        memo: Dict[int, object] = {}
        body_copy = copy.deepcopy(node.body, memo)
        for let, origin in written:
            let_copy = cast(Let, memo[id(let)])
            _WRITTEN[id(let_copy)] = (let_copy, origin)
        for binder, records in binder_origins:
            binder_copy = memo[id(binder)]
            _WRITTEN_BINDERS[id(binder_copy)] = (binder_copy, records)
        for name, origin in variable_origins:
            name_copy = cast(Name, memo[id(name)])
            _WRITTEN_VARIABLES[id(name_copy)] = (name_copy, origin)
        for span, span_origin in span_origins:
            span_copy = memo.get(id(span))
            if isinstance(span_copy, Span):
                _WRITTEN_SPANS[id(span_copy)] = (span_copy, span_origin)
        renamed = _rename_block_statements(
            body_copy.statements, node.var, elem_name, _seen=set(), written=variable
        )
        # Hygiene: alpha-rename each top-level local declared in THIS copy to a
        # copy-unique name, so the N copies don't re-declare the same local in
        # the shared callee scope. Done AFTER the loop-var rename so a `let`
        # named like the loop var (which the loop-var rename already shadow-stops
        # at) still gets its own fresh local name here.
        renamed = _rename_copy_locals(renamed, next(copy_numbers))
        renamed = _unroll_stmt_list(renamed, pack_param_fanout, copy_numbers)
        copy_id = next(_COPY_IDS)
        for stmt in renamed:
            stmt.expand_copies = (copy_id, *stmt.expand_copies)
        out.extend(renamed)
    return out


def _written_nodes(body: Block) -> Tuple[List[Let], List[object], List[Name], List[Span]]:
    """Every `Let`, other binder node, renamed loop variable and `Span` in ``body``."""
    lets: List[Let] = []
    binders: List[object] = []
    variables: List[Name] = []
    spans: List[Span] = []
    _collect_written(body, set(), (lets, binders, variables, spans))
    return lets, binders, variables, spans


def _collect_written(obj, _seen, found: Tuple[list, list, list, list]) -> None:
    lets, binders, variables, spans = found
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_written(item, _seen, found)
        return
    if not dataclasses.is_dataclass(obj) or _is_frozen_dataclass(obj) or id(obj) in _seen:
        return
    _seen.add(id(obj))
    if isinstance(obj, Span):
        spans.append(obj)
        return
    if isinstance(obj, Let):
        lets.append(obj)
    elif isinstance(obj, _BINDER_NODES):
        binders.append(obj)
    elif isinstance(obj, Name) and written_variable(obj) is not None:
        variables.append(obj)
    for f in dataclasses.fields(obj):
        _collect_written(getattr(obj, f.name), _seen, found)


def _rename_copy_locals(statements: List[Stmt], copy_number: int) -> List[Stmt]:
    """Alpha-rename top-level ``let`` locals in one unrolled copy to fresh names."""
    for idx, stmt in enumerate(statements):
        if isinstance(stmt, Let):
            renames = [(stmt, stmt.name)]
            # A destructure's binders are locals of the copy too (docs/design/tuples.md).
            renames.extend((binder, binder.name) for binder in destructure_binders(stmt.targets))
            for holder, old in renames:
                new = expand_copy_local_name(old, copy_number)
                holder.name = new
                # Rewrite references in the statements that follow (the local's
                # scope is from its declaration to the end of the block), honoring
                # any later re-`let` of the same name as a fresh binding.
                tail = _rename_block_statements(
                    statements[idx + 1:], old, new, _seen=set()
                )
                statements[idx + 1:] = tail
    return statements


def _unroll_in_nested_blocks(
    stmt: Stmt, pack_param_fanout: Dict[str, List[str]],
    copy_numbers: Iterator[int],
) -> Stmt:
    """Recurse into a non-Expand statement's nested blocks and unroll there."""
    _walk_unroll(stmt, pack_param_fanout, copy_numbers, _seen=set())
    return stmt


def _walk_unroll(obj, pack_param_fanout, copy_numbers, _seen) -> None:
    """Find Blocks reachable from ``obj`` and unroll their statement lists."""
    obj_id = id(obj)
    if obj_id in _seen:
        return
    if isinstance(obj, Block):
        obj.statements = _unroll_stmt_list(
            obj.statements, pack_param_fanout, copy_numbers
        )
        return
    if not dataclasses.is_dataclass(obj):
        return
    _seen.add(obj_id)
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        _walk_unroll_value(value, pack_param_fanout, copy_numbers, _seen)


def _walk_unroll_value(value, pack_param_fanout, copy_numbers, _seen) -> None:
    if isinstance(value, Block):
        value.statements = _unroll_stmt_list(
            value.statements, pack_param_fanout, copy_numbers
        )
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_unroll_value(item, pack_param_fanout, copy_numbers, _seen)
    elif dataclasses.is_dataclass(value):
        _walk_unroll(value, pack_param_fanout, copy_numbers, _seen)


def _rename_walk(obj, var: str, new_name: str, _seen,
                 written: Optional[WrittenLet] = None) -> None:
    """Recurse into a dataclass node, renaming FREE ``Name(id == var)`` within."""
    if not dataclasses.is_dataclass(obj):
        return
    # Frozen typesys Type nodes (EnumType, StructType, ...) are immutable and
    # never contain renameable expression Name nodes -- skip them entirely (both
    # a correctness guard against FrozenInstanceError and a perf win).
    if _is_frozen_dataclass(obj):
        return
    obj_id = id(obj)
    if obj_id in _seen:
        return
    _seen.add(obj_id)

    if isinstance(obj, Foreach) and obj.item_name == var:
        _set_if_changed(obj, "iterable",
                        _rename_value(obj.iterable, var, new_name, _seen, written))
        return

    if isinstance(obj, Expand) and obj.var == var:
        _set_if_changed(obj, "iterable",
                        _rename_value(obj.iterable, var, new_name, _seen, written))
        return

    # A Match: the scrutinee is in the outer scope and is renamed normally, but a
    # match arm whose pattern binds ``var`` introduces a shadow scope for THAT
    # arm's body -- the pattern binding is a distinct variable and must not be
    # renamed inside its arm. Other arms are renamed as usual.
    if isinstance(obj, Match):
        _set_if_changed(obj, "scrutinee",
                        _rename_value(obj.scrutinee, var, new_name, _seen, written))
        for arm in obj.arms:
            if isinstance(arm, MatchArm) and var in _pattern_binding_names(arm.pattern):
                continue
            _rename_walk(arm, var, new_name, _seen, written)
        return

    for f in dataclasses.fields(obj):
        _set_if_changed(obj, f.name, _rename_value(getattr(obj, f.name), var, new_name,
                                                   _seen, written))


def _set_if_changed(obj, field_name: str, new_value) -> None:
    """``setattr`` only when the value actually changed (by identity)."""
    if getattr(obj, field_name) is not new_value:
        setattr(obj, field_name, new_value)


def _rename_value(value, var: str, new_name: str, _seen,
                  written: Optional[WrittenLet] = None):
    """Rename within a single field value, returning the (possibly replaced) value.

    ``written`` is the record of the written loop variable when ``var`` is one; each
    renamed `Name` is filed under it.
    """
    if isinstance(value, Name):
        if value.id == var:
            replacement = copy.copy(value)
            replacement.id = new_name
            if written is not None:
                _WRITTEN_VARIABLES[id(replacement)] = (replacement, written)
            return replacement
        return value

    if isinstance(value, Block):
        value.statements = _rename_block_statements(
            value.statements, var, new_name, _seen, written
        )
        return value

    if isinstance(value, list):
        return [_rename_value(item, var, new_name, _seen, written) for item in value]
    if isinstance(value, tuple):
        return tuple(_rename_value(item, var, new_name, _seen, written) for item in value)

    if dataclasses.is_dataclass(value):
        _rename_walk(value, var, new_name, _seen, written)
        return value

    return value


def _rename_block_statements(statements, var: str, new_name: str, _seen,
                             written: Optional[WrittenLet] = None):
    """Rename within a statement list, respecting sequential ``let`` shadowing."""
    out = []
    shadowed = False
    for stmt in statements:
        if shadowed:
            out.append(stmt)
            continue
        if isinstance(stmt, Let) and (stmt.name == var or any(
                binder.name == var for binder in destructure_binders(stmt.targets))):
            if stmt.value is not None:
                stmt.value = _rename_value(stmt.value, var, new_name, _seen, written)
            out.append(stmt)
            shadowed = True
            continue
        out.append(_rename_value(stmt, var, new_name, _seen, written))
    return out
