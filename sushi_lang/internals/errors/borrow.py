"""Borrow and reference errors (CE24xx).

This module owns its numeric range: a code may only be added in the file that
owns it, which is what makes the grouping structural rather than conventional.
"""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


_add(ErrorMessage("CE2410", Severity.ERROR,
    "cannot move '{name}': it is a borrowed view of the process arguments (main's string[] args); borrow it instead with '&peek string[]'",
    Category.BORROW, "main's `string[] args` aliases the process argv, which the runtime owns and frees. Moving it by value (passing it to a by-value parameter, rebinding, or storing it) would make the callee free argv and double-free. Take it by reference with `&peek string[]`."))

# Borrow/reference errors (CE24xx)
_add(ErrorMessage("CE2400", Severity.ERROR,
    "cannot borrow '{name}': only a local variable can be borrowed",
    Category.BORROW, "A borrow takes the address of storage a frame owns, so its target must be a local -- a parameter, a `let`, or a binding. A constant, a top-level function, an enum type name and an FFI namespace all name something else, and none of them has a frame slot to point at. Read the value instead, or copy it into a local first. A name that is declared NOWHERE is CE1001; this code is only for a name that exists and is not a local, which is why the two are no longer reported together for one token."))

_add(ErrorMessage("CE2401", Severity.ERROR,
    "cannot move '{name}' while it is borrowed",
    Category.BORROW, "One statement borrowed a value and also handed it to a position that takes ownership -- `both(&peek s, s)`. The new owner frees the buffer while the borrow still points at it, so `both(&poke a, a)` is a double free plus a read of released memory, whichever order the arguments are written in. Borrow it twice (`&peek` is shareable), or clone the value the owning position needs: `both(&peek s, s.clone())`. A borrow lasts only for the statement that creates it, so the same two lines written as two statements are unaffected."))

# CE2402 ("cannot destroy '{name}' while it is borrowed") was RETIRED in R7 of the reference
# seam plan (`old/FIX-reference-seam.md`). It was unreachable: `.destroy()` returns `~`, so
# it is only ever a statement of its own, and
# borrow counters are cleared at the end of every statement -- no borrow can be live when
# it runs. Its intent is covered three ways: CE2408 (destroy through a `&peek` reference),
# CE2412 (destroy an owner a `let`-borrow binding reads out of) and CE2406 (use after
# destroy). A registered code that nothing can reach misinforms the registry's own promise.

_add(ErrorMessage("CE2403", Severity.ERROR,
    "'{name}' already has an active &poke borrow (only one exclusive borrow allowed)",
    Category.BORROW, "A variable can only have one active &poke (read-write) borrow at a time to prevent aliasing issues."))

_add(ErrorMessage("CE2404", Severity.ERROR,
    "cannot borrow '{expr}': expression has no stable address",
    Category.BORROW, "The borrow operator (&) can only be applied to variables and struct member access (e.g., &x, &obj.field), not temporary values or function call results."))

_add(ErrorMessage("CE2405", Severity.ERROR,
    "cannot borrow moved variable '{name}'",
    Category.BORROW, "Attempted to borrow a variable whose ownership has been transferred elsewhere."))

_add(ErrorMessage("CE2406", Severity.ERROR,
    "use of destroyed variable '{name}'",
    Category.BORROW, "Variable was explicitly destroyed via .destroy() and is no longer valid."))

_add(ErrorMessage("CE2407", Severity.ERROR,
    "cannot have &peek and &poke borrows of '{name}' simultaneously",
    Category.BORROW, "A variable cannot have both read-only (&peek) and read-write (&poke) borrows at the same time."))

_add(ErrorMessage("CE2408", Severity.ERROR,
    "cannot modify '{name}' through &peek reference (read-only)",
    Category.BORROW, "&peek references are read-only. Use &poke for mutable access."))

_add(ErrorMessage("CE2413", Severity.ERROR,
    "a 'let' binding cannot have a reference type ('&{mode} {ty}')",
    Category.BORROW, "A reference-typed `let` (`let &peek T x = ...`) parses but has no checked semantics: the binding would be an alias the borrow checker does not track, so two `&poke` bindings of one variable would compile silently (issue #252). Borrow at a USE site instead: pass `&peek x` / `&poke x` to a reference parameter, or take an independent value with `.clone()`. Checked local borrow bindings are a possible future feature; until they are designed, the form is rejected."))

_add(ErrorMessage("CE2412", Severity.ERROR,
    "cannot mutate '{owner}' while '{name}' borrows from it",
    Category.BORROW, "A `let` bound from a read THROUGH an owner -- `let v = h.items`, `let v = c.get(0)??` -- BORROWS: it names storage the owner keeps and still frees. Mutating, freeing, rebinding or moving that owner while the binding is live would leave the binding pointing at storage the owner no longer holds. The borrow lasts to the end of the block that declares it, so move the mutation after that block, or take an independent value with `.clone()`. This is Rust's E0502."))

_add(ErrorMessage("CE2414", Severity.ERROR,
    "cannot mutate through binding '{name}': a match/foreach binding is a read-only view",
    Category.BORROW, "A `match` payload binding and a `foreach` loop binding borrow a value the scrutinee or the container owns. The compiled binding is a private copy, so a write through it -- a mutating method, a field assignment, or a `&poke` borrow -- never reaches the owner and is silently lost (issue #253). Take an independent value with `.clone()`, mutate that, and store it back into the owner. A rebind of the binding ITSELF (`n := 99`) stays legal: it re-initializes a local and does not claim to write through."))

_add(ErrorMessage("CE2411", Severity.ERROR,
    "cannot consume '{name}': another owner keeps this value",
    Category.BORROW, "A borrow names storage something else owns and still frees, so a position that takes ownership cannot have it. Three shapes borrow: a `match` payload binding, a `foreach` loop binding, and every read THROUGH a live owner -- a field read (`h.inner`), an index (`rows[i]`) and a container get-out (`c.get(0)??`, `own.get()`). Reading through a borrow is free; clone it to take an independent value: `{name}.clone()`. Only a value whose type transitively owns heap (a dynamic array, List, Own, HashMap, a string or a capturing closure) is affected -- a primitive borrow is unrestricted, and so is a string bound directly from a literal, which points into read-only memory and owns nothing."))

# --- The undefined reference POSITIONS (R4) ------------------------------------------
#
# The grammar's `?type` rule is recursive and universal, so `&peek T` / `&poke T` parses in
# EVERY type position. Semantics defines it for exactly ONE: the parameter. Each of the six
# positions below was accepted and then failed in its own way -- an internal error, a
# dangling read, or silent dead code (old/BORROW.md section 6).
#
# SIX codes, not one parameterized code, following the two precedents this repo already has
# for a type with restricted positions: foreign `ptr` (CE5002/CE5008/CE5009/CE5012) and the
# variadic marker (CE0114/CE0115/CE0116). The reason is that each position has its OWN
# rationale and its OWN way out, which is what the long-form text carries, and each will be
# lifted separately as its feature is designed -- a shared code could only be retired all at
# once. The rejection SITE is shared: one `contains_reference` walk in
# `semantics/type_predicates.py`, called from six places.

_add(ErrorMessage("CE2415", Severity.ERROR,
    "a struct field cannot have a reference type ('{ty}')",
    Category.BORROW, "A `&peek` / `&poke` struct field parses but has no checked semantics: nothing relates the field's borrow to the value it points at, so the struct may outlive it. Reading such a field is an internal error today (issue #315). Store an owned value, or an index into a container the struct does not own. A borrow inside a struct needs lifetimes and is a possible future feature; until it is designed, the form is rejected."))

_add(ErrorMessage("CE2416", Severity.ERROR,
    "an enum variant payload cannot have a reference type ('{ty}')",
    Category.BORROW, "A `&peek` / `&poke` enum payload parses and runs with no tracking of any kind, so the enum may outlive the value it borrows (issue #316). It is also how a returned borrow escapes: a `Result@(&peek T, E)` is an enum payload, which is the shape that made a dangling read reachable (issue #314). Carry an owned value in the variant. Same lifetime problem as a reference struct field (CE2415)."))

_add(ErrorMessage("CE2417", Severity.ERROR,
    "a function cannot return a reference type ('{ty}')",
    Category.BORROW, "Returning a `&peek` / `&poke` lets a function hand out a borrow of its own local, and the caller reads it after the frame is gone -- a dangling read that compiles clean today (issue #314). `typesys.py` states the intended rule, 'borrows are function-scoped (end at function return)', and this is what enforces it. Return an owned value, or `.clone()` what you borrowed. Returning a borrow needs lifetimes to be sound."))

_add(ErrorMessage("CE2418", Severity.ERROR,
    "a reference to a reference is not supported ('&{outer} &{inner} ...')",
    Category.BORROW, "Both grammar rules for a borrow are recursive, so `&peek &peek i32` parses -- in a type position and in an expression position (issue #317). There is no double borrow in the language: a borrow of a borrow is the same borrow, and the extra level has no meaning at any layer. Write the single borrow."))

_add(ErrorMessage("CE2419", Severity.ERROR,
    "a reference type cannot be a generic type argument ('{ty}')",
    Category.BORROW, "A container of borrows -- `List@(&peek T)`, `HashMap@(&peek K, V)`, `Maybe@(&peek T)` -- has no defined semantics: nothing relates the stored borrows to the values they point at, and the backend cannot lay one out (issue #318). Store owned values, or indices into a container that outlives the uses. There is NO `Maybe` / `Result` exemption on purpose: those two are exactly how a returned borrow escapes (CE2417). Foreign `ptr` carries the same restriction, as CE5012."))

_add(ErrorMessage("CE2420", Severity.ERROR,
    "an extension cannot target a reference type ('{ty}')",
    Category.BORROW, "`extend &peek T` compiles and is permanently uncallable: a reference target falls through method resolution, so every call reports 'no such method' and the body is dead code the author believes they wrote (issue #319). Extend the referent instead -- the methods on `&T` ARE the methods on `T`, so `extend T` is already callable through a `&peek T` / `&poke T` receiver. This is the CE2097 shape: an extension that can never be reached is a diagnostic, not silence."))

# --- Method parameters, `self` included (R6) -------------------------------------------
#
# The third and fourth read-only receivers, after the match/foreach binding (CE2414) and
# the `&peek` reference (CE2408). All four share ONE gate in `semantics/passes/borrow.py`
# over a table of kinds, and each keeps its own code because each carries its own
# rationale and its own escape -- the same reasoning as the six position codes above.
#
# The two here are one rule (#298: every parameter of an extension or perk method is a
# borrow) with two escapes: a by-value parameter is redeclared `&poke T`, and a receiver
# `&poke self` (#327, shipped 2026-08-16). The codes stay separate because the escapes are
# what the help text has to name.

_add(ErrorMessage("CE2421", Severity.ERROR,
    "cannot write through 'self': a method receiver is a read-only borrow",
    Category.BORROW, "An extension or perk method receives `self` as a BORROW: the caller keeps the value (the ruling on issue #298, `docs/design/ownership-conventions.md` S8.6). The compiled receiver is a private copy, so a write through it -- a mutating method, a field assignment, or a `&poke` borrow of it -- never reaches the caller. A plain field was silently LOST; an owning field was a double free plus a leak, because the field rebind released the caller's buffer through the copy (issue #326). This is CE2414's rule for the one receiver CE2414 does not cover. The mutating receiver is spelled `&poke self` (issue #327): declare the method `extend T name(&poke self, ...)` and the write reaches the caller. Alternatively return the new value and let the caller store it."))

_add(ErrorMessage("CE2422", Severity.ERROR,
    "cannot write through '{name}': a by-value method parameter is a read-only borrow",
    Category.BORROW, "Every parameter of an extension or perk method is a BORROW of the caller's value, `self` and the explicit ones alike (the ruling on issue #298). A by-value one is compiled as a private copy, so a write through it -- a mutating method, a field assignment, or a `&poke` borrow of it -- never reaches the caller: a plain field was silently lost, and an owning field was a double free plus a leak, because the field rebind released the caller's buffer through the copy. CE2421 is the same rule for the receiver, and the plain-function form has no such problem, because there the callee OWNS its by-value parameters. Unlike the receiver, this one has an escape that exists today: declare the parameter `&poke T` and the write reaches the caller."))

# --- Reference bindings (#300 phase 1) --------------------------------------------------
#
# `foreach(&poke r in ...)` and `Own(&poke inner)` bind a POINTER into the container's /
# pointee's storage, so a write through the binding reaches the owner. The two codes here
# fence the phase-1 boundary: an iterable whose items have no address (CE2423), and the
# match-pattern position, which waits on the enum payload alignment fix (CE2424).

_add(ErrorMessage("CE2423", Severity.ERROR,
    "a reference binding needs addressable elements; this iterable yields values",
    Category.BORROW, "A `&peek`/`&poke` foreach binding is a pointer into the container's element storage, so the iterable must HAVE element storage. A range (`0..10`) synthesizes its values, and `HashMap.entries()` synthesizes each `Entry` pair on the fly -- there is no address to bind (issue #300). Iterate a container (`arr.iter()`, `list.iter()`, `map.keys()`, `map.values()`) or drop the marker and take the value."))

_add(ErrorMessage("CE2424", Severity.ERROR,
    "a reference binding in a NESTED match pattern is not supported",
    Category.BORROW, "A top-level `Variant(&poke x)` binds a pointer into the scrutinee's payload storage and is supported (issue #300 phase 3, on the aligned enum payload layout). A NESTED pattern is different: extraction walks through temporary copies of the inner enums, so a pointer into one writes to storage nobody reads -- the silently-lost-write class of issue #253. Bind the payload by value in the nested pattern, or restructure to match the inner enum at the top level."))

_add(ErrorMessage("CE2425", Severity.ERROR,
    "a '&peek self'/'&poke self' receiver parameter is not valid here",
    Category.BORROW, "The receiver parameter (#327) is the FIRST parameter of an EXTENSION or PERK method: `extend Counter bump(&poke self) ~:`. It is not valid in a plain top-level function (a plain function has no receiver -- take `&poke T name`), not valid after the first position, and a bare `&poke name` that is not `self` is a reference parameter missing its type."))
