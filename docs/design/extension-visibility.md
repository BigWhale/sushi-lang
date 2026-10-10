# Extension visibility

**Status: RULED 2026-10-10, NOT YET BUILT.** Epic #1251. This document is normative. It
replaces three earlier rules:

- `visibility.md` Ruling 2, for extension methods and static methods ("a method is as
  visible as its type"). A perk implementation keeps Ruling 2.
- `ufcs-combinators.md`, the section "Program-wide extension visibility (the stated
  asymmetry)".
- The per-unit import of a stdlib method (#942), for METHODS. A free function, a type, a
  constant and a perk keep the per-unit import.

The change is breaking, and there is no transition period.

## 1. The fault that this ruling corrects

Before this ruling, a method on a type had three import rules. The rule depended on how the
compiler emitted the body, and a user could not see that or predict it:

| Tier | Example | Rule before this ruling |
|---|---|---|
| The backend emits the body (an intrinsic) | `s.is_empty()`, `s.clone()`, every `T[]`, `List` and `HashMap` method | no import |
| A generator method in a stdlib `.bc` | `s.len()`, `s.split()`, `s.trim()` | an import in the unit of the call |
| A Sushi-source stdlib extension | `s.lines()`, `xs.map(f)`, `b.is_ascii_digit()` | an import in ANY unit of the program |

One module, `<collections/strings>`, had methods in all three tiers. Without an import, a
unit could call `s.is_empty()`, but it could not call `s.len()`.

A user extension had no marker and was visible in every unit of the program. So:

- a unit could not keep a private helper extension;
- an extension claimed its name on its target type for the whole program, so two libraries
  that each declared `extend u32 be_bytes()` could not be used in one program, and the user
  could not correct that;
- a unit could call an extension that it did not import, because another unit imported it.
  When that other unit removed its import, the call failed in a file that nobody edited.

## 2. The principle

1. **A method that comes with a type is never gated by an import.** It is available wherever
   there is a value of the type.
2. **A method that another unit adds to a type is a name of that unit.** It follows the
   import and visibility rules of the other names of that unit.
3. **How the compiler emits a method never decides whether a user may call it.** "Intrinsic",
   "generator" and "Sushi source" are implementation details and are not visible in the
   language.

## 3. The rules

### R1. The built-in types and their methods are always available

The built-in types are the types whose name needs no import: `i8`, `i16`, `i32`, `i64`,
`u8`, `u16`, `u32`, `u64`, `f32`, `f64`, `bool`, `string`, `T[]`, `T[N]`, `List@(T)`,
`Own@(T)`, `Maybe@(T)` and `Result@(T, E)`.

The stdlib is the home of these types. Every stdlib method on a built-in type is available
in every unit with no import. This includes the methods that the backend emits, the
generator methods, and the `public` extensions that a Sushi-source stdlib module declares
on a built-in type (`<collections/strings>`, `<collections/iter>`, `<text/ascii>`,
`<collections/sort>`).

### R2. A stdlib import makes names visible, and never methods

`use <collections/strings>` brings the free functions and the types of the module (for
example `StringBuilder`). It does not bring a method. The same is true for every stdlib
module.

### R3. A type that the stdlib adds

A type that a stdlib module declares (`HashMap@(K, V)`, `File`, `BufReader@(R)`) is not
built in. Its NAME and its static methods need the import of its module:
`HashMap@(i32, string)`, `HashMap.new()`. Its public methods travel with the type (R5): a
unit that receives a value of the type can call them with no import.

### R4. A user extension with no marker is private

<!-- docs-sweep: skip (ruled, not yet built: `public extend` does not parse yet) -->
```sushi
extend i32 twice() i32:             # visible in this unit only
    return self * 2
```

Another unit cannot call it, even when it imports the declaring unit.

### R5. A public extension on a type that the unit declares travels with the type

<!-- docs-sweep: skip (ruled, not yet built: `public extend` does not parse yet) -->
```sushi
public struct Vec:
    i32 x
    i32 y

public extend Vec length_sq() i32:  # visible wherever a Vec is
    return self.x * self.x + self.y * self.y
```

The HOME of a type is the unit that declares it. For a generic target (`Box@(T)`,
`Box@(i32)`) the home is the unit that declares the base type. A public extension in the
home unit is visible in every unit where there is a value of the type, with no import of
the home unit. This is the method set of the type: a Rust inherent method has the same
rule.

### R6. A public extension on a type that the unit does not declare is visible through `use`

<!-- docs-sweep: skip (ruled, not yet built: `public extend` does not parse yet) -->
```sushi
# unit "bytes"
public extend u32 be_bytes() u8[]:
    ...
```

`u32` is built in, so the unit `bytes` is not its home. `x.be_bytes()` is visible:

- in the unit `bytes`;
- in every unit that imports `bytes`: with `use "bytes"`, with `use "bytes" as b`, or
  through a `public use` chain of its own imports.

An aliased import brings the extension too: a method is found on the type of its
receiver, and a namespace cannot stand in front of it, so `use "bytes" as b` would
otherwise bring no way to call it.

It is NOT visible in a unit that does not import `bytes`, even when another unit of the
program imports it. The same rule applies to an extension on another unit's type, and to an
array target (`T[]`, `Crate[]`, `i32[]`): an array type has no home unit.

A project that has many such extensions can collect them in one unit with `public use`,
so that each unit writes one `use` line.

### R7. A static method follows R4 to R6

A static method (`extend Vec static at(i32 x, i32 y) Vec`) is an extension with no receiver.
It takes `public` in the same position, and R4, R5 and R6 apply to it.

### R8. A perk implementation and a conversion do not change

`extend T with P` is global and unique for each pair of type and perk, as a Rust `impl` is.
It takes no marker, and `public` on one of its methods is still [CE6103](../error-catalog.md#ce6103). A
conversion `extend A as B:` can be declared only in the home unit of its target `B`, so it
travels with `B` (`error-conversion.md` section 3).

### R9. The syntax

`public` stands before `extend`, as it stands before `fn`:

<!-- docs-sweep: skip (ruled, not yet built: `public extend` does not parse yet) -->
```sushi
public extend string shout() string:
    ...
public extend Vec static at(i32 x, i32 y) Vec:
    ...
```

The grammar accepts the marker on an extension method and on a static method. It does not
accept it on `extend T with P:` or on `extend A as B:`.

## 4. Collisions

| Case | Answer |
|---|---|
| C1. Two extensions of one name on one type in ONE unit | the duplicate error, [CE0101](../error-catalog.md#ce0101), as before |
| C2. An extension (private or public, in any unit) with the name of a method that is visible everywhere: an R1 stdlib method on a built-in type, an R5 public method from the type's home unit, or a method of a perk implementation on that type (R8) | an error at the declaration of the extension, with a note at the method that it collides with |
| C3. A unit's own extension and an imported public extension of one name on one type | the unit's own extension wins, as a unit's own declaration wins over an imported flat name (`unit-namespaces.md`). A WARNING at the own declaration, with a note at the imported one, so that the user knows: the model is [CW3002](../error-catalog.md#cw3002), a consumer that shadows a library export |
| C4. Two imported public extensions of one name on one type, in one unit, and no own extension | an error at the CALL, with a note at each declaration |
| C5. Private extensions of one name on one type, in two different units | no error. Each unit calls its own |

**C2 and the stdlib.** A built-in method always wins. When the stdlib adds a method to a
built-in type, a user extension of that name becomes an error. Additions of this kind are
rare, and the release notes announce them early.

**C2 and a perk implementation.** A perk implementation is global (R8), so its methods
are in the same tier as the stdlib methods. This keeps the rule that a method name on one
type has exactly one home: a perk method and an extension method of one name on one type
are never both visible.

**C4 has no escape in the call.** Sushi has no qualified method call, so a call cannot name
the unit that it means. The user moves the code that needs each extension into a unit of its
own. This case is rare, and the rule stays until a real program needs more.

## 5. Diagnostics

| Fault | Diagnostic |
|---|---|
| A call of another unit's private extension | the private-name error, [CE3005](../error-catalog.md#ce3005), with the kind word for an extension method and a note at the declaration |
| A call of a public R6 extension whose unit is in the program but not imported by this unit | an error whose help names the import: ``add `use "bytes"` `` |
| A call of a method that no unit of the program declares | [CE2008](../error-catalog.md#ce2008), as before |
| A unit's own extension that hides an imported public one (C3) | a warning at the own declaration, with a note at the imported one |
| Two imported public extensions of one name, called in one unit (C4) | an error at the call, with a note at each declaration |
| A private extension that nothing in its unit calls, under `--warn-unused` | the dead-code warning for a private declaration |
| A missing stdlib import for a METHOD | none any more: R1 and R3 make the method visible. [CE3015](../error-catalog.md#ce3015) no longer applies to a method |
| `public` on a perk-implementation method | [CE6103](../error-catalog.md#ce6103), as before |
| A `public` extension whose signature names a private type (the target type, a parameter, the return, the error arm) | the private-type leak error, through the leak fence (`visibility.md` section 5) |

## 6. Generic code and libraries

- **A template body** resolves an extension call in the scope of its home unit, never in
  the scope of the unit that instantiates it (`ufcs-combinators.md`, "The home unit of a
  copy"). So a template can call a private extension of its own unit, and the call still
  works when another unit instantiates the template.
- **A source library** is ordinary source, and R4 to R6 apply to it as written.
- **A binary or hybrid library** records the marker and the declaring unit of each extension
  in its manifest. The consumer applies R4 to R6 to these records. A private extension is
  not exported.
- **[CW3003](../error-catalog.md#cw3003)** warned that a library which extends a type it does not declare claims that
  name for every consumer. Under R6 the extension is visible only where it is imported, so
  that problem does not exist any more.

## 7. The costs that this ruling accepts

- A unit that calls a public extension on a type that its declaring unit does not own needs
  one `use` of the declaring unit, or of a unit that re-exports it with `public use`.
- A method on a public type needs `public` to be part of the API of the type.
- A new stdlib method on a built-in type can break a user extension of the same name (C2).
- A unit that needs two imported extensions of one name on one type must be split (C4).
- Method resolution reads the unit of the call. `visibility.md` Ruling 2 refused this cost,
  because method resolution is a hot path. The `typecheck` pass already runs per unit, so
  the unit of the call is always known.

## 8. Precedents

- **R1 and R2** follow the Java, Go and Python rule for the methods of a built-in type, and
  Kotlin's default imports and Rust's prelude for the stdlib: a method that comes with a
  type never needs an import.
- **R4 to R6** follow Rust and Kotlin. A Rust trait method and a Kotlin extension function
  are visible only where they are imported, and only the home of a type gives it methods
  that travel with it (Rust's orphan rule for inherent methods).
- **Swift's SE-0444** ("Member import visibility") corrected a leak: an extension member
  from a module that one file imported was visible in every file. Swift has overloading, so
  the leaked member could change which method a call ran. Sushi has no overloading (C1 to
  C4 are errors), and an R6 extension is never visible without an import.
- **No language in this group** gates the methods of a built-in type behind an import.

## 9. What the implementation must also decide

These are implementation questions, not language rules:

- **Loading.** A stdlib method is available with no import (R1), so the compiler must link
  the body even when no unit imports the module: the `.bc` of `<collections/strings>` and
  the Sushi-source modules that declare methods on built-in types. First measure the cost
  of loading them always. If that cost is too high, load a module when a scan of the
  program finds a method name that the module declares.
- **Symbol identity.** Two private extensions of one name on one type in two units (C5)
  need two symbols. The extension symbol carries the declaring unit.
