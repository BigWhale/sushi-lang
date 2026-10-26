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

    def names_extension(self, name: str) -> bool:
        validator = self.validator
        if any(name in methods for methods in validator.extension_table.by_type.values()):
            return True
        if any(key[0] == name for methods in validator.generic_extension_table.by_type.values()
               for key in methods):
            return True
        return any(m.name == name for impl in validator.perk_impl_table.implementations.values()
                   for m in impl.methods)

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
