"""One table sorts a unit's top-level declarations, and it covers the grammar.

`program` holds a `_NEWLINE` (filtered away), a `DOC_BLOCK` (a Token) or a `toplevel`,
and `toplevel` has no `?`, so Lark always builds the node. Every Tree child of
`program` is therefore a `toplevel` that holds exactly ONE declaration, and one table
keyed on the rule name answers for every kind. A kind the grammar adds must get a
table row, or this gate fails.
"""
from __future__ import annotations

import re
from pathlib import Path

from sushi_lang.internals.parser import build_parser
from sushi_lang.semantics.ast import Program
from sushi_lang.semantics.ast_builder import ASTBuilder
from sushi_lang.semantics.ast_builder.declarations.toplevel import (
    DECLARATION_TABLE, PRE_PASS_RULES, TOPLEVEL_RULES, declarations_of)
from sushi_lang.semantics.generics.types import GenericTypeRef

GRAMMAR = Path(__file__).resolve().parents[2] / "sushi_lang" / "grammar.lark"

EVERY_KIND = '''\
##: The unit block. :##
use <math>
public use <collections/hashmap>

const i32 LIMIT = 7

var i32 counter = 0

public struct Crate:
    i32 weight

struct Pair@(T, U):
    T first
    U second

enum Sign:
    Plus
    Minus

perk Weighable:
    fn heft(peek self) i32

unsafe external "C" as libc because "the absolute value":
    fn c_abs(i32 v) i32 = "abs"

extend Crate heavy(peek self) bool:
    return self.weight > LIMIT

extend Crate static empty() Crate:
    return Crate(0)

extend Pair@(T, U) left(peek self) T:
    return self.first

extend Crate with Weighable:
    fn heft(peek self) i32:
        return self.weight

fn main() i32:
    return Result.Ok(0)
'''


def _alternatives(rule: str) -> set[str]:
    """The alternatives of one rule of `grammar.lark`, as bare names."""
    text = GRAMMAR.read_text()
    line = re.search(rf"^{rule}: (.+)$", text, re.MULTILINE)
    assert line is not None, f"no rule named {rule}"
    return {part.strip() for part in line.group(1).split("|")}


def _program(src: str) -> Program:
    return ASTBuilder().build(build_parser().parse(src))


def test_the_table_covers_every_alternative_of_toplevel():
    """A declaration kind the grammar spells must have a row, or a case of its own."""
    assert _alternatives("toplevel") == set(TOPLEVEL_RULES)
    assert set(TOPLEVEL_RULES) == set(DECLARATION_TABLE) | set(PRE_PASS_RULES) | {"extend_stmt"}


def test_program_holds_nothing_but_a_toplevel_a_doc_block_and_a_newline():
    """The invariant that makes a second, bare-node dispatch unreachable."""
    assert _alternatives("program") == {"(_NEWLINE", "DOC_BLOCK", "toplevel)+"}


def test_every_toplevel_node_holds_exactly_one_declaration():
    tree = build_parser().parse(EVERY_KIND)
    pairs = list(declarations_of(tree))
    assert [str(top.data) for top, _ in pairs] == ["toplevel"] * len(pairs)
    assert {str(decl.data) for _, decl in pairs} <= set(TOPLEVEL_RULES)


def test_every_kind_lands_in_the_list_it_belongs_in():
    prog = _program(EVERY_KIND)
    assert [u.path for u in prog.uses] == ["math", "collections/hashmap"]
    assert [c.name for c in prog.constants] == ["LIMIT", "counter"]
    assert [s.name for s in prog.structs] == ["Crate", "Pair"]
    assert [e.name for e in prog.enums] == ["Sign"]
    assert [p.name for p in prog.perks] == ["Weighable"]
    assert [e.namespace for e in prog.externals] == ["libc"]
    assert [f.name for f in prog.functions] == ["main"]
    assert [x.name for x in prog.extensions] == ["heavy", "empty"]
    assert [x.name for x in prog.generic_extensions] == ["left"]
    assert all(isinstance(x.target_type, GenericTypeRef) for x in prog.generic_extensions)
    assert [x.perk_name for x in prog.perk_impls] == ["Weighable"]


def test_the_first_declaration_span_skips_every_import():
    prog = _program(EVERY_KIND)
    assert prog.first_declaration_span is not None
    assert prog.first_declaration_span.line == EVERY_KIND.splitlines().index("const i32 LIMIT = 7") + 1


def test_a_unit_of_imports_alone_has_no_first_declaration_span():
    prog = _program("use <math>\n")
    assert prog.first_declaration_span is None
