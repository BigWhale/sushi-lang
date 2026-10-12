"""What lowering asks the declaration tables about a written name (docs/design/ir.md 7.4, 7.10).

Lowering has no types. Two written shapes are told apart by a DECLARATION, never by a
type, so lowering reads the tables that the collect and namespaces passes made:

- `T.m(...)` and `T.m` on a type name construct a variant when `m` is a variant of the
  enum `T`. Otherwise `T.m(...)` calls a static method of `T`. A name behind the dot of a
  type is one of the two, never both (#542).
- `ns.f(...)` and `ns.f` on a namespace name what the namespace holds under `f`: a
  function, a generic function, a C function, a C global, a constant, a unit `var` or a
  type.
- A named struct construction gives its fields in the declaration order, so lowering
  reads the field names of the struct declaration.

The declaration table of ir.md 6.3 replaces this lookup when it exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from sushi_lang.semantics.namespaces import body_namespaces

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
    from sushi_lang.semantics.namespaces import NamespaceTable
    from sushi_lang.semantics.typesys import EnumType, StructType

#: The kinds of declaration behind a namespace that a call calls (`namespaces.Binding`).
CALLABLE_KINDS = frozenset({"function", "generic function", "extern"})

#: The kinds of declaration behind a namespace that a `GlobalRef` names: a callable, and
#: a C global of an `unsafe external` block (`libc.environ`).
GLOBAL_KINDS = CALLABLE_KINDS | {"extern variable"}

#: The kinds of declaration behind a namespace that name a type.
TYPE_KINDS = frozenset({"struct", "enum", "type"})

#: The kind that `member_kind` gives for a unit `var`. The namespace files a `var` as a
#: constant (one table holds both, `ConstSig.is_var`).
VARIABLE_KIND = "variable"


@dataclass(frozen=True, slots=True)
class DeclLookup:
    """The declaration tables of one unit: `unit` names it in `namespaces`."""
    enums: Mapping[str, EnumType]
    generic_enums: Mapping[str, GenericEnumType]
    structs: Mapping[str, StructType]
    generic_structs: Mapping[str, GenericStructType]
    namespaces: Mapping[str, NamespaceTable]
    unit: str

    def is_variant(self, type_name: str, member: str) -> bool:
        """True when `member` is a variant of the enum `type_name`."""
        enum = self.enums.get(type_name) or self.generic_enums.get(type_name)
        return enum is not None and any(variant.name == member for variant in enum.variants)

    def field_names(self, type_name: str) -> Optional[tuple[str, ...]]:
        """The field names of the struct `type_name` in the declaration order, or None."""
        struct = self.structs.get(type_name) or self.generic_structs.get(type_name)
        return None if struct is None else tuple(name for name, _ty in struct.fields)

    def member_kind(self, body: object, alias: str, member: str) -> Optional[str]:
        """The kind of the declaration that `alias.member` names in `body`, or None.

        The body of a copy of a library template names what its library unit names
        (`body_namespaces`); every other body names what its unit names.
        """
        table = body_namespaces(body, self.namespaces) or self.namespaces.get(self.unit)
        binding = None if table is None else table.lookup(alias, member)
        if binding is None:
            return None
        if binding.kind == "constant" and getattr(binding.record, "is_var", False):
            return VARIABLE_KIND
        return binding.kind
