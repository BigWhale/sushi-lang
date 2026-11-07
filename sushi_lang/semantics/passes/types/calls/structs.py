"""Struct construction checks (docs/design/ir.md 7.3).

The fields of a `StructInit` go through the one argument check, at the types of the
fields (`check_struct_fields`). The written names of a named construction whose names are
not the fields of the struct give CE2080, CE2081 or CE2082 (`reject_field_names`). A
generic construction that nothing solves is CE2112 or CE2065 (`refuse_unsolved_struct`).

The AST solver of a generic construction (`untyped_struct_instance`) is the answer that the
early passes read.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Mapping, NamedTuple, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.semantics.ast import Expr
from ..arguments import NOTHING_TYPED, check_arguments, reject_misplaced_spread
from ..utils import intern_declared_wrapper, resolve_declared_type

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.types import GenericStructType
    from .. import TypeValidator


class FieldSolution(NamedTuple):
    """What the arguments of a generic struct constructor give (#1150)."""
    type_args: tuple
    disagreement: Optional[str]


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
    return FieldSolution(type_args, disagreement)


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


def refuse_unsolved_struct(validator: 'TypeValidator', node: e.StructInit, template,
                           written: str, params: Sequence[Type], solution,
                           type_args: tuple, *, had_error: bool) -> None:
    """Report why a generic struct construction has no instance. The count comes first, as
    for a generic call (#790): a miscount is the fault, and an unsolved type parameter only
    follows from it. An argument with its own error of this pass gives no type argument,
    and nothing follows it. `written` is the construction as the source writes it (`Box`, `sh.Box`),
    and `params` holds the type of the field that each argument fills."""
    expected = len(template.fields)
    if not node.named and len(node.fields) != expected:
        er.emit(validator.reporter, er.ERR.CE2027, node.decl_loc,
                name=display_type(template_shape(template, ())),
                expected=expected, got=len(node.fields))
        return
    if had_error and any(ty is None for ty in solution.typed.values()):
        return
    spelled = f"{written}(...)" if node.fields else f"{written}()"
    if solution.misses:
        first = solution.misses[0]
        field = node.names[first] if node.names else template.fields[first][0]
        reason = solution.why[0] if solution.why else _disagreement(
            field, params[first], solution.typed[first], solution.solved)
        example = display_type(template_shape(template, ()))
        diag = er.emit_with(validator.reporter, er.ERR.CE2065, node.decl_loc,
                            constructor=spelled, reason=reason)
    else:
        example = display_type(template_shape(template, type_args))
        missing = [tp.name for tp, ty in zip(template.type_params, type_args, strict=True)
                   if ty is None]
        diag = er.emit_with(validator.reporter, er.ERR.CE2112, node.decl_loc,
                            constructor=spelled,
                            params=", ".join(f"'{name}'" for name in missing))
    diag.help(f"declare the type first, e.g. 'let {example} v = {spelled}', "
              "then use 'v'").emit()


def template_shape(template, type_args: tuple):
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


def check_struct_fields(validator: 'TypeValidator', node: e.StructInit, struct: StructType,
                        typed: Mapping[int, Optional[Type]] = NOTHING_TYPED) -> None:
    """The fields of a construction of `struct` against the types of its fields.

    A bloom argument is CE0120, and the fields are then not checked: a struct is never
    variadic. A positional construction with a wrong count is CE2027 at the written type,
    and a field
    that does not fit is CE2028, or CE2083 in a named construction. A named construction
    whose names are not the fields of the struct is CE2080, CE2081 or CE2082, and its
    fields are then checked with no type of their position.
    """
    if any([reject_misplaced_spread(validator, arg) for arg in node.fields]):
        return
    if node.names is not None:
        if not reject_field_names(validator, struct, node.names, node.decl_loc):
            er.raise_internal_error("CE0000", span=node.loc,
                                    detail=f"lowering kept the written names of a named "
                                           f"construction of '{struct.name}' whose names "
                                           f"are its fields")
        for index, arg in enumerate(node.fields):
            if index not in typed:
                visit.check_expr(validator, arg.value)
        return
    check_arguments(validator, display_type(struct),
                    [resolve_field_type(validator, ty) for _name, ty in struct.fields],
                    node.fields, node.decl_loc,
                    mismatch_code=er.ERR.CE2083 if node.named else er.ERR.CE2028,
                    arity_code=er.ERR.CE2027,
                    field_names=[name for name, _ty in struct.fields], typed=typed)


def reject_field_names(validator: 'TypeValidator', struct: StructType | GenericStructType,
                       names: Sequence[str], loc: Optional[Span]) -> bool:
    """The first fault of the written names of a named construction of `struct`: a name
    that the struct does not declare (CE2080), a repeated name (CE2081), or a missing
    field (CE2082). True when one was reported."""
    declared = [name for name, _ty in struct.fields]
    unknown = next((name for name in names if name not in declared), None)
    if unknown is not None:
        er.emit(validator.reporter, er.ERR.CE2080, loc, field=unknown,
                struct=display_type(struct))
        return True
    repeated = next((name for index, name in enumerate(names) if name in names[:index]),
                    None)
    if repeated is not None:
        er.emit(validator.reporter, er.ERR.CE2081, loc, field=repeated)
        return True
    missing = sorted(set(declared) - set(names))
    if missing:
        er.emit(validator.reporter, er.ERR.CE2082, loc, fields=", ".join(missing),
                struct=display_type(struct))
        return True
    return False


def resolve_field_type(validator: 'TypeValidator', field_type: Type) -> Type:
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
