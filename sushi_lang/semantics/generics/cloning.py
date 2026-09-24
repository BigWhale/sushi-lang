"""Auto-derived clone() method registration (#134)."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import EnumType, StructType, Type
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.generics.builtin_methods import derived_method
from sushi_lang.sushi_stdlib.src.common import get_clone_emitter_factory

# Named StructTypes that own heap through their own registries/method paths; a top-level
# .clone() on these must fall through, not route through the auto-derived struct clone.
# Also the one authority on "is this named struct a container?" for method-type inference
# (passes/types/method_registry.py) -- the derive pass's hash registration has no such exclusion,
# so a List<i32> monomorph does carry a registered hash that the typecheck pass nonetheless rejects.
CONTAINER_PREFIXES = ("Own<", "List<", "HashMap<")


#: The derived `clone` takes no argument; the typecheck pass reads this row (CE2009).
DERIVED_CLONE_ARITY: Mapping[str, int] = MappingProxyType({"clone": 0})


def _add_clone(target_type: Type, derived: DerivedMethodTable, kind: str) -> None:
    """Register the derived clone() of a type, unless the type already has one."""
    if derived.get_method(target_type, "clone") is None:
        derived.register_method(target_type, derived_method(
            target_type, name="clone", kind=kind, return_type=target_type,
            factory_getter=get_clone_emitter_factory, ice=er.ERR.CE0127))


def register_struct_clone_method(struct_type: StructType,
                                 derived: DerivedMethodTable) -> None:
    """Register the auto-derived clone() method for a user struct type.

    `Own`, `List` and `HashMap` get NO entry, and the absence is deliberate at both
    ends: each keeps its own method path, and the backend asks for a struct clone with
    `exclude_containers=True`. A census of the derived table therefore reads those three
    as empty by design and not as a registration that went missing (#720).
    """
    if struct_type.name.startswith(CONTAINER_PREFIXES):
        return
    _add_clone(struct_type, derived, "struct")


def register_enum_clone_method(enum_type: EnumType,
                               derived: DerivedMethodTable) -> None:
    """Register the auto-derived clone() method for an enum type."""
    _add_clone(enum_type, derived, "enum")
