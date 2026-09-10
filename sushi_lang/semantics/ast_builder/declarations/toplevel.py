"""The sort: which list of `Program` each top-level declaration goes in.

`program: (_NEWLINE | DOC_BLOCK | toplevel)+`. `_NEWLINE` is filtered away, `DOC_BLOCK`
is a Token, and `toplevel` carries no `?`, so Lark always builds the node. Every Tree
child of `program` is therefore a `toplevel`, and a `toplevel` holds exactly one
declaration. One table keyed on the rule name answers for every kind; a new kind needs
one row here and nothing else.
"""
from __future__ import annotations

from typing import Callable, Iterator, List, Optional, Tuple, TYPE_CHECKING

from lark import Tree

from sushi_lang.internals.report import Span, span_of
from sushi_lang.semantics.ast import (
    ConstDef, EnumDef, ExtendDef, ExtendWithDef, ExternalBlock, FuncDef, PerkDef,
    StructDef, UseStatement,
)
from sushi_lang.semantics.ast_builder.declarations import (
    constants, enums, extensions, externals, functions, imports, perks, structs,
)
from sushi_lang.semantics.ast_builder.utils.tree_navigation import expect
from sushi_lang.semantics.generics.types import GenericTypeRef

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder

# A rule name, the parser that reads it, and the attribute that holds the result.
DECLARATION_TABLE: dict[str, Tuple[Callable, str]] = {
    "use_stmt": (imports.parse_usestatement, "uses"),
    "struct_def": (structs.parse_structdef, "structs"),
    "enum_def": (enums.parse_enumdef, "enums"),
    "perk_def": (perks.parse_perkdef, "perks"),
    "external_block": (externals.parse_external_block, "externals"),
    "function_def": (functions.parse_funcdef, "functions"),
}

# The two kinds the pre-pass reads before every other kind, and which the sort then
# steps over.
PRE_PASS_RULES = ("const_def", "var_def")

# An `extend_stmt` takes no row: its suffix picks between three lists.
TOPLEVEL_RULES = (*DECLARATION_TABLE, *PRE_PASS_RULES, "extend_stmt")


def declarations_of(tree: Tree) -> Iterator[Tuple[Tree, Tree]]:
    """Every `(toplevel, declaration)` pair of a `program`, in source order.

    A kind the grammar adds and the builder does not know reads CE0003 here, which is
    what that code is for. A `DOC_BLOCK` Token is not a declaration and is stepped
    over: `attach_docs` reads the children again and binds each block by line.
    """
    for child in tree.children:
        if not isinstance(child, Tree):
            continue
        toplevel = expect(child, "toplevel")
        inner = toplevel.children[0] if toplevel.children else None
        yield toplevel, expect(inner, *TOPLEVEL_RULES)


class TopLevelDeclarations:
    """A unit's declarations, sorted into the lists `Program` takes."""

    def __init__(self) -> None:
        self.uses: List[UseStatement] = []
        self.constants: List[ConstDef] = []
        self.structs: List[StructDef] = []
        self.enums: List[EnumDef] = []
        self.perks: List[PerkDef] = []
        self.functions: List[FuncDef] = []
        self.extensions: List[ExtendDef] = []
        self.generic_extensions: List[ExtendDef] = []
        self.perk_impls: List[ExtendWithDef] = []
        self.externals: List[ExternalBlock] = []
        self.first_declaration_span: Optional[Span] = None

    def documented(self) -> List:
        """Every declaration a doc block can bind to, in one list."""
        return [*self.uses, *self.constants, *self.structs, *self.enums, *self.perks,
                *self.functions, *self.extensions, *self.generic_extensions,
                *self.perk_impls, *self.externals]

    def read_constants(self, tree: Tree, builder: 'ASTBuilder') -> None:
        """Read every constant first, whatever order the unit was written in.

        A fixed array's size may name a constant, and every other declaration can hold
        such a type. A unit variable rides in the same list and is NOT declared as a
        size: its value is read at run time (CE2099).
        """
        for _toplevel, decl in declarations_of(tree):
            if decl.data == "const_def":
                const = constants.parse_constdef(decl, builder)
                self.constants.append(const)
                builder.unit_constants.declare(const.name, const)
            elif decl.data == "var_def":
                self.constants.append(constants.parse_vardef(decl, builder))

    def add(self, toplevel: Tree, decl: Tree, builder: 'ASTBuilder') -> None:
        """Put the one declaration a `toplevel` holds in the list it belongs in."""
        if decl.data != "use_stmt" and self.first_declaration_span is None:
            # The first thing that is not an import. Source order lives in the tree and
            # nowhere else, so the rule that reads it (CE3014) is served here.
            self.first_declaration_span = span_of(toplevel)

        match decl.data:
            case "const_def" | "var_def":
                pass
            case "extend_stmt":
                self._add_extend(decl, builder)
            case _:
                parse, target = DECLARATION_TABLE[str(decl.data)]
                getattr(self, target).append(parse(decl, builder))

    def _add_extend(self, node: Tree, builder: 'ASTBuilder') -> None:
        """An `extend_stmt` goes by its suffix, and it carries one suffix only."""
        for child in node.children:
            if not isinstance(child, Tree):
                continue
            if child.data == "extend_with_def":
                self.perk_impls.append(perks.parse_handle_extend_stmt_with(node, builder))
                return
            if child.data == "extend_def":
                ext = extensions.parse_handle_extend_stmt_def(node, builder)
                target = (self.generic_extensions
                          if isinstance(ext.target_type, GenericTypeRef)
                          else self.extensions)
                target.append(ext)
                return
