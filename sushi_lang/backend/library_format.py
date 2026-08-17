"""Binary library format (.slib) for Sushi libraries."""
from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

import msgpack


def _read_bytes(f: BinaryIO, size: int, path: str, section: str) -> bytes:
    """Read exact number of bytes with truncation detection."""
    from sushi_lang.backend.library_errors import LibraryError

    data = f.read(size)
    if len(data) != size:
        # Two literal raises, not a variable code: the registry-completeness gate
        # (test_error_registry.py) only sees string-literal codes, and a code it
        # cannot see is a code it cannot prove is registered.
        if section == "metadata":
            raise LibraryError("CE3510", path=path, expected=size, actual=len(data))
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


class LibraryFormat:
    """Binary format reader/writer for .slib files."""

    MAGIC = b'\xf0\x9f\x8d\xa3SUSHILIB\xf0\x9f\x8d\xa3'
    # 3: every public-function parameter carries a `mode` field (borrow / nom /
    #    peek / poke). A v2 library states no mode, so its parameters cannot be
    #    told apart from unmarked ones -- CE3509 rejects it rather than guess.
    VERSION = 3
    FIXED_HEADER_SIZE = 52  # 16 (magic) + 4 (version) + 24 (spares) + 8 (meta_len)
    MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1GB sanity limit

    @staticmethod
    def write(output_path: Path, metadata: dict, bitcode: bytes) -> None:
        """Write .slib file with metadata and bitcode."""
        metadata_blob = msgpack.packb(metadata, use_bin_type=True)

        with open(output_path, 'wb') as f:
            f.write(LibraryFormat.MAGIC)

            f.write(struct.pack("<I", LibraryFormat.VERSION))
            f.write(struct.pack("<I", 0))  # SPARE_1
            f.write(struct.pack("<I", 0))  # SPARE_2
            f.write(struct.pack("<Q", 0))  # SPARE_3
            f.write(struct.pack("<Q", 0))  # SPARE_4

            f.write(struct.pack("<Q", len(metadata_blob)))
            f.write(metadata_blob)

            f.write(struct.pack("<Q", len(bitcode)))
            f.write(bitcode)

    @staticmethod
    def read(library_path: Path) -> tuple[dict, bytes]:
        """Read .slib file and return (metadata, bitcode)."""
        from sushi_lang.backend.library_errors import LibraryError

        path = str(library_path)

        with open(library_path, 'rb') as f:
            metadata = _read_header_and_metadata(f, path)

            bc_len = struct.unpack("<Q", _read_bytes(f, 8, path, "bitcode"))[0]
            bitcode = _read_bytes(f, bc_len, path, "bitcode")

            total_size = f.tell()
            if total_size > LibraryFormat.MAX_FILE_SIZE:
                raise LibraryError("CE3513", path=path,
                                   size=total_size, max_size=LibraryFormat.MAX_FILE_SIZE)

        return metadata, bitcode

    @staticmethod
    def read_metadata_only(library_path: Path) -> dict:
        """Read only metadata from .slib file (for introspection)."""
        with open(library_path, 'rb') as f:
            return _read_header_and_metadata(f, str(library_path))
