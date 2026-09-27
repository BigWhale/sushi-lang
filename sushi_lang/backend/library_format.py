"""Binary library format (.slib) for Sushi libraries."""
from __future__ import annotations

import errno
import io
import os
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Dict, Iterator, Optional, Tuple

import msgpack

if TYPE_CHECKING:
    from sushi_lang.backend.library_errors import LibraryError


# Header KIND values. A source library carries no bitcode, a binary one carries no
# source, a hybrid carries both. The manifest's `kind` field is the authority; the
# header mirrors it so a reader can branch before it unpacks any MessagePack.
KIND_SOURCE = 1
KIND_BINARY = 2
KIND_HYBRID = 3

KIND_BY_NAME = {"source": KIND_SOURCE, "binary": KIND_BINARY, "hybrid": KIND_HYBRID}


# The words for an OS error, one row per IoError variant the Sushi reader can answer.
# toolchain/src/slib_info.sushi (`io_cause`) prints the same words for the same errno.
_OS_ERROR_WORDS = {
    errno.ENOENT: "file not found",
    errno.EPERM: "permission denied",
    errno.EACCES: "permission denied",
    errno.EEXIST: "already exists",
    errno.EISDIR: "is a directory",
    errno.ENOSPC: "no space left on device",
    errno.EMFILE: "too many open files",
    errno.ENAMETOOLONG: "invalid path",
    errno.ENOTDIR: "invalid path",
    errno.ELOOP: "invalid path",
}


@contextmanager
def _open_for_read(library_path: Path) -> Iterator[BinaryIO]:
    """Open a library for reading; an OSError of the open or of a read is CE3515."""
    from sushi_lang.backend.library_errors import LibraryError

    try:
        with open(library_path, 'rb') as f:
            yield f
    except OSError as e:
        reason = _OS_ERROR_WORDS.get(e.errno or 0, "input/output error")
        raise LibraryError("CE3515", path=str(library_path), reason=reason) from e


def _file_size(f: BinaryIO) -> Optional[int]:
    """The size of the file under `f`, or None for a stream that is not a file."""
    try:
        return os.fstat(f.fileno()).st_size
    except (AttributeError, OSError, io.UnsupportedOperation):
        return None


def _truncated(section: str, path: str, expected: int, actual: int) -> LibraryError:
    """The truncation error of one section."""
    from sushi_lang.backend.library_errors import LibraryError

    # Three literal codes, not a variable one: the registry-completeness gate
    # (test_error_registry.py) only sees string-literal codes, and a code it cannot see
    # is a code it cannot prove is registered.
    if section == "metadata":
        return LibraryError("CE3510", path=path, expected=expected, actual=actual)
    if section == "source":
        return LibraryError("CE3506", path=path, expected=expected, actual=actual)
    return LibraryError("CE3511", path=path, expected=expected, actual=actual)


def _check_fits(f: BinaryIO, size: int, path: str, section: str) -> None:
    """Refuse a declared length that is longer than what is left of the file.

    The check comes BEFORE the read, so a length field that declares more bytes than
    the file holds is a truncation, and never an attempt to read that many bytes.
    """
    total = _file_size(f)
    if total is not None and size > total - f.tell():
        raise _truncated(section, path, size, max(total - f.tell(), 0))


def _read_bytes(f: BinaryIO, size: int, path: str, section: str) -> bytes:
    """Read exact number of bytes with truncation detection."""
    _check_fits(f, size, path, section)
    data = f.read(size)
    if len(data) != size:
        raise _truncated(section, path, size, len(data))
    return data


def _read_header_and_metadata(f: BinaryIO, path: str) -> dict:
    """Read and validate header, return deserialized metadata.

    The magic comes first in the file, so it is checked first: a file whose first bytes
    are not the magic is CE3508 whatever its length. Then the size limit, before any
    section is read, then the header length.
    """
    from sushi_lang.backend.library_errors import LibraryError

    magic = f.read(16)
    if magic != LibraryFormat.MAGIC[:len(magic)]:
        raise LibraryError("CE3508", path=path)
    total = _file_size(f)
    if total is not None and total > LibraryFormat.MAX_FILE_SIZE:
        raise LibraryError("CE3513", path=path,
                           size=total, max_size=LibraryFormat.MAX_FILE_SIZE)
    if total is not None and total < LibraryFormat.FIXED_HEADER_SIZE:
        raise _truncated("metadata", path, LibraryFormat.FIXED_HEADER_SIZE, total)
    if len(magic) != 16:
        raise _truncated("metadata", path, 16, len(magic))

    header_rest = _read_bytes(f, 28, path, "metadata")
    version = struct.unpack("<I", header_rest[0:4])[0]

    if version != LibraryFormat.VERSION:
        raise LibraryError("CE3509", path=path,
                           version=version, supported=LibraryFormat.VERSION)

    meta_len = struct.unpack("<Q", _read_bytes(f, 8, path, "metadata"))[0]
    metadata_blob = _read_bytes(f, meta_len, path, "metadata")

    try:
        return msgpack.unpackb(metadata_blob, raw=False)
    except Exception as e:
        raise LibraryError("CE3512", path=path, reason=str(e)) from e


def _read_source_section(f: BinaryIO, path: str) -> Dict[str, str]:
    """Read the source section that sits between the metadata and the bitcode."""
    from sushi_lang.backend.library_errors import LibraryError

    src_len = struct.unpack("<Q", _read_bytes(f, 8, path, "source"))[0]
    if src_len == 0:
        return {}
    blob = _read_bytes(f, src_len, path, "source")
    try:
        return msgpack.unpackb(blob, raw=False)
    except Exception as e:
        raise LibraryError("CE3512", path=path, reason=str(e)) from e


def _skip_source_section(f: BinaryIO, path: str) -> int:
    """Step over the source section and give its length, without reading it.

    `read()` wants the bitcode and `--lib-info` wants the lengths, and both must step
    over the source to reach the bitcode field. The length is checked against the file
    (CE3506) and the section is skipped with a seek: only `read_source_only` reads it.
    """
    src_len = struct.unpack("<Q", _read_bytes(f, 8, path, "source"))[0]
    _check_fits(f, src_len, path, "source")
    f.seek(src_len, os.SEEK_CUR)
    return src_len


# The templates schema this compiler writes and reads, beside the container's own
# VERSION: the producer (`library_manifest.py`) and the consumer's CE3512 gate import it
# from here. 5: every record carries its unit and a source-shipped template its
# `bindings` (D4). 6: every public perk ships, and a generic-target perk implementation
# ships as a template (#543). 7: every perk method record carries its signature and
# receiver mode (#537).
TEMPLATES_SCHEMA_VERSION = 7


# The shape of a manifest: one row per field a reader relies on, as (record kind, key,
# type, required). A type is `str`, `nstr` (a string or nil), `bool`, `map`, `list`,
# `strs` (a list of strings), `@kind` (a map of that kind), `[]kind` (a list of maps of
# that kind) or `{}kind` (a map whose every value is a map of that kind). `required` is `yes`, `no`, or the name of a
# bool field of the same record that makes the field required when it is true. The Sushi
# reader (`toolchain/slib.sushi`, `SLIB_MANIFEST_SCHEMA`) carries the same rows, and
# `tests/unit/test_slib_manifest_keys_agree.py` holds the two together.
MANIFEST_SCHEMA: Tuple[Tuple[str, str, str, str], ...] = (
    ("manifest", "library_name", "str", "yes"),
    ("manifest", "library_version", "str", "yes"),
    ("manifest", "platform", "str", "yes"),
    ("manifest", "compiler_version", "str", "yes"),
    ("manifest", "compiled_at", "str", "yes"),
    ("manifest", "sushi_lib_version", "str", "yes"),
    ("manifest", "kind", "str", "no"),
    ("manifest", "requires_compiler", "str", "no"),
    ("manifest", "units", "strs", "no"),
    ("manifest", "unit_docs", "{}doc", "no"),
    ("manifest", "reexports", "[]reexport", "no"),
    ("manifest", "public_functions", "[]function", "no"),
    ("manifest", "public_constants", "[]constant", "no"),
    ("manifest", "public_variables", "[]constant", "no"),
    ("manifest", "structs", "[]struct", "no"),
    ("manifest", "enums", "[]enum", "no"),
    ("manifest", "templates", "@templates", "no"),
    ("manifest", "not_exported", "[]not_exported", "no"),
    ("manifest", "foreign_extensions", "[]foreign_extension", "no"),
    ("manifest", "dependencies", "strs", "no"),
    ("templates", "generic_functions", "[]function", "no"),
    ("templates", "generic_structs", "[]generic_type", "no"),
    ("templates", "generic_enums", "[]generic_type", "no"),
    ("templates", "perks", "[]perk", "no"),
    ("templates", "perk_impls", "[]perk_impl", "no"),
    ("templates", "generic_perk_impls", "[]perk_impl", "no"),
    ("templates", "private_functions", "[]helper", "no"),
    ("templates", "constants", "[]closure_constant", "no"),
    ("templates", "private_types", "[]private_type", "no"),
    ("function", "name", "str", "yes"),
    ("function", "return_type", "str", "yes"),
    ("function", "params", "[]param", "no"),
    ("function", "type_params", "[]type_param", "no"),
    ("function", "error_type", "str", "no"),
    ("function", "source", "str", "no"),
    ("function", "unit", "str", "no"),
    ("function", "doc", "@doc", "no"),
    ("helper", "name", "str", "yes"),
    ("helper", "params", "[]param", "no"),
    ("helper", "return_type", "str", "no"),
    ("helper", "error_type", "str", "no"),
    ("helper", "unit", "str", "no"),
    ("helper", "link_symbol", "str", "no"),
    ("param", "name", "str", "yes"),
    ("param", "type", "str", "yes"),
    ("param", "mode", "str", "no"),
    ("type_param", "name", "str", "yes"),
    ("type_param", "constraints", "strs", "no"),
    ("type_param", "is_pack", "bool", "no"),
    ("constant", "name", "str", "yes"),
    ("constant", "type", "str", "yes"),
    ("constant", "source", "str", "no"),
    ("constant", "link_symbol", "str", "no"),
    ("constant", "doc", "@doc", "no"),
    ("closure_constant", "name", "str", "no"),
    ("closure_constant", "source", "str", "no"),
    ("closure_constant", "link_symbol", "str", "no"),
    ("private_type", "name", "str", "no"),
    ("private_type", "source", "str", "no"),
    ("struct", "name", "str", "yes"),
    ("struct", "fields", "[]field", "yes"),
    ("struct", "is_generic", "bool", "no"),
    ("struct", "type_params", "strs", "no"),
    ("struct", "doc", "@doc", "no"),
    ("field", "name", "str", "yes"),
    ("field", "type", "str", "yes"),
    ("field", "doc", "@doc", "no"),
    ("enum", "name", "str", "yes"),
    ("enum", "variants", "[]variant", "yes"),
    ("enum", "is_generic", "bool", "no"),
    ("enum", "type_params", "strs", "no"),
    ("enum", "doc", "@doc", "no"),
    ("variant", "name", "str", "yes"),
    ("variant", "has_data", "bool", "no"),
    ("variant", "data_types", "strs", "has_data"),
    ("variant", "doc", "@doc", "no"),
    ("generic_type", "name", "str", "yes"),
    ("generic_type", "source", "str", "no"),
    ("generic_type", "type_params", "[]type_param", "no"),
    ("generic_type", "doc", "@doc", "no"),
    ("perk", "name", "str", "yes"),
    ("perk", "source", "str", "no"),
    ("perk", "methods", "[]method", "no"),
    ("perk", "doc", "@doc", "no"),
    ("perk_impl", "type", "str", "yes"),
    ("perk_impl", "perk", "str", "yes"),
    ("perk_impl", "type_args", "strs", "no"),
    ("perk_impl", "source", "str", "no"),
    ("perk_impl", "methods", "[]method", "no"),
    ("perk_impl", "doc", "@doc", "no"),
    ("method", "name", "str", "yes"),
    ("method", "return_type", "str", "yes"),
    ("method", "params", "[]param", "no"),
    ("method", "self_mode", "str", "no"),
    ("method", "error_type", "str", "no"),
    ("method", "doc", "@doc", "no"),
    ("doc", "summary", "str", "no"),
    ("doc", "body", "str", "no"),
    ("doc", "params", "map", "no"),
    ("doc", "examples", "[]example", "no"),
    ("doc", "returns", "str", "no"),
    ("doc", "errors", "str", "no"),
    ("example", "code", "str", "yes"),
    ("example", "caption", "str", "no"),
    ("reexport", "unit", "str", "yes"),
    ("reexport", "path", "str", "yes"),
    ("reexport", "kind", "str", "no"),
    ("not_exported", "name", "str", "yes"),
    ("not_exported", "kind", "str", "no"),
    ("foreign_extension", "type", "str", "yes"),
    ("foreign_extension", "method", "str", "yes"),
)

_SCALAR_TYPES = {"str": (str, "a string"), "bool": (bool, "a bool"),
                 "map": (dict, "a map"), "list": (list, "a list")}


def check_manifest(metadata: object, path: str) -> None:
    """Refuse a manifest that does not have the shape `MANIFEST_SCHEMA` states (CE3512).

    The container readers hand back whatever map the file holds. Every reader of a
    manifest -- `--lib-info` and a consumer's `use <lib/...>` -- asks this first, so a
    field a reader subscripts is there and has its type.
    """
    from sushi_lang.backend.library_errors import LibraryError

    fault = (_record_fault(metadata, "manifest", "") if isinstance(metadata, dict)
             else "the metadata is not a map")
    if fault is not None:
        raise LibraryError("CE3512", path=path, reason=fault)


def _record_fault(record: dict, kind: str, where: str) -> Optional[str]:
    """The first field of `record` that breaks a row of `kind`, as a reason, or None."""
    for row_kind, key, spec, required in MANIFEST_SCHEMA:
        if row_kind != kind:
            continue
        name = f"{where}.{key}" if where else key
        if key not in record:
            if required == "yes" or (required != "no" and record.get(required) is True):
                return f"missing required field '{name}'"
            continue
        fault = _value_fault(record[key], spec, name)
        if fault is not None:
            return fault
    return None


def _value_fault(value: object, spec: str, name: str) -> Optional[str]:
    """Why `value` is not of the type `spec` names, as a reason, or None."""
    if spec == "nstr":
        return _value_fault(value, "str", name) if value is not None else None
    if spec in _SCALAR_TYPES:
        py_type, words = _SCALAR_TYPES[spec]
        return None if isinstance(value, py_type) else f"field '{name}' is not {words}"
    if spec == "strs" or spec.startswith("[]"):
        if not isinstance(value, list):
            return f"field '{name}' is not a list"
        for index, item in enumerate(value):
            fault = _value_fault(item, "str" if spec == "strs" else "@" + spec[2:],
                                 f"{name}[{index}]")
            if fault is not None:
                return fault
        return None
    if not isinstance(value, dict):
        return f"field '{name}' is not a map"
    if spec.startswith("@"):
        return _record_fault(value, spec[1:], name)
    for key, item in value.items():
        if not isinstance(key, str):
            return f"field '{name}' has a key that is not a string"
        fault = _value_fault(item, "@" + spec[2:], f"{name}.{key}")
        if fault is not None:
            return fault
    return None


class LibraryFormat:
    """Binary format reader/writer for .slib files."""

    MAGIC = b'\xf0\x9f\x8d\xa3SUSHILIB\xf0\x9f\x8d\xa3'
    # 3: every public-function parameter carries a `mode` field (borrow / nom /
    #    peek / poke). A v2 library states no mode, so its parameters cannot be
    #    told apart from unmarked ones -- CE3509 rejects it rather than guess.
    # 4: source-first distribution. SPARE_1 and SPARE_2 become FLAGS and KIND, and a
    #    length-prefixed SOURCE section sits between the metadata and the bitcode.
    #    There is no upgrade shim: Sushi has no users in the wild.
    VERSION = 4
    FIXED_HEADER_SIZE = 52  # 16 (magic) + 4 (version) + 4 (flags) + 4 (kind) + 16 (spares) + 8 (meta_len)
    MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1GB sanity limit

    # FLAGS bit 0 marks the source section as compressed. Always written as zero:
    # Nori archives are already tar.gz, and the self-hosted reader would need an
    # inflate written in Sushi. See docs/design/libraries.md section 2.
    FLAG_SOURCE_COMPRESSED = 1 << 0

    @staticmethod
    def write(output_path: Path, metadata: dict, bitcode: bytes,
              source: Optional[Dict[str, str]] = None) -> None:
        """Write .slib file with metadata, optional unit source, and bitcode."""
        metadata_blob = msgpack.packb(metadata, use_bin_type=True)
        source_blob = msgpack.packb(source, use_bin_type=True) if source else b""
        kind = KIND_BY_NAME.get(metadata.get("kind", "binary"), KIND_BINARY)

        with open(output_path, 'wb') as f:
            f.write(LibraryFormat.MAGIC)

            f.write(struct.pack("<I", LibraryFormat.VERSION))
            f.write(struct.pack("<I", 0))     # FLAGS
            f.write(struct.pack("<I", kind))  # KIND
            f.write(struct.pack("<Q", 0))     # SPARE_3
            f.write(struct.pack("<Q", 0))     # SPARE_4

            f.write(struct.pack("<Q", len(metadata_blob)))
            f.write(metadata_blob)

            f.write(struct.pack("<Q", len(source_blob)))
            f.write(source_blob)

            f.write(struct.pack("<Q", len(bitcode)))
            f.write(bitcode)

    @staticmethod
    def read(library_path: Path) -> Tuple[dict, bytes]:
        """Read .slib file and return (metadata, bitcode)."""
        path = str(library_path)

        with _open_for_read(library_path) as f:
            metadata = _read_header_and_metadata(f, path)
            _skip_source_section(f, path)

            bc_len = struct.unpack("<Q", _read_bytes(f, 8, path, "bitcode"))[0]
            bitcode = _read_bytes(f, bc_len, path, "bitcode")

        return metadata, bitcode

    @staticmethod
    def read_source_only(library_path: Path) -> Tuple[dict, Dict[str, str]]:
        """Read (metadata, unit source) without touching the bitcode section."""
        path = str(library_path)
        with _open_for_read(library_path) as f:
            metadata = _read_header_and_metadata(f, path)
            return metadata, _read_source_section(f, path)

    @staticmethod
    def read_section_sizes(library_path: Path) -> Tuple[dict, int, int]:
        """Read (metadata, source length, bitcode length), keeping neither payload.

        The `--lib-info` report states how big each section is, and nothing more, so it
        never pays to hold a whole library in memory. The bitcode LENGTH FIELD is what
        it reports -- the Sushi reader `sizes` reads that same field and stops, and
        the two must agree byte for byte.
        """
        path = str(library_path)
        with _open_for_read(library_path) as f:
            metadata = _read_header_and_metadata(f, path)
            source_len = _skip_source_section(f, path)
            bc_len = struct.unpack("<Q", _read_bytes(f, 8, path, "bitcode"))[0]
            _check_fits(f, bc_len, path, "bitcode")
            return metadata, source_len, bc_len

    @staticmethod
    def read_metadata_only(library_path: Path) -> dict:
        """Read only metadata from .slib file (for introspection)."""
        with _open_for_read(library_path) as f:
            return _read_header_and_metadata(f, str(library_path))
