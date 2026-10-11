"""What lowering asks the declaration tables about a written name (docs/design/ir.md 7.4, 7.10).

Lowering has no types. Two written shapes are told apart by a DECLARATION, never by a
type, so lowering reads the tables that the collect and namespaces passes made:

- `T.m(...)` on a type name constructs a variant when `m` is a variant of the enum `T`,
  and calls a static method of `T` otherwise. A name behind the dot of a type is one of
  the two, never both (#542).
- `ns.f(...)` on a namespace calls what the namespace holds under `f`: a function, a
  generic function or a C function, or a declaration that a call does not call.

The declaration table of ir.md 6.3 replaces this lookup when it exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from sushi_lang.semantics.namespaces import body_namespaces

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.types import GenericEnumType
    from sushi_lang.semantics.namespaces import NamespaceTable
    from sushi_lang.semantics.typesys import EnumType


@dataclass(frozen=True, slots=True)
class DeclLookup:
    """The declaration tables of one unit: `unit` names it in `namespaces`."""
    enums: Mapping[str, EnumType]
    generic_enums: Mapping[str, GenericEnumType]
    namespaces: Mapping[str, NamespaceTable]
    unit: str

    def is_variant(self, type_name: str, member: str) -> bool:
        """True when `member` is a variant of the enum `type_name`."""
        enum = self.enums.get(type_name) or self.generic_enums.get(type_name)
        return enum is not None and any(variant.name == member for variant in enum.variants)

    def member_kind(self, body: object, alias: str, member: str) -> Optional[str]:
        """The kind of the declaration that `alias.member` names in `body`, or None.

        The body of a copy of a library template names what its library unit names
        (`body_namespaces`); every other body names what its unit names.
        """
        table = body_namespaces(body, self.namespaces) or self.namespaces.get(self.unit)
        binding = None if table is None else table.lookup(alias, member)
        return None if binding is None else binding.kind
