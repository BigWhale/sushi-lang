"""Binary library format (.slib) for Sushi libraries.

This module handles reading and writing the unified .slib format that combines
LLVM bitcode with MessagePack-encoded metadata in a single file.

File format specification (version 1):

    ┌─────────────────────────────────────────────────────────────┐
    │ MAGIC (16 bytes): 🍣SUSHILIB🍣 (UTF-8)                      │
    ├─────────────────────────────────────────────────────────────┤
    │ VERSION (4 bytes): uint32 LE                                │
    ├─────────────────────────────────────────────────────────────┤
    │ SPARE_1 (4 bytes): uint32 LE (reserved)                     │
    ├─────────────────────────────────────────────────────────────┤
    │ SPARE_2 (4 bytes): uint32 LE (reserved)                     │
    ├─────────────────────────────────────────────────────────────┤
    │ SPARE_3 (8 bytes): uint64 LE (reserved)                     │
    ├─────────────────────────────────────────────────────────────┤
    │ SPARE_4 (8 bytes): uint64 LE (reserved)                     │
    ├─────────────────────────────────────────────────────────────┤
    │ METADATA_LENGTH (8 bytes): uint64 LE                        │
    ├─────────────────────────────────────────────────────────────┤
    │ METADATA_BLOB (N bytes): MessagePack-encoded dict           │
    ├─────────────────────────────────────────────────────────────┤
    │ BITCODE_LENGTH (8 bytes): uint64 LE                         │
    ├─────────────────────────────────────────────────────────────┤
    │ BITCODE_BLOB (M bytes): Raw LLVM bitcode                    │
    └─────────────────────────────────────────────────────────────┘

Fixed header size: 52 bytes (magic + version + spares + metadata_length)
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

import msgpack


def _read_bytes(f: BinaryIO, size: int, path: str, section: str) -> bytes:
    """Read exact number of bytes with truncation detection.

    Args:
        f: File handle.
        size: Expected number of bytes.
        path: File path for error messages.
        section: Section name for error messages ("metadata" or "bitcode").

    Returns:
        Bytes read.

    Raises:
        LibraryError: CE3510/CE3511 if truncated.
    """
    from sushi_lang.backend.library_linker import LibraryError

    data = f.read(size)
    if len(data) != size:
        code = "CE3510" if section == "metadata" else "CE3511"
        raise LibraryError(code, path=path, expected=size, actual=len(data))
    return data


def _read_header_and_metadata(f: BinaryIO, path: str) -> dict:
    """Read and validate header, return deserialized metadata.

    Args:
        f: File handle positioned at start.
        path: File path for error messages.

    Returns:
        Deserialized metadata dictionary.

    Raises:
        LibraryError: CE3508-CE3512 for format errors.
    """
    from sushi_lang.backend.library_linker import LibraryError

    # Read and validate magic (16 bytes)
    magic = _read_bytes(f, 16, path, "metadata")
    if magic != LibraryFormat.MAGIC:
        raise LibraryError("CE3508", path=path)

    # Read version + spares (28 bytes)
    header_rest = _read_bytes(f, 28, path, "metadata")
    version = struct.unpack("<I", header_rest[0:4])[0]

    # Check version compatibility
    if version != LibraryFormat.VERSION:
        raise LibraryError("CE3509", path=path,
                           version=version, supported=LibraryFormat.VERSION)

    # Read metadata length + blob
    meta_len = struct.unpack("<Q", _read_bytes(f, 8, path, "metadata"))[0]
    metadata_blob = _read_bytes(f, meta_len, path, "metadata")

    # Deserialize metadata
    try:
        return msgpack.unpackb(metadata_blob, raw=False)
    except Exception as e:
        raise LibraryError("CE3512", path=path, reason=str(e))


class LibraryFormat:
    """Binary format reader/writer for .slib files."""

    # Magic bytes: 🍣SUSHILIB🍣 (each emoji is 4 UTF-8 bytes)
    MAGIC = b'\xf0\x9f\x8d\xa3SUSHILIB\xf0\x9f\x8d\xa3'
    VERSION = 1
    FIXED_HEADER_SIZE = 52  # 16 (magic) + 4 (version) + 24 (spares) + 8 (meta_len)
    MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1GB sanity limit

    @staticmethod
    def write(output_path: Path, metadata: dict, bitcode: bytes) -> None:
        """Write .slib file with metadata and bitcode.

        Args:
            output_path: Path to output .slib file.
            metadata: Library metadata dictionary.
            bitcode: Raw LLVM bitcode bytes.
        """
        metadata_blob = msgpack.packb(metadata, use_bin_type=True)

        with open(output_path, 'wb') as f:
            # Write magic (16 bytes)
            f.write(LibraryFormat.MAGIC)

            # Write version + spares (28 bytes total)
            f.write(struct.pack("<I", LibraryFormat.VERSION))
            f.write(struct.pack("<I", 0))  # SPARE_1
            f.write(struct.pack("<I", 0))  # SPARE_2
            f.write(struct.pack("<Q", 0))  # SPARE_3
            f.write(struct.pack("<Q", 0))  # SPARE_4

            # Write metadata length + blob
            f.write(struct.pack("<Q", len(metadata_blob)))
            f.write(metadata_blob)

            # Write bitcode length + blob
            f.write(struct.pack("<Q", len(bitcode)))
            f.write(bitcode)

    @staticmethod
    def read(library_path: Path) -> tuple[dict, bytes]:
        """Read .slib file and return (metadata, bitcode).

        Args:
            library_path: Path to .slib file.

        Returns:
            Tuple of (metadata dict, bitcode bytes).

        Raises:
            LibraryError: CE3508-CE3513 for format errors.
        """
        from sushi_lang.backend.library_linker import LibraryError

        path = str(library_path)

        with open(library_path, 'rb') as f:
            metadata = _read_header_and_metadata(f, path)

            # Read bitcode length + blob
            bc_len = struct.unpack("<Q", _read_bytes(f, 8, path, "bitcode"))[0]
            bitcode = _read_bytes(f, bc_len, path, "bitcode")

            # Check file size sanity
            total_size = f.tell()
            if total_size > LibraryFormat.MAX_FILE_SIZE:
                raise LibraryError("CE3513", path=path,
                                   size=total_size, max_size=LibraryFormat.MAX_FILE_SIZE)

        return metadata, bitcode

    @staticmethod
    def read_metadata_only(library_path: Path) -> dict:
        """Read only metadata from .slib file (for introspection).

        This is faster than read() when bitcode is not needed.

        Args:
            library_path: Path to .slib file.

        Returns:
            Metadata dictionary.

        Raises:
            LibraryError: CE3508-CE3512 for format errors.
        """
        with open(library_path, 'rb') as f:
            return _read_header_and_metadata(f, str(library_path))
