"""SHIR to SLIR: what an instance of a generic function adds to the lowering (ir.md 8.2).

An instance is one body: it lowers as every other body does. The callee row of a generic
call names the instance, and the name of a written call can stand behind an alias.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Free

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.decls import DeclNames
    from sushi_lang.semantics.shir.ids import DeclId


def unqualified(name: str) -> str:
    """A written name with the alias before its last dot cut off."""
    return name.rpartition(".")[2]


def free_function_name(decls: DeclNames, written: object, callee: DeclId) -> str:
    """The name of a free function in its unit. The callee row of an instance names the
    instance: no alias stands before that name, and a pack marker can stand in it. Only the
    name of the written declaration `written` has an alias to cut off."""
    name = decls.name_of(callee)
    return unqualified(name) if written == callee else name


def free_callee_name(decls: DeclNames, node: e.Call, callee: Free) -> str:
    """The name of the free function that a call names."""
    written = isinstance(node.target, e.Named) and getattr(node.target.ref, "decl", None)
    return free_function_name(decls, written, callee.decl)
