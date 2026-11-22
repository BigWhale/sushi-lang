# Library Format Specification

[← Back to Documentation](index.md) | [Libraries](libraries.md)

Technical specification for the `.slib` library container.

## Overview

Sushi libraries use the `.slib` format: one container that holds a MessagePack index next
to the Sushi source text of the library. A library carries no machine code, so one file
works on every platform; the consumer compiles its units and caches the objects.

**Key features:**
- Single file distribution (no separate manifest)
- Efficient binary metadata (MessagePack)
- One artifact for every platform
- Forward-compatible via version field and reserved space
- Fast introspection with `--lib-info`

> Contributor-level design: see [design/libraries.md](design/libraries.md) for how the
> manifest is produced and how a consumer reads a library.

## Binary Layout

```
┌─────────────────────────────────────────────────────────────┐
│ MAGIC (16 bytes): 🍣SUSHILIB🍣 (UTF-8)                      │
│   0xF0 0x9F 0x8D 0xA3 "SUSHILIB" 0xF0 0x9F 0x8D 0xA3        │
├─────────────────────────────────────────────────────────────┤
│ VERSION (4 bytes): uint32 LE (current: 6)                   │
├─────────────────────────────────────────────────────────────┤
│ FLAGS (4 bytes): uint32 LE (bit 0: source blob compressed)  │
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
│ SOURCE_LENGTH (8 bytes): uint64 LE                          │
├─────────────────────────────────────────────────────────────┤
│ SOURCE_BLOB (S bytes): MessagePack map, unit name -> source │
└─────────────────────────────────────────────────────────────┘
```

**Fixed header size:** 52 bytes (before variable-length sections).

**Endianness:** Little-endian (matches x86-64/ARM64 targets)

The source section is the last section. Nothing follows it.

## Field Details

### Magic Bytes

16-byte UTF-8 string identifying the file format:

```
🍣SUSHILIB🍣
```

Byte sequence: `\xF0\x9F\x8D\xA3SUSHILIB\xF0\x9F\x8D\xA3`

Each sushi emoji is 4 UTF-8 bytes, total magic is 16 bytes.

### Version

4-byte unsigned integer (little-endian). Current version: `6`.

Used for forward compatibility checks. A reader accepts version 6 only; anything else is
**[CE3509](error-catalog.md#ce3509)**. There is no upgrade shim, and none is planned: Sushi has no users in the wild,
so a file of another version is rejected rather than read with a guess. Rebuild the
library.

Version 6 removed the bitcode section and the kind field of version 5. A library that an
older compiler built is therefore CE3509.

### Flags

4-byte bit field. Bit 0 marks the source blob as compressed.

The bit is claimed so the format is ready for it, and it is **always written as zero**
today. Nori archives are already `tar.gz`, so distribution is compressed regardless, and a
self-hosted reader would need an inflate written in Sushi. The metadata blob is never
compressed whatever happens: it is the index, and every reader must take it cheaply.

### Reserved Fields

20 bytes of reserved space (SPARE_2, SPARE_3 and SPARE_4) for future extensions, such as
checksums or additional section offsets. A writer writes zeros, and a reader does not
check them. SPARE_2 was the KIND field before version 6.

### Metadata Section

Variable-length MessagePack-encoded dictionary containing library information.

**Preceded by:** 8-byte length field (uint64 LE)

### Source Section

Variable-length MessagePack map from unit name to that unit's complete source text. Whole
files, not per-declaration slices: a library leaves nothing out. Private declarations ship
with the rest.

**Preceded by:** 8-byte length field (uint64 LE)

## Metadata Schema

The manifest is an **index, not the authority**. Everything a library contains must be
knowable from it alone, so `--lib-info` never parses source to answer what a library holds.
The index is derived from the units at build time: the source section is the authority,
and the index is a cache of it. The consumer reads `library_name`, `library_version`,
`requires_compiler` and `dependencies` to load the library graph, and compiles the source
section. `--lib-info` reads the other keys.

```python
{
    "sushi_lib_version": "3.0",        # Protocol version
    "library_name": str,               # Library identifier, from the output filename
    "library_version": str,            # The library's own version, "major.minor.patch"
    "units": [str],                    # Unit names present in the source section
                                       #   The library's OWN units, and every index
                                       #   below reads the same filter: a
                                       #   bundled stdlib module and an imported
                                       #   source library both arrive as ordinary
                                       #   compilation units, and each declares
                                       #   on its own account. `dependencies` is the
                                       #   exception -- it says what the consumer's
                                       #   build must load, not what this library
                                       #   declares.
    "requires_compiler": str,          # Compiler constraint, e.g. "~0.11" ("" if unknown)
    "compiler_version": str,           # Exactly which compiler built the file

    # The three lists below carry only CONCRETE declarations that the library MARKS
    # `public`. A generic function, struct or enum is filtered out of them and routes
    # to "templates" instead: a generic is not a concrete callable, and listing one
    # here would hand the reader a signature with unresolved type parameters. An
    # unmarked declaration is not API and is not in the index.
    #
    # `unit` names the unit that DECLARED the record, and every record carries it. See
    # "The unit key" below.
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
    # symbol's `doc.params`. A private symbol is not in the index.

    # A SIGNATURE is four keys, built by one function (`signature_record`) so the
    # concrete record and the generic record cannot drift apart.
    # `has_channel` is REQUIRED on every function, helper and method record: a reader
    # never guesses a channel from an absent key. It is true when
    # the declaration writes `| E` or returns an explicit `Result@(T, E)`, and false
    # when the callable is bare (`docs/design/error-channel.md`). There is no default
    # error type. `error_type` is present only when the declaration writes `| E`; an
    # explicit `Result@(T, E)` return carries its arms in `return_type`. `return_mode`
    # is present only for a borrowed return, `peek T` (#1229):
    # `return_type` holds `T`, and the consumer reads the source set from the
    # parameters (`docs/design/borrow-model.md` section 10h).
    #
    #   SIG = {
    #       "params": [{"name": str, "type": str, "mode": str,
    #                   "is_pack": bool,    # Present (true) for `...Ts args` alone
    #                   "variadic": bool}], # Present (true) for a native `...T` alone;
    #                                       #   `type` is then `T`, not the `T[]` of the
    #                                       #   body
    #       "return_type": str,
    #       "has_channel": bool,       # Required: does a call answer a Result
    #       "error_type": str,         # If the declaration says `| E`
    #       "return_mode": "peek"      # If the declaration says `peek T`
    #   }
    #
    # Every `type`, `return_type` and `error_type` is the INTERNAL identity spelling,
    # `List<i32>` and not `List@(i32)`. Rendering `@(...)` is the report's job.

    "public_functions": [
        {
            "name": str,
            "unit": str,               # The unit that declared it
            **SIG,
            "doc": DOC                 # If documented
        }
    ],

    "public_constants": [
        {
            "name": str,
            "unit": str,               # The unit that declared it
            "type": str,
            "doc": DOC                 # If documented
        }
    ],

    # A unit variable (`public var`, docs/design/unit-storage.md) mirrors a constant.
    # The consumer compiles the declaring unit and reads none of this.
    "public_variables": [
        {
            "name": str,
            "unit": str,
            "type": str,
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
                    "data_types": [str],  # If has_data: every payload type, in
                                          #   written order. A manifest that
                                          #   states `has_data` and no `data_types`
                                          #   is refused (CE3512) and must be rebuilt
                    "doc": DOC         # If documented
                }
            ],
            "is_error": bool,          # True for an enum declared with `error`
                                       #   (docs/design/error-conversion.md 2.1);
                                       #   only such an enum may be the E of a
                                       #   Result. A missing key reads False
            "is_generic": False,       # Always False; a generic enum is a template
            "type_params": [],         # Always empty, for the same reason
            "doc": DOC                 # If documented
        }
    ],

    # The CONVERSIONS between two error types that the library's own units declare
    # (`extend LowError as LibError:`, docs/design/error-conversion.md section 3). A
    # conversion lives in the unit of its TARGET, and it is as visible as that type,
    # so only a conversion into a public error type of the library ships. `source` and
    # `target` are the names of two non-generic error types, in the internal spelling.
    # A conversion is not an `extension` record. The whole key is absent when there is
    # no record.
    "conversions": [
        {
            "source": str,             # The source error type: "LowError"
            "target": str,             # The target error type: "LibError"
            "unit": str,               # The unit that declared it; optional
            "doc": DOC                 # If documented
        }
    ],

    # Each declaration of the library's own units that is marked `dont_panic`, with
    # its `because` text (docs/design/dont-panic.md, D10). `--lib-info` shows a
    # reviewer the unchecked code before the first build; the library carries the
    # marker in its text as well. A written declaration
    # only: a monomorphized copy is not listed. `name` is `fn_name` for a function or a
    # generic template, `Type.method` for an extension method, a static and a perk
    # implementation method (the type in the `@(...)` spelling), and `Source as Target`
    # for a conversion. `reason` is the raw text between the quotes. `unit` is the unit
    # that declares it; `--lib-info` prints it before `name`, because two units can each
    # declare a private function of one name. The whole key is absent when no
    # declaration is marked.
    "dont_panic": [
        {"unit": str, "name": str, "reason": str}
    ],

    # What each unit RE-EXPORTS: one record per `public use`, in written order.
    # `public use X` makes X's public names the unit's own, so the unit's importers get
    # them where its own names land. The consumer needs no record -- the units are in
    # the source section and the consumer parses the statement -- but the index must
    # answer without a parser, so the build writes it.
    #
    # `unit` is the unit that WROTE the statement, and only the library's own units
    # appear: a bundled stdlib module and an injected source library re-export
    # on their own account. `kind` says which producer the target is:
    #
    #   "unit"    -- a sibling unit of THIS library. `path` is the name the `units`
    #                index carries, so the consumer looks it up with no guessing
    #   "stdlib"  -- a stdlib module. `path` is the import path, `io/fs`
    #   "library" -- another library. `path` is the written `lib/...` path. The
    #                `dependencies` record of the same `use` is what LOADS it (#1120);
    #                a library that is not on the path is CE3502 with a note that
    #                names this library
    #
    # The whole key is absent when no unit says `public use`, so an ordinary library
    # grows by nothing. An absent key means no re-export.
    "reexports": [
        {"unit": str, "path": str, "kind": str}
    ],

    # A unit's OWN doc block -- the one that stands first in its file and documents no
    # declaration. A map beside "units" and not a change to it: "units" is an ordered
    # list and the order is load-bearing for the consumer's injection. Keyed by unit
    # name, over the library's own units only, so a bundled stdlib module cannot leak
    # in. The whole key is absent when no unit carries a block.
    "unit_docs": {str: DOC},

    # What a consumer's build must load (#1120): one record per stdlib module that the
    # build uses, and one per `use <lib/...>` of the library's OWN units, plain or
    # public, with the name and the version of the `.slib` that the build found. The
    # consumer loads every library of the graph from these records; a second version of
    # one library is CE3519. Visibility does not follow: a plain `use` gives the
    # consumer no name, and only a `public use` (`reexports`) hands names on.
    # `units` names the library's own units that write the `use` (#1123). The load and
    # the version check read the record whole. A module that only a unit the library
    # does not own uses has an empty list.
    "dependencies": [
        {"path": str, "kind": "stdlib", "units": [str]},
        {"path": str, "kind": "library", "library_name": str, "library_version": str,
         "units": [str]}
    ],

    # A library ships whole units, so a generic in it is already there as ordinary
    # source -- but the index must answer without a parser.
    # The index lists only what a consumer can see: a public item on a public type,
    # where a predefined perk is public.
    "templates": {                     # Instantiable cross-library templates
        # Public generic functions (incl. variadic packs).
        "generic_functions": [
            {
                "name": str,
                "unit": str,           # The unit that declared it
                "type_params": [{"name": str, "constraints": [str], "is_pack": bool}],
                **SIG,                 # A template's signature, so its `- Parameter`
                                       #   tags name something a report can print
                "free_perks": [str],   # Perk names from type-param bounds
                "doc": DOC             # If documented
            }
        ],

        # Public generic structs/enums, same record shape MINUS the signature: neither declares
        # parameters. A generic enum declared with `error` also carries
        # `"is_error": true`, so each instance at the consumer is an error type.
        "generic_structs": [ ... ],
        "generic_enums": [ ... ],

        # Perk DEFINITIONS: every public perk of the library. Each
        # method is a record: its signature, its
        # receiver mode when the contract declares `peek self` / `poke self`, and its
        # own block -- so `--lib-info` prints a contract as the methods that satisfy it.
        #
        #   METHOD = {
        #       "name": str,
        #       **SIG,
        #       "self_mode": str,      # "peek" | "poke", only when declared
        #       "doc": DOC             # If documented
        #   }
        "perks": [
            {"name": str, "unit": str, "methods": [METHOD], "doc": DOC}
        ],

        # Concrete perk IMPLEMENTATIONS that the library's own units write, of a public
        # perk or a predefined perk (`Drop`, `Hashable`, `Eq`, `Ord`, `Display`) on a
        # public type. The record carries the signature of each method.
        "perk_impls": [
            {
                "type": str,           # Concrete target type name
                "perk": str,
                "unit": str,           # The unit that declared it
                "methods": [METHOD],
                "doc": DOC             # If documented
            }
        ],

        # Generic-target perk IMPLEMENTATIONS: `extend Box@(T) with Show`
        # is a TEMPLATE. It names no instantiation, and the consumer cuts one copy per
        # instantiation of `Box` it names, exactly as for its own template. The
        # library's monomorphized copies are NOT in `perk_impls`.
        "generic_perk_impls": [
            {
                "type": str,           # The target's BASE name: "Box"
                "type_args": [str],    # The target's parameters as written: ["T"]
                "perk": str,
                "unit": str,           # The unit that declared it
                "methods": [METHOD],   # Written in the target's parameters
                "doc": DOC             # If documented
            }
        ],

        # Extension methods, instance and static: every PUBLIC one on a public type.
        # A CONCRETE one is a signature record. The whole key is absent when there is
        # no record.
        #
        #   EXTENSION = {
        #       "type": str,           # The target, in the internal spelling: "Vec",
        #                              #   "i32", "i32[]", "Box<T>", "T[]"
        #       "name": str,
        #       "public": bool,        # The `public` marker
        #       **SIG,
        #       "self_mode": str,      # "peek" | "poke", only when declared
        #       "static": bool,        # True, only for a static
        #       "unit": str,           # The unit that declared it
        #       "doc": DOC             # If documented
        #   }
        "extensions": [EXTENSION],

        # Extension TEMPLATES: a generic target (`Box@(T)`), a concrete-instance target
        # (`Box@(i32)`), an array target (`T[]`) and a method type parameter
        # (`pick@(U)`). A template names no instance, and the consumer makes one copy for each instance and method that a call reaches. Its
        # body may call a private function of the library. Only a public template on a
        # public type is listed. The whole key is absent when there is no record.
        "generic_extensions": [
            {
                **EXTENSION,
                "type_params": [{"name": str, "constraints": [str], "is_pack": bool}]
                                       # The method's own type parameters
            }
        ]
    }
}
```

## The unit key

**`unit` says whose declaration this is.** Every record carries it. A Sushi symbol is
`<unit>$<name>` (`docs/design/unit-namespaces.md` section 9), so two units may each
declare `helper`, and a reader that wants a namespace has to know which unit a name
belongs to. `--lib-info` prints it, for example before the name of a `dont_panic` record.

A record carries no link symbol. The consumer compiles the source section, and its units
are renamed to `lib/<library>/<unit>` on the way in, so the consumer derives every symbol
itself: `lib$<library>$<unit>$helper`.

There is **no scheme identifier**. A manifest records what is, not the recipe, and
`compiler_version` already says which compiler wrote it -- with `requires_compiler`
([CE3503](error-catalog.md#ce3503)) refusing a `.slib` the running compiler may not consume.

## Error Codes

| Code | Description |
|------|-------------|
| [CE3500](error-catalog.md#ce3500) | The `--lib` output path does not end in `.slib` |
| [CE3502](error-catalog.md#ce3502) | A `use <lib/...>` names a library that no search directory holds |
| [CE3503](error-catalog.md#ce3503) | The library's `requires_compiler` excludes the running compiler |
| [CE3505](error-catalog.md#ce3505) | No `library_version` available at build time (no `nori.toml`, no `--lib-version`) |
| [CE3506](error-catalog.md#ce3506) | Source section truncated |
| [CE3508](error-catalog.md#ce3508) | Invalid magic bytes (not a valid `.slib` file) |
| [CE3509](error-catalog.md#ce3509) | Unsupported format version (a container other than version 6) |
| [CE3510](error-catalog.md#ce3510) | Metadata section truncated |
| [CE3512](error-catalog.md#ce3512) | Invalid metadata: the MessagePack does not decode, a manifest field is missing or has the wrong type (`MANIFEST_SCHEMA`), a variant with `has_data` has no `data_types`, or a function or method record has no `has_channel` |
| [CE3513](error-catalog.md#ce3513) | File exceeds maximum size (1GB) |
| [CE3515](error-catalog.md#ce3515) | The file cannot be opened or read (a directory, no read permission, an I/O failure) |
| [CE3516](error-catalog.md#ce3516) | The path does not name a library file (the name of a `.slib` file ends in `.slib`) |
| [CE3517](error-catalog.md#ce3517) | The `nori.toml` in the working directory is not valid |
| [CE3518](error-catalog.md#ce3518) | The `nori.toml` in the working directory cannot be read |
| [CE3521](error-catalog.md#ce3521) | The `library_name` of the file is not the name of the `use` that loaded it (the last part of its path): the file was copied or renamed after its build |

One truncation code per section rather than one shared code: the message names which
section is short, and that is what tells a reader where the file was cut.

## Inspecting Libraries

Use `--lib-info` to display library metadata:

```bash
./sushic --lib-info mylib.slib --docs
```

Example output, with `--docs` (without it, no doc block prints):

```
Library: mylib
Version: 1.0.0
Compiler: 0.17.0
Requires compiler: ~0.14
Protocol: 3.0

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
    Hands the string back, and takes it over.

Public Structs (1):
  struct Point:
    A point in the plane.

    i32 x
      The distance along x.

    i32 y
      The distance along y.

Source: 643 bytes
```

A documented symbol prints its block two spaces further in than its own line, with a blank
line between the prose and each tag. A symbol with no block prints as a bare line: `multiply`
above has no doc block. The `nom` of `shout` shows the one parameter mode a type cannot spell
(`peek` and `poke` are part of the type string). `docs/documentation-blocks.md` carries the record and
what does not travel in it.

## Implementation Notes

### Reading

Before the file is opened, a path that does not end in `.slib` is [CE3516](error-catalog.md#ce3516). An OS error of the
open or of a read is [CE3515](error-catalog.md#ce3515).

1. Read and validate the 16-byte magic ([CE3508](error-catalog.md#ce3508))
2. Refuse a file larger than 1 GiB ([CE3513](error-catalog.md#ce3513)), before any section is read
3. Read the 4-byte version, reject it if unsupported ([CE3509](error-catalog.md#ce3509))
4. Read the 4-byte flags, skip 20 bytes of reserved fields
5. Read the 8-byte metadata length
6. Read the metadata blob, deserialize it with MessagePack ([CE3512](error-catalog.md#ce3512))
7. Read the 8-byte source length
8. Read the source blob, deserialize it with MessagePack

After the read, the consumer and `--lib-info` check the manifest against `MANIFEST_SCHEMA`
(`check_manifest`, [CE3512](error-catalog.md#ce3512)).

Each declared section length is checked against the bytes that are left in the file BEFORE the
read. A length that is too long is a truncation: [CE3510](error-catalog.md#ce3510) (metadata) or [CE3506](error-catalog.md#ce3506) (source).

`read` does every step. A reader that wants only part of this stops early:
`read_metadata_only` stops after step 6, and `read_source_size` reports the source length
after step 7 without keeping the blob.

### Writing

1. Write 16-byte magic
2. Write 4-byte version (6)
3. Write 4-byte flags (0)
4. Write 20 bytes of zeros (reserved)
5. Serialize metadata to MessagePack
6. Write 8-byte metadata length, then the metadata blob
7. Serialize the unit source map to MessagePack
8. Write 8-byte source length, then the source blob

## See Also

- [Libraries](libraries.md) - Creating and using libraries
- [Compiler Reference](compiler-reference.md) - `--lib` and `--lib-info` flags
- [Standard Library Build](internals/stdlib-build.md) - How stdlib is built
