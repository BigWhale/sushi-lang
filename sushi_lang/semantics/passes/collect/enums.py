"""Enum definition collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.structs import StructTable, GenericStructTable
from sushi_lang.semantics.ast import EnumDef, Program, BoundedTypeParam
from sushi_lang.semantics.typesys import (
    Type,
    BuiltinType,
    EnumType,
    EnumVariantInfo,
)
from sushi_lang.semantics.generics.types import GenericEnumType, TypeParameter

from sushi_lang.semantics.visibility import (
    VisibilityTable,
    library_clash_for_type_name,
    reject_library_clash,
)

from .utils import extract_type_param_names, note_first_declaration, reject_reference_in


@dataclass
class EnumTable:
    """Table of enum types collected by the collect pass."""
    by_name: Dict[str, EnumType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    spans: Dict[str, Optional[Span]] = field(default_factory=dict)
    # The unit each span is in, keyed alike: a duplicate is reported while ANOTHER
    # unit is being collected, so the note has to name this file (#473).
    files: Dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class GenericEnumTable:
    """Table of generic enum types collected by the collect pass."""
    by_name: Dict[str, GenericEnumType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    spans: Dict[str, Optional[Span]] = field(default_factory=dict)
    # The unit each span is in, keyed alike: a duplicate is reported while ANOTHER
    # unit is being collected, so the note has to name this file (#473).
    files: Dict[str, Optional[str]] = field(default_factory=dict)


class EnumCollector:
    """Collector for enum definitions."""

    def __init__(
        self,
        reporter: Reporter,
        enums: EnumTable,
        generic_enums: GenericEnumTable,
        structs: 'StructTable',
        generic_structs: 'GenericStructTable',
        known_types: Set[Type]
    ) -> None:
        """Initialize enum collector."""
        self.r = reporter
        # The unit being collected. This pass shares one reporter across every
        # unit, so a record it stores has to remember its own file (#473).
        self.current_unit_file: Optional[str] = None
        self.current_unit_name: Optional[str] = None
        self.library_units: Set[str] = set()
        self.visibility: Optional[VisibilityTable] = None
        self.enums = enums
        self.generic_enums = generic_enums
        self.structs = structs
        self.generic_structs = generic_structs
        self.known_types = known_types

    def collect(self, root: Program) -> None:
        """Collect all enum definitions from program AST."""
        enums = getattr(root, "enums", None)
        if isinstance(enums, list):
            for enum in enums:
                if isinstance(enum, EnumDef):
                    self._collect_enum_def(enum)

    def register_predefined_enums(self) -> None:
        """Register predefined enums for file operations and error handling."""
        file_mode_enum = EnumType(
            name="FileMode",
            variants=(
                EnumVariantInfo(name="Read", associated_types=()),      # Text read mode ("r")
                EnumVariantInfo(name="Write", associated_types=()),     # Text write mode ("w")
                EnumVariantInfo(name="Append", associated_types=()),    # Text append mode ("a")
                EnumVariantInfo(name="ReadB", associated_types=()),     # Binary read mode ("rb")
                EnumVariantInfo(name="WriteB", associated_types=()),    # Binary write mode ("wb")
                EnumVariantInfo(name="AppendB", associated_types=()),   # Binary append mode ("ab")
            )
        )
        self.enums.by_name["FileMode"] = file_mode_enum
        self.enums.order.append("FileMode")
        self.known_types.add(file_mode_enum)

        seek_from_enum = EnumType(
            name="SeekFrom",
            variants=(
                EnumVariantInfo(name="Start", associated_types=()),     # SEEK_SET (0)
                EnumVariantInfo(name="Current", associated_types=()),   # SEEK_CUR (1)
                EnumVariantInfo(name="End", associated_types=()),       # SEEK_END (2)
            )
        )
        self.enums.by_name["SeekFrom"] = seek_from_enum
        self.enums.order.append("SeekFrom")
        self.known_types.add(seek_from_enum)

        file_error_enum = EnumType(
            name="FileError",
            variants=(
                EnumVariantInfo(name="NotFound", associated_types=()),          # ENOENT - File does not exist
                EnumVariantInfo(name="PermissionDenied", associated_types=()),  # EACCES, EPERM - Insufficient permissions
                EnumVariantInfo(name="AlreadyExists", associated_types=()),     # EEXIST - File already exists
                EnumVariantInfo(name="IsDirectory", associated_types=()),       # EISDIR - Path refers to a directory
                EnumVariantInfo(name="DiskFull", associated_types=()),          # ENOSPC - No space left on device
                EnumVariantInfo(name="TooManyOpen", associated_types=()),       # EMFILE, ENFILE - Too many open files
                EnumVariantInfo(name="InvalidPath", associated_types=()),       # ENAMETOOLONG - Invalid path or filename
                EnumVariantInfo(name="IOError", associated_types=()),           # EIO - Generic I/O error
                EnumVariantInfo(name="Other", associated_types=()),             # Any other error
            )
        )
        self.enums.by_name["FileError"] = file_error_enum
        self.enums.order.append("FileError")
        self.known_types.add(file_error_enum)

        # FileResult enum - Result type for open() function
        # Variant: Ok(file) - success with file handle
        # Variant: Err(FileError) - failure with error information
        # Note: Uses Ok/Err naming (not Success/Error) to be consistent with Result<T>
        # No token conflict because enum variants are always qualified (FileResult.Ok vs Result.Ok)
        file_result_enum = EnumType(
            name="FileResult",
            variants=(
                EnumVariantInfo(name="Ok", associated_types=(BuiltinType.FILE,)),   # Success with file handle
                EnumVariantInfo(name="Err", associated_types=(file_error_enum,)),    # Failure with error information
            )
        )
        self.enums.by_name["FileResult"] = file_result_enum
        self.enums.order.append("FileResult")
        self.known_types.add(file_result_enum)

        std_error_enum = EnumType(
            name="StdError",
            variants=(
                EnumVariantInfo(name="Error", associated_types=()),  # Generic error
            )
        )
        self.enums.by_name["StdError"] = std_error_enum
        self.enums.order.append("StdError")
        self.known_types.add(std_error_enum)

        io_error_enum = EnumType(
            name="IoError",
            variants=(
                EnumVariantInfo(name="ReadError", associated_types=()),   # Failed to read
                EnumVariantInfo(name="WriteError", associated_types=()),  # Failed to write
                EnumVariantInfo(name="FlushError", associated_types=()),  # Failed to flush
            )
        )
        self.enums.by_name["IoError"] = io_error_enum
        self.enums.order.append("IoError")
        self.known_types.add(io_error_enum)

        process_error_enum = EnumType(
            name="ProcessError",
            variants=(
                EnumVariantInfo(name="SpawnFailed", associated_types=()),     # Failed to spawn process
                EnumVariantInfo(name="ExitFailure", associated_types=()),     # Process exited with error
                EnumVariantInfo(name="SignalReceived", associated_types=()),  # Process received signal
            )
        )
        self.enums.by_name["ProcessError"] = process_error_enum
        self.enums.order.append("ProcessError")
        self.known_types.add(process_error_enum)

        env_error_enum = EnumType(
            name="EnvError",
            variants=(
                EnumVariantInfo(name="NotFound", associated_types=()),          # Environment variable not found
                EnumVariantInfo(name="InvalidValue", associated_types=()),      # Invalid value
                EnumVariantInfo(name="PermissionDenied", associated_types=()),  # Insufficient permissions
            )
        )
        self.enums.by_name["EnvError"] = env_error_enum
        self.enums.order.append("EnvError")
        self.known_types.add(env_error_enum)

        math_error_enum = EnumType(
            name="MathError",
            variants=(
                EnumVariantInfo(name="DivisionByZero", associated_types=()),  # Division by zero
                EnumVariantInfo(name="Overflow", associated_types=()),        # Arithmetic overflow
                EnumVariantInfo(name="Underflow", associated_types=()),       # Arithmetic underflow
                EnumVariantInfo(name="InvalidInput", associated_types=()),    # Invalid input to math function
            )
        )
        self.enums.by_name["MathError"] = math_error_enum
        self.enums.order.append("MathError")
        self.known_types.add(math_error_enum)

    def _reject_library_clash(self, name: str, name_span: Optional[Span]) -> bool:
        """CE3011 when a library already took this name. True when it was refused."""
        clash = library_clash_for_type_name(
            self.visibility, name,
            current_unit=self.current_unit_name, library_units=self.library_units)
        if clash is None or clash.is_public:
            return False  # A public library type stays the plain duplicate (CE0004).
        reject_library_clash(self.r, clash, name_span, kind="enum", name=name,
                             filename=self.current_unit_file)
        return True

    def _collect_enum_def(self, enum: EnumDef) -> None:
        """Collect enum definition and create EnumType or GenericEnumType."""
        name = getattr(enum, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(enum, "name_span", None) or getattr(enum, "loc", None)

        # Check if this enum has type parameters (e.g., enum Result<T>:)
        # Note: In the collect pass, type_params is always None -- the grammar has no syntax for it yet
        type_params_raw = getattr(enum, "type_params", None)
        type_params: Optional[List[str]] = extract_type_param_names(type_params_raw)

        if (name in self.enums.by_name or name in self.structs.by_name
                or name in self.generic_structs.by_name
                or name in self.generic_enums.by_name):
            if self._reject_library_clash(name, name_span):
                return

        if name in self.enums.by_name:
            note_first_declaration(
                er.emit_with(self.r, ERR.CE2046, name_span, name=name),
                self.enums.spans, name, files=self.enums.files,
            ).emit()
            return

        if name in self.structs.by_name:
            note_first_declaration(
                er.emit_with(self.r, ERR.CE0006, name_span, name=name),
                self.structs.spans, name,
                what="already defined as a struct here", files=self.structs.files,
            ).emit()
            return

        if name in self.generic_structs.by_name:
            note_first_declaration(
                er.emit_with(self.r, ERR.CE0006, name_span, name=name),
                self.generic_structs.spans, name,
                what="already defined as a generic struct here",
                files=self.generic_structs.files,
            ).emit()
            return

        if name in self.generic_enums.by_name:
            note_first_declaration(
                er.emit_with(self.r, ERR.CE2046, name_span, name=name),
                self.generic_enums.spans, name,
                what="first defined here, as a generic enum",
                files=self.generic_enums.files,
            ).emit()
            return

        variants_list: List[EnumVariantInfo] = []
        variant_names: Set[str] = set()

        enum_variants = getattr(enum, "variants", [])
        for variant in enum_variants:
            variant_name = getattr(variant, "name", None)
            variant_types = getattr(variant, "associated_types", [])
            variant_loc = getattr(variant, "loc", None)

            if not isinstance(variant_name, str):
                continue

            if variant_name in variant_names:
                er.emit(self.r, ERR.CE2047, variant_loc, name=variant_name, enum_name=name)
                continue

            if variant_types is None:
                variant_types = []

            # A reference payload has no semantics -- the enum may outlive what it borrows
            # (CE2416, #316). Reported, then KEPT: dropping it would report a spurious arity
            # error at every construction. The variant's span carries it, there being no
            # per-payload one.
            for assoc_type in variant_types:
                reject_reference_in(self.r, assoc_type, variant_loc, ERR.CE2416)

            variant_names.add(variant_name)
            variants_list.append(EnumVariantInfo(
                name=variant_name,
                associated_types=tuple(variant_types)
            ))

        if type_params and len(type_params) > 0:
            # Generic enum - store in generic_enums table
            # Preserve BoundedTypeParam objects -- the monomorphize pass validates the constraints
            # Convert to tuple, handling both BoundedTypeParam and legacy string formats
            type_param_instances = tuple(
                tp if isinstance(tp, BoundedTypeParam)
                else TypeParameter(name=tp) if isinstance(tp, TypeParameter)
                else BoundedTypeParam(name=tp, constraints=[], loc=None)
                for tp in type_params_raw
            )

            generic_enum = GenericEnumType(
                name=name,
                type_params=type_param_instances,
                variants=tuple(variants_list)
            )

            self.generic_enums.order.append(name)
            self.generic_enums.by_name[name] = generic_enum
            self.generic_enums.spans[name] = name_span
            self.generic_enums.files[name] = self.current_unit_file

            # Note: Generic enums are not added to known_types until instantiated
        else:
            enum_type = EnumType(
                name=name,
                variants=tuple(variants_list)
            )

            self.enums.order.append(name)
            self.enums.by_name[name] = enum_type
            self.enums.spans[name] = name_span
            self.enums.files[name] = self.current_unit_file

            self.known_types.add(enum_type)
