# semantics/passes/collect/enums.py
"""Enum definition collection for Phase 0."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from internals.report import Reporter, Span
from internals import errors as er
from internals.errors import ERR
from semantics.ast import EnumDef, Program, BoundedTypeParam
from semantics.typesys import (
    Type,
    BuiltinType,
    EnumType,
    EnumVariantInfo,
    DynamicArrayType,
)
from semantics.generics.types import GenericEnumType, TypeParameter

from .utils import extract_type_param_names


@dataclass
class EnumTable:
    """Table of enum types collected in Phase 0."""
    by_name: Dict[str, EnumType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)


@dataclass
class GenericEnumTable:
    """Table of generic enum types collected in Phase 0.

    Generic enums are enum definitions with type parameters (e.g., Result<T>).
    They are stored separately from concrete enums because they need to be
    instantiated with concrete type arguments during monomorphization.
    """
    by_name: Dict[str, GenericEnumType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)


class EnumCollector:
    """Collector for enum definitions.

    Collects both regular and generic enum definitions during Phase 0, validating:
    - No duplicate names (across regular, generic, and struct namespaces)
    - No duplicate variant names within an enum
    - No dynamic array fields in enum variants (unsupported)

    Also registers predefined enums (FileMode, FileResult, FileError, SeekFrom).
    """

    def __init__(
        self,
        reporter: Reporter,
        enums: EnumTable,
        generic_enums: GenericEnumTable,
        structs: 'StructTable',
        generic_structs: 'GenericStructTable',
        known_types: Set[Type]
    ) -> None:
        """Initialize enum collector.

        Args:
            reporter: Error reporter
            enums: Shared regular enum table to populate
            generic_enums: Shared generic enum table to populate
            structs: Regular struct table for duplicate checking
            generic_structs: Generic struct table for duplicate checking
            known_types: Set of known types for registration
        """
        self.r = reporter
        self.enums = enums
        self.generic_enums = generic_enums
        self.structs = structs
        self.generic_structs = generic_structs
        self.known_types = known_types

    def collect(self, root: Program) -> None:
        """Collect all enum definitions from program AST.

        Args:
            root: Program AST node
        """
        enums = getattr(root, "enums", None)
        if isinstance(enums, list):
            for enum in enums:
                if isinstance(enum, EnumDef):
                    self._collect_enum_def(enum)

    def register_predefined_enums(self) -> None:
        """Register predefined enums for file operations and error handling.

        These enums are built into the language and available globally:
        - FileMode: File open modes (Read, Write, Append, ReadB, WriteB, AppendB)
        - SeekFrom: Seek origins (Start, Current, End)
        - FileError: File error types (NotFound, PermissionDenied, etc.)
        - FileResult: Result type for open() with Ok(file) and Err() variants
        - StdError: Generic standard library errors
        - IoError: I/O operation errors
        - ProcessError: Process control errors
        - EnvError: Environment variable errors
        - MathError: Mathematical operation errors

        Note: FileResult uses Ok/Err variant names (not Success/Error) which is
        consistent with Result<T, E> naming. There is no token conflict because
        variants are always qualified with the enum name (FileResult.Ok vs Result.Ok).
        """
        # FileMode enum - file open modes
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

        # SeekFrom enum - seek origins
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

        # FileError enum - file error types
        # Maps errno values to user-friendly error variants
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

        # StdError enum - Generic standard library errors
        std_error_enum = EnumType(
            name="StdError",
            variants=(
                EnumVariantInfo(name="Error", associated_types=()),  # Generic error
            )
        )
        self.enums.by_name["StdError"] = std_error_enum
        self.enums.order.append("StdError")
        self.known_types.add(std_error_enum)

        # IoError enum - I/O operation errors
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

        # ProcessError enum - Process control errors
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

        # EnvError enum - Environment variable errors
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

        # MathError enum - Mathematical operation errors
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

    def _collect_enum_def(self, enum: EnumDef) -> None:
        """Collect enum definition and create EnumType or GenericEnumType.

        If the enum has type_params (e.g., enum Result<T>:), it is stored as a
        GenericEnumType in the generic_enums table. Otherwise, it is stored as
        a regular EnumType in the enums table.

        Note: For Phase 0, the grammar does not support user-defined generic enums yet.
        This code is defensive and prepares for future phases when the grammar will
        support enum Result<T>: syntax.

        Args:
            enum: Enum definition AST node
        """
        name = getattr(enum, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(enum, "name_span", None) or getattr(enum, "loc", None)

        # Check if this enum has type parameters (e.g., enum Result<T>:)
        # Note: For Phase 0, type_params will always be None since the grammar doesn't support it yet
        type_params_raw = getattr(enum, "type_params", None)
        type_params: Optional[List[str]] = extract_type_param_names(type_params_raw)

        # Check for duplicate enum names (both regular and generic namespaces)
        if name in self.enums.by_name:
            prev = self.enums.by_name[name]
            # Use CE2046 for duplicate enum error
            er.emit(self.r, ERR.CE2046, name_span, name=name, prev_loc=str(prev))
            return

        if name in self.structs.by_name:
            prev = self.structs.by_name[name]
            er.emit(self.r, ERR.CE0006, name_span, name=name, prev_loc=str(prev))
            return

        if name in self.generic_structs.by_name:
            er.emit(self.r, ERR.CE0006, name_span, name=name, prev_loc="<predefined generic>")
            return

        if name in self.generic_enums.by_name:
            # Duplicate with existing generic enum
            er.emit(self.r, ERR.CE2046, name_span, name=name, prev_loc="<predefined generic>")
            return

        # Collect enum variants
        variants_list: List[EnumVariantInfo] = []
        variant_names: Set[str] = set()

        enum_variants = getattr(enum, "variants", [])
        for variant in enum_variants:
            variant_name = getattr(variant, "name", None)
            variant_types = getattr(variant, "associated_types", [])
            variant_loc = getattr(variant, "loc", None)

            if not isinstance(variant_name, str):
                continue

            # Check for duplicate variant names
            if variant_name in variant_names:
                er.emit(self.r, ERR.CE2047, variant_loc, name=variant_name, enum_name=name)
                continue

            # Convert associated types list to tuple
            if variant_types is None:
                variant_types = []

            # Validate: reject dynamic arrays in enum variants
            for assoc_type in variant_types:
                if isinstance(assoc_type, DynamicArrayType):
                    er.emit(self.r, ERR.CE2059, variant_loc,
                           variant=f"{name}.{variant_name}",
                           field_type=str(assoc_type))
                    # Continue collecting other variants, but this one is invalid

            variant_names.add(variant_name)
            variants_list.append(EnumVariantInfo(
                name=variant_name,
                associated_types=tuple(variant_types)
            ))

        # Branch based on whether this is a generic enum or regular enum
        if type_params and len(type_params) > 0:
            # Generic enum - store in generic_enums table
            # Preserve BoundedTypeParam objects (Phase 4: constraint validation)
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

            # Note: Generic enums are not added to known_types until instantiated
        else:
            # Regular enum - store in enums table (existing behavior)
            enum_type = EnumType(
                name=name,
                variants=tuple(variants_list)
            )

            self.enums.order.append(name)
            self.enums.by_name[name] = enum_type

            # Register enum type as known type for future lookups
            self.known_types.add(enum_type)
