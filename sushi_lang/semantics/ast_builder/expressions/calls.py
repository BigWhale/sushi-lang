"""Call expression parsing (function calls, method calls)."""
from __future__ import annotations
from typing import List, Union, Tuple, Optional, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.ast import Expr, Call, MethodCall, Name, Spread
from sushi_lang.semantics.ast_builder.types.generics import parse_type_list
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_tree, find_tree_recursive, first_name, ice, expect, mark_nom)
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def extract_call_args(call_node: Tree, ast_builder: 'ASTBuilder') -> Tuple[List[Expr], Optional[List[str]]]:
    """Extract arguments from a call node."""
    args: List[Expr] = []
    field_names: Optional[List[str]] = None

    if call_node and call_node.children:
        args_node = first_tree(call_node.children, "args") or find_tree_recursive(call_node, "args")
        if args_node:
            arg_list = first_tree(args_node.children, "arg_list")
            if arg_list:
                positional = first_tree(arg_list.children, "positional_args")
                named = first_tree(arg_list.children, "named_args")

                if positional:
                    for expr_node in positional.children:
                        if isinstance(expr_node, Tree) and expr_node.data == "spread_arg":
                            inner = ast_builder._expr(expr_node.children[0])
                            args.append(Spread(value=inner, loc=span_of(expr_node)))
                        elif isinstance(expr_node, Tree) and expr_node.data == "nom_arg":
                            # `f(nom x)` is a call-site MARKER, not an operator: it stamps
                            # a flag rather than wrapping the argument in a node every pass
                            # would dispatch on. At the ARGUMENT level, not in `?unary`, so
                            # `nom 1000 as i64` marks the cast and not just the literal.
                            marked = ast_builder._expr(expr_node.children[-1])
                            args.append(mark_nom(marked, expr_node.children[0]))
                        else:
                            args.append(ast_builder._expr(expr_node))
                    field_names = None

                elif named:
                    field_names = []
                    for named_arg in named.children:
                        named_arg = expect(named_arg, "named_arg")
                        name_token = first_name(named_arg.children)

                        expr_node = None
                        for child in named_arg.children:
                            if isinstance(child, Tree):
                                expr_node = child
                                break

                        if name_token is None or expr_node is None:
                            ice(named_arg, "malformed named_arg")

                        field_names.append(str(name_token))
                        args.append(ast_builder._expr(expr_node))
            else:
                for a in args_node.children:
                    args.append(ast_builder._expr(a))
                field_names = None

    return args, field_names


def extract_call_type_args(call_node: Tree, ast_builder: 'ASTBuilder'):
    """Extract explicit call-site type arguments from a `call` node."""
    if not (call_node and call_node.children):
        return None, None
    type_list_node = first_tree(call_node.children, "type_list")
    if type_list_node is None:
        return None, None
    type_args = parse_type_list(type_list_node, ast_builder)
    return (type_args or None), span_of(type_list_node)


def call_from_parts(callee_name: Union[Name, Token], call_tail: Tree, ast_builder: 'ASTBuilder') -> Call:
    """Build Call from its callee and its call tail.

    The callee arrives as the `Name` the atom was already parsed into, SPAN AND ALL. Built
    afresh from the bare id, it had no span, and every diagnostic anchored to a callee --
    CE2008, CE2009, CE3005, the CE206x family -- rendered as text with no caret.
    """
    if isinstance(callee_name, Name):
        callee = callee_name
    elif isinstance(callee_name, Token):
        callee = Name(id=str(callee_name), loc=span_of(callee_name))
    else:
        ice(call_tail, "invalid callee in call")

    args, field_names = extract_call_args(call_tail, ast_builder)
    type_args, type_args_loc = extract_call_type_args(call_tail, ast_builder)
    return Call(callee=callee, args=args, field_names=field_names,
                type_args=type_args, type_args_loc=type_args_loc,
                loc=span_of(call_tail))


def method_call_from_parts(receiver: Expr, method_call_node: Tree, ast_builder: 'ASTBuilder') -> MethodCall:
    """Parse method_call: \".\" NAME \"(\" [args] \")\" """
    method_call_node = expect(method_call_node, "method_call")

    method_name_tok = first_name(method_call_node.children)
    if method_name_tok is None:
        ice(method_call_node, "missing method NAME")

    args, field_names = extract_call_args(method_call_node, ast_builder)

    # Named arguments are not supported for method calls
    # If field_names is not None, validation will catch this later in semantic analysis
    # For now, we just ignore field_names for method calls

    return MethodCall(
        receiver=receiver,
        method=str(method_name_tok),
        args=args,
        loc=span_of(method_call_node)
    )
