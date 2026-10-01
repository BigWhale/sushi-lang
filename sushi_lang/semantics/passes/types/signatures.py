"""Declaration signature validation for type validation (the typecheck pass)."""
from __future__ import annotations

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import FuncDef, ExtendDef, ExtendWithDef, PerkDef
from sushi_lang.semantics.typesys import (
    BuiltinType, UnknownType, EnumType
)
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.channel import callable_text, has_channel
from sushi_lang.semantics.generics.results import (
    is_builtin_wrapper_enum, signature_result_arms)
from sushi_lang.semantics.generics.types import TypeParameter
from sushi_lang.semantics.generics.extension_targets import (
    CONCRETE_EXTENSION_TARGETS)

from .control_flow import block_always_returns, reject_dead_statements
from .utils import validate_type_name, validate_and_register_parameters
from .perks import validate_perk_implementation, check_no_conflicts_with_regular_methods
from sushi_lang.semantics.generics.type_display import display_type


# The positions `run()`'s per-declaration loop never reaches. A function, an
# extension and a perk implementation are all walked for their bodies, and a struct
# or an enum has none, so its written types were checked nowhere (#504).
_DECLARED_TYPE_POSITIONS = frozenset({"field", "variant"})

# The same hole one declaration further in: a perk CONTRACT is a callable with no body,
# so no body walk reaches what it writes either (#667). `signature_types()` already
# yields all three of its positions -- the channel came here with #663, and these are
# the other two -- so the leak fence (CE3009) and the `ptr` quarantine (CE5008/CE5009)
# already policed them while nothing asked whether the name is a type at all.
_CONTRACT_TYPE_POSITIONS = frozenset({"return", "parameter"})


def validate_declared_types(self, program) -> None:
    """Check every type a declaration WITHOUT a body writes down.

    Reads `signature_types()`, the one walk over a unit's declared type positions, so a
    position added there is policed here without a second walk. A GENERIC declaration is
    skipped for the reason the function loop skips one: its fields name type parameters,
    which are in no table until the instance is monomorphized -- and the instance is an
    ordinary declaration that this walk reaches.
    """
    from sushi_lang.semantics.ast_walk import signature_types

    # Not a LIBRARY unit, for `check_public_signatures`' reason: its declarations were
    # checked when the library was built, and here they carry the consumer's substitutions.
    if self.in_library_unit:
        return

    for site in signature_types(program):
        if site.ty is None or getattr(site.decl, "type_params", None):
            continue
        if site.position in _DECLARED_TYPE_POSITIONS:
            validate_type_name(self, site.ty, site.span)
        elif isinstance(site.decl, PerkDef):
            # The CONTRACT. Its implementation is reached through its body, and every
            # other kind writes the same three positions, so the rule comes here
            # (#663 for the channel, #667 for the return and the parameters).
            if site.position in _CONTRACT_TYPE_POSITIONS:
                validate_type_name(self, site.ty, site.span)
            elif site.position == "error":
                validate_error_channel(self, site.at.ret, site.ty, site.span)


def validate_error_channel(self, ret, err_type, span) -> None:
    """The Err arm of the Result a signature answers, in EITHER spelling.

    `T | E` is SUGAR for `Result@(T, E)` and nothing else, so the two must admit the
    same `E`. They did not: CE2084 was a rule on the SHORT form alone, and
    `fn f() Result@(i32, Bad):` with a struct error compiled while `fn f() i32 | Bad:`
    was refused (#668). `signature_result_arms` is the one derivation of the arms a
    signature answers -- an explicit Result is its own two arms, anything else is
    wrapped with the spelled `| E`, and a bare signature has none -- so reading the Err
    arm off it is what makes the short form sugar in the checker too.

    The rule: the arm must be an ENUM. A struct, a primitive, an array and a function
    type are CE2084. A built-in WRAPPER is an enum by representation and not by intent,
    so it is named and refused (CE2086): the Err arm already means failure, and
    `Result@(i32, Maybe@(string))` asks a reader to read an absence as one.

    "Enum" is a PROXY for "error type" and NEEDS.md records the want. Until that lands
    this is the line.
    """
    if err_type is not None:
        validate_type_name(self, err_type, span)

    arms = signature_result_arms(ret, err_type)
    if arms is None:
        return
    err_arm = arms[1]

    resolved = resolve_unknown_type(
        err_arm, self.struct_table.by_name, self.enum_table.by_name)

    # A name that spells nothing was refused where it was written -- CE2001 for the
    # short form's own name, and the return walk for the long form's. Each already says
    # everything a reader can act on, and a kind complaint beside one is a second
    # diagnostic about one fault (#663). A type PARAMETER is not this rule's either: it
    # names no type until the instance is monomorphized.
    if isinstance(resolved, (UnknownType, TypeParameter)):
        return

    if not isinstance(resolved, EnumType):
        self.err.emit(er.ERR.CE2084, span, type_name=display_type(err_arm))
        return

    if is_builtin_wrapper_enum(resolved):
        self.err.emit(er.ERR.CE2086, span, type_name=display_type(err_arm))


def _enter_body(self, node, name: str, kind: str, ret, err_type, err_span) -> None:
    """The state every body validates under: a function, a method and a lifted lambda.

    `channel_result` is the interned Result the body answers, or None for a BARE body
    (docs/design/error-channel.md). `validate_return_statement` and the `??` check read
    this state and nothing else, so the three kinds of body have one rule.
    """
    from .resolution import channel_result
    self.body_name = callable_text(name, kind)
    self.body_return_type = ret
    validate_error_channel(self, ret, err_type, err_span)
    self.channel_result = channel_result(self, ret, err_type) if has_channel(node) else None
    self.variable_types = {}
    self.destroyed_arrays = [set()]


def _leave_body(self) -> None:
    self.body_name = None
    self.body_return_type = None
    self.channel_result = None
    self.in_synthesized_body = False


def _reject_fall_off(self, body, ret, channel: bool, span) -> None:
    """CE0107: a body that answers a value or a Result reaches its end with no `return`.

    A bare `~` body answers nothing, so it may end (#824, #845).
    """
    if (ret != BuiltinType.BLANK or channel) and not block_always_returns(self, body):
        self.err.emit(er.ERR.CE0107, span, callable=self.body_name)


def validate_function(self, func: FuncDef) -> None:
    """Validate types within a function, or within a lambda the lift pass made one."""
    from sushi_lang.semantics.passes.lift import is_lifted_lambda
    self.current_function = func
    self.in_library_body = (self.in_library_unit
                            or bool(getattr(func, "is_library_template", False)))
    self.in_synthesized_body = bool(getattr(func, "is_synthesized", False))
    # And whose body it is: the file its spans belong to (#471), and whether it is one
    # of many copies of one source (#648). Set on every entry, so an ordinary body
    # clears what a transplanted or copied one set.
    self.reporter.enter_body(func)
    kind = "lambda" if is_lifted_lambda(func) else "function"
    _enter_body(self, func, func.name, kind, func.ret, func.err_type,
                func.err_span or func.ret_span)

    validate_and_register_parameters(self, func.params)

    validate_type_name(self, func.ret, func.ret_span)

    self._validate_block(func.body)
    reject_dead_statements(self, func.body)
    _reject_fall_off(self, func.body, func.ret, has_channel(func), func.name_span)

    self.current_function = None
    _leave_body(self)


def _self_registration_type(target_type, self_mode):
    """The type `self` registers under: the bare target, or its reference (#327).

    A `nom self` receiver registers the BARE target: it is the value, not a borrow of
    somebody else's, so nothing here should make it read as a reference.
    """
    from sushi_lang.semantics.param_modes import receiver_mode
    mode = receiver_mode(self_mode)
    if not mode.by_pointer:
        return target_type
    from sushi_lang.semantics.typesys import ReferenceType
    return ReferenceType(target_type, mode.borrow_mode)


def _validate_target_type(self, target_type, span, synthesized: bool) -> None:
    """The target type of an extension or a perk implementation, checked ONCE.

    It belongs to the header, not to a method, so a perk implementation with three
    methods and one bad target says so once. `synthesized` is True for a template's
    copy: it is checked in its home unit, but its type arguments were written at the
    call site and that site's own unit validated them (#1064), so the copy names nothing.
    """
    # An extension body is never a transplanted library body, so what a previously
    # validated function set must not colour this diagnostic (#471).
    self.reporter.leave_body()
    self.in_synthesized_body = synthesized
    validate_type_name(self, target_type, span)
    if target_type == BuiltinType.BLANK:
        self.err.emit(er.ERR.CE2032, span)


def _register_self(self, target_type, self_mode) -> None:
    """Register `self` under the target type, when the target names a type at all.

    A `poke self` / `peek self` receiver (#327) registers its full `ReferenceType`, so
    every consumer that asks "is this name a borrow?" answers truthfully and inference
    auto-dereferences.
    """
    self_type = None
    if isinstance(target_type, CONCRETE_EXTENSION_TARGETS):
        self_type = target_type
    elif isinstance(target_type, UnknownType):
        resolved = resolve_unknown_type(
            target_type, self.struct_table.by_name, self.enum_table.by_name)
        if resolved != target_type:
            self_type = resolved
    if self_type is not None:
        self.variable_types["self"] = _self_registration_type(self_type, self_mode)


def _validate_method_body(self, target_type, method, synthesized: bool) -> None:
    """One method body, under the state every body validates under.

    An extension method and a perk-implementation method differ in one thing only: where
    the target type comes from -- the declaration itself, or the `extend X with P` header.
    """
    self.current_function = None
    self.in_library_body = (self.in_library_unit
                            or bool(getattr(method, "is_library_template", False)))
    self.in_synthesized_body = synthesized
    # Whether this body is one of many copies of one source: a perk-implementation
    # method cut per instantiation is (#800); an extension method never is.
    self.reporter.enter_body(method)
    _enter_body(self, method, method.name, "method", method.ret, method.err_type,
                method.err_span or method.name_span)

    _register_self(self, target_type, getattr(method, "self_mode", None))

    validate_and_register_parameters(self, method.params)

    validate_type_name(self, method.ret, method.ret_span)

    self._validate_block(method.body)
    reject_dead_statements(self, method.body)
    _reject_fall_off(self, method.body, method.ret, has_channel(method), method.name_span)

    _leave_body(self)


def validate_extension_method(self, ext: ExtendDef) -> None:
    """Validate types within an extension method. The declaration IS the method."""
    synthesized = ext.home_unit is not None
    _validate_target_type(self, ext.target_type, ext.target_type_span, synthesized)
    _validate_method_body(self, ext.target_type, ext, synthesized)


def validate_perk_implementation_method(self, impl: ExtendWithDef) -> None:
    """Validate a perk implementation: the contract, the header, then each method."""
    perk_def = self.perk_table.by_name.get(impl.perk_name)
    if not perk_def:
        self.err.emit(er.ERR.CE4003, impl.perk_name_span, perk=impl.perk_name)
        return

    validate_perk_implementation(impl, perk_def, self.reporter)

    # A GENERIC target resolves like a plain name, and it must: `self` needs the
    # instantiation's own StructType to read a field through, and the interned name is
    # what the table already keyed the implementation under. Leaving the
    # `GenericTypeRef` in place registered `self` as an unresolved type, so every
    # `self.field` in the body silently failed to resolve -- and a `??` beside one then
    # reached codegen unannotated, as a CE0124.
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.ast_walk import is_written
    resolved_type = impl.target_type
    if isinstance(impl.target_type, (UnknownType, GenericTypeRef)):
        resolved_type = resolve_unknown_type(
            impl.target_type, self.struct_table.by_name, self.enum_table.by_name)
    # A copy for one instantiation carries the template's header, and the template
    # is judged once, where it is written (#861).
    if resolved_type is not None and is_written(impl):
        check_no_conflicts_with_regular_methods(
            resolved_type, impl, self.extension_table, self.reporter)

    synthesized = not is_written(impl)
    _validate_target_type(self, resolved_type, impl.target_type_span, synthesized)

    for method in impl.methods:
        _validate_method_body(self, resolved_type, method, synthesized)
