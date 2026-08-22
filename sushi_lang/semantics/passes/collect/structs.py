"""Struct definition collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR
from sushi_lang.semantics.ast import StructDef, Program, BoundedTypeParam
from sushi_lang.semantics.typesys import Type, StructType
from sushi_lang.semantics.generics.types import GenericStructType, TypeParameter

from .utils import extract_type_param_names, note_first_declaration, reject_reference_in


@dataclass
class StructTable:
    """Table of struct types collected by the collect pass."""
    by_name: Dict[str, StructType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    # Where each name was declared. A StructType is a frozen semantic type and has
    # no business carrying a source span, but a duplicate-declaration error needs to
    # point at the original -- so the TABLE remembers. A name that is here but not in
    # `spans` was predefined by the compiler.
    spans: Dict[str, Optional[Span]] = field(default_factory=dict)


@dataclass
class GenericStructTable:
    """Table of generic struct types collected by the collect pass."""
    by_name: Dict[str, GenericStructType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    spans: Dict[str, Optional[Span]] = field(default_factory=dict)


class StructCollector:
    """Collector for struct definitions."""

    def __init__(
        self,
        reporter: Reporter,
        structs: StructTable,
        generic_structs: GenericStructTable,
        known_types: Set[Type]
    ) -> None:
        """Initialize struct collector."""
        self.r = reporter
        self.structs = structs
        self.generic_structs = generic_structs
        self.known_types = known_types

    def collect(self, root: Program) -> None:
        """Collect all struct definitions from program AST."""
        structs = getattr(root, "structs", None)
        if isinstance(structs, list):
            for struct in structs:
                if isinstance(struct, StructDef):
                    self._collect_struct_def(struct)

    def register_predefined_structs(self) -> None:
        """Register stdlib-provided structs that are built into the language."""
        from sushi_lang.semantics.typesys import BuiltinType

        # Note: fields are named *_text (not stdout/stderr) because `stdin`/`stdout`/
        # `stderr` are reserved stream tokens in the grammar and cannot be member names.
        process_output = StructType(
            name="ProcessOutput",
            fields=(
                ("exit_code", BuiltinType.I32),
                ("stdout_text", BuiltinType.STRING),
                ("stderr_text", BuiltinType.STRING),
            ),
        )
        if "ProcessOutput" not in self.structs.by_name:
            self.structs.order.append("ProcessOutput")
            self.structs.by_name["ProcessOutput"] = process_output
            self.known_types.add(process_output)

    def _collect_struct_def(self, struct: StructDef) -> None:
        """Collect struct definition and create StructType or GenericStructType."""
        name = getattr(struct, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(struct, "name_span", None) or getattr(struct, "loc", None)

        type_params_raw = getattr(struct, "type_params", None)
        type_params: Optional[List[str]] = extract_type_param_names(type_params_raw)

        if name in self.structs.by_name:
            note_first_declaration(
                er.emit_with(self.r, ERR.CE0004, name_span, name=name),
                self.structs.spans, name,
            ).emit()
            return

        if name in self.generic_structs.by_name:
            note_first_declaration(
                er.emit_with(self.r, ERR.CE0004, name_span, name=name),
                self.generic_structs.spans, name,
                what="first defined here, as a generic struct",
            ).emit()
            return

        fields_list: List[Tuple[str, Type]] = []
        field_spans: Dict[str, Optional[Span]] = {}

        struct_fields = getattr(struct, "fields", [])
        for field_node in struct_fields:
            field_name = getattr(field_node, "name", None)
            field_type = getattr(field_node, "ty", None)
            field_loc = getattr(field_node, "loc", None)

            if not isinstance(field_name, str):
                continue

            if field_name in field_spans:
                note_first_declaration(
                    er.emit_with(self.r, ERR.CE0005, field_loc,
                                 name=field_name, struct_name=name),
                    field_spans, field_name,
                    what="first declared here",
                ).emit()
                continue

            if field_type is None:
                er.emit(self.r, ERR.CE0104, field_loc, name=f"field '{field_name}'")
                continue

            # A reference field has no semantics, and reading one is an internal error
            # (CE2415, #315). Reported, then KEPT: dropping it makes every construction
            # report a spurious CE2027 about a field the user did write, and the report
            # already stops the compile before codegen.
            reject_reference_in(self.r, field_type, field_loc, ERR.CE2415)

            # NOTE: Field types may be TypeParameter instances (e.g., T, U) for generic structs
            # These will be resolved during monomorphization
            field_spans[field_name] = field_loc
            fields_list.append((field_name, field_type))

        if type_params and len(type_params) > 0:

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
            self.generic_structs.spans[name] = name_span

            # Note: Generic structs are not added to known_types until instantiated
        else:
            struct_type = StructType(
                name=name,
                fields=tuple(fields_list)
            )

            self.structs.order.append(name)
            self.structs.by_name[name] = struct_type
            self.structs.spans[name] = name_span

            self.known_types.add(struct_type)

            # Hash registration is deferred to the derive pass (passes/derive.py), which runs
            # after the resolve pass resolved every type and the monomorphize pass made every
            # generic concrete.
            # before we attempt to register hash methods
