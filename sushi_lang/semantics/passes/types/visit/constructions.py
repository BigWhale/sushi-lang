"""The instance of a construction of a generic type in the typecheck pass over SHIR (ir.md 7.3).

The position gives the instance when it holds an instance of the type. Else the arguments
solve the type parameters through the one leading solver (`solve_leading_type_args`). The
pass checks each argument one time, so `solve_arguments` keeps each answer, and the one
argument check compares it with the type of the parameter (`check_arguments`, `typed`).

An argument that takes its type from its position (`stands_alone`) waits: when the other
arguments solve the type of its parameter, the argument check types it at that type. The
instance is looked up or interned through `intern_constructed_instance`, never rebuilt.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, NamedTuple, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.pack_inference import solve_leading_type_args
from sushi_lang.semantics.generics.types import substitute_type_params
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import (
    NOTHING_TYPED, marked_type, reject_misplaced_spread)
from sushi_lang.semantics.passes.types.calls.enums import intern_constructed_instance
from sushi_lang.semantics.passes.types.calls.user_defined import own_concrete_function
from sushi_lang.semantics.passes.types.calls.statics import (
    _interned_static_target, reject_unreached_type_params, static_template_for)
from sushi_lang.semantics.passes.types.calls.structs import (
    refuse_unsolved_struct, reject_field_names)
from sushi_lang.semantics.passes.types.utils import names_no_type, reject_unpromised_arguments
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.statics import solve_target_type_args
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, Type, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

#: The answer of each argument that the solver checked, by its index.
TypedArguments = Mapping[int, Optional[Type]]


class ArgumentSolution(NamedTuple):
    """What the arguments of a construction give. `solved` holds a type for each type
    parameter that an argument reaches; `misses` the index of each argument that does not
    unify with what the arguments before it gave, and `why` the reason of each one."""
    typed: dict[int, Optional[Type]]
    solved: dict[str, Type]
    misses: list[int]
    why: list[str]


class Constructed(NamedTuple):
    """The instance of a construction, and the arguments that the pass has checked. The
    instance is None after a refusal."""
    instance: Optional[Type]
    typed: TypedArguments = NOTHING_TYPED


def names_a_parameter(ty: Optional[Type], names: Sequence[str]) -> bool:
    """Does the declared type `ty` name one of the type parameters `names`?"""
    return ty is not None and substitute_type_params(
        ty, dict.fromkeys(names, BuiltinType.BLANK)) != ty


def is_generic_value(validator: TypeValidator, value: e.ShirExpr) -> bool:
    """Is `value` a generic function written as a value? It has a type only at a position
    that states a function type, so a solver leaves it to the argument check."""
    if not isinstance(value, e.GlobalRef):
        return False
    alias, dot, name = validator.decl_name(value.decl).rpartition(".")
    if dot:
        binding = validator.namespaces.lookup(alias, name)
        return binding is not None and binding.kind == "generic function"
    return (own_concrete_function(validator.func_table, name, validator.current_unit_name) is None
            and validator.generic_sig(name) is not None)


def stands_alone(validator: TypeValidator, value: e.ShirExpr) -> bool:
    """Does the value have a type with no type from its position? `Maybe.Some(1)` does, and
    `Maybe.None`, `Result.Ok(1)`, `from([])`, `List.new()` and a generic function do not."""
    if is_generic_value(validator, value) or isinstance(value, e.Lambda):
        return False
    if isinstance(value, e.EnumInit):
        return _variant_stands_alone(validator, value)
    if isinstance(value, e.StructInit):
        _alias, _dot, name = validator.decl_name(value.decl).rpartition(".")
        return (name not in validator.generic_struct_table.by_name
                or all(stands_alone(validator, arg.value) for arg in value.fields))
    if isinstance(value, e.ArrayInit):
        return bool(value.runs)
    if isinstance(value, e.Call) and isinstance(value.target, e.Static):
        return bool(value.args)
    return True


def _variant_stands_alone(validator: TypeValidator, node: e.EnumInit) -> bool:
    _alias, _dot, name = validator.decl_name(node.decl).rpartition(".")
    template = validator.generic_enum_table.by_name.get(name)
    if template is None:
        return True
    variant = next((v for v in template.variants if v.name == node.variant), None)
    if variant is None or len(variant.associated_types) != len(node.payload):
        return False
    return (all(any(names_a_parameter(ty, [tp.name]) for ty in variant.associated_types)
                for tp in template.type_params)
            and all(stands_alone(validator, arg.value) for arg in node.payload))


def solve_arguments(validator: TypeValidator, names: Sequence[str],
                    params: Sequence[Optional[Type]], args: Sequence[e.Arg],
                    order: Optional[Sequence[int]] = None,
                    given: Optional[Mapping[str, Type]] = None) -> ArgumentSolution:
    """Check the arguments that can solve a type parameter, and unify them with `params`.

    `order` is the written order of the arguments, and `given` holds what a second source
    gives (the position of a static). A bloom argument and an argument whose parameter
    names no type parameter are left to the argument check, and so is one that waits for a
    type that the others solve. An argument that waits for a type that nothing solves is checked at no type.
    """
    indexes = [index for index in (range(len(args)) if order is None else order)
               if index < len(params) and not args[index].bloom
               and names_a_parameter(params[index], names)]
    typed: dict[int, Optional[Type]] = {}
    waiting = []
    for index in indexes:
        if stands_alone(validator, args[index].value):
            typed[index] = typed_alone(validator, args[index])
        else:
            waiting.append(index)
    for index in waiting:
        known = {**(given or {}), **unify_arguments(validator, names, params, indexes, typed).solved}
        position = substitute_type_params(params[index], known)
        if not names_a_parameter(position, names):
            continue
        if isinstance(args[index].value, e.Lambda):
            type_lambda(validator, typed, index, args[index], position, names)
        else:
            typed[index] = typed_alone(validator, args[index])
    return unify_arguments(validator, names, params, indexes, typed)


def is_bare_lambda(validator: TypeValidator, value: e.ShirExpr) -> bool:
    """A lambda with a parameter of no written type has no type of its own to solve from."""
    return isinstance(value, e.Lambda) and any(
        validator.body_locals[param.local].declared is None for param in value.params)


def type_lambda(validator: TypeValidator, typed: dict[int, Optional[Type]], index: int,
                arg: e.Arg, position: Optional[Type], names: Sequence[str]) -> None:
    """The answer of a lambda argument while its function type `position` names a type
    parameter that the other arguments do not solve. A bare lambda solves nothing. A lambda
    whose function type is known is left to the argument check."""
    if names_a_parameter(position, names) and not is_bare_lambda(validator, arg.value):
        typed[index] = marked_type(arg, visit.check_expr(
            validator, arg.value, _known_part(position, names)))


def _known_part(position: Optional[Type], names: Sequence[str]) -> Optional[Type]:
    """What a function type says to a lambda before its call is solved: each part that names
    no unsolved type parameter. A lambda takes its channel from it."""
    if not isinstance(position, FunctionType):
        return None

    def known(part: Optional[Type]) -> Optional[Type]:
        return None if part is None or names_a_parameter(part, names) else part

    return FunctionType(param_types=tuple(known(part) for part in position.param_types),
                        ok_type=known(position.ok_type), err_type=known(position.err_type),
                        param_modes=position.param_modes)


def typed_alone(validator: TypeValidator, arg: e.Arg) -> Optional[Type]:
    """The answer of an argument that no type of a position types."""
    return marked_type(arg, visit.check_expr(validator, arg.value))


def unify_arguments(validator: TypeValidator, names: Sequence[str],
                    params: Sequence[Optional[Type]], indexes: Sequence[int],
                    typed: dict[int, Optional[Type]]) -> ArgumentSolution:
    """What the answers `typed` at `indexes` give through the one leading solver."""
    why: list[str] = []
    misses: list[int] = []
    solved, _unsolved = solve_leading_type_args(
        None, [typed.get(index) for index in indexes], validator.struct_table.by_name,
        validator.enum_table.by_name, partial=True,
        param_types=[params[index] for index in indexes], type_param_names=names,
        why=why, misses=misses)
    return ArgumentSolution(typed, solved, [indexes[miss] for miss in misses], why)


def solved_type_args(validator: TypeValidator, names: Sequence[str],
                     solved: Mapping[str, Type]) -> tuple[Optional[Type], ...]:
    """One entry for each type parameter, resolved, and None where nothing gives it."""
    structs, enums = validator.struct_table.by_name, validator.enum_table.by_name
    return tuple(resolve_unknown_type(solved[name], structs, enums) if name in solved else None
                 for name in names)


def interned_instance(validator: TypeValidator, loc: Span, name: str,
                      type_args: tuple[Optional[Type], ...]) -> Type:
    """The instance that the solved type arguments name. The typecheck pass always has the
    interner, so an instance that does not come back is an internal error."""
    instance = intern_constructed_instance(validator, name, type_args)
    if instance is None:
        er.raise_internal_error("CE0000", span=loc,
                                detail=f"the interner gave no instance of '{name}'")
    return instance


def walk_unchecked(validator: TypeValidator, args: Sequence[e.Arg], typed: TypedArguments,
                   order: Optional[Sequence[int]] = None) -> None:
    """Check at no type each argument that the solver left, after a refusal, so that an
    error in the argument is still reported."""
    for index in range(len(args)) if order is None else order:
        if index not in typed and not reject_misplaced_spread(validator, args[index]):
            visit.check_expr(validator, args[index].value)


def generic_struct_instance(validator: TypeValidator, node: e.StructInit, name: str,
                            written: str, expected: Optional[Type]) -> Constructed:
    """The instance that a construction of the generic struct `name` builds (#1150). A
    construction that nothing solves is one error, CE2112, or CE2065 when two arguments
    disagree (`refuse_unsolved_struct`)."""
    if is_instance_of(expected, name):
        return Constructed(expected)
    if any([reject_misplaced_spread(validator, arg) for arg in node.fields]):
        return Constructed(None)
    template = validator.generic_struct_table.by_name[name]
    offered = validator.errors_offered()
    params = _filled_fields(template, node)
    if params is None:
        walk_unchecked(validator, node.fields, {}, node.written_order)
        reject_field_names(validator, template, node.names or (), node.decl_loc)
        return Constructed(None)
    names = [tp.name for tp in template.type_params]
    solution = solve_arguments(validator, names, params, node.fields, node.written_order)
    type_args = solved_type_args(validator, names, solution.solved)
    if solution.misses or any(arg is None for arg in type_args):
        walk_unchecked(validator, node.fields, solution.typed, node.written_order)
        refuse_unsolved_struct(validator, node, template, written, params, solution, type_args,
                               had_error=validator.errors_offered() != offered)
        return Constructed(None)
    instance = interned_instance(validator, node.loc, name, type_args)
    reject_unpromised_arguments(validator, instance, node.loc)
    return Constructed(instance, solution.typed)


def _filled_fields(template, node: e.StructInit) -> Optional[list[Type]]:
    """The declared type of the field that each argument fills, or None when a written name
    is no field. A repeated or a missing name still solves: the instance reports it."""
    if node.names is None:
        return [ty for _field, ty in template.fields]
    fields = dict(template.fields)
    if any(name not in fields for name in node.names):
        return None
    return [fields[name] for name in node.names]


def generic_static_owner(validator: TypeValidator, node: e.Call, target: e.Static, base: str,
                         expected: Optional[Type]) -> Optional[Constructed]:
    """The instance of the generic type `base` whose static a call names: the arguments
    first, then the position (#573). A static that nothing solves is CE2060, unless the
    declared type of the position was refused (CE2001), which is the one fault. The answer
    is None when `base` declares no static of the name."""
    position = deref_type(expected)
    stamped = position if is_instance_of(position, base) else None
    template = static_template_for(validator, base, target.name)
    if template is None:
        if stamped is not None:
            return Constructed(stamped)
        declared = validator.generic_extension_table.declarations(base, target.name)
        if not any(getattr(each, "is_static", False) for each in declared):
            return None
        reject_unreached_type_params(validator, node.loc, base, target.name,
                                     _type_params_of(validator, base), declared)
        return Constructed(None)
    names = [getattr(p, "name", str(p)) for p in template.type_params]
    stamped_args = None if stamped is None else stamped.generic_args
    given = dict(zip(names, stamped_args or (), strict=False))
    solution = solve_arguments(validator, names, [p.ty for p in template.params], node.args,
                               given=given)
    arg_types = [solution.typed.get(index) for index in range(len(node.args))]
    type_args, unsolved = solve_target_type_args(template, arg_types, stamped_args)
    if type_args is None:
        walk_unchecked(validator, node.args, solution.typed)
        if position is None or not names_no_type(validator, position):
            reject_unreached_type_params(
                validator, node.loc, base, target.name, unsolved,
                validator.generic_extension_table.declarations(base, target.name))
        return Constructed(None)
    resolved = tuple(solved_type_args(validator, names, dict(zip(names, type_args, strict=True))))
    return Constructed(_interned_static_target(validator, target.name, node.loc, template, base,
                                               resolved), solution.typed)


def _type_params_of(validator: TypeValidator, base: str) -> tuple[str, ...]:
    generic = (validator.generic_struct_table.by_name.get(base)
               or validator.generic_enum_table.by_name.get(base))
    return tuple(getattr(p, "name", str(p)) for p in getattr(generic, "type_params", ())) or ("T",)
