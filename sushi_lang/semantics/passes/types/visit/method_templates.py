"""The extension method or the static that a call names in the typecheck pass over SHIR
(docs/design/ir.md 7.3, 8.2).

A copy of a template is an instance: one body. The extension rungs cut the copy of a
generic-target template and of an `extend T[]` template at the call. A method-level type
parameter is solved here from the arguments of the call, through the one leading solver, and
the receiver gives the other type parameters. The pass keeps the answer of each argument that
the solver checked. The signature of the copy holds the solved method arguments, and the callee
row reads them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from sushi_lang.semantics.generics.types import substitute_type_params
from sushi_lang.semantics.passes.types.arguments import NOTHING_TYPED
from sushi_lang.semantics.passes.types.calls.methods import (
    RESOLUTION_REPORTED, find_method_generic_template, method_generic_copy,
    reject_unsolved_margs, resolve_extension_method, unsolved_margs_help)
from sushi_lang.semantics.passes.types.visit.constructions import TypedArguments, solve_arguments
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


class UserMethod(NamedTuple):
    """The answer of the rungs: the signature, None when no rung answers, or
    `RESOLUTION_REPORTED` after an error. `typed` holds what the solver checked."""
    method: Any
    typed: TypedArguments = NOTHING_TYPED


def resolve_user_method(validator: TypeValidator, node: e.Call, owner: Type, name: str, *,
                        static: bool) -> UserMethod:
    """The extension method or the static `name` of `owner` that the calling unit calls."""
    method = resolve_extension_method(validator, owner, name, report=True, static=static,
                                      site=node.loc)
    if method is not None:
        return UserMethod(method)
    return _method_generic(validator, node, owner, name, static)


def _method_generic(validator: TypeValidator, node: e.Call, owner: Type, name: str,
                    static: bool) -> UserMethod:
    """The copy of a method-generic template at the type arguments that the call solves.
    A parameter that no argument solves is CE2063."""
    template, receiver_subst = find_method_generic_template(validator, owner, name, node.loc, True)
    if template is None or template is RESOLUTION_REPORTED:
        return UserMethod(template)
    if bool(template.is_static) != static:
        return UserMethod(None)
    names = list(template.method_type_param_names)
    bare = [_is_bare_lambda(validator, arg.value) for arg in node.args]
    params = [None if index < len(bare) and bare[index]
              else substitute_type_params(param.ty, receiver_subst)
              for index, param in enumerate(template.params)]
    solution = solve_arguments(validator, names, params, node.args)
    unsolved = [each for each in names if each not in solution.solved]
    if unsolved:
        reject_unsolved_margs(validator, name, unsolved, node.loc,
                              unsolved_margs_help(any(bare), len(unsolved)))
        return UserMethod(RESOLUTION_REPORTED, solution.typed)
    method = method_generic_copy(validator, template, owner, receiver_subst, solution.solved,
                                 node.loc, True)
    return UserMethod(method, solution.typed)


def _is_bare_lambda(validator: TypeValidator, value: e.ShirExpr) -> bool:
    """A lambda with a parameter of no written type has no type of its own to solve from."""
    return isinstance(value, e.Lambda) and any(
        validator.body_locals[param.local].declared is None for param in value.params)
