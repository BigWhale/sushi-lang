"""Perk (trait) validation for Sushi compiler."""

from sushi_lang.semantics.ast import ExtendWithDef, PerkDef, FuncDef, PerkMethodSignature
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.typesys import DynamicArrayType, ReceiverType, Type
from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables


def check_constraint_perks(validator, program) -> None:
    """Every `@(T: P)` this unit writes names a perk that exists and is reachable.

    Reads `signature_constraints()`, the one walk over a unit's constraint names. The
    name was recorded by the collect pass and measured against nothing, so a constraint
    naming no perk at all compiled clean (#505).

    A QUALIFIED constraint reads the same rule one alias out (#733), and it asks the
    namespace seam because a name behind an alias never enters the flat scope the bare
    arm measures against. `check_qualified_constraints` has run already and owns the
    two refusals above this one -- an alias that is bound to nothing, and an alias that
    does not hold the name -- so a site it refused is silent here.
    """
    from sushi_lang.semantics.ast_walk import signature_constraints
    from .visibility import reject_out_of_scope_perk

    # Not a LIBRARY unit, for `check_public_signatures`' reason: its declarations were
    # checked when the library was built.
    if validator.in_library_unit:
        return

    for site in signature_constraints(program):
        if site.namespace is not None:
            _reject_qualified_non_perk(validator, site.namespace, site.perk_name,
                                       site.span)
            continue
        # A perk some unit declares and this one cannot name is CE4003 with the import
        # in the help, as at an implementation (#1124). A name NO unit declares is
        # CE4003 with no help.
        if reject_out_of_scope_perk(validator, site.perk_name, site.span):
            continue
        if validator.perk_table.get(site.perk_name) is None:
            er.emit(validator.reporter, er.ERR.CE4003, site.span, perk=site.perk_name)


def reject_unnamable_implemented_perk(validator, impl: ExtendWithDef) -> bool:
    """The perk of a WRITTEN implementation is in this unit's scope (#1124).

    The rule of a constraint, at the other perk position: a bare name must be in the
    unit's own scope, and a qualified one must be a perk behind a bound alias. A perk
    that no unit declares is the collect pass's CE4003, so it is not asked here.
    """
    from .qualified import reject_qualified_name
    from .visibility import reject_out_of_scope_perk

    if impl.perk_namespace is None:
        return reject_out_of_scope_perk(validator, impl.perk_name, impl.perk_name_span)
    if reject_qualified_name(validator, impl.perk_namespace, impl.perk_name,
                             impl.perk_name_span, kind="perk"):
        return True
    return _reject_qualified_non_perk(validator, impl.perk_namespace, impl.perk_name,
                                      impl.perk_name_span)


def _reject_qualified_non_perk(validator, namespace: str, name: str, span) -> bool:
    """CE4003 for `@(T: h.Vec)`: the alias holds the name, and it is not a perk (#733).

    The qualified path asked only whether the namespace holds a MEMBER of that name, so
    a struct, an enum, a constant or a function there was accepted and the constraint
    did nothing. The binding carries its KIND, which is the one fact the bare arm reads
    out of the perk table, so the answer is the same code with the WRITTEN name in it.

    A binding of None is the qualifier's own miss and is already reported.
    """
    from .qualified import written_name

    binding = validator.namespaces.lookup(namespace, name)
    if binding is None or binding.kind == "perk":
        return False
    er.emit(validator.reporter, er.ERR.CE4003, span, perk=written_name(namespace, name))
    return True


def validate_perk_implementation(
    impl: ExtendWithDef,
    perk_def: PerkDef,
    reporter: Reporter
) -> bool:
    """Validate that an implementation satisfies a perk's requirements.

    A copy the compiler cut for one instantiation of a generic target is not judged:
    its header is the template's, and `validate_template_header` judges the template
    once, where the source wrote it (#811).
    """
    from sushi_lang.semantics.ast_walk import is_written
    if not is_written(impl):
        return True

    implemented_methods = {m.name: m for m in impl.methods}
    required_methods = {m.name: m for m in perk_def.methods}

    missing = set(required_methods.keys()) - set(implemented_methods.keys())
    if missing:
        for method_name in missing:
            er.emit(reporter, er.ERR.CE4005, impl.loc,
                   method=method_name, perk=perk_def.name)
        return False

    valid = True
    for method_name, impl_method in implemented_methods.items():
        if method_name not in required_methods:
            continue

        required_sig = required_methods[method_name]
        # The channel arm answers FIRST and with its own code. CE4004 says "the
        # signature does not match" and points nowhere; a channel mismatch is
        # relational, and CE0133 is the code that carries the note.
        if _reject_channel_mismatch(impl_method, required_sig, perk_def, reporter):
            valid = False
            continue

        if not _signatures_match(impl_method, required_sig, impl.target_type):
            diag = er.emit_with(reporter, er.ERR.CE4004, impl_method.loc,
                                method=method_name, perk=perk_def.name)
            if _names_receiver(required_sig):
                diag.help(f"'{perk_def.name}' requires "
                          f"{_contract_spelling(required_sig, impl.target_type)}")
            diag.emit()
            valid = False

    return valid


def validate_template_header(validator, impl: ExtendWithDef) -> None:
    """The contract check on a generic-target perk implementation as WRITTEN (#811).

    Each instantiation gets its own copy of the implementation, and every copy carries
    the template's header. Judged on the copies, one written method reported its fault
    once per instantiation, and a template with no instantiation was never judged.

    The name-conflict check is part of the header, so it is judged here too (#861).

    A perk that does not exist is refused where the copies are read, so it is not
    judged here.
    """
    perk_def = validator.perk_table.by_name.get(impl.perk_name)
    if perk_def is not None and not reject_unnamable_implemented_perk(validator, impl):
        validate_perk_implementation(impl, perk_def, validator.reporter)
        existing = _template_extensions_beside(impl, validator.tables)
        if existing is not None:
            _reject_name_conflicts(impl, existing, validator.reporter)


def _answer(sig) -> tuple:
    """What a signature answers: (has a channel, the value arm, the error arm).

    `T | E` is sugar for `Result@(T, E)`, so both spellings give the same answer, and
    a bare signature answers its return with no error arm. The channel is read by the
    one predicate (docs/design/error-channel.md).
    """
    from sushi_lang.semantics.channel import has_channel
    from sushi_lang.semantics.generics.results import signature_result_arms
    if not has_channel(sig):
        return False, sig.ret, None
    arms = signature_result_arms(sig.ret, getattr(sig, "err_type", None))
    if arms is None:
        return True, sig.ret, None
    return True, arms[0], arms[1]


def _channel_phrase(answer: tuple) -> str:
    """How a signature's error channel reads in a diagnostic, present or absent."""
    if not answer[0]:
        return "no error channel"
    if answer[2] is None:
        return "an error channel"
    from sushi_lang.semantics.generics.type_display import display_type
    return f"the error channel '| {display_type(answer[2])}'"


def _reject_channel_mismatch(impl: FuncDef, required: PerkMethodSignature,
                             perk_def: PerkDef, reporter: Reporter) -> bool:
    """CE0133: the implementation's channel must be the contract's channel (ruling R1).

    Relational in both directions -- a contract that declares a channel the
    implementation omits, and an implementation that invents one the contract has
    not got, are the same mismatch read from opposite ends. Either spelling of the
    channel counts, `| E` or an explicit `Result@(T, E)`.
    """
    impl_answer = _answer(impl)
    required_answer = _answer(required)
    if (impl_answer[0], impl_answer[2]) == (required_answer[0], required_answer[2]):
        return False

    diag = er.emit_with(
        reporter, er.ERR.CE0133,
        getattr(impl, "name_span", None) or getattr(impl, "loc", None),
        name=impl.name, found=_channel_phrase(impl_answer), perk=perk_def.name,
        expected=_channel_phrase(required_answer))
    contract_span = (getattr(required, "name_span", None)
                     or getattr(required, "loc", None))
    if contract_span is not None:
        diag.note_at(f"perk '{perk_def.name}' declares '{impl.name}' here", contract_span)
    diag.emit()
    return True


def _names_receiver(required: PerkMethodSignature) -> bool:
    """Does the contract name the implementing type (a predefined perk's `Self`)?"""
    return any(isinstance(p.ty, ReceiverType) for p in required.params)


def _contract_spelling(required: PerkMethodSignature, target: Type) -> str:
    """The contract as the implementation must write it, with the target filled in."""
    from sushi_lang.semantics.generics.type_display import display_type
    params = ", ".join(
        f"{display_type(target if isinstance(p.ty, ReceiverType) else p.ty)} {p.name}"
        for p in required.params)
    return f"fn {required.name}({params}) {display_type(required.ret)}"


def _same_param_type(impl_ty, required_ty, target) -> bool:
    """One parameter against the contract's; the placeholder is the implementation's
    target, compared by the key both sides file under, since the target may still
    spell a name that the parameter already resolved (or the other way round)."""
    if not isinstance(required_ty, ReceiverType):
        return impl_ty == required_ty
    from sushi_lang.semantics.passes.collect.perks import _get_type_name
    return impl_ty == target or (
        target is not None and _get_type_name(impl_ty) == _get_type_name(target))


def _signatures_match(impl: FuncDef, required: PerkMethodSignature,
                      target: Optional[Type] = None) -> bool:
    """Check if implementation signature matches requirement."""
    # Check receiver mode (#327)
    if getattr(impl, "self_mode", None) != getattr(required, "self_mode", None):
        return False

    if len(impl.params) != len(required.params):
        return False

    for impl_param, req_param in zip(impl.params, required.params, strict=False):
        if not _same_param_type(impl_param.ty, req_param.ty, target):
            return False

    impl_answer = _answer(impl)
    required_answer = _answer(required)
    if impl_answer[1] != required_answer[1]:
        return False

    # The channel is part of the signature (ruling R1). `_reject_channel_mismatch`
    # owns the diagnostic; the predicate stays total so no other reader of it can
    # call a mismatched pair a match.
    if (impl_answer[0], impl_answer[2]) != (required_answer[0], required_answer[2]):
        return False

    return True


def check_no_conflicts_with_regular_methods(
    resolved_type: Type,
    perk_impl: ExtendWithDef,
    tables: 'SymbolTables',
    reporter: Reporter
) -> bool:
    """Ensure perk methods don't conflict with regular extension methods.

    A generic-target method has no row until a call cuts its copy, so the one lookup of
    the templates that give the instance the method answers too (#1196), as it does in
    `validate_unreached_header`.
    """
    return _reject_name_conflicts(
        perk_impl, _extensions_beside(resolved_type, perk_impl, tables), reporter)


def reject_extensions_beside_shipped_impl(impl: ExtendWithDef, tables: 'SymbolTables',
                                          reporter: Reporter, template: bool) -> None:
    """CE4007 for an extension method beside a perk implementation a library ships (#1255).

    A binary or hybrid library ships its implementation as a manifest record, so no
    written `extend ... with` in the build meets the check of a written one. Its methods
    are visible in every unit all the same, so the extension method of that name on that
    type can never be called, and the error stands at it. A `template` is judged as a
    written template is, against every extension on its base.
    """
    if template:
        existing = _template_extensions_beside(impl, tables)
    else:
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        target = resolve_unknown_type(impl.target_type, tables.structs.by_name,
                                      tables.enums.by_name)
        existing = None if target is None else _extensions_beside(target, impl, tables)
    if existing is not None:
        _reject_name_conflicts(impl, existing, reporter, shipped=True)


def _extensions_beside(target, perk_impl: ExtendWithDef, tables: 'SymbolTables') -> dict:
    """The extension method of each perk method's name that applies to `target`.

    A generic-target method has no row until a call cuts its copy, so the one lookup of
    the templates that give the instance the method answers too (#1196). A target that
    is still a `GenericTypeRef` names an instantiation no unit reaches (#898), and the
    template that applies to its key answers.
    """
    from sushi_lang.semantics.generics.extension_targets import instantiation_key
    from sushi_lang.semantics.generics.extensions import target_methods_of
    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(target, GenericTypeRef):
        key = instantiation_key(target.base_name, tuple(target.type_args))
        existing = {}
        for method in perk_impl.methods:
            found = tables.generic_extensions.find_applicable(target.base_name, method.name,
                                                              key)
            if found is not None:
                existing[method.name] = found
        return existing
    existing = dict(tables.extensions.first_by_name(target))
    for method in perk_impl.methods:
        if method.name not in existing:
            found = target_methods_of(tables.generic_extensions, target, method.name, tables)
            if found:
                existing[method.name] = found[0]
    return existing


def _template_extensions_beside(impl: ExtendWithDef, tables: 'SymbolTables') -> Optional[dict]:
    """The extension methods a template implementation meets, by name (#861, #699).

    The template covers every instantiation of its base, so an extension method of the
    same name on that base -- a template or one concrete instantiation -- gives the name
    a second home. An array template's base is every dynamic array, so an extension on
    one concrete array type is on it too.
    """
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY
    is_array = isinstance(impl.target_type, DynamicArrayType)
    base_name = (ARRAY_BASE_KEY if is_array
                 else getattr(impl.target_type, "base_name", None))
    if base_name is None:
        return None
    existing: dict = {}
    for (name, _key, _unit), method in tables.generic_extensions.by_type.get(
            base_name, {}).items():
        existing.setdefault(name, method)
    if is_array:
        for target in tables.extensions.by_type:
            if isinstance(target, DynamicArrayType):
                for name, method in tables.extensions.first_by_name(target).items():
                    existing.setdefault(name, method)
    return existing


def validate_unreached_header(tables, impl: ExtendWithDef, reporter: Reporter) -> None:
    """The header of a perk implementation on one instantiation that no unit reaches (#898).

    Such an implementation is dropped before the typecheck pass, and the header is
    judged here on the way out: the contract check, and CE4007 against the extension
    method that applies to the same instantiation, a concrete one or a template. A
    reached one is judged in the typecheck pass, so the two answers agree.
    """
    perk_def = tables.perks.by_name.get(impl.perk_name)
    if perk_def is None:
        return
    validate_perk_implementation(impl, perk_def, reporter)
    _reject_name_conflicts(impl, _extensions_beside(impl.target_type, impl, tables), reporter)


def _reject_name_conflicts(perk_impl: ExtendWithDef, existing_methods: dict,
                           reporter: Reporter, shipped: bool = False) -> bool:
    """One CE4007 for each perk method that an extension method of one name meets.

    A `shipped` implementation has no source in the build, so the error stands at the
    extension method, and a prose note names the implementation (#1255).
    """
    conflicts = {m.name for m in perk_impl.methods} & set(existing_methods)
    if not conflicts:
        return True

    for method in perk_impl.methods:
        if method.name in conflicts:
            existing = existing_methods[method.name]
            if shipped:
                from sushi_lang.semantics.generics.type_display import display_type
                er.emit_with(reporter, er.ERR.CE4007, existing.name_span or existing.loc,
                             filename=getattr(existing, "filename", None),
                             method=method.name, perk=perk_impl.perk_name).note(
                    f"a compiled library implements '{perk_impl.perk_name}' for "
                    f"'{display_type(perk_impl.target_type)}', and its method "
                    f"'{method.name}' is visible in every unit").emit()
                continue
            # Relational: the perk method is only a conflict BECAUSE the extension
            # exists. Point at it -- the table already carries its span.
            diag = er.emit_with(reporter, er.ERR.CE4007, method.loc,
                                method=method.name, perk=perk_impl.perk_name)
            prev_span = existing.name_span or existing.loc
            if prev_span is not None:
                diag.note_at(f"extension method '{method.name}' is defined here", prev_span,
                             filename=getattr(existing, "filename", None))
            diag.emit()

    return False
