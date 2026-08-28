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
    "sushi_lib_version": "2.2",        # Protocol version
    "library_name": str,               # Library identifier, from the output filename
    "library_version": str,            # The library's own version, "major.minor.patch"
    "kind": str,                       # "source" / "binary" / "hybrid", matching KIND
    "units": [str],                    # Unit names present in the source section
    "requires_compiler": str,          # Compiler constraint, e.g. "~0.11" ("" if unknown)
    "compiled_at": str,                # ISO 8601 timestamp
    "platform": str,                   # "darwin", "linux", "windows"
                                       #   meaningful only when kind != "source"
    "compiler_version": str,           # Exactly which compiler built the file

    # The three lists below carry only CONCRETE declarations that the library MARKS
    # `public`. A generic function, struct or enum is filtered out of them and routes
    # to "templates" instead: a generic is not a concrete callable, and listing one
    # here would hand the consumer a signature with unresolved type parameters. An
    # unmarked declaration is not API and goes to "not_exported" instead -- protocol
    # 2.1, and the reason an older `.slib` has to be rebuilt.
    #
    # Protocol 2.2 adds two keys, and they answer different questions. `unit` names the
    # unit that DECLARED the record, and every record carries it: a consumer binding
    # `use <lib/foo/bar> as f` binds the alias to the unit `bar`, and for a binary
    # library the manifest is the only place that can say which unit a name came from.
    # `link_symbol` names the symbol the record HAS IN THE SHIPPED BITCODE, and only a
    # record that has one carries it. See "The two symbol keys" below.
    # DOC is the parsed parts of one `##: ... :##` block. Every field is optional and an
    # empty one is OMITTED, so a reader cannot mistake an absent field for an empty
    # string. The whole block is deliberately not stored. A record that names a symbol
    # an author can document carries this key, and the key is ABSENT when there is no
    # block -- an undocumented library grows by nothing.
    #
    #   DOC = {
    #       "summary": str,            # The first paragraph
    #       "body":    str,            # The prose between the summary and the first tag
    #       "params":  {str: str},     # `- Parameter` text, keyed by parameter NAME
    #       "returns": str,            # `- Returns:` text
    #       "errors":  str,            # `- Errors:` text
    #       "examples": [{"caption": str, "code": str}]
    #   }                              #   in source order; `caption` is the tag's own
    #                                  #   text and is absent when it has none. The fence
    #                                  #   attributes are a harness instruction, not docs
    #
    # A PARAMETER record deliberately carries no doc: its text lives in the enclosing
    # symbol's `doc.params`. So does a private or closure-path record: a private symbol
    # is not part of the documented API.

    # A SIGNATURE is three keys, built by one function (`signature_record`) so the
    # concrete record, the generic record and the closure record cannot drift apart.
    # `error_type` is absent when the declaration does not spell one: the default is
    # StdError, and a record that named the default would claim the author wrote it.
    #
    #   SIG = {
    #       "params": [{"name": str, "type": str, "mode": str}],
    #       "return_type": str,
    #       "error_type": str          # If the declaration says `| E`
    #   }
    #
    # Every `type` and `return_type` is the INTERNAL identity spelling, `List<i32>` and
    # not `List@(i32)`: a consumer reads these back with `parse_type_string`, so this is
    # a wire format. Rendering `@(...)` is the report's job.

    "public_functions": [
        {
            "name": str,
            "unit": str,               # The unit that declared it
            "link_symbol": str,        # Its symbol in the shipped bitcode
            **SIG,
            "doc": DOC                 # If documented
        }
    ],

    "public_constants": [
        {
            "name": str,
            "unit": str,               # The unit that declared it
            "type": str,
            "source": str,             # The whole `public const ...` declaration
            "doc": DOC                 # If documented
        }
    ],

    "structs": [
        {
            "name": str,
            "unit": str,               # The unit that declared it
            "fields": [{"name": str, "type": str, "doc": DOC}],
            "is_generic": False,       # Always False; a generic struct is a template
            "type_params": [],         # Always empty, for the same reason
            "doc": DOC                 # If documented
        }
    ],

    "enums": [
        {
            "name": str,
            "unit": str,               # The unit that declared it
            "variants": [
                {
                    "name": str,
                    "has_data": bool,
                    "data_type": str,  # If has_data
                    "doc": DOC         # If documented
                }
            ],
            "is_generic": False,       # Always False; a generic enum is a template
            "type_params": [],         # Always empty, for the same reason
            "doc": DOC                 # If documented
        }
    ],

    # A unit's OWN doc block -- the one that stands first in its file and documents no
    # declaration. A map beside "units" and not a change to it: "units" is an ordered
    # list and the order is load-bearing for the consumer's injection. Keyed by unit
    # name, over the library's own units only, so a bundled stdlib module cannot leak
    # in. The whole key is absent when no unit carries a block.
    "unit_docs": {str: DOC},

    "dependencies": [str],             # Stdlib/library dependencies

    # Written for EVERY kind. A source library ships whole units, so a generic in it is
    # already there as ordinary source -- but the index must answer without a parser, and
    # a template's own doc block stands OUTSIDE its source slice, so the record is the
    # only place it can travel.
    "templates": {                     # Instantiable cross-library templates
        "version": 4,                  # Templates schema version

        # Generic functions (incl. variadic packs), as re-parsable source
        # slices; monomorphized at the consumer's call sites. Public ones plus
        # export-closure PRIVATE helpers (flagged "private": true - the
        # consumer applies CE5007 clash, not local-wins, semantics to those).
        "generic_functions": [
            {
                "name": str,
                "unit": str,           # The unit that declared it
                "type_params": [{"name": str, "constraints": [str], "is_pack": bool}],
                **SIG,                 # A template's signature, so its `- Parameter`
                                       #   tags name something a report can print
                "source": str,         # Self-contained, re-parsable decl text
                "free_perks": [str],   # Perk names from type-param bounds
                "private": bool,       # Present (true) for closure-shipped helpers
                "doc": DOC             # If documented, and never when private
            }
        ],

        # Generic structs/enums, same record shape MINUS the signature: neither declares
        # parameters.
        "generic_structs": [ ... ],
        "generic_enums": [ ... ],

        # Perk DEFINITIONS referenced by exported generics' constraints. There is no
        # methods array here, so a perk method's own block travels only inside `source`.
        "perks": [
            {"name": str, "unit": str, "source": str, "doc": DOC}
        ],

        # Concrete perk IMPLEMENTATIONS of those perks (v3). Bodies live in
        # the bitcode (weak linkage); the record carries signatures (source)
        # and symbol names for declare-and-link at the consumer.
        "perk_impls": [
            {
                "type": str,           # Concrete target type name
                "perk": str,
                "unit": str,           # The unit that declared it
                "source": str,         # The whole `extend T with P:` block
                "methods": [{"name": str, "symbol": str, "doc": DOC}],
                "doc": DOC             # If documented
            }
        ],

        # Export closure (v4): private symbols exported generics transitively
        # reference. Concrete helpers ship as signature records (definitions
        # carry external linkage in the bitcode); constants and types ship with
        # source -- the consumer needs a constant's value for compile-time
        # evaluation, and a type's shape to register it before a monomorphized
        # template body names it.
        "private_functions": [
            {                          # No doc: a private symbol is not documented API
                "name": str,
                "unit": str,           # The unit that declared it
                "link_symbol": str,    # Its symbol in the shipped bitcode
                **SIG,
            }
        ],
        "constants": [
            {"name": str, "source": str}
        ],
        "private_types": [             # A private struct or enum a template body names
            {"name": str, "source": str}
        ],
        "closure_summary": {           # What shipped, by kind (sorted names)
            "private_functions": [str],
            "private_generic_functions": [str],
            "constants": [str],
            "private_types": [str]
        }
    },

    # What the library DECLARES and does not export -- the closure's complement, and the
    # other half of the same bookkeeping: a private is named in exactly one of the two.
    # A name and its kind, and nothing else: no signature, no body, no source. Written
    # for every kind, and the whole key is ABSENT when a library keeps nothing.
    #
    # It exists so that a consumer naming one hears CE3005 rather than CE2008 (#469): on
    # the binary path the symbol is in the consumer's tables not at all, and "undefined"
    # was the wrong word for a function the library defines and deliberately kept. A name
    # here is not shipped and clashes with nothing, so a consumer may declare its own
    # function of the same name. A kept TYPE answers the same way from the type funnel,
    # where the wrong word was "unknown type". A kept CONSTANT answers from the scope
    # pass, which lets the name through so the type pass can say whose it is (#487): a
    # PUBLIC constant registers from `public_constants[].source`, so "no such name" would
    # be the wrong word for the one next to it that the library kept.
    #
    # A name the CLOSURE ships is not here. Each private is named in exactly one of the
    # two places, and the closure carries a private constant and a private type as
    # SOURCE (`templates.constants`, `templates.private_types`), because a monomorphized
    # template body names them and the consumer has to register them.
    "not_exported": [
        {
            "name": str,
            "kind": str                # "function" / "generic_function" / "struct"
                                       #   / "enum" / "constant"
        }
    ]
}
```

## The two symbol keys

Protocol 2.2 gives a record two ways to name where it came from, and neither substitutes
for the other.

**`unit` says whose declaration this is.** Every record carries it. A Sushi symbol is
`<unit>$<name>` (`docs/design/unit-namespaces.md` section 9), so two units may each
declare `helper`, and a consumer that wants a namespace has to know which unit a name
belongs to. A source library's units are in the source section and could be re-read for
it; a BINARY library ships no source at all, so the manifest is the only place that can
answer.

**`link_symbol` says what the shipped bitcode calls it**, and only a record with a symbol
in that bitcode carries one: a public function, and an export-closure private function.
It is **written by every build and read by the BINARY path alone.**

That asymmetry is the point:

- A **source** library recompiles at the consumer, and its units are renamed to
  `lib/<library>/<unit>` on the way in. The consumer's own mangling therefore produces
  `lib$<library>$<unit>$helper` and never the producer's `<unit>$helper`. Reading the
  producer's field there would name a symbol that does not exist in the consumer's build.
- A **binary** library links. Its private closure helpers ship as signatures with
  `"source": null` -- the body is in the bitcode -- so the consumer compiles a
  re-monomorphized template body that CALLS a name it cannot derive from anything it can
  see. `link_symbol` is that name.

**A record without a symbol takes no `link_symbol`.** A public CONSTANT ships its
`source` and is re-evaluated at the consumer. A TEMPLATE ships source and is
monomorphized there, so its instances take the consumer's mangling. A PERK-IMPL method
already carries `symbol`, and it needs nothing more: a method's symbol is derived from the
receiver's TYPE, which is nominal and program-wide, so there is no unit in it to record.

There is **no scheme identifier**. A manifest records what is, not the recipe, and
`compiler_version` already says which compiler wrote it -- with `requires_compiler`
(CE3503) refusing a `.slib` the running compiler may not consume.

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
Protocol: 2.2

Units (1):
  mylib
    Arithmetic that reports its own failures.

Public Functions (3):
  fn add(i32 a, i32 b) i32
    Adds two numbers.
    - Parameter a: The first addend.
    - Parameter b: The second addend.
    - Returns: The sum.
  fn multiply(i32 a, i32 b) i32
  fn shout(nom string s) string

Public Structs (1):
  struct Point:
    A point in the plane.
    i32 x
      The distance along x.
    i32 y
      The distance along y.

Source: 1,204 bytes
```

A documented symbol prints its block two spaces further in than its own line, and a symbol
with no block prints exactly as it always did. `multiply` above has no doc block; `shout`
has none either, and its `nom` shows the one parameter mode a type cannot spell (`peek` and
`poke` are part of the type string). `docs/documentation-blocks.md` carries the record and
what does not travel in it.

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
