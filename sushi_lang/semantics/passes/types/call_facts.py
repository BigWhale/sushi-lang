"""The facts that a stop of the typecheck pass at a `Call` gives to the label of the stop.

`dispatch.construct_of` is the one place that makes the label. This class answers its
questions from the tables of the pass, in the same order as the call arms read them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.ffi_boundary import ERRNO_FUNCTION
from sushi_lang.semantics.passes.types.calls.user_defined import own_concrete_function
from sushi_lang.semantics.shir.ids import DeclId

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


class TypecheckCallFacts:
    """The call facts of one typecheck pass."""

    def __init__(self, validator: TypeValidator) -> None:
        self.validator = validator

    def written_name(self, decl: DeclId) -> str:
        return self.validator.decl_name(decl)

    def declaring_units(self, name: str) -> frozenset[Optional[str]]:
        """The units that declare an extension or a perk method of this name.

        The answer reads the name alone, because the receiver type is not known at the
        stop: a user extension of a user type that reuses a built-in method name gives a
        built-in call of that name the label `extension`. This is a known limit.
        """
        validator = self.validator
        units: set[Optional[str]] = set()
        for methods in validator.extension_table.by_type.values():
            units.update(record.unit_name for record in methods.get(name, ()))
        for table in validator.generic_extension_table.by_type.values():
            units.update(unit for (method, _key, unit) in table if method == name)
        perks = validator.perk_impl_table
        units.update(perks.units.get(key) for key, impl in perks.implementations.items()
                     if any(m.name == name for m in impl.methods))
        return frozenset(units)

    def namespace_kind(self, alias: str, name: str) -> Optional[str]:
        binding = self.validator.namespaces.lookup(alias, name)
        if binding is None or binding.kind != "function":
            return None
        return binding.provider.namespace_kind

    def is_generic_function(self, name: str) -> bool:
        validator = self.validator
        own = own_concrete_function(validator.func_table, name, validator.current_unit_name)
        return own is None and validator.generic_sig(name) is not None

    def is_stdlib_function(self, name: str) -> bool:
        validator = self.validator
        return (validator.func_sig(name) is None
                and validator.func_table.lookup_stdlib_by_name(name, validator.scope) is not None)

    def is_extern_function(self, name: str) -> bool:
        return name == ERRNO_FUNCTION
