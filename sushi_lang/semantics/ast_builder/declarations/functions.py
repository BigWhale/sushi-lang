"""Function definition and parameter parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from lark import Tree, Token
from sushi_lang.semantics.ast import FuncDef, Param
from sushi_lang.semantics.typesys import Type, TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree, find_tree_recursive, ice, expect
from sushi_lang.semantics.ast_builder.declarations.docs import lift_body_doc
from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of


def strip_self_param(params: List[Param], where_span=None):
    """Lift a `poke self` / `peek self` parameter off a parsed param list (#327)."""
    self_mode = None
    self_mode_span = None
    remaining: List[Param] = []
    for index, param in enumerate(params):
        if param.self_mode is not None:
            if index != 0:
                raise SyntaxDiagnostic("CE2425", span=param.loc or where_span) \
                    .help("the receiver comes first: `(poke self, <params>)`")
            self_mode = param.self_mode
            self_mode_span = param.loc
        else:
            remaining.append(param)
    return self_mode, self_mode_span, remaining

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_funcdef(t: Tree, ast_builder: 'ASTBuilder') -> FuncDef:
    """Parse function_def: [PUBLIC] FN NAME [type_params] "(" [parameters] ")" type ":" block"""
    name_tok = first_name(t.children)

    if name_tok is None:
        ice(t, "missing NAME")

    is_public = False
    for child in t.children:
        if isinstance(child, Token) and child.type == "PUBLIC":
            is_public = True
            break

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
        ice(t, "missing body block")

    # Type-pack names declared by this function. A `variadic_param` whose element
    # type names one of these is a v2 type-pack value-param (...Ts args), not a v1
    # native variadic; `parse_params` uses this set to disambiguate.
    pack_names = (
        {tp.name for tp in type_params if tp.is_pack} if type_params else frozenset()
    )

    params = parse_params(params_node, ast_builder, pack_names) if params_node else []
    # A perk-impl method parses through this rule and may declare a receiver (#327);
    # a plain top-level function may not -- collect rejects it there (CE2425).
    self_mode, self_mode_span, params = strip_self_param(params, span_of(t))
    ret_ty: Optional[Type] = ast_builder._parse_type(ret_node) if ret_node is not None else None
    err_ty: Optional[Type] = ast_builder._parse_type(err_node) if err_node is not None else None
    body = ast_builder._block(body_node)

    return FuncDef(
        name=str(name_tok),
        params=params,
        ret=ret_ty,
        body=body,
        is_public=is_public,
        type_params=type_params,
        err_type=err_ty,
        loc=span_of(t),
        name_span=span_of(name_tok),
        ret_span=span_of(ret_node),
        self_mode=self_mode,
        self_mode_span=self_mode_span,
        doc=lift_body_doc(body, ast_builder),
    )


def parse_params(t: Tree, ast_builder: 'ASTBuilder', pack_names=frozenset()) -> List[Param]:
    """Parse parameters: param ("," param)* where param is typed_param | variadic_param."""
    t = expect(t, "parameters", "extern_params")

    from sushi_lang.semantics.typesys import DynamicArrayType
    from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name as _first_name

    out: List[Param] = []
    for ch in t.children:
        node = ch
        if isinstance(node, Tree) and node.data == "param":
            inner = next((c for c in node.children if isinstance(c, Tree)), None)
            if inner is None:
                continue
            node = inner

        if not isinstance(node, Tree):
            continue

        if node.data == "self_param":
            # `poke self` / `peek self` (#327): a receiver-mode parameter. The mode
            # rides on the Param; `strip_self_param` lifts it onto the declaration and
            # validates the position, so collect never sees a `self`-named Param.
            mode_tok = next((c for c in node.children
                             if isinstance(c, Token) and c.type == "BORROW_MODE"), None)
            name_tok = first_name(node.children)
            if mode_tok is None or name_tok is None:
                ice(node, "malformed self_param")
            if str(name_tok) != "self":
                raise SyntaxDiagnostic("CE2425", span=span_of(node)) \
                    .help("a reference parameter is written `poke T name`; the bare "
                          "form is only the receiver, spelled `poke self`")
            out.append(Param(
                name="self", ty=None,
                name_span=span_of(name_tok), loc=span_of(node),
                self_mode=str(mode_tok.value),
            ))
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
                ice(node, "missing type")

            ty = ast_builder._parse_type(ty_node)

            nm_tok = first_name(node.children)
            if nm_tok is None:
                ice(node, "missing NAME")

            is_variadic = node.data == "variadic_param"
            is_pack = False
            if is_variadic:
                # A `...Ts` whose element type is a bare NAME matching one of the
                # function's declared type-pack type-params is a v2 type-pack
                # value-param: keep `ty` as the bare pack-name reference (the same
                # representation `_parse_type` produced) so the collect pass recognizes it.
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

            # `nom T name`: the callee takes ownership. The grammar admits the marker on
            # `typed_param` only, so a variadic or a pack can never carry one.
            nom_tok = next((c for c in node.children
                            if isinstance(c, Token) and c.type == "NOM"), None)

            out.append(
                Param(
                    name=str(nm_tok),
                    ty=ty,
                    name_span=span_of(nm_tok),
                    type_span=span_of(ty_node),
                    loc=span_of(node),
                    is_variadic=is_variadic,
                    is_pack=is_pack,
                    is_nom=nom_tok is not None,
                    nom_span=span_of(nom_tok) if nom_tok is not None else None,
                )
            )

    return out


