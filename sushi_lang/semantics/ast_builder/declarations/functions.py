"""Function definition and parameter parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from lark import Tree, Token
from sushi_lang.semantics.ast import FuncDef, Param
from sushi_lang.semantics.typesys import DynamicArrayType, Type
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    expect, find_tree_recursive, first_name, first_token, first_tree, ice,
    is_type_node, read_public)
from sushi_lang.semantics.ast_builder.declarations.docs import lift_body_doc
from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of


def strip_self_param(params: List[Param], where_span=None):
    """Lift a `poke self` / `peek self` / `nom self` parameter off a param list."""
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

    is_public, public_span = read_public(t.children)

    type_params_node = first_tree(t.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    params_node = first_tree(t.children, "parameters")

    # Look for type nodes (return type and optional error type)
    # Grammar: ")" type? ["|" type] ":"
    # First type after params is return type, second type (if exists) is error type
    type_nodes = []
    for child in t.children:
        if is_type_node(child):
            type_nodes.append(child)

    ret_node = type_nodes[0] if len(type_nodes) >= 1 else None
    err_node = type_nodes[1] if len(type_nodes) >= 2 else None

    body_node = first_tree(t.children, "block") or find_tree_recursive(t, "block")
    if body_node is None:
        ice(t, "missing body block")

    # Type-pack names declared by this function. A `variadic_param` whose element
    # type names one of these is a v2 type-pack value-param (...Ts args), not a v1
    # native variadic; `_variadic_or_pack` reads this set to tell the two apart.
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
        public_span=public_span,
    )


def parse_params(t: Tree, ast_builder: 'ASTBuilder', pack_names=frozenset()) -> List[Param]:
    """Parse parameters: param ("," param)* where param is typed_param | variadic_param.

    The loop unwraps the `param` node and hands the rule inside it to the reader that
    owns that rule. A child that is no parameter rule is stepped over: `extern_params`
    holds its `typed_param`s bare and adds an ELLIPSIS token for a C variadic.
    """
    t = expect(t, "parameters", "extern_params")

    out: List[Param] = []
    for ch in t.children:
        node = _param_rule(ch)
        if node is None:
            continue
        if node.data in ("self_param", "nom_self_param"):
            out.append(_self_param(node))
        elif node.data == "typed_param":
            out.append(_typed_param(node, ast_builder))
        elif node.data == "variadic_param":
            out.append(_variadic_or_pack(node, ast_builder, pack_names))
    return out


def _param_rule(ch: object) -> Optional[Tree]:
    """Step through a `param` wrapper to the rule it holds; answer None for anything else."""
    node: object = ch
    if isinstance(node, Tree) and node.data == "param":
        node = next((c for c in node.children if isinstance(c, Tree)), None)
    return node if isinstance(node, Tree) else None


def _self_param(node: Tree) -> Param:
    """Read a receiver parameter: `peek self` / `poke self` (#327), or `nom self` (R25).

    The mode rides on the Param. `strip_self_param` lifts it onto the declaration and
    validates the position, so collect never sees a Param named `self`.
    """
    token_type = "NOM" if node.data == "nom_self_param" else "BORROW_MODE"
    mode_tok = first_token(node.children, token_type)
    name_tok = first_name(node.children)
    if mode_tok is None or name_tok is None:
        ice(node, "malformed self_param")
    if str(name_tok) != "self":
        raise SyntaxDiagnostic("CE2425", span=span_of(node)) \
            .help("a reference parameter is written `poke T name`; the bare "
                  "form is only the receiver, spelled `poke self`")
    return Param(
        name="self", ty=None,
        name_span=span_of(name_tok), loc=span_of(node),
        self_mode=str(mode_tok.value),
    )


def _typed_param(node: Tree, ast_builder: 'ASTBuilder') -> Param:
    """Read `typed_param`: an optional `nom` marker, a type and a name.

    `nom T name` gives ownership to the callee. The grammar admits the marker on this
    rule only, so a variadic and a pack can never carry one.
    """
    ty_node, ty, name_tok = _type_and_name(node, ast_builder)
    nom_tok = first_token(node.children, "NOM")
    return Param(
        name=str(name_tok),
        ty=ty,
        name_span=span_of(name_tok),
        type_span=span_of(ty_node),
        loc=span_of(node),
        is_nom=nom_tok is not None,
        nom_span=span_of(nom_tok) if nom_tok is not None else None,
    )


def _variadic_or_pack(node: Tree, ast_builder: 'ASTBuilder', pack_names) -> Param:
    """Read `variadic_param`: a native `...T`, or a type-pack value parameter `...Ts`.

    The element type tells the two apart. A bare NAME that this function declared as a
    type pack makes a v2 pack value-param, and `ty` stays that bare pack-name reference,
    which is the representation the collect pass reads. Everything else is a v1 native
    variadic: the body sees a homogeneous `T[]`, and the element type stays available as
    `ty.base_type`.
    """
    ty_node, ty, name_tok = _type_and_name(node, ast_builder)
    is_pack = _names_a_type_pack(ty_node, pack_names)
    if not is_pack:
        ty = DynamicArrayType(base_type=ty)
    return Param(
        name=str(name_tok),
        ty=ty,
        name_span=span_of(name_tok),
        type_span=span_of(ty_node),
        loc=span_of(node),
        is_variadic=not is_pack,
        is_pack=is_pack,
    )


def _names_a_type_pack(ty_node: Tree, pack_names) -> bool:
    """Is this element type a bare NAME that the function declared as a type pack?"""
    if ty_node.data != "name_t":
        return False
    elem_tok = first_name(ty_node.children)
    return elem_tok is not None and str(elem_tok) in pack_names


def _type_and_name(node: Tree,
                   ast_builder: 'ASTBuilder') -> tuple[Tree, Optional[Type], Token]:
    """Read the written type, the parsed type and the name off one parameter rule."""
    ty_node = next((sub for sub in node.children if is_type_node(sub)), None)
    if not isinstance(ty_node, Tree):
        ice(node, "missing type")
    ty = ast_builder._parse_type(ty_node)
    name_tok = first_name(node.children)
    if name_tok is None:
        ice(node, "missing NAME")
    return ty_node, ty, name_tok


