"""The tables that instantiation collection reads (ruling E4: it types no expression)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


@dataclass
class ScanTables:
    """The written type of each local in scope, and the struct, enum and function tables."""
    variable_types: dict[str, "Type"]
    struct_table: dict
    enum_table: dict
    func_table: dict = field(default_factory=dict)
