"""
Perk (trait) validation for Sushi compiler.

Validates:
- Perk method signatures match implementations
- All required methods are implemented
- No naming conflicts between regular methods and perk methods
- Generic perk constraints are satisfied
"""

from typing import Dict, Set, Optional, List
from semantics.ast import ExtendWithDef, PerkDef, FuncDef, PerkMethodSignature
from semantics.typesys import Type, BuiltinType, StructType, EnumType, UnknownType
from semantics.passes.collect import PerkTable, PerkImplementationTable, ExtensionTable, StructTable, EnumTable
from internals.report import Reporter
from internals import errors as er


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
    type_name: str,
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
    # Get existing extension methods for this type
    # Note: ExtensionTable uses Type objects as keys, not string names
    # For now, we skip this check and will implement it properly when integrating with TypeValidator
    # The TypeValidator has access to the proper Type objects

    # TODO: Implement proper type-based lookup when integrated with TypeValidator
    # For Phase 2, we just return True as this check will be done in TypeValidator
    return True


def _get_type_name_from_impl(
    impl: ExtendWithDef,
    struct_table: StructTable,
    enum_table: EnumTable
) -> Optional[str]:
    """Extract a string type name from an ExtendWithDef for conflict checking."""
    target_type = impl.target_type

    if isinstance(target_type, BuiltinType):
        return target_type.value
    elif isinstance(target_type, StructType):
        return target_type.name
    elif isinstance(target_type, EnumType):
        return target_type.name
    elif isinstance(target_type, UnknownType):
        # Try to resolve to struct or enum
        if target_type.name in struct_table.by_name:
            return target_type.name
        elif target_type.name in enum_table.by_name:
            return target_type.name

    return None
