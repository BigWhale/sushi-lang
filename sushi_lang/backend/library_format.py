"""Binary library format (.slib) for Sushi libraries."""
from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO, Dict, Optional, Tuple

import msgpack


# Header KIND values. A source library carries no bitcode, a binary one carries no
# source, a hybrid carries both. The manifest's `kind` field is the authority; the
# header mirrors it so a reader can branch before it unpacks any MessagePack.
KIND_SOURCE = 1
KIND_BINARY = 2
KIND_HYBRID = 3

KIND_BY_NAME = {"source": KIND_SOURCE, "binary": KIND_BINARY, "hybrid": KIND_HYBRID}
KIND_BY_CODE = {code: name for name, code in KIND_BY_NAME.items()}


def _read_bytes(f: BinaryIO, size: int, path: str, section: str) -> bytes:
    """Read exact number of bytes with truncation detection."""
    from sushi_lang.backend.library_errors import LibraryError

    data = f.read(size)
    if len(data) != size:
        # Three literal raises, not a variable code: the registry-completeness gate
        # (test_error_registry.py) only sees string-literal codes, and a code it
        # cannot see is a code it cannot prove is registered.
        if section == "metadata":
            raise LibraryError("CE3510", path=path, expected=size, actual=len(data))
        if section == "source":
            raise LibraryError("CE3506", path=path, expected=size, actual=len(data))
        raise LibraryError("CE3511", path=path, expected=size, actual=len(data))
    return data


def _read_header_and_metadata(f: BinaryIO, path: str) -> dict:
    """Read and validate header, return deserialized metadata."""
    from sushi_lang.backend.library_errors import LibraryError

    magic = _read_bytes(f, 16, path, "metadata")
    if magic != LibraryFormat.MAGIC:
        raise LibraryError("CE3508", path=path)

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

    blob = _skip_source_section(f, path)
    if not blob:
        return {}
    try:
        return msgpack.unpackb(blob, raw=False)
    except Exception as e:
        raise LibraryError("CE3512", path=path, reason=str(e)) from e


def _skip_source_section(f: BinaryIO, path: str) -> bytes:
    """Consume the source section, returning its raw bytes without unpacking them.

    `read()` wants the bitcode and must still step over the source to reach it. A whole
    library's source can be large, so the bytes are checked for truncation (CE3506) and
    handed back unparsed -- only `read_source_only` pays to unpack them.
    """
    src_len = struct.unpack("<Q", _read_bytes(f, 8, path, "source"))[0]
    if src_len == 0:
        return b""
    return _read_bytes(f, src_len, path, "source")


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
        from sushi_lang.backend.library_errors import LibraryError

        path = str(library_path)

        with open(library_path, 'rb') as f:
            metadata = _read_header_and_metadata(f, path)
            _skip_source_section(f, path)

            bc_len = struct.unpack("<Q", _read_bytes(f, 8, path, "bitcode"))[0]
            bitcode = _read_bytes(f, bc_len, path, "bitcode")

            total_size = f.tell()
            if total_size > LibraryFormat.MAX_FILE_SIZE:
                raise LibraryError("CE3513", path=path,
                                   size=total_size, max_size=LibraryFormat.MAX_FILE_SIZE)

        return metadata, bitcode

    @staticmethod
    def read_source_only(library_path: Path) -> Tuple[dict, Dict[str, str]]:
        """Read (metadata, unit source) without touching the bitcode section."""
        path = str(library_path)
        with open(library_path, 'rb') as f:
            metadata = _read_header_and_metadata(f, path)
            return metadata, _read_source_section(f, path)

    @staticmethod
    def read_metadata_only(library_path: Path) -> dict:
        """Read only metadata from .slib file (for introspection)."""
        with open(library_path, 'rb') as f:
            return _read_header_and_metadata(f, str(library_path))
