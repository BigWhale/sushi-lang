"""Import/use statement parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.ast import UseStatement
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_token, first_tree, ice, expect, name_tokens)
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


# The two bracketed forms spell one path, `NAME ("/" NAME)*`, and differ in what that
# path means: the prefix it carries, and the flag it sets. The rule name is what tells
# them apart, so it is what the table is keyed on (#638). A bracketed form the grammar
# adds gets a row here and no reader of its own.
BRACKETED_IMPORTS = {
    # rule name: (path prefix, is_stdlib, is_library)
    "stdlib_import": ("", True, False),
    "lib_import": ("lib/", False, True),
}

# Every import form `use_stmt` admits. The quoted form reads a STRING and not a
# `use_path`, so it keeps a reader of its own.
IMPORT_RULES = tuple(BRACKETED_IMPORTS) + ("user_import",)


def _joined_path(import_node: Tree) -> str:
    """The segments of a bracketed import's `use_path`, joined with slashes."""
    use_path = first_tree(import_node.children, "use_path")
    if use_path is None:
        ice(import_node, "missing use_path")
    return "/".join(str(tok.value) for tok in name_tokens(use_path.children))


def _quoted_path(import_node: Tree) -> str:
    """The path of `use "geometry"`: the STRING, without its quotes."""
    string_tok = first_token(import_node.children, "STRING")
    if string_tok is None:
        ice(import_node, "missing STRING path")
    path = str(string_tok.value)
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path


def parse_usestatement(t: Tree, ast_builder: 'ASTBuilder') -> UseStatement:
    """Parse use_stmt: PUBLIC? USE (stdlib_import | lib_import | user_import) [AS NAME]"""
    t = expect(t, "use_stmt")

    import_node = None
    alias_tok = None
    public_tok = None
    for child in t.children:
        if isinstance(child, Tree) and child.data in IMPORT_RULES:
            import_node = child
        elif isinstance(child, Token) and child.type == "NAME":
            alias_tok = child
        elif isinstance(child, Token) and child.type == "PUBLIC":
            public_tok = child

    if import_node is None:
        ice(t, "missing import node")

    row = BRACKETED_IMPORTS.get(import_node.data)
    if row is not None:
        prefix, is_stdlib, is_library = row
        path = prefix + _joined_path(import_node)
    else:
        path = _quoted_path(import_node)
        is_stdlib = False
        is_library = False

    return UseStatement(
        path=path,
        is_stdlib=is_stdlib,
        is_library=is_library,
        alias=str(alias_tok.value) if alias_tok is not None else None,
        alias_span=span_of(alias_tok) if alias_tok is not None else None,
        is_public=public_tok is not None,
        public_span=span_of(public_tok) if public_tok is not None else None,
        loc=span_of(t)
    )
