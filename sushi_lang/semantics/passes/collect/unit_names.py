"""One name, one declaration, in one unit: the holder of a name is decided in SOURCE order.

The collectors run kind by kind, so the order between two kinds is not in the collection
order. This walk reads every top-level declaration of the unit, sorts it by its position,
and refuses each later declaration of a name that an earlier one of ANOTHER kind holds
(#1076, #1069). A refused declaration does not enter its table, so the uses of the holder
give no more errors. Two declarations of ONE kind stay with that kind's collector and its
own code (CE0004, CE2046, CE4001, CE0101, CE0105).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator, List, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import (
    ConstDef, EnumDef, FuncDef, PerkDef, Program, StructDef, VarDef)
from sushi_lang.semantics.ast_walk import is_written
from sushi_lang.semantics.passes.collect.utils import article, type_clash_note, type_kind_word


@dataclass(frozen=True)
class _Declared:
    family: str
    word: str
    name: str
    span: Span
    node: object


def _word(kind: str, node: StructDef | EnumDef | PerkDef | FuncDef) -> str:
    return type_kind_word(kind, bool(node.type_params))


def _declared(program: Program) -> Iterator[_Declared]:
    rows: List[tuple[str, str, list]] = [
        ("value", "", program.constants),
        ("struct", "struct", program.structs),
        ("enum", "enum", program.enums),
        ("perk", "perk", program.perks),
        ("function", "function", program.functions),
    ]
    for family, kind, nodes in rows:
        for node in nodes:
            if not isinstance(node, (ConstDef, StructDef, EnumDef, PerkDef, FuncDef)):
                continue
            span: Optional[Span] = node.name_span or node.loc
            if not is_written(node) or not isinstance(node.name, str) or span is None:
                continue
            word = (("variable" if isinstance(node, VarDef) else "constant")
                    if isinstance(node, ConstDef) else _word(kind, node))
            yield _Declared(family, word, node.name, span, node)


class RefusedDeclarations:
    """The declarations of one unit that lost their name to an earlier one."""

    def __init__(self, nodes: Optional[List[_Declared]] = None) -> None:
        self._ids = {id(decl.node) for decl in nodes or ()}
        self.names = frozenset(decl.name for decl in nodes or ())

    def admits(self, node: object) -> bool:
        return id(node) not in self._ids


def claim_unit_names(reporter: Reporter, program: Program,
                     filename: Optional[str]) -> RefusedDeclarations:
    """Refuse every later declaration of a name an earlier one of another kind holds."""
    holders: dict[str, _Declared] = {}
    refused: List[_Declared] = []
    for decl in sorted(_declared(program), key=lambda d: (d.span.line, d.span.col)):
        holder = holders.setdefault(decl.name, decl)
        if holder is decl or holder.family == decl.family:
            continue
        _reject(reporter, decl, holder, filename)
        refused.append(decl)
    return RefusedDeclarations(refused)


def _reject(reporter: Reporter, decl: _Declared, holder: _Declared,
            filename: Optional[str]) -> None:
    if {decl.family, holder.family} == {"struct", "enum"}:
        builder = er.emit_with(reporter, er.ERR.CE0006, decl.span, filename=filename,
                               kind=decl.word, name=decl.name, other=holder.word)
        note = type_clash_note(holder.word)
    else:
        builder = er.emit_with(reporter, er.ERR.CE1005, decl.span, filename=filename,
                               kind=decl.word, name=decl.name,
                               other=f"{article(holder.word)} {holder.word}")
        note = f"first declared here, as {article(holder.word)} {holder.word}"
    builder.note_at(note, holder.span, filename).emit()
