# Library Format Specification

[← Back to Documentation](index.md) | [Libraries](libraries.md)

Technical specification for the `.slib` library container.

## Overview

Sushi libraries use the `.slib` format: one container that holds a MessagePack index next
to a payload. The payload is Sushi source text, or LLVM bitcode, or both. The header states
which, in the `KIND` field.

**Source is the default.** A source library carries no machine code, so one file works on
every platform; the consumer compiles its units and caches the objects. A binary library is
the opt-in (`--lib-kind binary`) and is bound to the platform that built it.

**Key features:**
- Single file distribution (no separate manifest)
- Efficient binary metadata (MessagePack)
- One artifact for every platform, when the payload is source
- Forward-compatible via version field and reserved space
- Fast introspection with `--lib-info`

> Contributor-level design: see [design/libraries.md](design/libraries.md) for how the
> `templates` section is produced/consumed (the export closure, perk-impl shipping,
> the two link paths a consumer uses).

## Binary Layout

```
┌─────────────────────────────────────────────────────────────┐
│ MAGIC (16 bytes): 🍣SUSHILIB🍣 (UTF-8)                      │
│   0xF0 0x9F 0x8D 0xA3 "SUSHILIB" 0xF0 0x9F 0x8D 0xA3        │
├─────────────────────────────────────────────────────────────┤
│ VERSION (4 bytes): uint32 LE (current: 4)                   │
├─────────────────────────────────────────────────────────────┤
│ FLAGS (4 bytes): uint32 LE (bit 0: source blob compressed)  │
├─────────────────────────────────────────────────────────────┤
│ KIND (4 bytes): uint32 LE (1 source, 2 binary, 3 hybrid)    │
├─────────────────────────────────────────────────────────────┤
│ SPARE_3 (8 bytes): uint64 LE (reserved, must be 0)          │
├─────────────────────────────────────────────────────────────┤
│ SPARE_4 (8 bytes): uint64 LE (reserved, must be 0)          │
├─────────────────────────────────────────────────────────────┤
│ METADATA_LENGTH (8 bytes): uint64 LE                        │
├─────────────────────────────────────────────────────────────┤
│ METADATA_BLOB (N bytes): MessagePack-encoded dict           │
├─────────────────────────────────────────────────────────────┤
│ SOURCE_LENGTH (8 bytes): uint64 LE                          │
├─────────────────────────────────────────────────────────────┤
│ SOURCE_BLOB (S bytes): MessagePack map, unit name -> source │
├─────────────────────────────────────────────────────────────┤
│ BITCODE_LENGTH (8 bytes): uint64 LE                         │
├─────────────────────────────────────────────────────────────┤
│ BITCODE_BLOB (M bytes): Raw LLVM bitcode                    │
└─────────────────────────────────────────────────────────────┘
```

**Fixed header size:** 52 bytes (before variable-length sections). Version 4 claimed two
reserved fields for `FLAGS` and `KIND`, so the header did not change size.

**Endianness:** Little-endian (matches x86-64/ARM64 targets)

`SOURCE_LENGTH` is zero when `KIND` is binary. `BITCODE_LENGTH` is zero when `KIND` is
source. A hybrid carries both payloads.

## Field Details

### Magic Bytes

16-byte UTF-8 string identifying the file format:

```
🍣SUSHILIB🍣
```

Byte sequence: `\xF0\x9F\x8D\xA3SUSHILIB\xF0\x9F\x8D\xA3`

Each sushi emoji is 4 UTF-8 bytes, total magic is 16 bytes.

### Version

4-byte unsigned integer (little-endian). Current version: `4`.

Used for forward compatibility checks. A reader accepts version 4 only; anything else is
**CE3509**. There is no upgrade shim, and none is planned: Sushi has no users in the wild,
so an older `.slib` is rejected rather than read with a guess.

- **Version 3** added the per-parameter `mode` field (`borrow` / `nom` / `peek` / `poke`),
  which carries who frees each argument across the boundary. A version-2 file states no
  mode, so its parameters cannot be told apart from unmarked ones. See
  `docs/design/borrow-model.md`.
- **Version 4** made source the primary payload. `SPARE_1` and `SPARE_2` became `FLAGS` and
  `KIND`, and a length-prefixed source section joined the container between the metadata and
  the bitcode. The manifest gained `library_version`, `requires_compiler`, `kind` and
  `units`.

### Flags

4-byte bit field. Bit 0 marks the source blob as compressed.

The bit is claimed so the format is ready for it, and it is **always written as zero**
today. Nori archives are already `tar.gz`, so distribution is compressed regardless, and a
self-hosted reader would need an inflate written in Sushi. The metadata blob is never
compressed whatever happens: it is the index, and every reader must take it cheaply.

### Kind

4-byte unsigned integer, and the authority on which payload sections a file carries:

| Value | Kind | Carries |
|-------|--------|-------------------------|
| 1 | source | the source section |
| 2 | binary | the bitcode section |
| 3 | hybrid | both |

The manifest repeats the value as the `kind` string, so a reader can branch on the header
before it unpacks any MessagePack.

### Reserved Fields

16 bytes of reserved space (SPARE_3 and SPARE_4) for future extensions, such as checksums
or additional section offsets. Both must be zero in version 4.

### Metadata Section

Variable-length MessagePack-encoded dictionary containing library information.

**Preceded by:** 8-byte length field (uint64 LE)

### Source Section

Variable-length MessagePack map from unit name to that unit's complete source text. Whole
files, not per-declaration slices: a source library has no export closure to compute,
because it leaves nothing out. Private declarations ship with the rest.

**Preceded by:** 8-byte length field (uint64 LE)

### Bitcode Section

Variable-length raw LLVM bitcode (identical to `.bc` files).

**Preceded by:** 8-byte length field (uint64 LE)

## Metadata Schema

The manifest is an **index, not the authority**. Everything a library contains must be
knowable from it alone, so `--lib-info` never parses source to answer what a library holds.
For a source library the index is derived from the units at build time: the source section
is the authority, and the index is a cache of it.

```python
{
    "sushi_lib_version": "2.0",        # Protocol version
    "library_name": str,               # Library identifier, from the output filename
    "library_version": str,            # The library's own version, "major.minor.patch"
    "kind": str,                       # "source" / "binary" / "hybrid", matching KIND
    "units": [str],                    # Unit names present in the source section
    "requires_compiler": str,          # Compiler constraint, e.g. "~0.11" ("" if unknown)
    "compiled_at": str,                # ISO 8601 timestamp
    "platform": str,                   # "darwin", "linux", "windows"
                                       #   meaningful only when kind != "source"
    "compiler_version": str,           # Exactly which compiler built the file

    # The three lists below carry only CONCRETE declarations. A generic function,
    # struct or enum is filtered out of them and routes to "templates" instead: a
    # generic is not a concrete callable, and listing one here would hand the
    # consumer a signature with unresolved type parameters.
    "public_functions": [
        {
            "name": str,
            "params": [{"name": str, "type": str, "mode": str}],
            "return_type": str
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
            "is_generic": False,       # Always False; a generic struct is a template
            "type_params": []          # Always empty, for the same reason
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
            "is_generic": False,       # Always False; a generic enum is a template
            "type_params": []          # Always empty, for the same reason
        }
    ],

    "dependencies": [str],             # Stdlib/library dependencies

    # Written only when kind != "source". A source library ships whole units, so
    # every generic in it is already there as ordinary source.
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
                "params": [{"name": str, "type": str, "mode": str}],
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
| CE3503 | The library's `requires_compiler` excludes the running compiler |
| CE3505 | No `library_version` available at build time (no `nori.toml`, no `--lib-version`) |
| CE3506 | Source section truncated |
| CE3508 | Invalid magic bytes (not a valid `.slib` file) |
| CE3509 | Unsupported format version |
| CE3510 | Metadata section truncated |
| CE3511 | Bitcode section truncated |
| CE3512 | Invalid MessagePack metadata |
| CE3513 | File exceeds maximum size (1GB) |

One truncation code per section rather than one shared code: the message names which
section is short, and that is what tells a reader where the file was cut.

## Inspecting Libraries

Use `--lib-info` to display library metadata:

```bash
./sushic --lib-info mylib.slib
```

Example output:

```
Library: mylib
Version: 1.0.0
Kind: source
Compiler: 0.11.1
Requires compiler: ~0.11
Compiled: 2026-08-23T10:30:00+00:00
Protocol: 2.0

Units (1):
  mylib

Public Functions (2):
  fn add(i32 a, i32 b) i32
  fn multiply(i32 a, i32 b) i32

Structs (1):
  struct Point:
    i32 x
    i32 y

Source: 1,204 bytes
```

## Implementation Notes

### Reading

1. Read and validate 16-byte magic
2. Read 4-byte version, reject if unsupported
3. Read 4-byte flags and 4-byte kind, skip 16 bytes of reserved fields
4. Read 8-byte metadata length
5. Read metadata blob, deserialize with MessagePack
6. Read 8-byte source length
7. Read source blob, deserialize with MessagePack (skip it to reach the bitcode)
8. Read 8-byte bitcode length
9. Read bitcode blob

A reader that wants only part of this stops early: `read_metadata_only` stops after step 5,
`read_source_only` after step 7, and `read_section_sizes` reports both payload lengths
without keeping either blob.

### Writing

1. Write 16-byte magic
2. Write 4-byte version (4)
3. Write 4-byte flags (0), then 4-byte kind
4. Write 16 bytes of zeros (reserved)
5. Serialize metadata to MessagePack
6. Write 8-byte metadata length, then the metadata blob
7. Serialize the unit source map to MessagePack (empty for a binary library)
8. Write 8-byte source length, then the source blob
9. Write 8-byte bitcode length, then the bitcode blob (both empty for a source library)

## See Also

- [Libraries](libraries.md) - Creating and using libraries
- [Compiler Reference](compiler-reference.md) - `--lib` and `--lib-info` flags
- [Standard Library Build](internals/stdlib-build.md) - How stdlib is built
