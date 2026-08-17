"""The whole-program pre-pass: which `poke` parameters a call transitively destroys."""

from __future__ import annotations
from typing import Dict, FrozenSet, Iterable, Iterator, Optional, Set

from sushi_lang.semantics.ast import (
    Borrow,
    Block,
    Call,
    DotCall,
    ExprStmt,
    Expr,
    Foreach,
    FuncDef,
    If,
    Let,
    Match,
    MethodCall,
    Name,
    Program,
    Rebind,
    Stmt,
    TryExpr,
    While,
)
from sushi_lang.semantics.typesys import ReferenceType


def _iter_stmts(block: Block) -> Iterator[Stmt]:
    """Every statement in a body, descending into nested blocks."""
    for stmt in block.statements:
        yield stmt
        if isinstance(stmt, If):
            for _cond, arm in stmt.arms:
                yield from _iter_stmts(arm)
            if stmt.else_block:
                yield from _iter_stmts(stmt.else_block)
        elif isinstance(stmt, (While, Foreach)):
            yield from _iter_stmts(stmt.body)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                if isinstance(arm.body, Block):
                    yield from _iter_stmts(arm.body)


def _leading_call(expr: Optional[Expr]) -> Optional[Call]:
    """The Call an expression evaluates to, unwrapping `??` -- None if it is not a call."""
    while isinstance(expr, TryExpr):
        expr = expr.expr
    return expr if isinstance(expr, Call) else None


def _statement_call(stmt: Stmt) -> Optional[Call]:
    """The call a statement makes, for the shapes a `poke` argument can appear in."""
    if isinstance(stmt, ExprStmt):
        return _leading_call(stmt.expr)
    if isinstance(stmt, (Let, Rebind)):
        return _leading_call(stmt.value)
    return None


def _poke_param_indices(func: FuncDef) -> Dict[str, int]:
    """`poke` parameters of `func`, by name -> positional index."""
    return {
        param.name: i
        for i, param in enumerate(func.params)
        if isinstance(param.ty, ReferenceType) and param.ty.is_poke()
    }


def compute_destroy_effects(programs: Iterable[Program]) -> Dict[str, FrozenSet[int]]:
    """`fn name -> the poke parameter indices it destroys`, transitively (#168).

    The one inter-procedural analysis in semantics, and deliberately an UNDER-approximation:
    it can miss a destroy but never invent one, so it cannot produce a false CE2406. Known
    misses: a generic callee (monomorphized fns are not in `program.functions`), an
    extension/perk method destroying its implicit `self` (not in `func.params`), and a call
    nested anywhere but a statement's leading expression.
    """
    funcs: Dict[str, FuncDef] = {}
    for program in programs:
        for func in program.functions:
            funcs.setdefault(func.name, func)

    effects: Dict[str, Set[int]] = {}

    for name, func in funcs.items():
        poke = _poke_param_indices(func)
        destroyed: Set[int] = set()
        for stmt in _iter_stmts(func.body):
            if not isinstance(stmt, ExprStmt):
                continue
            call = stmt.expr
            if isinstance(call, (MethodCall, DotCall)) and call.method == "destroy":
                if isinstance(call.receiver, Name) and call.receiver.id in poke:
                    destroyed.add(poke[call.receiver.id])
        effects[name] = destroyed

    changed = True
    while changed:
        changed = False
        for name, func in funcs.items():
            poke = _poke_param_indices(func)
            if not poke:
                continue
            for stmt in _iter_stmts(func.body):
                call = _statement_call(stmt)
                if call is None or not isinstance(call.callee, Name):
                    continue
                for index in effects.get(call.callee.id, ()):
                    if index >= len(call.args):
                        continue
                    arg = call.args[index]
                    if isinstance(arg, Borrow):
                        arg = arg.expr
                    if isinstance(arg, Name) and arg.id in poke:
                        own_index = poke[arg.id]
                        if own_index not in effects[name]:
                            effects[name].add(own_index)
                            changed = True

    return {name: frozenset(indices) for name, indices in effects.items() if indices}
