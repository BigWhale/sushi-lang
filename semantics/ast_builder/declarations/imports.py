"""Import/use statement parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree, Token
from semantics.ast import UseStatement
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_usestatement(t: Tree, ast_builder: 'ASTBuilder') -> UseStatement:
    """Parse use_stmt: USE (stdlib_import | lib_import | user_import) _NEWLINE"""
    assert t.data == "use_stmt"

    # Find the import node (stdlib_import, lib_import, or user_import)
    import_node = None
    for child in t.children:
        if isinstance(child, Tree) and child.data in ("stdlib_import", "lib_import", "user_import"):
            import_node = child
            break

    if import_node is None:
        raise NotImplementedError("use_stmt: missing import node")

    is_stdlib = False
    is_library = False

    if import_node.data == "stdlib_import":
        # <module> or <module/submodule>
        # Find the use_path node
        use_path = None
        for child in import_node.children:
            if isinstance(child, Tree) and child.data == "use_path":
                use_path = child
                break

        if use_path is None:
            raise NotImplementedError("stdlib_import: missing use_path")

        # Extract NAME tokens and join with "/"
        parts = []
        for child in use_path.children:
            if isinstance(child, Token) and child.type == "NAME":
                parts.append(str(child.value))

        path = "/".join(parts)
        is_stdlib = True

    elif import_node.data == "lib_import":
        # <lib/mylib> or <lib/vendor/utils>
        # Find the use_path node (everything after "lib/")
        use_path = None
        for child in import_node.children:
            if isinstance(child, Tree) and child.data == "use_path":
                use_path = child
                break

        if use_path is None:
            raise NotImplementedError("lib_import: missing use_path")

        # Extract NAME tokens and join with "/"
        parts = []
        for child in use_path.children:
            if isinstance(child, Token) and child.type == "NAME":
                parts.append(str(child.value))

        # Store path with lib/ prefix for consistency
        path = "lib/" + "/".join(parts)
        is_library = True

    else:
        # user_import: "module"
        # Extract the string token
        string_tok = None
        for child in import_node.children:
            if isinstance(child, Token) and child.type == "STRING":
                string_tok = child
                break

        if string_tok is None:
            raise NotImplementedError("user_import: missing STRING path")

        # Remove quotes from the string literal
        path = str(string_tok.value)
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]

    return UseStatement(
        path=path,
        is_stdlib=is_stdlib,
        is_library=is_library,
        loc=span_of(t)
    )
