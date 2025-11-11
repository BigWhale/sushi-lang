"""Call expression parsing (function calls, method calls)."""
from __future__ import annotations
from typing import List, Union, TYPE_CHECKING
from lark import Tree, Token
from semantics.ast import Expr, Call, MethodCall, Name
from semantics.ast_builder.utils.tree_navigation import first_tree, find_tree_recursive, first_name
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def extract_call_args(call_node: Tree, ast_builder: 'ASTBuilder') -> List[Expr]:
    """Extract arguments from a call node."""
    args: List[Expr] = []
    if call_node and call_node.children:
        args_node = first_tree(call_node.children, "args") or find_tree_recursive(call_node, "args")
        if args_node:
            for a in args_node.children:
                args.append(ast_builder._expr(a))
    return args


def call_from_parts(name_tok_or_str: Union[Token, str], call_tail: Tree, ast_builder: 'ASTBuilder') -> Call:
    """Build Call from name and call tail."""
    if isinstance(name_tok_or_str, Token):
        callee = Name(id=str(name_tok_or_str), loc=span_of(name_tok_or_str))
    elif isinstance(name_tok_or_str, str):
        callee = Name(id=name_tok_or_str, loc=None)
    else:
        raise AssertionError("invalid callee in call")

    args = extract_call_args(call_tail, ast_builder)
    return Call(callee=callee, args=args, loc=span_of(call_tail))


def method_call_from_parts(receiver: Expr, method_call_node: Tree, ast_builder: 'ASTBuilder') -> MethodCall:
    """Parse method_call: \".\" NAME \"(\" [args] \")\" """
    assert method_call_node.data == "method_call"

    method_name_tok = first_name(method_call_node.children)
    if method_name_tok is None:
        raise NotImplementedError("method_call: missing method NAME")

    args: List[Expr] = []
    args_node = first_tree(method_call_node.children, "args") or find_tree_recursive(method_call_node, "args")
    if args_node:
        for a in args_node.children:
            args.append(ast_builder._expr(a))

    return MethodCall(
        receiver=receiver,
        method=str(method_name_tok),
        args=args,
        loc=span_of(method_call_node)
    )
