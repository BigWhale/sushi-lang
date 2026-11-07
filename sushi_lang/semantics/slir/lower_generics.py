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


def free_callee_name(decls: DeclNames, node: e.Call, callee: Free) -> str:
    """The name of a free function in its unit. The callee row of an instance names the
    instance: no alias stands before that name, and a pack marker can stand in it."""
    name = decls.name_of(callee.decl)
    written = isinstance(node.target, e.Named) and getattr(node.target.ref, "decl", None)
    return name.rpartition(".")[2] if written == callee.decl else name
