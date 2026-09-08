"""The tables of compiler-defined methods: the process's, and one compilation's."""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.sushi_stdlib.src.common import BuiltinMethod


class BuiltinMethodRegistry:
    """A table of compiler-defined extension methods, keyed by target type."""

    def __init__(self) -> None:
        self._methods: Dict['Type', Dict[str, 'BuiltinMethod']] = {}

    def register_method(self, target_type: 'Type', method: 'BuiltinMethod') -> None:
        """Register a compiler-defined method for a type."""
        self._methods.setdefault(target_type, {})[method.name] = method

    def get_method(self, target_type: 'Type',
                   method_name: str) -> Optional['BuiltinMethod']:
        """The method registered for this type under this name, or None."""
        return self._methods.get(target_type, {}).get(method_name)


# What the compiler defines for every program: hash, to_str, to_bits and clone on the
# primitives (backend/types/primitives/). Each is registered once, at import time, and
# each emitter closes over a BuiltinType -- nothing a program can change -- so one table
# serves the process.
builtin_registry = BuiltinMethodRegistry()


class DerivedMethodTable(BuiltinMethodRegistry):
    """The hash() and clone() the `derive` pass wrote for ONE compilation (#601).

    A derived method closes over the type it was derived for, and type identity is
    nominal, so two programs that each declare a `Point` name one key. While this table
    was the module's, the second program compiled in a process was handed the first
    one's emitter -- closed over the first one's fields -- and the first one's answer to
    "can this type be hashed".

    The compilation owns one (`SymbolTables.derived_methods`). A lookup that misses
    falls through to the process-wide built-ins, so one call answers both.
    """

    def get_method(self, target_type: 'Type',
                   method_name: str) -> Optional['BuiltinMethod']:
        """This compilation's derived method, else the process-wide built-in."""
        own = super().get_method(target_type, method_name)
        if own is not None:
            return own
        return builtin_registry.get_method(target_type, method_name)
