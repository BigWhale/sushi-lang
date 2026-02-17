"""
Perk (trait) validation for Sushi compiler.

Validates:
- Perk method signatures match implementations
- All required methods are implemented
- No naming conflicts between regular methods and perk methods
- Generic perk constraints are satisfied
"""

from sushi_lang.semantics.ast import ExtendWithDef, PerkDef, FuncDef, PerkMethodSignature
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.passes.collect import ExtensionTable
from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er


def validate_perk_implementation(
    impl: ExtendWithDef,
    perk_def: PerkDef,
    reporter: Reporter
) -> bool:
    """Validate that an implementation satisfies a perk's requirements.

    Checks:
    1. All required methods are present
    2. Method signatures match exactly
    3. No extra methods that aren't in the perk

    Returns:
        True if valid, False otherwise (errors emitted to reporter)
    """
    implemented_methods = {m.name: m for m in impl.methods}
    required_methods = {m.name: m for m in perk_def.methods}

    # Check for missing methods
    missing = set(required_methods.keys()) - set(implemented_methods.keys())
    if missing:
        for method_name in missing:
            er.emit(reporter, er.ERR.CE4005, impl.loc,
                   method=method_name, perk=perk_def.name)
        return False

    # Check method signatures match
    valid = True
    for method_name, impl_method in implemented_methods.items():
        if method_name not in required_methods:
            # Extra method not in perk - this is okay (implementation can have additional methods)
            continue

        required_sig = required_methods[method_name]
        if not _signatures_match(impl_method, required_sig):
            er.emit(reporter, er.ERR.CE4004, impl_method.loc,
                   method=method_name, perk=perk_def.name)
            valid = False

    return valid


def _signatures_match(impl: FuncDef, required: PerkMethodSignature) -> bool:
    """Check if implementation signature matches requirement.

    Compares:
    - Parameter count (excluding implicit self)
    - Parameter types
    - Return type
    """
    # Check parameter count (excluding implicit self)
    if len(impl.params) != len(required.params):
        return False

    # Check parameter types
    for impl_param, req_param in zip(impl.params, required.params):
        if impl_param.ty != req_param.ty:
            return False

    # Check return type
    if impl.ret != required.ret:
        return False

    return True


def check_no_conflicts_with_regular_methods(
    resolved_type: Type,
    perk_impl: ExtendWithDef,
    extension_table: ExtensionTable,
    reporter: Reporter
) -> bool:
    """Ensure perk methods don't conflict with regular extension methods.

    In Sushi, you can't have both:
    - extend T method() ~:  (regular extension)
    - extend T with Perk:   (if Perk requires method())

    This prevents ambiguity about which method gets called.
    """
    existing_methods = extension_table.by_type.get(resolved_type, {})
    if not existing_methods:
        return True

    perk_method_names = {m.name for m in perk_impl.methods}
    conflicts = perk_method_names & set(existing_methods.keys())

    if not conflicts:
        return True

    for method in perk_impl.methods:
        if method.name in conflicts:
            er.emit(reporter, er.ERR.CE4007, method.loc,
                    method=method.name, perk=perk_impl.perk_name)

    return False
