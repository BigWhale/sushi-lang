"""Perk (trait) validation for Sushi compiler."""

from sushi_lang.semantics.ast import ExtendWithDef, PerkDef, FuncDef, PerkMethodSignature
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.passes.collect import ExtensionTable
from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er


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
    from .visibility import reject_out_of_scope_type

    # Not a LIBRARY unit, for `check_public_signatures`' reason: its declarations were
    # checked when the library was built.
    if validator.in_library_unit:
        return

    for site in signature_constraints(program):
        if site.namespace is not None:
            _reject_qualified_non_perk(validator, site)
            continue
        # A perk some unit declares and this one did not import is out of scope, not
        # missing, and CE2001 is the code that says so -- `_TYPE_KINDS` already reads
        # the perk kind. A name NO unit declares falls through to CE4003.
        if reject_out_of_scope_type(validator, site.perk_name, site.span):
            continue
        if validator.perk_table.get(site.perk_name) is None:
            er.emit(validator.reporter, er.ERR.CE4003, site.span, perk=site.perk_name)


def _reject_qualified_non_perk(validator, site) -> None:
    """CE4003 for `@(T: h.Vec)`: the alias holds the name, and it is not a perk (#733).

    The qualified path asked only whether the namespace holds a MEMBER of that name, so
    a struct, an enum, a constant or a function there was accepted and the constraint
    did nothing. The binding carries its KIND, which is the one fact the bare arm reads
    out of the perk table, so the answer is the same code with the WRITTEN name in it.

    A binding of None is the qualifier's own miss and is already reported.
    """
    from .qualified import written_name

    binding = validator.namespaces.lookup(site.namespace, site.perk_name)
    if binding is None or binding.kind == "perk":
        return
    er.emit(validator.reporter, er.ERR.CE4003, site.span,
            perk=written_name(site.namespace, site.perk_name))


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

        if not _signatures_match(impl_method, required_sig):
            er.emit(reporter, er.ERR.CE4004, impl_method.loc,
                   method=method_name, perk=perk_def.name)
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
    if perk_def is not None:
        validate_perk_implementation(impl, perk_def, validator.reporter)
        _reject_template_name_conflicts(validator, impl)


def _channel_phrase(err_type) -> str:
    """How a signature's error channel reads in a diagnostic, present or absent."""
    if err_type is None:
        return "no error channel"
    from sushi_lang.semantics.generics.type_display import display_type
    return f"the error channel '| {display_type(err_type)}'"


def _reject_channel_mismatch(impl: FuncDef, required: PerkMethodSignature,
                             perk_def: PerkDef, reporter: Reporter) -> bool:
    """CE0133: the implementation's `| E` must be the contract's `| E` (ruling R1).

    Relational in both directions -- a contract that declares a channel the
    implementation omits, and an implementation that invents one the contract has
    not got, are the same mismatch read from opposite ends.
    """
    impl_err = getattr(impl, "err_type", None)
    required_err = getattr(required, "err_type", None)
    if impl_err == required_err:
        return False

    diag = er.emit_with(
        reporter, er.ERR.CE0133,
        getattr(impl, "name_span", None) or getattr(impl, "loc", None),
        name=impl.name, found=_channel_phrase(impl_err), perk=perk_def.name,
        expected=_channel_phrase(required_err))
    contract_span = (getattr(required, "name_span", None)
                     or getattr(required, "loc", None))
    if contract_span is not None:
        diag.note_at(f"perk '{perk_def.name}' declares '{impl.name}' here", contract_span)
    diag.emit()
    return True


def _signatures_match(impl: FuncDef, required: PerkMethodSignature) -> bool:
    """Check if implementation signature matches requirement."""
    # Check receiver mode (#327)
    if getattr(impl, "self_mode", None) != getattr(required, "self_mode", None):
        return False

    if len(impl.params) != len(required.params):
        return False

    for impl_param, req_param in zip(impl.params, required.params, strict=False):
        if impl_param.ty != req_param.ty:
            return False

    if impl.ret != required.ret:
        return False

    # The channel is part of the signature (ruling R1). `_reject_channel_mismatch`
    # owns the diagnostic; the predicate stays total so no other reader of it can
    # call a mismatched pair a match.
    if getattr(impl, "err_type", None) != getattr(required, "err_type", None):
        return False

    return True


def check_no_conflicts_with_regular_methods(
    resolved_type: Type,
    perk_impl: ExtendWithDef,
    extension_table: ExtensionTable,
    reporter: Reporter
) -> bool:
    """Ensure perk methods don't conflict with regular extension methods."""
    return _reject_name_conflicts(
        perk_impl, extension_table.by_type.get(resolved_type, {}), reporter)


def _reject_template_name_conflicts(validator, impl: ExtendWithDef) -> None:
    """CE4007 for a generic-target implementation, judged on the template (#861).

    The template covers every instantiation of its base, so an extension method of the
    same name on that base -- a template or one concrete instantiation -- gives the name
    a second home.
    """
    base_name = getattr(impl.target_type, "base_name", None)
    if base_name is None:
        return
    existing = {}
    for (name, _key), method in validator.generic_extension_table.by_type.get(
            base_name, {}).items():
        existing.setdefault(name, method)
    _reject_name_conflicts(impl, existing, validator.reporter)


def validate_unreached_header(tables, impl: ExtendWithDef, reporter: Reporter) -> None:
    """The header of a perk implementation on one instantiation that no unit reaches (#898).

    Such an implementation is dropped before the typecheck pass, and the header is
    judged here on the way out: the contract check, and CE4007 against the extension
    method that applies to the same instantiation, a concrete one or a template. A
    reached one is judged in the typecheck pass, so the two answers agree.
    """
    from sushi_lang.semantics.generics.extension_targets import instantiation_key
    perk_def = tables.perks.by_name.get(impl.perk_name)
    if perk_def is None:
        return
    validate_perk_implementation(impl, perk_def, reporter)
    target = impl.target_type
    key = instantiation_key(target.base_name, tuple(target.type_args))
    existing = {}
    for method in impl.methods:
        found = tables.generic_extensions.find_applicable(target.base_name, method.name, key)
        if found is not None:
            existing[method.name] = found
    _reject_name_conflicts(impl, existing, reporter)


def _reject_name_conflicts(perk_impl: ExtendWithDef, existing_methods: dict,
                           reporter: Reporter) -> bool:
    """One CE4007 for each perk method that an extension method of one name meets."""
    conflicts = {m.name for m in perk_impl.methods} & set(existing_methods)
    if not conflicts:
        return True

    for method in perk_impl.methods:
        if method.name in conflicts:
            # Relational: the perk method is only a conflict BECAUSE the extension
            # exists. Point at it -- the table already carries its span.
            existing = existing_methods[method.name]
            diag = er.emit_with(reporter, er.ERR.CE4007, method.loc,
                                method=method.name, perk=perk_impl.perk_name)
            prev_span = existing.name_span or existing.loc
            if prev_span is not None:
                diag.note_at(f"extension method '{method.name}' is defined here", prev_span)
            diag.emit()

    return False
