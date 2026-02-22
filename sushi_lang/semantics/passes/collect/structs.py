# semantics/passes/collect/structs.py
"""Struct definition collection for Phase 0."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR
from sushi_lang.semantics.ast import StructDef, Program, BoundedTypeParam
from sushi_lang.semantics.typesys import Type, StructType
from sushi_lang.semantics.generics.types import GenericStructType, TypeParameter

from .utils import extract_type_param_names


@dataclass
class StructTable:
    """Table of struct types collected in Phase 0."""
    by_name: Dict[str, StructType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)


@dataclass
class GenericStructTable:
    """Table of generic struct types collected in Phase 0.

    Generic structs are struct definitions with type parameters (e.g., Pair<T, U>).
    They are stored separately from concrete structs because they need to be
    instantiated with concrete type arguments during monomorphization.
    """
    by_name: Dict[str, GenericStructType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)


class StructCollector:
    """Collector for struct definitions.

    Collects both regular and generic struct definitions during Phase 0, validating:
    - No duplicate names (across regular and generic namespaces)
    - No duplicate field names within a struct
    - All fields have explicit type annotations

    Generic structs are stored separately for later monomorphization.
    """

    def __init__(
        self,
        reporter: Reporter,
        structs: StructTable,
        generic_structs: GenericStructTable,
        known_types: Set[Type]
    ) -> None:
        """Initialize struct collector.

        Args:
            reporter: Error reporter
            structs: Shared regular struct table to populate
            generic_structs: Shared generic struct table to populate
            known_types: Set of known types for registration
        """
        self.r = reporter
        self.structs = structs
        self.generic_structs = generic_structs
        self.known_types = known_types

    def collect(self, root: Program) -> None:
        """Collect all struct definitions from program AST.

        Args:
            root: Program AST node
        """
        structs = getattr(root, "structs", None)
        if isinstance(structs, list):
            for struct in structs:
                if isinstance(struct, StructDef):
                    self._collect_struct_def(struct)

    def _collect_struct_def(self, struct: StructDef) -> None:
        """Collect struct definition and create StructType or GenericStructType.

        Args:
            struct: Struct definition AST node
        """
        name = getattr(struct, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(struct, "name_span", None) or getattr(struct, "loc", None)

        # Check if this struct has type parameters (e.g., struct Pair<T, U>:)
        type_params_raw = getattr(struct, "type_params", None)
        type_params: Optional[List[str]] = extract_type_param_names(type_params_raw)

        # Check for duplicate struct names (both regular and generic namespaces)
        if name in self.structs.by_name:
            er.emit(self.r, ERR.CE0004, name_span, name=name)
            return

        if name in self.generic_structs.by_name:
            er.emit_with(self.r, ERR.CE0004, name_span, name=name) \
                .note("predefined as generic struct").emit()
            return

        # Collect struct fields
        fields_list: List[Tuple[str, Type]] = []
        field_names: Set[str] = set()

        struct_fields = getattr(struct, "fields", [])
        for field in struct_fields:
            field_name = getattr(field, "name", None)
            field_type = getattr(field, "ty", None)
            field_loc = getattr(field, "loc", None)

            if not isinstance(field_name, str):
                continue

            # Check for duplicate field names
            if field_name in field_names:
                er.emit(self.r, ERR.CE0005, field_loc, name=field_name, struct_name=name)
                continue

            # Check for missing field type
            if field_type is None:
                er.emit(self.r, ERR.CE0104, field_loc, name=f"field '{field_name}'")
                continue

            # NOTE: Field types may be TypeParameter instances (e.g., T, U) for generic structs
            # These will be resolved during monomorphization
            field_names.add(field_name)
            fields_list.append((field_name, field_type))

        # Branch based on whether this is a generic struct or regular struct
        if type_params and len(type_params) > 0:
            # Generic struct - store in generic_structs table

            # Preserve BoundedTypeParam objects (Phase 4: constraint validation)
            # Convert to tuple, handling both BoundedTypeParam and legacy string formats
            type_param_instances = tuple(
                tp if isinstance(tp, BoundedTypeParam)
                else TypeParameter(name=tp) if isinstance(tp, TypeParameter)
                else BoundedTypeParam(name=tp, constraints=[], loc=None)
                for tp in type_params_raw
            )

            generic_struct = GenericStructType(
                name=name,
                type_params=type_param_instances,
                fields=tuple(fields_list)
            )

            self.generic_structs.order.append(name)
            self.generic_structs.by_name[name] = generic_struct

            # Note: Generic structs are not added to known_types until instantiated
        else:
            # Regular struct - store in structs table (existing behavior)
            struct_type = StructType(
                name=name,
                fields=tuple(fields_list)
            )

            self.structs.order.append(name)
            self.structs.by_name[name] = struct_type

            # Register struct type as known type for future lookups
            self.known_types.add(struct_type)

            # Hash registration is deferred to Pass 1.8 (hash_registration.py)
            # This ensures all types are resolved (Pass 1.7) and generics are monomorphized (Pass 1.6)
            # before we attempt to register hash methods
