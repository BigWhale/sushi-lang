"""Struct construction checks (docs/design/ir.md 7.3).

The fields of a `StructInit` go through the one argument check, at the types of the
fields (`check_struct_fields`). The written names of a named construction whose names are
not the fields of the struct give CE2080, CE2081 or CE2082 (`reject_field_names`). A
generic construction that nothing solves is CE2112 or CE2065 (`refuse_unsolved_struct`).
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Mapping, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import StructType, Type
from ..arguments import NOTHING_TYPED, check_arguments, reject_misplaced_spread
from ..utils import intern_declared_wrapper, resolve_declared_type

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.types import GenericStructType
    from .. import TypeValidator


def _disagreement(field: str, field_type: Type, arg_type: Type, solved: dict) -> str:
    """Why the argument for `field` does not fit what the arguments before it gave."""
    given = solved.get(str(field_type))
    if given is not None:
        return (f"the arguments give '{field_type}' two types, '{display_type(given)}' "
                f"and '{display_type(arg_type)}' (field '{field}')")
    return (f"field '{field}' is '{display_type(field_type)}', and the argument is "
            f"'{display_type(arg_type)}'")


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
