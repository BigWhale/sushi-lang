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
│ VERSION (4 bytes): uint32 LE (current: 5)                   │
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

**Fixed header size:** 52 bytes (before variable-length sections).

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

4-byte unsigned integer (little-endian). Current version: `5`.

Used for forward compatibility checks. A reader accepts version 5 only; anything else is
**[CE3509](error-catalog.md#ce3509)**. There is no upgrade shim, and none is planned: Sushi has no users in the wild,
so a file of another version is rejected rather than read with a guess. A file of another
version can have a different ABI in its bitcode, and a reader must not call it with the wrong
ABI. Rebuild the library.

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
or additional section offsets. Both must be zero in version 5.

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
    "sushi_lib_version": "2.4",        # Protocol version
    "library_name": str,               # Library identifier, from the output filename
    "library_version": str,            # The library's own version, "major.minor.patch"
    "kind": str,                       # "source" / "binary" / "hybrid", matching KIND
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
    "compiled_at": str,                # ISO 8601 timestamp
    "platform": str,                   # "darwin", "linux", "windows"
                                       #   meaningful only when kind != "source"
    "compiler_version": str,           # Exactly which compiler built the file

    # The three lists below carry only CONCRETE declarations that the library MARKS
    # `public`. A generic function, struct or enum is filtered out of them and routes
    # to "templates" instead: a generic is not a concrete callable, and listing one
    # here would hand the consumer a signature with unresolved type parameters. An
    # unmarked declaration is not API and goes to "not_exported" instead.
    #
    # Two keys answer different questions. `unit` names the
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

    # A SIGNATURE is four keys, built by one function (`signature_record`) so the
    # concrete record, the generic record and the closure record cannot drift apart.
    # `has_channel` is REQUIRED on every function, helper and method record (templates
    # schema 8): a reader never guesses a channel from an absent key. It is true when
    # the declaration writes `| E` or returns an explicit `Result@(T, E)`, and false
    # when the callable is bare (`docs/design/error-channel.md`). There is no default
    # error type. `error_type` is present only when the declaration writes `| E`; an
    # explicit `Result@(T, E)` return carries its arms in `return_type`.
    #
    #   SIG = {
    #       "params": [{"name": str, "type": str, "mode": str,
    #                   "is_pack": bool}],  # Present (true) for `...Ts args` alone
    #       "return_type": str,
    #       "has_channel": bool,       # Required: does a call answer a Result
    #       "error_type": str          # If the declaration says `| E`
    #   }
    #
    # Every `type`, `return_type` and `error_type` is the INTERNAL identity spelling,
    # `List<i32>` and not `List@(i32)`: a consumer reads these back with
    # `parse_type_string`, so this is a wire format. Rendering `@(...)` is the report's
    # job. The consumer's ONE reader is `LibraryRegistry._parse_functions`: it reads all
    # three keys into the signature, so a `| E` is the call's Err arm at the consumer,
    # and a spelling that names an instantiation the consumer has not interned
    # (`Box<i32>`, an explicit `Result<i32, MyErr>` return) reads back as the generic
    # reference the producer wrote it from -- so the instantiate pass collects it and a
    # Result is never wrapped twice.

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

    # A unit variable (`public var`, docs/design/unit-storage.md) mirrors a constant --
    # the consumer needs the declared type -- and adds the symbol of its ONE storage,
    # which the library's bitcode defines and the consumer declares as external. A
    # source library recompiles the declaring unit and reads none of this.
    "public_variables": [
        {
            "name": str,
            "unit": str,
            "type": str,
            "source": str,             # The whole `public var ...` declaration
            "link_symbol": str,        # `<unit>$<name>`, the data symbol
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
    # The body is in the bitcode under `link_symbol`, which is always the symbol made
    # from the pair (`conversion_symbol`, `<Source>_as__<Target>`); the consumer
    # declares it and files the pair, so its `??` and `as` call it. A record whose
    # sides are not two error types, or whose symbol is another one, is refused
    # (CE3512). A conversion is not an `extension` record and makes no
    # `foreign_extensions` claim. The whole key is absent when there is no record.
    "conversions": [
        {
            "source": str,             # The source error type: "LowError"
            "target": str,             # The target error type: "LibError"
            "link_symbol": str,        # Its symbol in the shipped bitcode
            "unit": str,               # The unit that declared it; optional
            "doc": DOC                 # If documented
        }
    ],

    # What each unit RE-EXPORTS: one record per `public use`, in written order.
    # `public use X` makes X's public names the unit's own, so the unit's importers get
    # them where its own names land. A SOURCE library needs no record -- its units are
    # in the source section and the consumer re-parses the statement -- but the index
    # must answer without a parser, so every kind writes it. A compiled library has
    # nothing else: the consumer composes the unit's namespace from its own records
    # plus the providers of what these name.
    #
    # `unit` is the unit that WROTE the statement, and only the library's own units
    # appear: a bundled stdlib module and an injected source library re-export
    # on their own account. `kind` says which producer the target is, so the consumer
    # reaches the same provider builder the written statement would have:
    #
    #   "unit"    -- a sibling unit of THIS library. `path` is the name the `units`
    #                index carries, so the consumer looks it up with no guessing
    #   "stdlib"  -- a stdlib module. `path` is the import path, `io/fs`. The
    #                consumer's build compiles the module and links its bitcode on
    #                the strength of this record alone, because no unit wrote the
    #                import (`_compiled_stdlib_modules`)
    #   "library" -- another library. `path` is the written `lib/...` path. The
    #                record gives the consumer the library's public NAMES. The
    #                `dependencies` record of the same `use` is what LOADS it (#1120),
    #                and a library built before that key reads this record for both;
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

    # The types this library claims methods on and does not declare -- the consumer's
    # half of CW3003. One record per extension method, in declaration order; the whole
    # key is absent when the library extends only what it declares. A perk
    # implementation makes no record: the consumer's own implementation is the
    # sanctioned override, so that claim has an escape.
    "foreign_extensions": [
        {"type": str, "method": str, "unit": str}
    ],

    # What a consumer's build must load (#1120): one record per stdlib module that the
    # build uses, and one per `use <lib/...>` of the library's OWN units, plain or
    # public, with the name and the version of the `.slib` that the build found. The
    # consumer loads every library of the graph from these records; a second version of
    # one library is CE3519. Visibility does not follow: a plain `use` gives the
    # consumer no name, and only a `public use` (`reexports`) hands names on.
    # `units` names the library's own units that write the `use` (#1123). The load and
    # the version check read the record whole. A copy of a template of the library
    # resolves its body in the scope of its own unit, and that scope holds only the
    # records that name the unit. A module that only a unit the library does not own
    # uses has an empty list. A record with no `units` is read for every unit.
    "dependencies": [
        {"path": str, "kind": "stdlib", "units": [str]},
        {"path": str, "kind": "library", "library_name": str, "library_version": str,
         "units": [str]}
    ],

    # Written for EVERY kind. A source library ships whole units, so a generic in it is
    # already there as ordinary source -- but the index must answer without a parser, and
    # a template's own doc block stands OUTSIDE its source slice, so the record is the
    # only place it can travel.
    "templates": {                     # Instantiable cross-library templates
        "version": 9,                  # Templates schema version. A binary or
                                       #   hybrid .slib with another schema is
                                       #   refused (CE3512) and must be rebuilt.
                                       #   The one constant is
                                       #   `TEMPLATES_SCHEMA_VERSION` in
                                       #   `backend/library_format.py`

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
                "doc": DOC,            # If documented, and never when private
                "bindings": {str: str} # Every free name in `source`
                                       #   the producer's closure resolved, mapped to
                                       #   its link symbol. The consumer re-parses
                                       #   the source and binds each named call to
                                       #   that symbol -- a non-dependent name in a
                                       #   template binds in the DEFINITION's scope,
                                       #   never at the point of use. Absent when
                                       #   the body resolved nothing
            }
        ],

        # Generic structs/enums, same record shape MINUS the signature: neither declares
        # parameters. A generic enum declared with `error` also carries
        # `"is_error": true`, so each instance at the consumer is an error type.
        "generic_structs": [ ... ],
        "generic_enums": [ ... ],

        # Perk DEFINITIONS: every PUBLIC perk (it is API, whether or not a constraint
        # names it), plus any perk an exported template names in a constraint
        # or implements. Each method is a record: its signature, its
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
            {"name": str, "unit": str, "source": str, "methods": [METHOD], "doc": DOC}
        ],

        # Concrete perk IMPLEMENTATIONS of those perks, and of every predefined
        # perk (`Drop`, `Hashable`, `Eq`, `Ord`, `Display`). Bodies live in
        # the bitcode (weak linkage); the record carries signatures (source)
        # and symbol names for declare-and-link at the consumer. A `Drop` record
        # puts the type in the consumer's Drop set, so the type moves there and
        # its scope exit calls the library's compiled drop().
        "perk_impls": [
            {
                "type": str,           # Concrete target type name
                "perk": str,
                "unit": str,           # The unit that declared it
                "source": str,         # The whole `extend T with P:` block
                "methods": [{**METHOD, "symbol": str}],   # the symbol to link
                "doc": DOC             # If documented
            }
        ],

        # Generic-target perk IMPLEMENTATIONS: `extend Box@(T) with Show`
        # is a TEMPLATE. It names no instantiation, so there is no symbol to declare
        # and link: it ships as source alone, and the consumer cuts one copy per
        # instantiation of `Box` it names, exactly as for its own template. The
        # library's monomorphized copies are NOT in `perk_impls` -- a copy's source
        # slice is the template's, and shipping it re-parsed to `Box@(T)`.
        "generic_perk_impls": [
            {
                "type": str,           # The target's BASE name: "Box"
                "type_args": [str],    # The target's parameters as written: ["T"]
                "perk": str,
                "unit": str,           # The unit that declared it
                "source": str,         # The whole `extend Box@(T) with P:` block
                "methods": [METHOD],   # Written in the target's parameters; no symbol,
                                       #   because there is no copy to link
                "doc": DOC             # If documented
            }
        ],

        # Extension methods, instance and static. A CONCRETE one ships as a signature:
        # the body is in the bitcode, and the consumer declares `link_symbol` and links
        # it. The methods of an implementation of a perk that does not ship are records
        # here too: the contract stays hidden, and each method is an ordinary method of
        # the type. An extension on a private type that nothing ships is left out. The
        # whole key is absent when there is no record.
        #
        #   EXTENSION = {
        #       "type": str,           # The target, in the internal spelling: "Vec",
        #                              #   "i32", "i32[]", "Box<T>", "T[]"
        #       "name": str,
        #       **SIG,
        #       "self_mode": str,      # "peek" | "poke", only when declared
        #       "static": bool,        # True, only for a static
        #       "unit": str,           # The unit that declared it
        #       "doc": DOC             # If documented
        #   }
        "extensions": [
            {**EXTENSION, "link_symbol": str}   # Its symbol in the shipped bitcode
        ],                                      #   (see "The two symbol keys")

        # Extension TEMPLATES: a generic target (`Box@(T)`), a concrete-instance target
        # (`Box@(i32)`), an array target (`T[]`) and a method type parameter
        # (`pick@(U)`). A template names no instance, so it ships as source, and the
        # consumer makes one copy for each instance it names. It joins the export
        # closure, so its body may call a private function of the library. The whole
        # key is absent when there is no record.
        "generic_extensions": [
            {
                **EXTENSION,
                "type_params": [{"name": str, "constraints": [str], "is_pack": bool}],
                                       # The method's own type parameters
                "source": str,         # The whole `extend ...` declaration
                "bindings": {str: str} # As for a generic function; absent when empty
            }
        ],

        # Export closure: private symbols exported generics transitively
        # reference. Concrete helpers ship as signature records (definitions
        # carry external linkage in the bitcode); constants and types ship with
        # source -- the consumer needs a constant's value for compile-time
        # evaluation, and a type's shape to register it before a monomorphized
        # template body names it. Every record is one per (unit, name):
        # two of the library's own units may each ship a private `helper`, and
        # each record names its unit.
        "private_functions": [
            {                          # No doc: a private symbol is not documented API
                "name": str,
                "unit": str,           # The unit that declared it
                "link_symbol": str,    # Its symbol in the shipped bitcode
                **SIG,
            }
        ],
        "constants": [
            {"name": str, "unit": str, "source": str}
        ],
        "private_types": [             # A private struct or enum a template body names
            {"name": str, "unit": str, "source": str}
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
    # It exists so that a consumer naming one hears CE3005: on the binary path the
    # symbol is not in the consumer's tables, and "undefined" would be the wrong word for
    # a function the library defines and deliberately keeps. A name here is not shipped
    # and clashes with nothing, so a consumer may declare its own function of the same
    # name. A kept TYPE answers CE3005 the same way from the type funnel. A kept CONSTANT
    # answers from the scope pass, which lets the name through so the type pass can say
    # whose it is: a PUBLIC constant registers from `public_constants[].source`, so "no
    # such name" would be the wrong word for the one next to it that the library keeps.
    #
    # A name the CLOSURE ships is not here. Each private is named in exactly one of the
    # two places, and the closure carries a private constant and a private type as
    # SOURCE (`templates.constants`, `templates.private_types`), because a monomorphized
    # template body names them and the consumer has to register them.
    #
    # One record for each (unit, name): two units may each keep a `helper` (#1112).
    # `unit` is the producer's unit that keeps the declaration, so CE5013 refuses the
    # one symbol `<unit>$<name>` that the bitcode defines. A record with no `unit` (a
    # library built before the field) matches that name in each unit of `units`.
    "not_exported": [
        {
            "name": str,
            "kind": str,               # "function" / "generic_function" / "struct"
                                       #   / "enum" / "constant" / "variable"
            "unit": str                # optional
        }
    ]
}
```

## The two symbol keys

The protocol gives a record two ways to name where it came from, and neither substitutes
for the other.

**`unit` says whose declaration this is.** Every record carries it. A Sushi symbol is
`<unit>$<name>` (`docs/design/unit-namespaces.md` section 9), so two units may each
declare `helper`, and a consumer that wants a namespace has to know which unit a name
belongs to. A source library's units are in the source section and could be re-read for
it; a BINARY library ships no source at all, so the manifest is the only place that can
answer.

**`link_symbol` says what the shipped bitcode calls it**, and only a record with a symbol
in that bitcode carries one: a public function, and an export-closure private function.
It is **written by every build and read by the BINARY path alone.** A concrete extension
method's record carries one too. Its symbol has no unit in it, as a perk-impl method's
has not, so the consumer's backend gets the same name from the same rule
(`extension_receiver_name`), declares it, and links the body from the bitcode.

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
([CE3503](error-catalog.md#ce3503)) refusing a `.slib` the running compiler may not consume.

## Error Codes

| Code | Description |
|------|-------------|
| [CE3500](error-catalog.md#ce3500) | The `--lib` output path does not end in `.slib` |
| [CE3502](error-catalog.md#ce3502) | A `use <lib/...>` names a library that no search directory holds |
| [CE3503](error-catalog.md#ce3503) | The library's `requires_compiler` excludes the running compiler |
| [CE3504](error-catalog.md#ce3504) | A binary or hybrid library was built for another platform |
| [CE3505](error-catalog.md#ce3505) | No `library_version` available at build time (no `nori.toml`, no `--lib-version`) |
| [CE3506](error-catalog.md#ce3506) | Source section truncated |
| [CE3508](error-catalog.md#ce3508) | Invalid magic bytes (not a valid `.slib` file) |
| [CE3509](error-catalog.md#ce3509) | Unsupported format version (a container other than version 5) |
| [CE3510](error-catalog.md#ce3510) | Metadata section truncated |
| [CE3511](error-catalog.md#ce3511) | Bitcode section truncated |
| [CE3507](error-catalog.md#ce3507) | The bitcode of a binary or hybrid library does not link |
| [CE3512](error-catalog.md#ce3512) | Invalid metadata: the MessagePack does not decode, a manifest field is missing or has the wrong type, a template does not parse or holds more than one declaration, a variant with `has_data` has no `data_types`, a function, helper or method record has no `has_channel`, a `conversions` record whose sides are not two error types or whose `link_symbol` is not the symbol of the pair, or a binary or hybrid library's templates schema is not version 9 (a library built before schema 9 is rebuilt) |
| [CE3513](error-catalog.md#ce3513) | File exceeds maximum size (1GB) |
| [CE3515](error-catalog.md#ce3515) | The file cannot be opened or read (a directory, no read permission, an I/O failure) |
| [CE3516](error-catalog.md#ce3516) | The path does not name a library file (the name of a `.slib` file ends in `.slib`) |
| [CE3517](error-catalog.md#ce3517) | The `nori.toml` in the working directory is not valid |
| [CE3518](error-catalog.md#ce3518) | The `nori.toml` in the working directory cannot be read |

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
Kind: source
Compiler: 0.15.0
Requires compiler: ~0.14
Compiled: 2026-09-28T19:18:00+00:00
Protocol: 2.4

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
4. Read the 4-byte flags and 4-byte kind, skip 16 bytes of reserved fields
5. Read the 8-byte metadata length
6. Read the metadata blob, deserialize it with MessagePack ([CE3512](error-catalog.md#ce3512))
7. Read the 8-byte source length
8. Read the source blob, deserialize it with MessagePack (skip it to reach the bitcode)
9. Read the 8-byte bitcode length
10. Read the bitcode blob
After the read, the consumer and `--lib-info` check the manifest against `MANIFEST_SCHEMA`
(`check_manifest`, [CE3512](error-catalog.md#ce3512)).

Each declared section length is checked against the bytes that are left in the file BEFORE the
read. A length that is too long is a truncation: [CE3510](error-catalog.md#ce3510) (metadata), [CE3506](error-catalog.md#ce3506) (source) or [CE3511](error-catalog.md#ce3511)
(bitcode).

A reader that wants only part of this stops early: `read_metadata_only` stops after step 6,
`read_source_only` after step 8, and `read_section_sizes` reports both payload lengths
without keeping either blob.

### Writing

1. Write 16-byte magic
2. Write 4-byte version (5)
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
