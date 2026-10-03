"""Struct constructor validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, NamedTuple, Optional, Sequence, Tuple

from sushi_lang.internals import errors as er
from ..visibility import type_name_is_contested
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.semantics.ast import Call, Expr
from ..compatibility import types_compatible
from ..propagation import propagate_types_to_value
from ..utils import (
    intern_declared_wrapper, reject_spread_args, resolve_declared_type)

if TYPE_CHECKING:
    from .. import TypeValidator


class FieldSolution(NamedTuple):
    """What the arguments of a generic struct constructor give (#1150)."""
    type_args: tuple
    disagreement: Optional[str]
    has_untyped_argument: bool


def solve_constructor_fields(validator: 'TypeValidator', base: str, args: Sequence[Expr],
                             field_names: Optional[Sequence[str]]
                             ) -> Optional[FieldSolution]:
    """Solve a generic struct's type parameters from a constructor's arguments. Silent.

    The one leading solver solves it, as it solves a generic call: each argument against
    the field it fills. `type_args` holds one entry per type parameter, None where no
    argument gives it. `disagreement` names the first argument that does not unify with
    what the arguments before it gave. The answer is None when `base` is no generic
    struct, or when an argument name is not a field: validation reports that.
    """
    from sushi_lang.semantics.generics.pack_inference import (
        infer_call_arg_type, solve_leading_type_args)
    from sushi_lang.semantics.type_resolution import (
        resolve_type_recursively, resolve_unknown_type)

    template = validator.generic_struct_table.by_name.get(base)
    if template is None:
        return None
    fields = dict(template.fields)
    if field_names:
        if any(name not in fields for name in field_names):
            return None
        filled = list(field_names)
    else:
        filled = [name for name, _ in template.fields][:len(args)]
    field_types = [fields[name] for name in filled]
    args = list(args)[:len(field_types)]
    names = [tp.name for tp in template.type_params]
    structs, enums = validator.struct_table.by_name, validator.enum_table.by_name
    arg_types = [infer_call_arg_type(validator, arg) for arg in args]
    why: list[str] = []
    misses: list[int] = []
    solved, _unsolved = solve_leading_type_args(
        None, arg_types, structs, enums, partial=True,
        param_types=field_types, type_param_names=names, why=why, misses=misses)

    disagreement = None
    if misses:
        first = misses[0]
        disagreement = why[0] if why else _disagreement(
            filled[first], field_types[first],
            resolve_type_recursively(arg_types[first], structs, enums), solved)
    type_args = tuple(resolve_unknown_type(solved[name], structs, enums)
                      if name in solved else None for name in names)
    return FieldSolution(type_args, disagreement, any(t is None for t in arg_types))


def _disagreement(field: str, field_type: Type, arg_type: Type, solved: dict) -> str:
    """Why the argument for `field` does not fit what the arguments before it gave."""
    given = solved.get(str(field_type))
    if given is not None:
        return (f"the arguments give '{field_type}' two types, '{display_type(given)}' "
                f"and '{display_type(arg_type)}' (field '{field}')")
    return (f"field '{field}' is '{display_type(field_type)}', and the argument is "
            f"'{display_type(arg_type)}'")


def untyped_struct_instance(validator: 'TypeValidator', base: str, args: Sequence[Expr],
                            field_names: Optional[Sequence[str]]) -> Optional[Type]:
    """The instance a generic struct constructor with no expected type builds (#1150).

    The validating and the inferring half both read this one answer, and so do the
    flat spelling and the spelling behind an alias (#1151). None when the arguments do
    not solve every type parameter, or when two of them disagree.
    """
    from .enums import intern_constructed_instance
    solution = solve_constructor_fields(validator, base, args, field_names)
    if (solution is None or solution.disagreement is not None
            or any(t is None for t in solution.type_args)):
        return None
    return intern_constructed_instance(validator, base, solution.type_args)


def validate_generic_struct_constructor(validator: 'TypeValidator', call: Call,
                                        written: str) -> Optional[StructType]:
    """A generic struct constructor that no expected type stamped (#1150).

    The arguments give the instance, and the construction is then checked as the
    construction of that instance. `call.callee` is renamed to the instance, which is
    the name every later reader emits from. A constructor that cannot be solved is
    one error at the constructor, with the annotation in the help: CE2112 when no
    argument gives a type parameter, CE2065 when two arguments disagree. `written` is
    the constructor as the source writes it (`Box`, `sh.Box`).
    """
    base = call.callee.id
    instance = untyped_struct_instance(validator, base, call.args, call.field_names)
    if isinstance(instance, StructType):
        call.callee.id = instance.name
        validate_struct_constructor(validator, call)
        return instance
    _refuse_unsolved_struct_constructor(validator, call, base, written)
    return None


def _refuse_unsolved_struct_constructor(validator: 'TypeValidator', call: Call,
                                        base: str, written: str) -> None:
    """Report why a generic struct constructor has no instance, then walk its arguments."""
    template = validator.generic_struct_table.by_name[base]
    solution = solve_constructor_fields(validator, base, call.args, call.field_names)
    field_names = call.field_names
    call.field_names = None
    reported = sum(validator.reporter.errors_offered.values())
    for arg in call.args:
        validator.validate_expression(arg)
    # The count is asked before inference, as a generic call asks it (#790): a
    # miscount is the fault, and an unsolved type parameter only follows from it.
    expected = len(template.fields)
    if not field_names and len(call.args) != expected:
        er.emit(validator.reporter, er.ERR.CE2027, call.callee.loc,
                name=display_type(_template_shape(template, ())),
                expected=expected, got=len(call.args))
        return
    if solution is None:
        from ..field_matcher import validate_and_reorder_named_args
        validate_and_reorder_named_args(template, call.args, field_names,
                                        validator.reporter, call.callee.loc)
        return
    if (sum(validator.reporter.errors_offered.values()) != reported
            and solution.has_untyped_argument):
        return
    names = [tp.name for tp in template.type_params]
    spelled = f"{written}(...)" if call.args else f"{written}()"
    if solution.disagreement is not None:
        example = display_type(_template_shape(template, ()))
        diag = er.emit_with(validator.reporter, er.ERR.CE2065, call.callee.loc,
                            constructor=spelled, reason=solution.disagreement)
    else:
        example = display_type(_template_shape(template, solution.type_args))
        missing = [name for name, t in zip(names, solution.type_args, strict=True)
                   if t is None]
        diag = er.emit_with(validator.reporter, er.ERR.CE2112, call.callee.loc,
                            constructor=spelled,
                            params=", ".join(f"'{name}'" for name in missing))
    diag.help(f"declare the type first, e.g. 'let {example} v = {spelled}', "
              "then use 'v'").emit()


def _template_shape(template, type_args: tuple):
    """`Base@(solved..., T...)`: the instance with each unsolved parameter left named.

    A constructor whose arguments disagree names every parameter, because no solution
    is the one the author meant.
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter
    names = [tp.name for tp in template.type_params]
    solved = type_args if type_args else (None,) * len(names)
    return GenericTypeRef(base_name=template.name, type_args=tuple(
        TypeParameter(name) if t is None else t
        for name, t in zip(names, solved, strict=True)))


def validate_struct_constructor(validator: 'TypeValidator', call: Call) -> None:
    """Validate struct constructor call - field count, field names, and types."""
    # A bloom spread `arr...` is not a struct-construction argument (CE0120).
    if reject_spread_args(validator, call.args):
        return

    struct_name = call.callee.id

    # A type name this unit lost (#863, #921): the table holds the winner, a struct or
    # an enum, and the declaration's CE0004 / CE0006 / CE3011 is the one fault. The
    # arguments are still checked.
    if type_name_is_contested(validator, struct_name):
        call.field_names = None
        for arg in call.args:
            validator.validate_expression(arg)
        return

    if struct_name not in validator.struct_table.by_name:
        er.emit(validator.reporter, er.ERR.CE2001, call.callee.loc, name=struct_name)
        for arg in call.args:
            validator.validate_expression(arg)
        return

    struct_type = validator.struct_table.by_name[struct_name]

    expected_fields = list(struct_type.fields)

    field_names = getattr(call, 'field_names', None)

    if field_names is not None:
        _validate_named_struct_constructor(validator, call, struct_type, expected_fields, field_names)
    else:
        _validate_positional_struct_constructor(validator, call, struct_type, expected_fields)


def _validate_named_struct_constructor(
    validator: 'TypeValidator',
    call: Call,
    struct_type: StructType,
    expected_fields: List[Tuple[str, Type]],
    field_names: List[str]
) -> None:
    """Validate named struct constructor and reorder arguments."""
    from ..field_matcher import validate_and_reorder_named_args

    actual_args = call.args

    reordered_args = validate_and_reorder_named_args(
        struct_type,
        actual_args,
        field_names,
        validator.reporter,
        call.callee.loc
    )

    if reordered_args is None:
        # The names are spent even when they do not fit: the field matcher has already
        # said what is wrong with them, and CE6104 must not report them a second time.
        call.field_names = None
        for arg in actual_args:
            validator.validate_expression(arg)
        return

    call.args = reordered_args

    call.field_names = None

    _check_field_arguments(validator, reordered_args, expected_fields,
                           code=er.ERR.CE2083, field_key="field")


def _validate_positional_struct_constructor(
    validator: 'TypeValidator',
    call: Call,
    struct_type: StructType,
    expected_fields: List[Tuple[str, Type]]
) -> None:
    """Validate positional struct constructor (existing logic)."""
    actual_args = call.args

    if len(actual_args) != len(expected_fields):
        er.emit(validator.reporter, er.ERR.CE2027, call.callee.loc,
               name=display_type(struct_type), expected=len(expected_fields),
               got=len(actual_args))

    _check_field_arguments(validator, actual_args, expected_fields,
                           code=er.ERR.CE2028, field_key="field_name")

    for i in range(len(expected_fields), len(actual_args)):
        validator.validate_expression(actual_args[i])


def _check_field_arguments(
    validator: 'TypeValidator',
    args: List[Expr],
    expected_fields: List[Tuple[str, Type]],
    *,
    code: er.ErrorMessage,
    field_key: str,
) -> None:
    """Check each argument against the field it fills, and stamp it for the backend.

    The one walk both constructors take: a named construction puts its arguments in
    field order first, and from there the two are the same work. Only the CODE differs
    -- CE2083 for a named construction, CE2028 for a positional one -- and the two
    registry texts spell one sentence with two placeholder names, which is why the
    keyword travels beside the code.
    """
    for arg, (field_name, field_type) in zip(args, expected_fields, strict=False):
        resolved_field_type = _resolve_field_type(validator, field_type)

        propagate_types_to_value(validator, arg, resolved_field_type)

        if (isinstance(arg, Call) and hasattr(arg.callee, 'id')
                and isinstance(resolved_field_type, StructType)
                and arg.callee.id in validator.generic_struct_table.by_name):
            arg.callee.id = resolved_field_type.name

        validator.validate_expression(arg)

        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, resolved_field_type):
            er.emit(validator.reporter, code, arg.loc,
                    expected=display_type(resolved_field_type),
                    got=display_type(arg_type), **{field_key: field_name})


def _resolve_field_type(validator: 'TypeValidator', field_type: Type) -> Type:
    """The concrete type a field's DECLARED type names.

    `resolve_declared_type` answers for every kind but one: a written wrapper may name an
    enum no declaration has built yet, so it is INTERNED. The `resolve` pass resolves
    every field before this reads one, so the intern is a guard and not a hot path.
    """
    interned = intern_declared_wrapper(validator, field_type)
    if interned is not None:
        return interned

    resolved = resolve_declared_type(validator, field_type)
    return resolved if resolved is not None else field_type
