"""Perk (trait) validation for Sushi compiler."""

from sushi_lang.semantics.ast import ExtendWithDef, PerkDef, FuncDef, PerkMethodSignature
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.passes.collect import ExtensionTable
from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er


def check_constraint_perks(validator, program) -> None:
    """Every bare `@(T: P)` this unit writes names a perk that exists and is reachable.

    Reads `signature_constraints()`, the one walk over a unit's constraint names. The
    name was recorded by the collect pass and measured against nothing, so a constraint
    naming no perk at all compiled clean (#505).

    A QUALIFIED constraint is skipped: `check_qualified_constraints` has already asked
    whether the namespace holds the name, and a name behind an alias never enters the
    flat scope that the rule below measures against.
    """
    from sushi_lang.semantics.ast_walk import signature_constraints
    from .visibility import reject_out_of_scope_type

    # Not a LIBRARY unit, for `check_public_signatures`' reason: its declarations were
    # checked when the library was built.
    if validator.in_library_unit:
        return

    for site in signature_constraints(program):
        if site.namespace is not None:
            continue
        # A perk some unit declares and this one did not import is out of scope, not
        # missing, and CE2001 is the code that says so -- `_TYPE_KINDS` already reads
        # the perk kind. A name NO unit declares falls through to CE4003.
        if reject_out_of_scope_type(validator, site.perk_name, site.span):
            continue
        if validator.perk_table.get(site.perk_name) is None:
            er.emit(validator.reporter, er.ERR.CE4003, site.span, perk=site.perk_name)


def validate_perk_implementation(
    impl: ExtendWithDef,
    perk_def: PerkDef,
    reporter: Reporter
) -> bool:
    """Validate that an implementation satisfies a perk's requirements."""
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
        if not _signatures_match(impl_method, required_sig):
            er.emit(reporter, er.ERR.CE4004, impl_method.loc,
                   method=method_name, perk=perk_def.name)
            valid = False

    return valid


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

    return True


def check_no_conflicts_with_regular_methods(
    resolved_type: Type,
    perk_impl: ExtendWithDef,
    extension_table: ExtensionTable,
    reporter: Reporter
) -> bool:
    """Ensure perk methods don't conflict with regular extension methods."""
    existing_methods = extension_table.by_type.get(resolved_type, {})
    if not existing_methods:
        return True

    perk_method_names = {m.name for m in perk_impl.methods}
    conflicts = perk_method_names & set(existing_methods.keys())

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
                diag.note(f"extension method '{method.name}' is defined here", prev_span)
            diag.emit()

    return False
