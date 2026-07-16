# Library Format Specification

[← Back to Documentation](index.md) | [Libraries](libraries.md)

Technical specification for the `.slib` binary library format.

## Overview

Sushi libraries use the `.slib` format, a binary container that combines LLVM bitcode with MessagePack-encoded metadata in a single file.

**Key features:**
- Single file distribution (no separate manifest)
- Efficient binary metadata (MessagePack)
- Forward-compatible via version field and reserved space
- Fast introspection with `--lib-info`

## Binary Layout

```
┌─────────────────────────────────────────────────────────────┐
│ MAGIC (16 bytes): 🍣SUSHILIB🍣 (UTF-8)                      │
│   0xF0 0x9F 0x8D 0xA3 "SUSHILIB" 0xF0 0x9F 0x8D 0xA3        │
├─────────────────────────────────────────────────────────────┤
│ VERSION (4 bytes): uint32 LE (current: 2)                   │
├─────────────────────────────────────────────────────────────┤
│ SPARE_1 (4 bytes): uint32 LE (reserved, must be 0)          │
├─────────────────────────────────────────────────────────────┤
│ SPARE_2 (4 bytes): uint32 LE (reserved, must be 0)          │
├─────────────────────────────────────────────────────────────┤
│ SPARE_3 (8 bytes): uint64 LE (reserved, must be 0)          │
├─────────────────────────────────────────────────────────────┤
│ SPARE_4 (8 bytes): uint64 LE (reserved, must be 0)          │
├─────────────────────────────────────────────────────────────┤
│ METADATA_LENGTH (8 bytes): uint64 LE                        │
├─────────────────────────────────────────────────────────────┤
│ METADATA_BLOB (N bytes): MessagePack-encoded dict           │
├─────────────────────────────────────────────────────────────┤
│ BITCODE_LENGTH (8 bytes): uint64 LE                         │
├─────────────────────────────────────────────────────────────┤
│ BITCODE_BLOB (M bytes): Raw LLVM bitcode                    │
└─────────────────────────────────────────────────────────────┘
```

**Fixed header size:** 52 bytes (before variable-length sections)

**Endianness:** Little-endian (matches x86-64/ARM64 targets)

## Field Details

### Magic Bytes

16-byte UTF-8 string identifying the file format:

```
🍣SUSHILIB🍣
```

Byte sequence: `\xF0\x9F\x8D\xA3SUSHILIB\xF0\x9F\x8D\xA3`

Each sushi emoji is 4 UTF-8 bytes, total magic is 16 bytes.

### Version

4-byte unsigned integer (little-endian). Current version: `1`.

Used for forward compatibility checks. Readers should reject files with unsupported versions.

### Reserved Fields

24 bytes of reserved space (SPARE_1 through SPARE_4) for future extensions:

- Compression flags
- Checksums
- Additional metadata offsets

All spare fields must be zero in version 2.

### Metadata Section

Variable-length MessagePack-encoded dictionary containing library information.

**Preceded by:** 8-byte length field (uint64 LE)

### Bitcode Section

Variable-length raw LLVM bitcode (identical to `.bc` files).

**Preceded by:** 8-byte length field (uint64 LE)

## Metadata Schema

```python
{
    "sushi_lib_version": "1.0",        # Protocol version
    "library_name": str,               # Library identifier
    "compiled_at": str,                # ISO 8601 timestamp
    "platform": str,                   # "darwin", "linux", "windows"
    "compiler_version": str,           # Compiler version used

    "public_functions": [
        {
            "name": str,
            "params": [{"name": str, "type": str}],
            "return_type": str,
            "is_generic": bool,
            "type_params": [str]       # If is_generic
        }
    ],

    "public_constants": [
        {
            "name": str,
            "type": str
        }
    ],

    "structs": [
        {
            "name": str,
            "fields": [{"name": str, "type": str}],
            "is_generic": bool,
            "type_params": [str]       # If is_generic
        }
    ],

    "enums": [
        {
            "name": str,
            "variants": [
                {
                    "name": str,
                    "has_data": bool,
                    "data_type": str   # If has_data
                }
            ],
            "is_generic": bool,
            "type_params": [str]       # If is_generic
        }
    ],

    "dependencies": [str],             # Stdlib/library dependencies

    "templates": {                     # Instantiable cross-library templates
        "version": 4,                  # Templates schema version

        # Generic functions (incl. variadic packs), as re-parsable source
        # slices; monomorphized at the consumer's call sites. Public ones plus
        # export-closure PRIVATE helpers (flagged "private": true - the
        # consumer applies CE5007 clash, not local-wins, semantics to those).
        "generic_functions": [
            {
                "name": str,
                "type_params": [{"name": str, "constraints": [str], "is_pack": bool}],
                "source": str,         # Self-contained, re-parsable decl text
                "free_perks": [str],   # Perk names from type-param bounds
                "private": bool        # Present (true) for closure-shipped helpers
            }
        ],

        # Generic structs/enums, same record shape as generic_functions.
        "generic_structs": [ ... ],
        "generic_enums": [ ... ],

        # Perk DEFINITIONS referenced by exported generics' constraints.
        "perks": [
            {"name": str, "source": str}
        ],

        # Concrete perk IMPLEMENTATIONS of those perks (v3). Bodies live in
        # the bitcode (weak linkage); the record carries signatures (source)
        # and symbol names for declare-and-link at the consumer.
        "perk_impls": [
            {
                "type": str,           # Concrete target type name
                "perk": str,
                "source": str,         # The whole `extend T with P:` block
                "methods": [{"name": str, "symbol": str}]
            }
        ],

        # Export closure (v4): private symbols exported generics transitively
        # reference. Concrete helpers ship as signature records (definitions
        # carry external linkage in the bitcode); constants ship with source
        # (the consumer needs the value for compile-time evaluation).
        "private_functions": [
            {
                "name": str,
                "params": [{"name": str, "type": str}],
                "return_type": str
            }
        ],
        "constants": [
            {"name": str, "source": str}
        ],
        "closure_summary": {           # What shipped, by kind (sorted names)
            "private_functions": [str],
            "private_generic_functions": [str],
            "constants": [str]
        }
    }
}
```

## Error Codes

| Code | Description |
|------|-------------|
| CE3508 | Invalid magic bytes (not a valid `.slib` file) |
| CE3509 | Unsupported format version |
| CE3510 | Metadata section truncated |
| CE3511 | Bitcode section truncated |
| CE3512 | Invalid MessagePack metadata |
| CE3513 | File exceeds maximum size (1GB) |

## Inspecting Libraries

Use `--lib-info` to display library metadata:

```bash
./sushic --lib-info mylib.slib
```

Example output:

```
Library: mylib
Platform: darwin
Compiler: 0.6.0
Compiled: 2025-12-20T10:30:00+00:00
Protocol: 1.0

Public Functions (2):
  fn add(i32 a, i32 b) i32
  fn multiply(i32 a, i32 b) i32

Structs (1):
  struct Point:
    i32 x
    i32 y

Bitcode: 5,432 bytes
```

## Implementation Notes

### Reading

1. Read and validate 16-byte magic
2. Read 4-byte version, reject if unsupported
3. Skip 24 bytes of reserved fields
4. Read 8-byte metadata length
5. Read metadata blob, deserialize with MessagePack
6. Read 8-byte bitcode length
7. Read bitcode blob

### Writing

1. Write 16-byte magic
2. Write 4-byte version (1)
3. Write 24 bytes of zeros (reserved)
4. Serialize metadata to MessagePack
5. Write 8-byte metadata length
6. Write metadata blob
7. Write 8-byte bitcode length
8. Write bitcode blob

## See Also

- [Libraries](libraries.md) - Creating and using libraries
- [Compiler Reference](compiler-reference.md) - `--lib` and `--lib-info` flags
- [Standard Library Build](internals/stdlib-build.md) - How stdlib is built
