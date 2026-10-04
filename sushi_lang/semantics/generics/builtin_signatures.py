"""The signature of a built-in method that has a method-level type parameter.

docs/design/error-conversion.md section 8.3. A built-in method answers its return type
from its receiver alone, read off its family's table. `or_err@(E)` on `Maybe@(T)` is the
first that has a type parameter of its own, so its return type comes from its ARGUMENT.
Such a method states its signature here, as an extension would write it: the type
parameters of the receiver, the method-level ones, each parameter with its mode, the
return, and the receiver mode.

`solve_builtin_signature` is the one solver. It solves the method-level type parameters
from the argument types through `solve_leading_type_args`, as for a method-generic
extension, and answers the substituted signature. The typecheck pass reads the instance
for the return type, the parameter types and the modes that it stamps for the `borrow`
pass and the backend. The instantiate pass reads the return type.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from sushi_lang.semantics.generics.types import GenericTypeRef, substitute_type_params
from sushi_lang.semantics.typesys import EnumType, StructType, Type


@dataclass(frozen=True)
class BuiltinParam:
    """One parameter of a built-in signature. The mode is read by `param_modes.param_mode`."""
    name: str
    ty: Type
    is_nom: bool = False


@dataclass(frozen=True)
class BuiltinSignature:
    """A built-in method, written as a generic extension of its receiver would be."""
    receiver_params: tuple[str, ...]
    type_params: tuple[str, ...]
    params: tuple[BuiltinParam, ...]
    ret_type: Type
    self_mode: Optional[str] = None
    # A `nom self` receiver that is a borrow is read through, and the answer carries the
    # borrow, as `??` reads through a borrowed wrapper (design 4, borrow-model.md S10d).
    reads_borrow_through: bool = False

    @property
    def error_parameters(self) -> tuple[str, ...]:
        """The method-level type parameters in an `E` position, judged by E3 at each call."""
        from sushi_lang.semantics.error_types import error_parameters
        written = [(self.ret_type, None)] + [(p.ty, None) for p in self.params]
        return tuple(error_parameters(written, self.type_params))


@dataclass(frozen=True)
class BuiltinInstance:
    """A built-in signature with every type parameter substituted. Nothing is interned."""
    signature: BuiltinSignature
    substitution: Mapping[str, Type]
    params: tuple[BuiltinParam, ...]
    ret_type: Type


def builtin_signature_of(receiver_type: Any, method_name: str) -> Optional[BuiltinSignature]:
    """The signature of the built-in method `method_name` on this receiver, or None."""
    from sushi_lang.semantics.type_predicates import generic_base_of
    if not isinstance(receiver_type, EnumType):
        return None
    return builtin_signature_for_base(generic_base_of(receiver_type), method_name)


def builtin_signature_for_base(base: Optional[str],
                               method_name: str) -> Optional[BuiltinSignature]:
    """The signature of the built-in method `method_name` of the generic `base`, or None.

    `or_err` on `Maybe@(T)` and `map_err` on `Result@(T, E)` (design 8.3).
    """
    if base == "Maybe":
        from sushi_lang.semantics.generics.maybe import MAYBE_METHOD_SIGNATURES
        return MAYBE_METHOD_SIGNATURES.get(method_name)
    if base == "Result":
        from sushi_lang.semantics.generics.results import RESULT_METHOD_SIGNATURES
        return RESULT_METHOD_SIGNATURES.get(method_name)
    return None


def read_through_receiver_of(expr: Any) -> Optional[Any]:
    """The receiver of a built-in call that reads a borrowed receiver through, or None.

    `m.or_err(nom e)` is such a call (`reads_borrow_through`). The question is about the
    call's shape and its stamped receiver type, not about who owns the receiver.
    """
    from sushi_lang.semantics.ast import DotCall, MethodCall
    if not isinstance(expr, (MethodCall, DotCall)):
        return None
    signature = builtin_signature_of(expr.resolved_enum_type, expr.method)
    if signature is None or not signature.reads_borrow_through:
        return None
    return expr.receiver


def receiver_type_arguments(receiver_type: Any) -> Optional[tuple[Type, ...]]:
    """The type arguments of an interned instance or of a written generic reference."""
    if isinstance(receiver_type, (EnumType, StructType)):
        return tuple(receiver_type.generic_args) if receiver_type.generic_args else None
    if isinstance(receiver_type, GenericTypeRef):
        return tuple(receiver_type.type_args)
    return None


def solve_builtin_signature(
    signature: BuiltinSignature,
    receiver_type: Any,
    arg_types: Sequence[Optional[Type]],
    structs: Any,
    enums: Any,
) -> tuple[Optional[BuiltinInstance], tuple[str, ...]]:
    """Solve the method-level type parameters of one call: `(instance, unsolved names)`.

    The receiver binds its own type parameters, and the arguments bind the method-level
    ones through the one solver. An argument type that is None solves nothing. The
    instance is None when a name stays unsolved or the receiver has no type arguments.
    """
    from sushi_lang.semantics.generics.pack_inference import solve_leading_type_args
    from sushi_lang.semantics.type_resolution import (
        resolve_type_recursively, resolve_unknown_type)

    receiver_args = receiver_type_arguments(receiver_type)
    if receiver_args is None or len(receiver_args) != len(signature.receiver_params):
        return None, signature.type_params
    tables = (structs or {}, enums or {})
    receiver_subst = {name: resolve_type_recursively(arg, *tables)
                      for name, arg in zip(signature.receiver_params, receiver_args,
                                           strict=True)}
    expected = [substitute_type_params(p.ty, receiver_subst) for p in signature.params]
    solved, unsolved = solve_leading_type_args(
        signature, arg_types, structs, enums, partial=True, param_types=expected,
        type_param_names=signature.type_params)
    if unsolved:
        return None, unsolved

    type_args = tuple(resolve_unknown_type(solved[name], structs, enums)
                      for name in signature.type_params)
    substitution = dict(receiver_subst)
    substitution.update(zip(signature.type_params, type_args, strict=True))
    params = tuple(BuiltinParam(p.name, substitute_type_params(p.ty, substitution), p.is_nom)
                   for p in signature.params)
    return BuiltinInstance(signature=signature, substitution=substitution, params=params,
                           ret_type=substitute_type_params(signature.ret_type,
                                                           substitution)), ()


def refuses_an_error_argument(instance: BuiltinInstance, structs: Any, enums: Any) -> bool:
    """Whether a type argument in an `E` position of the instance is not an error type (E3)."""
    from sushi_lang.semantics.error_types import non_error_type
    return any(non_error_type(instance.substitution[name], structs or {}, enums or {})
               is not None
               for name in instance.signature.error_parameters)
