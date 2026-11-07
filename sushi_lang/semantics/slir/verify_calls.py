"""The checks of a call in the SLIR verifier (docs/design/ir.md 8.13).

A call writes a place that is no view, reads each operand, and gives no copy of an owning
value other than a `string` to an `Owned` argument. The conventions of a `Direct` callee
are its declared parameters (`declared_params`), and the conventions of a static of a
built-in container are the answer of `argument_conventions`. A call of a built-in method
has a receiver operand. The receiver of a method of a container family with a receiver
rule, or of `Result` or `Maybe`, has the convention of `receiver_convention`, and each
argument the convention of `argument_conventions`, or of the mode of its row. The call of
the concat peephole (`concat_in_place`) has an `Inout` receiver and one `Guaranteed`
argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional, TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.name_mangling import method_symbol
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import (
    Convention, declared_modes, mode_of_type, receiver_mode)
from sushi_lang.semantics.generics.maybe import MAYBE_METHOD_MODES
from sushi_lang.semantics.generics.results import RESULT_METHOD_MODES
from sushi_lang.semantics.method_effects import (
    CONCAT_IN_PLACE, MAYBE_FAMILY, RESULT_FAMILY, RULED_EFFECT_FAMILIES, STRING_FAMILY,
    steps_a_cursor,
    argument_conventions, receiver_convention,
)
from sushi_lang.semantics.param_modes import BuiltinModes
from sushi_lang.semantics.shir.callee import BuiltinMethod, Intrinsic
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.printer import show
from sushi_lang.semantics.slir.verify_rules import CONVENTIONS, OWNED_COPY, RECEIVER_OPERAND
from sushi_lang.semantics.statics import CONTAINER_STATIC_FAMILIES
from sushi_lang.semantics.stdlib_registry import stdlib_arity
from sushi_lang.semantics.typesys import BuiltinType, owns_resource
from sushi_lang.semantics.unit_symbols import function_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.externals import ExternalTable
    from sushi_lang.semantics.passes.collect.functions import ExtensionTable, FunctionTable
    from sushi_lang.semantics.slir.verify import Check


@dataclass(frozen=True, slots=True)
class DeclaredParams:
    """The convention of each declared parameter of a direct callee. The tail of a `var_arg`
    extern declares no parameter, so the rule reads the declared prefix alone."""
    conventions: tuple[Convention, ...]
    var_arg: bool = False


#: The declared parameters of a `Direct` callee, or None when the build declares none.
DeclaredLookup: TypeAlias = Callable[[st.Direct], Optional[DeclaredParams]]


def declared_params(funcs: FunctionTable, extensions: ExtensionTable,
                    callee: st.Direct, *,
                    externals: Optional[ExternalTable] = None) -> Optional[DeclaredParams]:
    """The declared parameters of the function that `callee` names. A free function is the
    function of its name in the unit whose symbol is the symbol of the callee; the name of a
    pack instance has a dot too. A method or a static has the written name `<type>.<method>`:
    it is the extension of that name whose symbol is the symbol of the callee, and an instance
    method has its receiver first. A function of a registry stdlib module is the row of its
    module, and each of its parameters borrows. A C function is the declaration of `externals`
    whose link name is the symbol of the callee: each parameter has the convention of its
    declared mode, and a variadic one has `var_arg`."""
    if callee.module is not None:
        arity = stdlib_arity(callee.module, callee.name)
        return None if arity is None else DeclaredParams((Convention.GUARANTEED,) * arity)
    for unit, own in funcs.by_unit.items():
        sig = own.get(callee.name)
        if sig is not None and function_symbol(callee.name, unit) == callee.symbol:
            return DeclaredParams(tuple(mode.convention for mode in declared_modes(sig.params)))
    _, dot, method = callee.name.rpartition(".")
    if dot:
        for record in extensions.signatures_named(method):
            if method_symbol(record.target_type, method, record.unit_name,
                             record.method_type_args) == callee.symbol:
                receiver = () if record.is_static else (
                    receiver_mode(record.self_mode).convention,)
                return DeclaredParams(receiver + tuple(
                    mode.convention for mode in declared_modes(record.params)))
        return None
    extern = None if externals is None else externals.by_link_name(callee.symbol, callee.name)
    if extern is not None:
        return DeclaredParams(tuple(mode_of_type(ty).convention for ty in extern.param_types),
                              var_arg=extern.is_variadic)
    return None


def check_call(check: Check, stmt: st.Call) -> None:
    check.place(stmt.dest, stmt.span)
    check.not_a_view(stmt.dest, stmt.span)
    callee = stmt.callee
    if isinstance(callee, st.Indirect):
        check.operand(callee.operand, stmt.span)
    for operand, convention in stmt.args:
        check.operand(operand, stmt.span)
        if convention.consumes and isinstance(operand, p.Copy):
            _owned_copy(check, operand, stmt.span)
    if isinstance(callee, st.Direct):
        _conventions_of(check, stmt, callee)
    elif isinstance(callee, st.Builtin) and isinstance(callee.method, Intrinsic):
        _static_conventions(check, stmt, callee.method.name)
    elif isinstance(callee, st.Builtin) and isinstance(callee.method, BuiltinMethod):
        if not stmt.args:
            check.fail(RECEIVER_OPERAND, f"the call of {callee.method.family}."
                                         f"{callee.method.name} has no receiver", stmt.span)
        _method_conventions(check, stmt, callee.method)


def _owned_copy(check: Check, operand: p.Copy, span: Span) -> None:
    """An `Owned` argument takes a copy of a plain value alone. A `string` is the one
    owning type that may come as a copy: one bound from a literal owns no heap, and
    `borrow` adopts it (ir.md 7.9)."""
    ty = check.operand_type(operand, span)
    if ty is None or ty == BuiltinType.STRING:
        return
    if owns_resource(ty, check.drops):
        check.fail(OWNED_COPY, f"{show(operand)} has the type {display_type(ty)}, which "
                               f"owns", span)


def _conventions_of(check: Check, stmt: st.Call, callee: st.Direct) -> None:
    declared = check.declared(callee)
    if declared is None:
        check.fail(CONVENTIONS, f"the callee @{callee.name} has no declaration", stmt.span)
    wanted, given = declared.conventions, tuple(c for _, c in stmt.args)
    if len(given) < len(wanted) or (len(given) > len(wanted) and not declared.var_arg):
        check.fail(CONVENTIONS, f"the call of @{callee.name} has {len(given)} arguments, "
                                f"and @{callee.name} declares {len(wanted)}", stmt.span)
    for index, (have, want) in enumerate(zip(given[:len(wanted)], wanted, strict=True)):
        if have is not want:
            check.fail(CONVENTIONS, f"argument {index} of @{callee.name} is @{have.value}, "
                                    f"and its parameter is @{want.value}", stmt.span)


def _static_conventions(check: Check, stmt: st.Call, name: str) -> None:
    """A static of a built-in container (`Own.alloc`): each argument has the convention
    that `argument_conventions` gives it. Any other intrinsic has no rule here."""
    owner, _, method = name.partition(".")
    family = CONTAINER_STATIC_FAMILIES.get(owner)
    if family is None:
        return
    wanted = argument_conventions(family, method, len(stmt.args))
    for index, ((_, have), want) in enumerate(zip(stmt.args, wanted, strict=True)):
        if have is not want:
            check.fail(CONVENTIONS, f"argument {index} of {name} is @{have.value}, and the "
                                    f"static takes it @{want.value}", stmt.span)


#: The table of the parameter modes of each family of `Result` and `Maybe` methods.
WRAPPER_MODES: dict[str, BuiltinModes] = {
    MAYBE_FAMILY: MAYBE_METHOD_MODES,
    RESULT_FAMILY: RESULT_METHOD_MODES,
}


def _method_conventions(check: Check, stmt: st.Call, method: BuiltinMethod) -> None:
    """A method of a container family with a receiver rule (`RULED_EFFECT_FAMILIES`): the
    receiver has the convention of `receiver_convention`, and each argument the convention
    of `argument_conventions`. A method of `Result` or `Maybe` (`WRAPPER_MODES`): the
    receiver has the convention of `receiver_convention`, and each argument the convention
    of the mode of its row. `concat_in_place` of the string family: the receiver has the
    convention of `receiver_convention`, and its one argument is `Guaranteed`. A
    `cursor_next` row of the map family: the map is `InGuaranteed`, and the cursor is
    `Inout`. Any other family has no rule here."""
    count = len(stmt.args) - 1
    name = f"{method.family}.{method.name}"
    arguments: tuple[Convention, ...]
    if steps_a_cursor(method):
        if count != 1:
            check.fail(CONVENTIONS, f"the call of {name} has {count} arguments, and it "
                                    f"takes 1", stmt.span)
        arguments = (Convention.INOUT,)
    elif method.family in RULED_EFFECT_FAMILIES:
        arguments = argument_conventions(method.family, method.name, count)
    elif method.family in WRAPPER_MODES:
        modes = WRAPPER_MODES[method.family].of(method.name, count) or ()
        arguments = tuple(mode.convention for mode in modes)
    elif method.family == STRING_FAMILY and method.name == CONCAT_IN_PLACE:
        if count != 1:
            check.fail(CONVENTIONS, f"the call of {name} has {count} arguments, and it "
                                    f"takes 1", stmt.span)
        arguments = (Convention.GUARANTEED,)
    else:
        return
    receiver = receiver_convention(method)
    if receiver is None:
        return
    wanted = (receiver,) + arguments
    for index, ((_, have), want) in enumerate(zip(stmt.args, wanted, strict=True)):
        if have is not want:
            what = "the receiver" if index == 0 else f"argument {index - 1}"
            check.fail(CONVENTIONS, f"{what} of {name} is @{have.value}, and the method takes "
                                    f"it @{want.value}", stmt.span)
