"""The arms of the `iterator` family in the typecheck pass over SHIR (docs/design/ir.md 7.6).

`it.clone()` yields the type of its receiver. An adapter has a signature row: an argument with
a type parameter in its position is typed first, and the solver reads those types
(`settle_builtin_instance`). A bare lambda solves nothing. Then the one argument check compares
each argument with the parameter of the instance, and `adapter_result` gives the answer.

`Iterator.new(nom x)` is a `BuiltinStatic`: a bare `Iterator` needs its import (CE2001), a type
with no `next()` that answers `Maybe@(T)` is CE2033, and the call has the row of that `next()`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.iterators import ITERATOR_BASE, ITERATOR_METHOD_SIGNATURES
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.types import substitute_type_params
from sushi_lang.semantics.method_effects import ITERATOR_FAMILY
from sushi_lang.semantics.param_modes import ParamMode, param_mode, receiver_mode
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import (
    Parameters, check_arguments, marked_type, record_conventions, reject_misplaced_spread)
from sushi_lang.semantics.passes.types.calls.builtin_signature import settle_builtin_instance
from sushi_lang.semantics.passes.types.iterator_adapters import adapter_result, zip_instance
from sushi_lang.semantics.passes.types.method_registry import (
    METHOD_TYPE_REGISTRY, MethodFamily)
from sushi_lang.semantics.passes.types.statements import protocol_item_type
from sushi_lang.semantics.passes.types.visibility import reject_out_of_scope_type
from sushi_lang.semantics.passes.types.visit.container_statics import type_name_span
from sushi_lang.semantics.passes.types.visit.constructions import (
    TypedArguments, is_bare_lambda, names_a_parameter, type_lambda, typed_alone, walk_unchecked)
from sushi_lang.semantics.passes.types.visit.method_templates import resolve_user_method
from sushi_lang.semantics.passes.types.visit.signature_methods import unsolved_help
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import BuiltinStatic, Method
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.statics import is_builtin_static
from sushi_lang.semantics.typesys import IteratorType, Type, UnknownType, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.builtin_signatures import BuiltinSignature
    from sushi_lang.semantics.passes.types import TypeValidator


def check_iterator_method(validator: TypeValidator, node: e.Call, receiver: Type, name: str,
                          family: MethodFamily) -> Optional[Type]:
    """`it.clone()` or an adapter: the type of the receiver, or the answer of the adapter."""
    callee_name = f"{display_type(receiver)}.{name}"
    if reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args), callee_name,
                               family.arity):
        return receiver if name == "clone" else None
    if name == "clone":
        return receiver
    signature = ITERATOR_METHOD_SIGNATURES[name]
    typed = _typed_arguments(validator, node, signature, receiver)
    written = [typed.get(index) for index in range(len(node.args))]
    instance = settle_builtin_instance(validator, signature, receiver, written, node.loc, name,
                                       unsolved_help)
    if instance is None:
        walk_unchecked(validator, node.args, typed)
        return None
    if name == "zip":
        instance = zip_instance(validator, instance, written[0])
    check_arguments(validator, callee_name, [param.ty for param in instance.params], node.args,
                    node.loc, mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                    declared=Parameters(node.shir_id,
                                        tuple(param_mode(param) for param in instance.params),
                                        [param.name for param in instance.params]),
                    typed=typed)
    return adapter_result(validator, node, name, receiver, instance, report=True)


def _typed_arguments(validator: TypeValidator, node: e.Call, signature: BuiltinSignature,
                     receiver: Type) -> TypedArguments:
    """The answer of each argument whose position names a type parameter of the adapter. A
    bare lambda has none."""
    item = receiver.element_type if isinstance(receiver, IteratorType) else None
    names = signature.type_params
    typed: dict[int, Optional[Type]] = {}
    for index, (arg, param) in enumerate(zip(node.args, signature.params, strict=True)):
        if reject_misplaced_spread(validator, arg):
            continue
        position = substitute_type_params(param.ty, {"T": item})
        if isinstance(arg.value, e.Lambda):
            type_lambda(validator, typed, index, arg, position, names)
        elif names_a_parameter(position, names) and not is_bare_lambda(validator, arg.value):
            typed[index] = typed_alone(validator, arg)
    return typed


def names_iterator_new(validator: TypeValidator, target: e.Static) -> bool:
    """Whether `target` is `Iterator.new`, bare or behind an alias that holds `Iterator`."""
    ty = target.ty
    if not (isinstance(ty, UnknownType) and ty.name == ITERATOR_BASE
            and is_builtin_static(ty.name, target.name)):
        return False
    if ty.namespace is None:
        return True
    binding = validator.namespaces.lookup(ty.namespace, ty.name)
    return binding is not None and binding.name == ty.name


def check_iterator_new(validator: TypeValidator, node: e.Call,
                       target: e.Static) -> Optional[Type]:
    """`Iterator.new(nom x)`: the rows of the static and of the `next()` of `x`, and an
    iterator with owned items."""
    family = METHOD_TYPE_REGISTRY.family(ITERATOR_FAMILY)
    bare = getattr(target.ty, "namespace", None) is None
    if bare and reject_out_of_scope_type(validator, ITERATOR_BASE, type_name_span(node.loc, ITERATOR_BASE)):
        for arg in node.args:
            visit.check_expr(validator, arg.value)
        return None
    if reject_builtin_miscount(validator.reporter, node.loc, target.name, len(node.args),
                               f"{ITERATOR_BASE}.{target.name}", family.arity):
        return None
    arg = node.args[0]
    if reject_misplaced_spread(validator, arg):
        return None
    written = marked_type(arg, visit.check_expr(validator, arg.value))
    if written is None:
        return None
    value = deref_type(written)
    item = protocol_item_type(validator, value)
    if item is None:
        er.emit(validator.reporter, er.ERR.CE2033, arg.value.loc, got=display_type(value))
        return None
    answer = IteratorType(element_type=item)
    answers = validator.body_answers()
    answers.record_callee(node.shir_id, ResolvedCall(BuiltinStatic(answer,
                                                                   family.callee(target.name))))
    record_conventions(validator, Parameters(node.shir_id, (ParamMode.NOM,), ["x"]), [arg])
    _record_next(validator, node, arg.value, value)
    return answer


def _record_next(validator: TypeValidator, node: e.Call, value: e.ShirExpr, owner: Type) -> None:
    """The row of the `next()` that the state of the iterator calls."""
    perk = validator.perk_impl_table.get_method(owner, "next")
    method = perk if perk is not None else resolve_user_method(
        validator, node, owner, "next", static=False).method
    callee = Method(value, validator.method_decl(owner, "next"),
                    receiver_mode(method.self_mode).convention)
    row = ResolvedCall(callee) if perk is not None else ResolvedCall(
        callee, method.method_type_args, None if method.link_symbol else method.unit_name)
    validator.body_answers().record_iterator_next(node.shir_id, row)
