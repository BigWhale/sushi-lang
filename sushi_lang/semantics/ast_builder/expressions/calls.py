"""Call expression parsing (function calls, method calls)."""
from __future__ import annotations
from typing import List, Union, Tuple, Optional, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.ast import Expr, Call, MethodCall, Name
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree, find_tree_recursive, first_name
from sushi_lang.semantics.ast_builder.utils.expression_discovery import _EXPR_NODES
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def extract_call_args(call_node: Tree, ast_builder: 'ASTBuilder') -> Tuple[List[Expr], Optional[List[str]]]:
    """Extract arguments from a call node.

    Returns:
        Tuple of (argument expressions, field names or None)
        - field_names is None for positional arguments
        - field_names is List[str] for named arguments
    """
    args: List[Expr] = []
    field_names: Optional[List[str]] = None

    if call_node and call_node.children:
        args_node = first_tree(call_node.children, "args") or find_tree_recursive(call_node, "args")
        if args_node:
            # Check for arg_list wrapper
            arg_list = first_tree(args_node.children, "arg_list")
            if arg_list:
                # Check which style: positional_args or named_args
                positional = first_tree(arg_list.children, "positional_args")
                named = first_tree(arg_list.children, "named_args")

                if positional:
                    # Positional arguments
                    for expr_node in positional.children:
                        args.append(ast_builder._expr(expr_node))
                    field_names = None

                elif named:
                    # Named arguments
                    field_names = []
                    for named_arg in named.children:
                        # named_arg: NAME ":" expr
                        assert named_arg.data == "named_arg"
                        name_token = first_name(named_arg.children)

                        # Find the expression node (it's the first Tree child after the NAME)
                        expr_node = None
                        for child in named_arg.children:
                            if isinstance(child, Tree):
                                expr_node = child
                                break

                        if name_token is None or expr_node is None:
                            raise NotImplementedError("Malformed named_arg")

                        field_names.append(str(name_token))
                        args.append(ast_builder._expr(expr_node))
            else:
                # Legacy: direct expr children (positional)
                for a in args_node.children:
                    args.append(ast_builder._expr(a))
                field_names = None

    return args, field_names


def call_from_parts(name_tok_or_str: Union[Token, str], call_tail: Tree, ast_builder: 'ASTBuilder') -> Call:
    """Build Call from name and call tail."""
    if isinstance(name_tok_or_str, Token):
        callee = Name(id=str(name_tok_or_str), loc=span_of(name_tok_or_str))
    elif isinstance(name_tok_or_str, str):
        callee = Name(id=name_tok_or_str, loc=None)
    else:
        raise AssertionError("invalid callee in call")

    args, field_names = extract_call_args(call_tail, ast_builder)
    return Call(callee=callee, args=args, field_names=field_names, loc=span_of(call_tail))


def method_call_from_parts(receiver: Expr, method_call_node: Tree, ast_builder: 'ASTBuilder') -> MethodCall:
    """Parse method_call: \".\" NAME \"(\" [args] \")\" """
    assert method_call_node.data == "method_call"

    method_name_tok = first_name(method_call_node.children)
    if method_name_tok is None:
        raise NotImplementedError("method_call: missing method NAME")

    # Extract arguments (named arguments not supported for methods yet)
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
