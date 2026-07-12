"""Atom and call chain expression parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.ast import Expr, Name, BlankLit, MemberAccess, DotCall, TryExpr, Call
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree, first_method_name, ice, unhandled
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

    # Primitive float type names usable as a static-method namespace, e.g.
    # f64.from_bits(bits) / f32.from_bits(bits). They lower to a Name receiver so the
    # call chain builds a DotCall, exactly like List.new(). A bare `f64`/`f32` in value
    # position is meaningless and is rejected downstream by the from_bits handler.
    if isinstance(atom, Tree) and atom.data == "f64_name":
        return Name(id="f64", loc=span_of(atom))

    if isinstance(atom, Tree) and atom.data == "f32_name":
        return Name(id="f32", loc=span_of(atom))

    # Expression-body lambda literal (closure). Must precede the parenthesized-
    # expression fallback below, which would otherwise grab the lambda's first Tree
    # child (the params). Block-body lambdas are not atoms; they are built from the
    # `let` statement (see statements/variables.py).
    if isinstance(atom, Tree) and atom.data == "lambda_expr":
        from sushi_lang.semantics.ast_builder.expressions import lambdas
        return lambdas.parse_lambda(atom, ast_builder)

    # Parenthesized expression
    inner = next((c for c in atom.children if isinstance(c, Tree)), None)
    if inner is not None:
        return ast_builder._expr(inner)

    # Single literal/name token
    lone_tok = next((c for c in atom.children if isinstance(c, Token)), None)
    if lone_tok is not None:
        return literals.expr_from_token(lone_tok, ast_builder)

    unhandled(atom)


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
                    # Call-through an arbitrary expression that evaluates to a
                    # function value: arr[0](), (e)(), getfn()(). The callee is
                    # any Expr; the type checker requires it to be a FunctionType.
                    args, field_names = calls.extract_call_args(call_node, ast_builder)
                    result_expr = Call(
                        callee=result_expr,
                        args=args,
                        field_names=field_names,
                        loc=span_of(t),
                    )

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
                    ice(call_node, "missing method NAME")

            elif call_node.data == "member_access":
                result_expr = members.member_access_from_parts(result_expr, call_node)

            elif call_node.data == "index":
                result_expr = members.index_access_from_parts(result_expr, call_node, ast_builder)

            elif call_node.data == "try_op":
                result_expr = TryExpr(expr=result_expr, loc=span_of(call_node))

            else:
                unhandled(call_node)

    return result_expr
