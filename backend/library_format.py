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
from typing import TYPE_CHECKING

import msgpack

if TYPE_CHECKING:
    pass


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
        # Serialize metadata to MessagePack
        metadata_blob = msgpack.packb(metadata, use_bin_type=True)

        with open(output_path, 'wb') as f:
            # Write magic (16 bytes)
            f.write(LibraryFormat.MAGIC)

            # Write version + spares (28 bytes total)
            # Format: version(4) + spare1(4) + spare2(4) + spare3(8) + spare4(8)
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
        from backend.library_linker import LibraryError

        with open(library_path, 'rb') as f:
            # Read and validate magic (16 bytes)
            magic = f.read(16)
            if len(magic) < 16:
                raise LibraryError("CE3510", path=str(library_path),
                                   expected=16, actual=len(magic))

            if magic != LibraryFormat.MAGIC:
                raise LibraryError("CE3508", path=str(library_path))

            # Read version + spares (28 bytes)
            header_rest = f.read(28)
            if len(header_rest) < 28:
                raise LibraryError("CE3510", path=str(library_path),
                                   expected=28, actual=len(header_rest))

            version = struct.unpack("<I", header_rest[0:4])[0]
            # spare1 = struct.unpack("<I", header_rest[4:8])[0]
            # spare2 = struct.unpack("<I", header_rest[8:12])[0]
            # spare3 = struct.unpack("<Q", header_rest[12:20])[0]
            # spare4 = struct.unpack("<Q", header_rest[20:28])[0]

            # Check version compatibility
            if version != LibraryFormat.VERSION:
                raise LibraryError("CE3509", path=str(library_path),
                                   version=version, supported=LibraryFormat.VERSION)

            # Read metadata length
            meta_len_bytes = f.read(8)
            if len(meta_len_bytes) != 8:
                raise LibraryError("CE3510", path=str(library_path),
                                   expected=8, actual=len(meta_len_bytes))
            meta_len = struct.unpack("<Q", meta_len_bytes)[0]

            # Read metadata blob
            metadata_blob = f.read(meta_len)
            if len(metadata_blob) != meta_len:
                raise LibraryError("CE3510", path=str(library_path),
                                   expected=meta_len, actual=len(metadata_blob))

            # Read bitcode length
            bc_len_bytes = f.read(8)
            if len(bc_len_bytes) != 8:
                raise LibraryError("CE3511", path=str(library_path),
                                   expected=8, actual=len(bc_len_bytes))
            bc_len = struct.unpack("<Q", bc_len_bytes)[0]

            # Read bitcode blob
            bitcode = f.read(bc_len)
            if len(bitcode) != bc_len:
                raise LibraryError("CE3511", path=str(library_path),
                                   expected=bc_len, actual=len(bitcode))

            # Check file size sanity
            total_size = 16 + 28 + 8 + meta_len + 8 + bc_len
            if total_size > LibraryFormat.MAX_FILE_SIZE:
                raise LibraryError("CE3513", path=str(library_path),
                                   size=total_size, max_size=LibraryFormat.MAX_FILE_SIZE)

        # Deserialize metadata
        try:
            metadata = msgpack.unpackb(metadata_blob, raw=False)
        except Exception as e:
            raise LibraryError("CE3512", path=str(library_path), reason=str(e))

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
        from backend.library_linker import LibraryError

        with open(library_path, 'rb') as f:
            # Read and validate magic
            magic = f.read(16)
            if len(magic) < 16:
                raise LibraryError("CE3510", path=str(library_path),
                                   expected=16, actual=len(magic))

            if magic != LibraryFormat.MAGIC:
                raise LibraryError("CE3508", path=str(library_path))

            # Read version (skip spares)
            header_rest = f.read(28)
            if len(header_rest) < 28:
                raise LibraryError("CE3510", path=str(library_path),
                                   expected=28, actual=len(header_rest))

            version = struct.unpack("<I", header_rest[0:4])[0]
            if version != LibraryFormat.VERSION:
                raise LibraryError("CE3509", path=str(library_path),
                                   version=version, supported=LibraryFormat.VERSION)

            # Read metadata length
            meta_len_bytes = f.read(8)
            if len(meta_len_bytes) != 8:
                raise LibraryError("CE3510", path=str(library_path),
                                   expected=8, actual=len(meta_len_bytes))
            meta_len = struct.unpack("<Q", meta_len_bytes)[0]

            # Read metadata blob
            metadata_blob = f.read(meta_len)
            if len(metadata_blob) != meta_len:
                raise LibraryError("CE3510", path=str(library_path),
                                   expected=meta_len, actual=len(metadata_blob))

        # Deserialize metadata
        try:
            metadata = msgpack.unpackb(metadata_blob, raw=False)
        except Exception as e:
            raise LibraryError("CE3512", path=str(library_path), reason=str(e))

        return metadata
