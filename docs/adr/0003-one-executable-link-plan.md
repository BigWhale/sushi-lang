---
status: accepted
---

# One executable runs the link, and the block names its C libraries

A Sushi program could call only the C runtime, because the link command was
`cc <objects> -o <out>` with `-lm` on Linux, written in the driver. We add the `link`
clause to the `unsafe external` block: `unsafe external "C" link "ssl", "crypto" as tls
because "...":`. The block that needs the symbols names the library, as
`#[link(name = "...")]` does on a Rust extern block. `link "z"` gives `-lz`, and
`sushic -L <dir>` adds a search directory.

`sushic` stays the one command, and it runs the system linker (`cc`), as `rustc`, `zig` and
`go build` do. There is no second executable for the link. Inside the compiler, the link is
one seam: a `LinkPlan` (the objects, the libraries, the search directories, the output, the
debug flag), and one function, `link_command`, makes the `cc` command from it. Both link
paths (the whole program and the per-unit objects) use it, and no other code writes `-l` or
`-L`.

The libraries of the C runtime are one constant of the plan: `m` on Linux, none on macOS.
LLVM lowers a float `%` (`frem`) to a call of `fmod`, and glibc keeps `fmod` in libm, so a
program that never names libm still needs it on Linux. The constant replaces the `-lm` of
the driver, and the plan writes it in the same way as each other library.

## Considered Options

- **A separate linker executable.** Rejected: two commands to learn and to keep in step,
  for no gain while `cc` does the work. A linker written in Sushi can come later behind the
  same seam.
- **The library on the command line only (`sushic -lz`).** Rejected: the build of each
  program must then repeat what its source already knows, and a library could not record
  what it needs.
- **Record each use of a float `%` in the backend, and add libm only then.** Rejected: a
  second source of libraries, and the object cache would have to keep the fact.

## Consequences

The `externs` pass collects the clauses of each unit that has an AST, so a unit from the
object cache still gives its libraries, and the libraries are not in the key of an object.
The consumer of a `.slib` compiles the units of the library, so it collects their clauses
too. pkg-config, a C struct by
value, a C callback, rpath and a mode that writes the objects and prints the link line are
later steps.
