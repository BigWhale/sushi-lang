"""The arm of a static of a built-in container: `List.new`, `List.with_capacity`,
`HashMap.new` and `Own.alloc` (docs/design/ir.md 7.6, 8.3).

The static reads its type arguments from its position: the instance that the position
gives is its answer, and a position that gives no instance of the container is CE2060,
except a position whose type CE2001 refused, which is the one fault there. A
bare `HashMap` needs its import (CE2001, #506); behind an alias the namespace holds it. A
wrong count is CE2009, and the arguments are then not checked. The count of
`with_capacity` is an i32 (CE2006), and the value of `Own.alloc` is the payload of the
instance. The convention of each argument is the answer of `argument_conventions`:
`Own.alloc` takes its value by position (`Owned`), with no marker to compare, and
`with_capacity` borrows its count.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.own import own_payload_type
from sushi_lang.semantics.generics.types import template_type_param_names
from sushi_lang.semantics.method_effects import argument_conventions
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import (
    argument_span, check_argument, marked_type, record_builtin_conventions)
from sushi_lang.semantics.passes.types.arrays import check_index_operand, reject_non_index
from sushi_lang.semantics.passes.types.calls.statics import emit_no_source, spell_names
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
from sushi_lang.semantics.passes.types.utils import names_no_type
from sushi_lang.semantics.passes.types.visibility import reject_out_of_scope_type
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import BuiltinStatic
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.statics import CONTAINER_STATIC_FAMILIES, is_builtin_static
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import Type, UnknownType, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def container_static_base(validator: TypeValidator, target: e.Static) -> Optional[str]:
    """The container that `target` names when it calls one of its statics (`List`,
    `HashMap`, `Own`), bare or behind an alias that holds it; else None."""
    ty = target.ty
    if not isinstance(ty, UnknownType) or ty.name not in CONTAINER_STATIC_FAMILIES:
        return None
    if not is_builtin_static(ty.name, target.name):
        return None
    if ty.namespace is not None:
        binding = validator.namespaces.lookup(ty.namespace, ty.name)
        if binding is None or binding.name != ty.name:
            return None
    return ty.name


def check_container_static(validator: TypeValidator, node: e.Call, target: e.Static,
                           base: str, expected: Optional[Type]) -> Optional[Type]:
    """`List.new()`, `List.with_capacity(n)`, `HashMap.new()`, `Own.alloc(v)`: the instance
    of `base` that the position gives, or None after an error."""
    name = f"{base}.{target.name}"
    family = METHOD_TYPE_REGISTRY.family(CONTAINER_STATIC_FAMILIES[base])
    bare = getattr(target.ty, "namespace", None) is None
    if bare and reject_out_of_scope_type(validator, base, type_name_span(node.loc, base)):
        return _walk_arguments(validator, node)
    if reject_builtin_miscount(validator.reporter, node.loc, target.name, len(node.args),
                               name, family.arity):
        return None
    instance = deref_type(expected)
    if not is_instance_of(instance, base):
        if instance is None or not names_no_type(validator, instance):
            _reject_no_position(validator, node, base, name)
        return _walk_arguments(validator, node)
    callee = BuiltinStatic(instance, family.callee(target.name))
    validator.body_answers().record_callee(node.shir_id, ResolvedCall(callee))
    if _check_values(validator, node, name, instance):
        _record_conventions(validator, node, callee)
    return instance


def _check_values(validator: TypeValidator, node: e.Call, name: str, instance: Type) -> bool:
    """Check the arguments at the types of the static. True when each one fits."""
    if not node.args:
        return True
    if is_instance_of(instance, "Own"):
        return check_argument(validator, name, node.args[0], 1, own_payload_type(instance),
                              mismatch_code=er.ERR.CE2006)
    count = node.args[0]
    got = marked_type(count, check_index_operand(validator, count.value))
    return got is not None and not reject_non_index(validator, count.value, got, argument=1,
                                                    span=argument_span(count))


def _record_conventions(validator: TypeValidator, node: e.Call, callee: BuiltinStatic) -> None:
    """The convention of each argument (`record_builtin_conventions`). A borrowed argument
    has no `nom` marker (CE2427), and the argument that the static takes is a slot: it
    takes it by position, as a container insert does, with no marker to compare."""
    record_builtin_conventions(validator, node.shir_id, node.args,
                               argument_conventions(callee.method.family,
                                                    callee.method.name, len(node.args)))


def _reject_no_position(validator: TypeValidator, node: e.Call, base: str, name: str) -> None:
    """CE2060: the position gives the static no instance of its container (#570). The help
    names the type parameters of the container."""
    params = template_type_param_names(validator.generic_struct_table.by_name.get(base),
                                       node.loc)
    emit_no_source(validator, node.loc, name,
                   "the built-in static reads its type from the binding alone, and "
                   "this position declares no type",
                   f"bind the result to a declared type ('let {base}@(...) x = {name}(...)'), "
                   f"or name {spell_names(params)} in a parameter so the argument "
                   f"solves it")


def type_name_span(call: Span, name: str) -> Span:
    """The span of the bare type name that a static call starts with (`HashMap.new()`)."""
    return Span(call.line, call.col, call.line, call.col + len(name))


def _walk_arguments(validator: TypeValidator, node: e.Call) -> None:
    """Check each argument at no type after an error of the static, so that an error in an
    argument is still reported."""
    for arg in node.args:
        visit.check_expr(validator, arg.value)
    return None
