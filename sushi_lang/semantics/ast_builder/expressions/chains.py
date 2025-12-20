"""Atom and call chain expression parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, Union
from lark import Tree, Token
from sushi_lang.semantics.ast import Expr, Name, BlankLit, MemberAccess, DotCall, TryExpr
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree, first_method_name
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def expr_atom(atom: Tree | Token, ast_builder: 'ASTBuilder') -> Expr:
    """Parse atom: INT | TRUE | FALSE | NAME | array_literal | \"(\" expr \")\" """
    from sushi_lang.semantics.ast_builder.expressions import literals, arrays

    if isinstance(atom, Token):
        return literals.expr_from_token(atom, ast_builder)

    if isinstance(atom, Tree) and atom.data == "array_literal":
        return arrays.array_literal(atom, ast_builder)

    if isinstance(atom, Tree) and atom.data == "dynamic_array_new":
        return arrays.dynamic_array_new(atom)

    if isinstance(atom, Tree) and atom.data == "dynamic_array_from":
        return arrays.dynamic_array_from(atom, ast_builder)

    if isinstance(atom, Tree) and atom.data == "blank_literal":
        return BlankLit(loc=span_of(atom))

    if isinstance(atom, Tree) and atom.data == "stdin_literal":
        return Name(id="stdin", loc=span_of(atom))

    if isinstance(atom, Tree) and atom.data == "stdout_literal":
        return Name(id="stdout", loc=span_of(atom))

    if isinstance(atom, Tree) and atom.data == "stderr_literal":
        return Name(id="stderr", loc=span_of(atom))

    # Parenthesized expression
    inner = next((c for c in atom.children if isinstance(c, Tree)), None)
    if inner is not None:
        return ast_builder._expr(inner)

    # Single literal/name token
    lone_tok = next((c for c in atom.children if isinstance(c, Token)), None)
    if lone_tok is not None:
        return literals.expr_from_token(lone_tok, ast_builder)

    raise NotImplementedError("malformed atom")


def expr_call_chain(t: Tree, ast_builder: 'ASTBuilder') -> Expr:
    """Handle maybe_call: atom followed by zero or more calls/method_calls/member_access/index/try_op."""
    from sushi_lang.semantics.ast_builder.expressions import calls, members

    atom_node = t.children[0]
    result_expr = expr_atom(atom_node, ast_builder)

    for i in range(1, len(t.children)):
        call_node = t.children[i]
        if isinstance(call_node, Tree):
            if call_node.data == "call":
                if isinstance(result_expr, Name):
                    result_expr = calls.call_from_parts(result_expr.id, call_node, ast_builder)
                elif isinstance(result_expr, MemberAccess):
                    args, field_names = calls.extract_call_args(call_node, ast_builder)
                    # Note: field_names for DotCall (enum constructors) are ignored for now
                    # Named parameters for enum variants are not yet supported
                    result_expr = DotCall(
                        receiver=result_expr.receiver,
                        method=result_expr.member,
                        args=args,
                        loc=span_of(t)
                    )
                else:
                    raise NotImplementedError("only NAME(...) or X.Y(...) calls supported for now")

            elif call_node.data == "method_call":
                method_name_tree = first_tree(call_node.children, "method_name")
                if method_name_tree:
                    method_name_tok = first_method_name(method_name_tree.children)
                else:
                    method_name_tok = first_method_name(call_node.children)

                if method_name_tok:
                    args, field_names = calls.extract_call_args(call_node, ast_builder)
                    # Note: field_names for DotCall (method calls) are ignored for now
                    # Named parameters for method calls are not yet supported
                    result_expr = DotCall(
                        receiver=result_expr,
                        method=str(method_name_tok),
                        args=args,
                        loc=span_of(t)
                    )
                else:
                    raise NotImplementedError("method_call: missing method NAME")

            elif call_node.data == "member_access":
                result_expr = members.member_access_from_parts(result_expr, call_node)

            elif call_node.data == "index":
                result_expr = members.index_access_from_parts(result_expr, call_node, ast_builder)

            elif call_node.data == "try_op":
                result_expr = TryExpr(expr=result_expr, loc=span_of(call_node))

            else:
                raise NotImplementedError(f"unexpected call type: {call_node.data}")

    return result_expr
