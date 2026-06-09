# semantics/generics/monomorphize/unroll.py
"""Compile-time unrolling of ``expand(...)`` over parameter packs.

This runs as a post-pass during monomorphization, AFTER the generic body has
been deep-copied and type-substituted (see ``transformer.substitute_body``) and
AFTER the pack value-parameter has fanned out into its concrete per-element
parameters (``args_0 .. args_{N-1}``; see ``transformer.expand_pack_param``).

Rewriting ``expand(...)`` here -- instead of in the backend -- means every later
pass (scope, type validation, borrow checking, RAII, codegen) sees ONLY ordinary
statements. No downstream pass needs any ``Expand`` handling.

Unroll semantics for ``expand(a in args): BODY``:

* ``fanout = pack_param_fanout[args]`` is the ordered list
  ``[args_0, ..., args_{N-1}]`` of fan-out parameter names (N may be 0).
* The statement is replaced, in place, by N independent deep copies of ``BODY``
  spliced in order 0..N-1. In copy i, every *free* reference to the binding
  variable ``a`` (a ``Name`` with ``id == var``) is renamed to ``fanout[i]`` so
  ``a`` aliases the owned fan-out parameter directly (reference replacement, not
  a ``let`` -- a ``let`` would copy/move the owned element and break RAII).
* Arity 0 -> the ``Expand`` vanishes (zero copies).
* Each copy is a fresh ``copy.deepcopy`` so later passes can annotate the copies
  independently without cross-contamination.
* Nesting: ``Expand`` nodes inside compound statements (if/while/foreach/match)
  are unrolled recursively. A nested ``Expand`` inside an expanded body is itself
  unrolled (the renamed copies are re-walked).

Shadowing: the rename is shadow-aware. If the body re-binds ``var`` via an inner
``let`` / ``foreach`` (or a nested ``expand`` using the same name), occurrences in
the shadowed scope are left untouched -- only free occurrences of ``var`` are
renamed.
"""
from __future__ import annotations

import copy
import dataclasses
from typing import Dict, List

from sushi_lang.semantics.ast import (
    Block, Expand, Name, Let, Foreach, Stmt, Node,
)


def unroll_expands(
    body: Block, pack_param_fanout: Dict[str, List[str]]
) -> Block:
    """Rewrite every ``Expand`` in ``body`` into its unrolled ordinary statements.

    Args:
        body: The concrete (already type-substituted) function body.
        pack_param_fanout: Maps each pack VALUE-parameter name (e.g. ``"args"``)
            to the ordered list of its fan-out parameter names
            (e.g. ``["args_0", "args_1"]``). Arity 0 -> empty list.

    Returns:
        The same ``Block`` object with its (nested) statement lists rewritten in
        place. Returned for convenience.
    """
    body.statements = _unroll_stmt_list(body.statements, pack_param_fanout)
    return body


def _unroll_stmt_list(
    statements: List[Stmt], pack_param_fanout: Dict[str, List[str]]
) -> List[Stmt]:
    """Unroll a flat statement list, splicing expanded copies in place."""
    result: List[Stmt] = []
    for stmt in statements:
        if isinstance(stmt, Expand):
            result.extend(_unroll_expand(stmt, pack_param_fanout))
        else:
            result.append(_unroll_in_nested_blocks(stmt, pack_param_fanout))
    return result


def _unroll_expand(
    node: Expand, pack_param_fanout: Dict[str, List[str]]
) -> List[Stmt]:
    """Expand a single ``Expand`` node into its N unrolled body copies."""
    # The iterable must be a Name referencing a pack value-parameter.
    pack_name = node.iterable.id if isinstance(node.iterable, Name) else None
    fanout = pack_param_fanout.get(pack_name, []) if pack_name is not None else []

    out: List[Stmt] = []
    for elem_name in fanout:
        # Independent deep copy so later passes annotate each copy separately.
        body_copy = copy.deepcopy(node.body)
        # Rename free occurrences of the binding var to this fan-out param,
        # honoring sequential let-shadowing across the whole statement list.
        renamed = _rename_block_statements(
            body_copy.statements, node.var, elem_name, _seen=set()
        )
        # A nested expand inside this copy must itself be unrolled.
        renamed = _unroll_stmt_list(renamed, pack_param_fanout)
        out.extend(renamed)
    return out


def _unroll_in_nested_blocks(
    stmt: Stmt, pack_param_fanout: Dict[str, List[str]]
) -> Stmt:
    """Recurse into a non-Expand statement's nested blocks and unroll there.

    Generic over the AST shape: walks every dataclass field and recurses into any
    ``Block`` (or list/tuple of statements) it finds, unrolling expands within.
    The statement node itself is mutated in place (it was already deep-copied by
    the substitutor), and nested ``Block`` statement lists are rewritten.
    """
    _walk_unroll(stmt, pack_param_fanout, _seen=set())
    return stmt


def _walk_unroll(obj, pack_param_fanout, _seen) -> None:
    """Find Blocks reachable from ``obj`` and unroll their statement lists."""
    obj_id = id(obj)
    if obj_id in _seen:
        return
    if isinstance(obj, Block):
        obj.statements = _unroll_stmt_list(obj.statements, pack_param_fanout)
        return
    if not dataclasses.is_dataclass(obj):
        return
    _seen.add(obj_id)
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        _walk_unroll_value(value, pack_param_fanout, _seen)


def _walk_unroll_value(value, pack_param_fanout, _seen) -> None:
    if isinstance(value, Block):
        value.statements = _unroll_stmt_list(value.statements, pack_param_fanout)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_unroll_value(item, pack_param_fanout, _seen)
    elif dataclasses.is_dataclass(value):
        _walk_unroll(value, pack_param_fanout, _seen)


# ---------------------------------------------------------------------------
# Shadow-aware Name renaming
# ---------------------------------------------------------------------------

def _rename_walk(obj, var: str, new_name: str, _seen) -> None:
    """Recurse into a dataclass node, renaming FREE ``Name(id == var)`` within.

    Mutates ``obj`` in place (it is an already-isolated deep copy). Recursion is
    generic over the dataclass field graph so it reaches Name nodes buried in
    calls, member access, string interpolations, binary ops, etc.

    Shadowing: a scope that re-binds ``var`` (an inner ``let var = ...``, a
    ``foreach (var in ...)``, or a nested ``expand(var in ...)``) suppresses
    renaming within that shadowed scope.
    """
    # Replace happens at the parent level (so we can swap the field); here we only
    # descend. A bare Name at the top is handled by callers via _rename_value.
    if not dataclasses.is_dataclass(obj):
        return
    obj_id = id(obj)
    if obj_id in _seen:
        return
    _seen.add(obj_id)

    # A Foreach binding the same name shadows it inside its body.
    if isinstance(obj, Foreach) and obj.item_name == var:
        # Still rename in the iterable (evaluated in the outer scope), but not
        # in the body.
        obj.iterable = _rename_value(obj.iterable, var, new_name, _seen)
        return

    # An Expand binding the same name shadows it inside its body.
    if isinstance(obj, Expand) and obj.var == var:
        obj.iterable = _rename_value(obj.iterable, var, new_name, _seen)
        return

    for f in dataclasses.fields(obj):
        setattr(obj, f.name, _rename_value(getattr(obj, f.name), var, new_name, _seen))


def _rename_value(value, var: str, new_name: str, _seen):
    """Rename within a single field value, returning the (possibly replaced) value.

    A ``Name`` whose ``id == var`` is replaced by a fresh ``Name(new_name)``
    carrying the same ``loc``. For a ``Block`` we honor ``let``-introduced
    shadowing sequentially (a ``let var = ...`` makes subsequent statements opaque
    to the rename).
    """
    if isinstance(value, Name):
        if value.id == var:
            replacement = copy.copy(value)
            replacement.id = new_name
            return replacement
        return value

    if isinstance(value, Block):
        value.statements = _rename_block_statements(
            value.statements, var, new_name, _seen
        )
        return value

    if isinstance(value, list):
        return [_rename_value(item, var, new_name, _seen) for item in value]
    if isinstance(value, tuple):
        return tuple(_rename_value(item, var, new_name, _seen) for item in value)

    if dataclasses.is_dataclass(value):
        _rename_walk(value, var, new_name, _seen)
        return value

    return value


def _rename_block_statements(statements, var: str, new_name: str, _seen):
    """Rename within a statement list, respecting sequential ``let`` shadowing."""
    out = []
    shadowed = False
    for stmt in statements:
        if shadowed:
            # Inside the shadowed tail: leave statements untouched.
            out.append(stmt)
            continue
        # A `let var = VALUE` shadows `var` for all subsequent statements, but the
        # VALUE itself is evaluated in the pre-shadow scope and must be renamed.
        if isinstance(stmt, Let) and stmt.name == var:
            if stmt.value is not None:
                stmt.value = _rename_value(stmt.value, var, new_name, _seen)
            out.append(stmt)
            shadowed = True
            continue
        out.append(_rename_value(stmt, var, new_name, _seen))
    return out
