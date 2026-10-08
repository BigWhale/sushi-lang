"""Parser for generic type instantiations."""
from __future__ import annotations
from typing import Callable, NoReturn, Optional, List, TYPE_CHECKING, Tuple
from lark import Tree, Token
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.semantics.generics.extension_targets import TargetParams
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import DynamicArrayType, Type, UnknownType
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_name, first_tree, ice, is_type_node, name_tokens)
from sushi_lang.internals.report import Span, span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def _is_type_arg(child: object) -> bool:
    """An element of a `type_list`: a type, or a bounded type argument (`T: Clone`)."""
    return is_type_node(child) or (isinstance(child, Tree)
                                   and child.data == "bounded_type_arg")


# What one reader makes of one element of a `type_list`: the type, and what a target
# argument declares (None outside a target).
ArgReader = Callable[[Tree], Tuple[Optional[Type], Optional[BoundedTypeParam]]]


def _read_type_args(type_list_node: Tree, read_arg: ArgReader
                    ) -> Tuple[List[Type], TargetParams]:
    """Each element of a `type_list` through `read_arg`: the ONE walk of the list."""
    args: List[Type] = []
    params: List[Optional[BoundedTypeParam]] = []
    for child in type_list_node.children:
        if not _is_type_arg(child):
            continue
        arg_type, param = read_arg(child)
        if arg_type is not None:
            args.append(arg_type)
            params.append(param)
    return args, tuple(params)


def _read_generic(node: Tree, read_arg: ArgReader
                  ) -> Tuple[Optional[GenericTypeRef], TargetParams]:
    """A generic type instantiation, bare or behind an alias: the ONE reader of its parts.

    `qualified_generic_type_t` carries two NAMEs and `generic_type_t` one. The
    qualifier is recorded and the base name is the table key (Ruling 4).
    """
    names = name_tokens(node.children)
    type_list_node = first_tree(node.children, "type_list")
    if not names or type_list_node is None:
        return None, ()
    args, params = _read_type_args(type_list_node, read_arg)
    if not args:
        return None, ()
    namespace = str(names[0]) if len(names) > 1 else None
    return (GenericTypeRef(base_name=str(names[-1]), type_args=tuple(args),
                           namespace=namespace), params)


def _plain_reader(ast_builder: 'ASTBuilder') -> ArgReader:
    """Every position but the top level of a target: a bound reaches `_parse_type` (CE6110)."""
    return lambda child: (ast_builder._parse_type(child), None)


def parse_type_list(type_list_node: Tree, ast_builder: 'ASTBuilder') -> List[Type]:
    """Turn a `type_list` parse node into a list of resolved Types.

    A bounded argument goes through `_parse_type` too, which refuses it (CE6110): this
    reader serves every position but the top level of an `extend` target.
    """
    return _read_type_args(type_list_node, _plain_reader(ast_builder))[0]


# The two grammar nodes of a bound in a type position: `T: Clone` in a type-argument
# list, and `(T: Clone)` as a parenthesized type. Only the target reader below takes one.
BOUNDED_ARGUMENT_NODES = ("bounded_type_arg", "bounded_paren_t")


def parse_extension_target(node: Tree, ast_builder: 'ASTBuilder') -> Tuple[Optional[Type], TargetParams]:
    """The target of an `extend`, and what each argument at its top level declares.

    A target can put a bound on a type parameter, `extend List@(T: Clone)` and
    `extend (T: Clone)[]` (#1070). This reads the TOP level of the target and nothing
    deeper: an argument that is not a bare name goes through `_parse_type`, so a bound
    inside it is CE6110. The second value is index-aligned with the `@(...)` arguments, or
    it holds the one array element: a `BoundedTypeParam` for a bare name (no constraints)
    or a bounded name, and None for every other argument.
    """
    if node.data in ("generic_type_t", "qualified_generic_type_t"):
        return _bounded_generic_target(node, ast_builder)
    if node.data == "dynamic_array_t":
        element = next((child for child in node.children if is_type_node(child)), None)
        if element is not None and element.data == "bounded_paren_t":
            param = _target_param(element)
            return DynamicArrayType(base_type=UnknownType(name=param.name)), (param,)
        if element is not None and element.data == "name_t":
            return ast_builder._parse_type(node), (_target_param(element),)
    return ast_builder._parse_type(node), ()


def _bounded_generic_target(node: Tree, ast_builder: 'ASTBuilder'
                            ) -> Tuple[Optional[Type], TargetParams]:
    """A `@(...)` target: a bounded name is an `UnknownType`, any other argument a type."""
    def read_arg(child: Tree) -> Tuple[Optional[Type], Optional[BoundedTypeParam]]:
        if child.data == "bounded_type_arg":
            param = _target_param(child)
            return UnknownType(name=param.name), param
        return (ast_builder._parse_type(child),
                _target_param(child) if child.data == "name_t" else None)

    return _read_generic(node, read_arg)


def _target_param(node: Tree) -> BoundedTypeParam:
    """A bare name or a bounded name at the top level of a target, with its span."""
    name = first_name(node.children)
    if name is None:
        ice(node, "a target argument without a name")
    constraints, namespaces, spans = _parse_perk_constraints(
        first_tree(node.children, "perk_constraints"))
    return BoundedTypeParam(name=str(name), constraints=constraints, loc=span_of(node),
                            constraint_namespaces=namespaces, constraint_spans=spans)


def reject_misplaced_bound(node: Tree) -> NoReturn:
    """CE6110: a bound in a type position that is not the top level of an `extend` target.

    Fatal, as CE6105 is, so that no later pass sees a half-built type.
    """
    param = _target_param(node)
    perks = " + ".join(param.written_constraints())
    raise SyntaxDiagnostic("CE6110", span=span_of(node), param=param.name, perks=perks) \
        .help(f"write the bound in the type-parameter list of the declaration: "
              f"'@({param.name}: {perks})'")


def parse_generic_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """Parse a generic type instantiation, bare or behind an alias (`_read_generic`)."""
    return _read_generic(node, _plain_reader(ast_builder))[0]


def parse_bounded_type_params(type_params_node: Optional[Tree]) -> Optional[List[BoundedTypeParam]]:
    """Parse type_params node and extract bounded type parameters with constraints."""
    if type_params_node is None:
        return None

    param_list_node = first_tree(type_params_node.children, "type_param_list")
    if param_list_node is None:
        return None

    bounded_params: List[BoundedTypeParam] = []

    # `type_param_list: type_param ("," type_param)*` and `type_param` carries no `?`,
    # so every child is a `type_param` Tree. A bare NAME Token never arrives here.
    for child in param_list_node.children:
        if isinstance(child, Tree) and child.data == "type_param":
            # A type pack (`...Ts`) is prefixed with an ELLIPSIS token; the NAME is
            # then `children[1]`. A regular param has no prefix (NAME is first).
            is_pack = any(
                isinstance(c, Token) and c.type == "ELLIPSIS"
                for c in child.children
            )

            param_name = first_name(child.children)
            if param_name is None:
                continue

            perk_constraints_node = first_tree(child.children, "perk_constraints")
            constraints, namespaces, spans = _parse_perk_constraints(
                perk_constraints_node)

            bounded_params.append(BoundedTypeParam(
                name=str(param_name),
                constraints=constraints,
                loc=span_of(child),
                is_pack=is_pack,
                constraint_namespaces=namespaces,
                constraint_spans=spans,
            ))

    return bounded_params if bounded_params else None


def _parse_perk_constraints(
    perk_constraints_node: Optional[Tree],
) -> tuple[List[str], List[Optional[str]], List[Optional[Span]]]:
    """The perk names one type parameter is constrained by, their qualifiers and spans.

    Index-aligned lists rather than one list of triples: `constraints` is the table key
    every existing reader wants, only the rule that checks a qualifier reaches for the
    second (`docs/design/unit-namespaces.md` section 5), and only a diagnostic reaches
    for the third. A span covers the `perk_constraint` node whole, so a qualified name
    is marked with its qualifier (#706).
    """
    constraints: List[str] = []
    namespaces: List[Optional[str]] = []
    spans: List[Optional[Span]] = []
    if perk_constraints_node is None:
        return constraints, namespaces, spans

    constraint_list_node = first_tree(perk_constraints_node.children,
                                      "perk_constraint_list")
    if constraint_list_node is None:
        return constraints, namespaces, spans

    for constraint_child in constraint_list_node.children:
        if not isinstance(constraint_child, Tree) or constraint_child.data != "perk_constraint":
            continue
        names = name_tokens(constraint_child.children)
        if not names:
            continue
        constraints.append(str(names[-1]))
        namespaces.append(str(names[0]) if len(names) > 1 else None)
        spans.append(span_of(constraint_child))

    return constraints, namespaces, spans
