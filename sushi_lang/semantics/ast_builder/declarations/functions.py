"""Function definition and parameter parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from lark import Tree, Token
from sushi_lang.semantics.ast import FuncDef, Param
from sushi_lang.semantics.typesys import Type, TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree, find_tree_recursive
from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_funcdef(t: Tree, ast_builder: 'ASTBuilder') -> FuncDef:
    """Parse function_def: [PUBLIC] FN NAME [type_params] "(" [parameters] ")" type ":" block"""
    name_tok = first_name(t.children)

    if name_tok is None:
        raise NotImplementedError("function_def: missing NAME")

    # Check for PUBLIC token
    is_public = False
    for child in t.children:
        if isinstance(child, Token) and child.type == "PUBLIC":
            is_public = True
            break

    # Extract type parameters if present
    type_params_node = first_tree(t.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    params_node = first_tree(t.children, "parameters")

    # Look for type nodes (return type and optional error type)
    # Grammar: ")" type? ["|" type] ":"
    # First type after params is return type, second type (if exists) is error type
    type_nodes = []
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            type_nodes.append(child)

    ret_node = type_nodes[0] if len(type_nodes) >= 1 else None
    err_node = type_nodes[1] if len(type_nodes) >= 2 else None

    body_node = first_tree(t.children, "block") or find_tree_recursive(t, "block")
    if body_node is None:
        raise NotImplementedError("function_def: missing body block")

    # Type-pack names declared by this function. A `variadic_param` whose element
    # type names one of these is a v2 type-pack value-param (...Ts args), not a v1
    # native variadic; `parse_params` uses this set to disambiguate.
    pack_names = (
        {tp.name for tp in type_params if tp.is_pack} if type_params else frozenset()
    )

    params = parse_params(params_node, ast_builder, pack_names) if params_node else []
    ret_ty: Optional[Type] = ast_builder._parse_type(ret_node) if ret_node is not None else None
    err_ty: Optional[Type] = ast_builder._parse_type(err_node) if err_node is not None else None

    return FuncDef(
        name=str(name_tok),
        params=params,
        ret=ret_ty,
        body=ast_builder._block(body_node),
        is_public=is_public,
        type_params=type_params,
        err_type=err_ty,
        loc=span_of(t),
        name_span=span_of(name_tok),
        ret_span=span_of(ret_node),
    )


def parse_params(t: Tree, ast_builder: 'ASTBuilder', pack_names=frozenset()) -> List[Param]:
    """Parse parameters: param ("," param)* where param is typed_param | variadic_param.

    A `variadic_param` (`...T NAME`) is one of two things, disambiguated by
    `pack_names` (the set of the enclosing function's type-pack type-param names):

    - v2 type-pack value-param (`...Ts args`): when the element type is a bare
      NAME matching a declared type-pack type-param. The resulting `Param.ty` is
      the bare pack type-param reference (the same `UnknownType(name="Ts")` a
      regular `Ts x` param yields), `is_pack` is set, `is_variadic` is False.
      Phase 0's monomorphizer recognizes this shape and fans it out.
    - v1 native variadic (`...T values`): any other element type (concrete, or a
      non-pack generic param). The resulting `Param.ty` holds the collected
      `DynamicArrayType(T)` and `is_variadic` is set. Last-position / at-most-one
      / context restrictions are enforced semantically in the collect pass.

    `pack_names` defaults to empty, so callers that do not pass it (e.g. extern
    params) treat every `variadic_param` as v1 — unchanged.

    Also accepts `extern_params` (which adds an optional trailing ELLIPSIS for
    untyped C varargs); the ELLIPSIS token is ignored here and handled by the
    extern declaration parser.
    """
    assert t.data in ("parameters", "extern_params")

    from sushi_lang.semantics.typesys import DynamicArrayType
    from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name as _first_name

    out: List[Param] = []
    for ch in t.children:
        # The shared `parameters` rule wraps each entry in a `param` node.
        node = ch
        if isinstance(node, Tree) and node.data == "param":
            inner = next((c for c in node.children if isinstance(c, Tree)), None)
            if inner is None:
                continue
            node = inner

        if not isinstance(node, Tree):
            continue

        if node.data in ("typed_param", "variadic_param"):
            ty_node = next(
                (
                    sub
                    for sub in node.children
                    if isinstance(sub, Tree)
                    and (sub.data in TYPE_NODE_NAMES or sub.data == "name_t")
                ),
                None,
            )
            if ty_node is None:
                raise NotImplementedError(f"{node.data}: missing type")

            ty = ast_builder._parse_type(ty_node)

            nm_tok = first_name(node.children)
            if nm_tok is None:
                raise NotImplementedError(f"{node.data}: missing NAME")

            is_variadic = node.data == "variadic_param"
            is_pack = False
            if is_variadic:
                # A `...Ts` whose element type is a bare NAME matching one of the
                # function's declared type-pack type-params is a v2 type-pack
                # value-param: keep `ty` as the bare pack-name reference (the same
                # representation `_parse_type` produced) so Phase 0 recognizes it.
                elem_name = None
                if ty_node.data == "name_t":
                    elem_tok = _first_name(ty_node.children)
                    if elem_tok is not None:
                        elem_name = str(elem_tok)

                if elem_name is not None and elem_name in pack_names:
                    is_pack = True
                    is_variadic = False
                    # `ty` already holds the bare pack-name reference; do NOT wrap.
                else:
                    # v1 native variadic: the body sees a homogeneous T[]; `ty`
                    # (the element type) is the collected dynamic-array type. The
                    # element type stays recoverable as `ty.base_type`.
                    ty = DynamicArrayType(base_type=ty)

            out.append(
                Param(
                    name=str(nm_tok),
                    ty=ty,
                    name_span=span_of(nm_tok),
                    type_span=span_of(ty_node),
                    loc=span_of(node),
                    is_variadic=is_variadic,
                    is_pack=is_pack,
                )
            )

    return out


