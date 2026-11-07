"""The one argument check: the arity, then each argument against the parameter it fills.

Every call-shaped position in the typecheck pass measures its arguments the same way --
count them, check each one at the type of its parameter, compare, and say where it does
not fit. The shape was written once per callee kind, and a copy that falls behind is a
behaviour the compiler has on one path and has lost on another: the generics copy lost
the bloom-spread refusal and the borrow help (#747), and the FFI copy numbered its
arguments from zero (#746).

The arguments are the SHIR `Arg`s of the call (docs/design/ir.md 7.4). A `peek x` or a
`poke x` argument is a borrow of the type of `x`, so a wrong or a missing `peek`/`poke`
marker is a type mismatch. A `nom` marker changes no type: when the caller gives the
declared parameters (`Parameters`), each written marker is compared against the declared
mode (CE2427), and each argument that fits gets its convention, a row of the
`TypeckResults` of the body. A construction has no declared modes and gives none.

What legitimately differs between the callers is a four-tuple -- the callee's display
name, the parameter types, the arity code and the mismatch code -- plus five policy
questions, each a keyword:

  ``minimum_arity``  a variadic callee's FIXED prefix. More arguments than parameters is
                     the tail, not a fault, and the tail carries its own rule at each
                     caller.
  ``stop_on_arity``  the callers that report the count and then say nothing more.
  ``also_accepts``   per parameter, the other types it takes (`string.join` takes a
                     `List@(string)` beside a `string[]`). The mismatch names the
                     declared type, and the help names the others.
  ``field_names``    per parameter, the field that it fills, for a mismatch code whose
                     text names the field (a struct construction: CE2028, CE2083).
  ``compares_nom``   False for a C function, whose `nom` marker is inert (no CE2427).

The codes travel IN, so this module spells neither the arity code nor the mismatch code.
That is what lets the enum constructor read the same check with CE2049/CE2050, which name
a VARIANT where the function pair names a function, and the struct constructor with
CE2027 and CE2028 or CE2083. The gate is
``tests/unit/test_argument_check_is_one.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Collection, Dict, Mapping, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import Convention, ParamMode
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.source_text import source_text
from sushi_lang.semantics.typesys import BorrowMode, ReferenceType, Type, deref_type

from .compatibility import types_compatible

if TYPE_CHECKING:
    from . import TypeValidator

#: The type that a `peek x` or a `poke x` argument gives to the value of `x`.
_BORROWS = {Convention.IN_GUARANTEED: BorrowMode.PEEK, Convention.INOUT: BorrowMode.POKE}


@dataclass(frozen=True)
class Parameters:
    """The declared mode and the name of each parameter of a callee, and the call whose
    arguments get their conventions."""
    call: ShirId
    modes: Sequence[ParamMode]
    names: Sequence[str]


def check_arguments(
    validator: 'TypeValidator',
    callee_name: str,
    params: Sequence[Optional[Type]],
    args: Sequence[e.Arg],
    loc: Any,
    *,
    mismatch_code: er.ErrorMessage,
    arity_code: er.ErrorMessage,
    declared: Optional[Parameters] = None,
    minimum_arity: bool = False,
    stop_on_arity: bool = False,
    also_accepts: Sequence[Sequence[Type]] = (),
    field_names: Sequence[str] = (),
    compares_nom: bool = True,
    typed: Mapping[int, Optional[Type]] = {},
) -> bool:
    """Measure `args` against `params`, and answer whether the COUNT fit.

    `params` holds the expected types in order, one per parameter, resolved. `loc` is the
    span that names the callee, which is where a wrong count is reported; a wrong TYPE is
    reported at the argument itself.

    The answer is the arity alone, for the callers that stop there. A caller that wants
    every fault reported ignores it. `compares_nom` False gives no CE2427: a C function
    takes nothing from Sushi, so its `nom` marker is inert. `typed` holds, by index, the
    answer of each argument that the caller checked to solve the parameter types.
    """
    arity_ok = (len(args) >= len(params) if minimum_arity
                else len(args) == len(params))
    if not arity_ok:
        er.emit(validator.reporter, arity_code, loc,
                expected=len(params), got=len(args),
                **_callee_key(arity_code, callee_name))
        if stop_on_arity:
            return False

    for index, (arg, expected) in enumerate(zip(args, params, strict=False), start=1):
        if reject_misplaced_spread(validator, arg):
            continue
        others = also_accepts[index - 1] if index <= len(also_accepts) else ()
        field = field_names[index - 1] if index <= len(field_names) else None
        if index - 1 in typed:
            fits = compare_argument(validator, callee_name, arg, index, typed[index - 1],
                                    expected, mismatch_code=mismatch_code, others=others,
                                    field=field)
        else:
            fits = check_argument(validator, callee_name, arg, index, expected,
                                  mismatch_code=mismatch_code, others=others, field=field)
        if declared is not None and index <= len(declared.modes):
            mode = declared.modes[index - 1]
            if compares_nom and reject_nom_marker(validator, arg, mode,
                                                  declared.names[index - 1]):
                fits = False
            if fits:
                validator.body_answers().record_convention(declared.call, index - 1,
                                                           mode.convention)

    if minimum_arity:
        return arity_ok

    for extra in args[len(params):]:
        if not reject_misplaced_spread(validator, extra):
            visit.check_expr(validator, extra.value)
    return arity_ok


def record_reads(validator: 'TypeValidator', call: ShirId, indexes: Sequence[int]) -> None:
    """Give each argument at `indexes`, which no declared parameter takes (the variadic
    tail of a C function), the convention of a read."""
    answers = validator.body_answers()
    for index in indexes:
        answers.record_convention(call, index, ParamMode.BORROW.convention)


def record_conventions(validator: 'TypeValidator', declared: Parameters,
                       args: Sequence[e.Arg], *, slots: Collection[int] = ()) -> None:
    """Give each argument of a call whose arguments a row checks one by one (an array
    method) the convention of its declared mode, after the `nom` marker check (CE2427). A
    container slot takes its argument by position, as a `nom` parameter does, and has no
    marker to compare."""
    answers = validator.body_answers()
    for index, arg in enumerate(args):
        if index in slots:
            answers.record_convention(declared.call, index, ParamMode.NOM.convention)
            continue
        mode = declared.modes[index]
        if not reject_nom_marker(validator, arg, mode, declared.names[index]):
            answers.record_convention(declared.call, index, mode.convention)


def record_builtin_conventions(validator: 'TypeValidator', call: ShirId,
                               args: Sequence[e.Arg],
                               conventions: Sequence[Convention]) -> None:
    """Give each argument of a built-in method or of a container static the convention of
    `argument_conventions` (docs/design/ir.md 8.3): an `Owned` argument is a slot, and each
    other argument borrows, with the `nom` marker check (CE2427). A parameter has the name
    of its position (`#1`)."""
    declared = Parameters(call, (ParamMode.BORROW,) * len(args),
                          [f"#{index + 1}" for index in range(len(args))])
    record_conventions(validator, declared, args,
                       slots=frozenset(index for index, convention in enumerate(conventions)
                                       if convention is Convention.OWNED))


def check_argument(validator: 'TypeValidator', callee_name: str, arg: e.Arg, index: int,
                   expected: Optional[Type], *, mismatch_code: er.ErrorMessage,
                   others: Sequence[Type] = (), field: Optional[str] = None,
                   typed_by_position: bool = True) -> bool:
    """Check ONE argument at the type of the parameter that it fills, at the position
    `index` (from 1), and answer whether it fits. A mismatch is `mismatch_code` at the
    argument. `typed_by_position` False checks the value with no type from its position."""
    position = deref_type(expected) if typed_by_position else None
    actual = marked_type(arg, visit.check_expr(validator, arg.value, position))
    return compare_argument(validator, callee_name, arg, index, actual, expected,
                            mismatch_code=mismatch_code, others=others, field=field)


def compare_argument(validator: 'TypeValidator', callee_name: str, arg: e.Arg, index: int,
                     actual: Optional[Type], expected: Optional[Type], *,
                     mismatch_code: er.ErrorMessage, others: Sequence[Type] = (),
                     field: Optional[str] = None) -> bool:
    """Compare ONE argument that the pass typed, `actual`, with the type of the parameter
    that it fills, and answer whether it fits. A mismatch is `mismatch_code` at the
    argument. A call whose parameter types its arguments solve types them first."""
    if actual is None or expected is None:
        return False
    if (types_compatible(validator, actual, expected)
            or any(types_compatible(validator, actual, other) for other in others)):
        return True
    _emit_mismatch(validator, mismatch_code, callee_name, arg, index, expected, actual,
                   others, field)
    return False


def marked_type(arg: e.Arg, ty: Optional[Type]) -> Optional[Type]:
    """The type of an argument whose value has the type `ty`: a borrow of that type when
    the argument is written `peek x` or `poke x`."""
    borrow = None if arg.marker is None else _BORROWS.get(arg.marker)
    if ty is None or borrow is None:
        return ty
    return ReferenceType(ty, borrow)


def reject_nom_marker(validator: 'TypeValidator', arg: e.Arg, mode: ParamMode,
                      name: str) -> bool:
    """CE2427: the `nom` marker is written at the call site if and only if it is declared."""
    if (arg.marker is Convention.OWNED) == mode.consumes:
        return False
    help_text = ("the callee takes ownership here; write `nom` at the call site too, or "
                 "`nom <arg>.clone()` to keep your own value") if mode.consumes else (
                 "the callee only borrows this argument, so it stays yours after the "
                 "call; drop the `nom`")
    er.emit_with(validator.reporter, er.ERR.CE2427, arg.marker_loc or arg.value.loc,
                 name=name).help(help_text).emit()
    return True


def reject_misplaced_spread(validator: 'TypeValidator', arg: e.Arg) -> bool:
    """Refuse a bloom spread `arr...` in a position that is never variadic (CE0120).

    The value is still checked, so nothing downstream reads an unwalked argument.
    Answers True when the argument was refused.
    """
    if not arg.bloom:
        return False
    er.emit(validator.reporter, er.ERR.CE0120, arg.value.loc,
            message="bloom argument 'arr...' is only allowed as the last argument "
                    "of a call to a variadic '...T' function")
    visit.check_expr(validator, arg.value)
    return True


def _callee_key(code: er.ErrorMessage, callee_name: str) -> Dict[str, str]:
    """The placeholder a code's registry text spells for the callee.

    The function pair (CE2006/CE2009) names a `{name}`, the enum-constructor pair
    (CE2049/CE2050) a `{variant}`, and CE2006 names neither. Reading the registry text
    is what keeps the seam total over a code it has not met.
    """
    if "{variant}" in code.text:
        return {"variant": callee_name}
    if "{name}" in code.text:
        return {"name": callee_name}
    return {}


def _field_key(code: er.ErrorMessage, field: Optional[str]) -> Dict[str, str]:
    """The placeholder a code's registry text spells for the field that an argument fills:
    `{field_name}` (CE2028) or `{field}` (CE2083); none when no field is given."""
    if field is None:
        return {}
    for key in ("field_name", "field"):
        if "{" + key + "}" in code.text:
            return {key: field}
    return {}


def _spell_place(validator: 'TypeValidator', value: e.ShirExpr) -> Optional[str]:
    """The source spelling of a PLACE the user can borrow, or None when there is none.

    The help offers the argument back to the user, so it must be text the user could type.
    A local spells its name, and a field chain off a local spells the local and then each
    field (`b.inner.label`), through the one place walk. A constant or a unit variable
    spells its root as the source writes it (`geo.ORIGIN.x`). Anything else has no spelling
    here and is offered no help.
    """
    walked = walk_place(value, Step.MEMBER)
    if isinstance(walked.node, (e.ConstRef, e.StorageRef)):
        root = source_text(validator.reporter.body_source(), walked.node.loc)
    else:
        local = None if walked.local is None else validator.body_locals.get(walked.local)
        root = None if local is None else local.name
    if root is None:
        return None
    return ".".join([root, *(field.name for field in reversed(walked.path))])


def argument_span(arg: e.Arg) -> Optional[Span]:
    """Where a mismatch of an argument stands: a `peek x` or a `poke x` from its marker,
    any other argument at its value."""
    if arg.marker in _BORROWS and arg.marker_loc is not None:
        return arg.marker_loc
    return arg.value.loc


def _emit_mismatch(validator: 'TypeValidator', code: er.ErrorMessage, callee_name: str,
                   arg: e.Arg, index: int, expected_ty: Type, actual_ty: Type,
                   others: Sequence[Type] = (), field: Optional[str] = None) -> None:
    """Report the mismatch, and say how to borrow when the parameter wants a borrow."""
    diag = er.emit_with(validator.reporter, code, argument_span(arg),
                        index=index, expected=display_type(expected_ty),
                        got=display_type(actual_ty),
                        **_callee_key(code, callee_name), **_field_key(code, field))
    place = None
    if isinstance(expected_ty, ReferenceType) and arg.marker not in _BORROWS:
        place = _spell_place(validator, arg.value)
    if place is not None and isinstance(expected_ty, ReferenceType):
        diag.help(f"borrow it at the call site: `{expected_ty.mutability} {place}`")
    if others:
        spelled = ", ".join(f"`{display_type(other)}`" for other in others)
        diag.help(f"this argument also takes {spelled}")
    diag.emit()


def names_a_type_pack(validator: 'TypeValidator', expr: Any) -> bool:
    """Is `expr` the bare name of a type pack of the instance being checked (#1109)?"""
    from sushi_lang.semantics.ast import Name
    func = validator.current_function
    return (isinstance(expr, Name) and func is not None
            and expr.id in func.pack_names and expr.id not in validator.variable_types)


def reject_type_pack_value(validator: 'TypeValidator', expr: Any) -> bool:
    """CE0144 for a type-pack name used as a value. True when it was refused."""
    if not names_a_type_pack(validator, expr):
        return False
    er.emit_with(validator.reporter, er.ERR.CE0144, expr.loc, name=expr.id).help(
        f"walk a pack with `expand(a in {expr.id}):`; pack forwarding and pack "
        "indexing are not supported").emit()
    return True
