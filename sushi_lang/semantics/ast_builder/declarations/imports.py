"""Import/use statement parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.ast import UseStatement
from sushi_lang.semantics.ast_builder.utils.tree_navigation import ice, expect
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_usestatement(t: Tree, ast_builder: 'ASTBuilder') -> UseStatement:
    """Parse use_stmt: USE (stdlib_import | lib_import | user_import) _NEWLINE"""
    t = expect(t, "use_stmt")

    import_node = None
    for child in t.children:
        if isinstance(child, Tree) and child.data in ("stdlib_import", "lib_import", "user_import"):
            import_node = child
            break

    if import_node is None:
        ice(t, "missing import node")

    is_stdlib = False
    is_library = False

    if import_node.data == "stdlib_import":
        use_path = None
        for child in import_node.children:
            if isinstance(child, Tree) and child.data == "use_path":
                use_path = child
                break

        if use_path is None:
            ice(import_node, "missing use_path")

        parts = []
        for child in use_path.children:
            if isinstance(child, Token) and child.type == "NAME":
                parts.append(str(child.value))

        path = "/".join(parts)
        is_stdlib = True

    elif import_node.data == "lib_import":
        use_path = None
        for child in import_node.children:
            if isinstance(child, Tree) and child.data == "use_path":
                use_path = child
                break

        if use_path is None:
            ice(import_node, "missing use_path")

        parts = []
        for child in use_path.children:
            if isinstance(child, Token) and child.type == "NAME":
                parts.append(str(child.value))

        path = "lib/" + "/".join(parts)
        is_library = True

    else:
        string_tok = None
        for child in import_node.children:
            if isinstance(child, Token) and child.type == "STRING":
                string_tok = child
                break

        if string_tok is None:
            ice(import_node, "missing STRING path")

        path = str(string_tok.value)
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]

    return UseStatement(
        path=path,
        is_stdlib=is_stdlib,
        is_library=is_library,
        loc=span_of(t)
    )
