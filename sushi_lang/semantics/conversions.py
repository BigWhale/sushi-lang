"""The conversion seam: which declared conversion turns one error type into another.

docs/design/error-conversion.md sections 3 and 8.2. `extend <Source> as <Target>:` is a
declaration, and the collect pass files it here, keyed by the pair of type NAMES. The
source and the target are non-generic error types (C6), so a pair of names is the whole
identity. A conversion is found by its pair, never by a name: an import does not bring it
and does not hide it (3.8).

`find_conversion` is the ONE reader. The `??` check and the cast check call it, and
nothing else reads the table (`tests/unit/test_conversion_lookup_is_one.py`). A writer
calls `ConversionTable.file`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import CONVERSION_METHOD
from sushi_lang.semantics.typesys import EnumType, ReferenceType, UnknownType

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.typesys import Type


@dataclass(frozen=True)
class Conversion:
    """One declared conversion: the pair, the symbol of its body, and where it is written."""

    source: EnumType
    target: EnumType
    # The link symbol of the body, `extension_symbol(source, "as", (target,))`.
    symbol: str
    unit_name: Optional[str] = None
    filename: Optional[str] = None
    name_span: Optional[Span] = None


def conversion_symbol(source: 'Type', target: 'Type') -> str:
    """The symbol of the body of a conversion: the extension symbol of `as`, one per target."""
    from sushi_lang.semantics.generics.name_mangling import (
        extension_receiver_name, extension_symbol)
    return extension_symbol(extension_receiver_name(source), CONVERSION_METHOD, (target,))


@dataclass
class ConversionTable:
    """Every conversion of the program, keyed `(source name, target name)`."""

    _by_pair: dict[tuple[str, str], Conversion] = field(default_factory=dict)

    def file(self, conversion: Conversion) -> Optional[Conversion]:
        """File one conversion. Answers the FIRST declaration when the pair has one already.

        The first declaration is kept, as every symbol table keeps it. The caller reports
        the second one as a duplicate (CE0101).
        """
        key = (conversion.source.name, conversion.target.name)
        first = self._by_pair.get(key)
        if first is not None:
            return first
        self._by_pair[key] = conversion
        return None

    def _lookup(self, source: str, target: str) -> Optional[Conversion]:
        return self._by_pair.get((source, target))


def _error_type(ty: Optional['Type'], tables: 'SymbolTables') -> Optional[EnumType]:
    """The non-generic error type `ty` names, or None when it cannot be a side of a pair."""
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if isinstance(ty, UnknownType):
        ty = tables.enums.by_name.get(ty.name)
    if isinstance(ty, EnumType) and ty.is_error and not ty.generic_args:
        return ty
    return None


def find_conversion(tables: 'SymbolTables', source: Optional['Type'],
                    target: Optional['Type']) -> Optional[Conversion]:
    """The declared conversion that turns `source` into `target`, or None.

    The lookup is an exact match on the pair (C4): a chain is never followed, so the
    answer can never be ambiguous. An identity pair has no declaration (CE2521), so it
    answers None.
    """
    source_error = _error_type(source, tables)
    target_error = _error_type(target, tables)
    if source_error is None or target_error is None:
        return None
    return tables.conversions._lookup(source_error.name, target_error.name)
