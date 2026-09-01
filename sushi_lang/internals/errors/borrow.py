"""Borrow and reference errors (CE24xx)."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


_add(ErrorMessage("CE2410", Severity.ERROR,
    "cannot move '{name}': it is a borrowed view of the process arguments (main's string[] args); borrow it instead with 'peek string[]'",
    Category.BORROW, "main's `string[] args` aliases the process argv, which the runtime owns and frees. Moving it by value (passing it to a by-value parameter, rebinding, or storing it) would make the callee free argv and double-free. Take it by reference with `peek string[]`."))

# Borrow/reference errors (CE24xx)
_add(ErrorMessage("CE2400", Severity.ERROR,
    "cannot borrow '{name}': only a local variable can be borrowed",
    Category.BORROW, "A borrow takes the address of storage a frame owns, so its target must be a local -- a parameter, a `let`, or a binding. A constant, a top-level function, an enum type name and an FFI namespace all name something else, and none of them has a frame slot to point at. Read the value instead, or copy it into a local first. A name that is declared NOWHERE is CE1001; this code is only for a name that exists and is not a local, which is why the two are no longer reported together for one token."))

_add(ErrorMessage("CE2401", Severity.ERROR,
    "cannot move '{name}' while it is borrowed",
    Category.BORROW, "One statement borrowed a value and also handed it to a position that takes ownership -- `both(peek s, s)`. The new owner frees the buffer while the borrow still points at it, so `both(poke a, a)` is a double free plus a read of released memory, whichever order the arguments are written in. Borrow it twice (`peek` is shareable), or clone the value the owning position needs: `both(peek s, s.clone())`. A borrow lasts only for the statement that creates it, so the same two lines written as two statements are unaffected."))

# CE2402 ("cannot destroy '{name}' while it is borrowed") was RETIRED as unreachable:
# `.destroy()` returns `~`, so
# it is only ever a statement of its own, and
# borrow counters are cleared at the end of every statement -- no borrow can be live when
# it runs. Its intent is covered three ways: CE2408 (destroy through a `peek` reference),
# CE2412 (destroy an owner a `let`-borrow binding reads out of) and CE2406 (use after
# destroy). A registered code that nothing can reach misinforms the registry's own promise.

_add(ErrorMessage("CE2403", Severity.ERROR,
    "'{name}' already has an active poke borrow (only one exclusive borrow allowed)",
    Category.BORROW, "A variable can only have one active poke (read-write) borrow at a time to prevent aliasing issues."))

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
    "cannot have peek and poke borrows of '{name}' simultaneously",
    Category.BORROW, "A variable cannot have both read-only (peek) and read-write (poke) borrows at the same time."))

_add(ErrorMessage("CE2408", Severity.ERROR,
    "cannot modify '{name}' through peek reference (read-only)",
    Category.BORROW, "peek references are read-only. Use poke for mutable access."))

_add(ErrorMessage("CE2413", Severity.ERROR,
    "a 'let' binding cannot have a reference type ('{mode} {ty}')",
    Category.BORROW, "A reference-typed `let` (`let peek T x = ...`) parses but has no checked semantics: the binding would be an alias the borrow checker does not track, so two `poke` bindings of one variable would compile silently (issue #252). Borrow at a USE site instead: pass `peek x` / `poke x` to a reference parameter, or take an independent value with `.clone()`. Checked local borrow bindings are a possible future feature; until they are designed, the form is rejected."))

_add(ErrorMessage("CE2412", Severity.ERROR,
    "cannot mutate '{owner}' while '{name}' borrows from it",
    Category.BORROW, "A `let` bound from a read THROUGH an owner -- `let v = h.items`, `let v = c.get(0)??` -- BORROWS: it names storage the owner keeps and still frees. Mutating, freeing, rebinding or moving that owner while the binding is live would leave the binding pointing at storage the owner no longer holds. The borrow lasts to the end of the block that declares it, so move the mutation after that block, or take an independent value with `.clone()`. This is Rust's E0502."))

_add(ErrorMessage("CE2414", Severity.ERROR,
    "cannot mutate through binding '{name}': a match/foreach binding is a read-only view",
    Category.BORROW, "A BARE `match` payload binding and a BARE `foreach` loop binding borrow a value the scrutinee or the container owns. The compiled binding is a private copy, so a write through it -- a mutating method, a field assignment, or a `poke` borrow -- never reaches the owner and is silently lost (issue #253). The binding carries a MODE, and the mode is the escape (HANDLES.md ruling R11): `poke` binds a pointer into the owner's storage, so the write reaches it, and `nom` takes the value outright where the match owns its scrutinee. A copy is the third way -- `.clone()`, mutate, store back -- and a type that owns a resource has none, so for one of those the message names `.share()` instead, exactly as CE2411 does. A rebind of the binding ITSELF (`n := 99`) stays legal: it re-initializes a local and does not claim to write through."))

_add(ErrorMessage("CE2411", Severity.ERROR,
    "cannot consume '{name}': another owner keeps this value",
    Category.BORROW, "A borrow names storage something else owns and still frees, so a position that takes ownership cannot have it. Three shapes borrow: a `match` payload binding, a `foreach` loop binding, and every read THROUGH a live owner -- a field read (`h.inner`), an index (`rows[i]`) and a container get-out (`c.get(0)??`, `own.get()`). Reading through a borrow is free; clone it to take an independent value: `{name}.clone()`. Only a value whose type transitively owns heap (a dynamic array, List, Own, HashMap, a string or a capturing closure) is affected -- a primitive borrow is unrestricted, and so is a string bound directly from a literal, which points into read-only memory and owns nothing."))

# --- The undefined reference POSITIONS (R4) ------------------------------------------
#
# The grammar's `?type` rule is recursive and universal, so `peek T` / `poke T` parses in
# EVERY type position. Semantics defines it for exactly ONE: the parameter. Each of the six
# positions below was accepted and then failed in its own way -- an internal error, a
# dangling read, or silent dead code.
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
    Category.BORROW, "A `peek` / `poke` struct field parses but has no checked semantics: nothing relates the field's borrow to the value it points at, so the struct may outlive it. Reading such a field is an internal error today (issue #315). Store an owned value, or an index into a container the struct does not own. A borrow inside a struct needs lifetimes and is a possible future feature; until it is designed, the form is rejected."))

_add(ErrorMessage("CE2416", Severity.ERROR,
    "an enum variant payload cannot have a reference type ('{ty}')",
    Category.BORROW, "A `peek` / `poke` enum payload parses and runs with no tracking of any kind, so the enum may outlive the value it borrows (issue #316). It is also how a returned borrow escapes: a `Result@(peek T, E)` is an enum payload, which is the shape that made a dangling read reachable (issue #314). Carry an owned value in the variant. Same lifetime problem as a reference struct field (CE2415)."))

_add(ErrorMessage("CE2417", Severity.ERROR,
    "a function cannot return a reference type ('{ty}')",
    Category.BORROW, "Returning a `peek` / `poke` lets a function hand out a borrow of its own local, and the caller reads it after the frame is gone -- a dangling read that compiles clean today (issue #314). `typesys.py` states the intended rule, 'borrows are function-scoped (end at function return)', and this is what enforces it. Return an owned value, or `.clone()` what you borrowed. Returning a borrow needs lifetimes to be sound."))

_add(ErrorMessage("CE2418", Severity.ERROR,
    "a reference to a reference is not supported ('&{outer} &{inner} ...')",
    Category.BORROW, "Both grammar rules for a borrow are recursive, so `peek peek i32` parses -- in a type position and in an expression position (issue #317). There is no double borrow in the language: a borrow of a borrow is the same borrow, and the extra level has no meaning at any layer. Write the single borrow."))

_add(ErrorMessage("CE2419", Severity.ERROR,
    "a reference type cannot be a generic type argument ('{ty}')",
    Category.BORROW, "A container of borrows -- `List@(peek T)`, `HashMap@(peek K, V)`, `Maybe@(peek T)` -- has no defined semantics: nothing relates the stored borrows to the values they point at, and the backend cannot lay one out (issue #318). Store owned values, or indices into a container that outlives the uses. There is NO `Maybe` / `Result` exemption on purpose: those two are exactly how a returned borrow escapes (CE2417). Foreign `ptr` carries the same restriction, as CE5012."))

_add(ErrorMessage("CE2420", Severity.ERROR,
    "an extension cannot target a reference type ('{ty}')",
    Category.BORROW, "`extend peek T` compiles and is permanently uncallable: a reference target falls through method resolution, so every call reports 'no such method' and the body is dead code the author believes they wrote (issue #319). Extend the referent instead -- the methods on `&T` ARE the methods on `T`, so `extend T` is already callable through a `peek T` / `poke T` receiver. This is the CE2097 shape: an extension that can never be reached is a diagnostic, not silence."))

# --- Method parameters, `self` included (R6) -------------------------------------------
#
# The third and fourth read-only receivers, after the match/foreach binding (CE2414) and
# the `peek` reference (CE2408). All the read-only kinds share ONE gate -- the
# `READONLY_RECEIVERS` table in `semantics/passes/borrow/writes.py` -- and each keeps
# its own code because each carries its own
# rationale and its own escape -- the same reasoning as the six position codes above.
#
# The two here are one rule (#298: every parameter of an extension or perk method is a
# borrow) with two escapes: a by-value parameter is redeclared `poke T`, and a receiver
# `poke self` (#327, shipped 2026-08-16). The codes stay separate because the escapes are
# what the help text has to name.

_add(ErrorMessage("CE2421", Severity.ERROR,
    "cannot write through 'self': a method receiver is a read-only borrow",
    Category.BORROW, "An extension or perk method receives `self` as a BORROW: the caller keeps the value (the ruling on issue #298, `docs/design/ownership-conventions.md` S8.6). The compiled receiver is a private copy, so a write through it -- a mutating method, a field assignment, or a `poke` borrow of it -- never reaches the caller. A plain field was silently LOST; an owning field was a double free plus a leak, because the field rebind released the caller's buffer through the copy (issue #326). This is CE2414's rule for the one receiver CE2414 does not cover. The mutating receiver is spelled `poke self` (issue #327): declare the method `extend T name(poke self, ...)` and the write reaches the caller. Alternatively return the new value and let the caller store it."))

_add(ErrorMessage("CE2422", Severity.ERROR,
    "cannot write through '{name}': a by-value method parameter is a read-only borrow",
    Category.BORROW, "Every parameter of an extension or perk method is a BORROW of the caller's value, `self` and the explicit ones alike (the ruling on issue #298). A by-value one is compiled as a private copy, so a write through it -- a mutating method, a field assignment, or a `poke` borrow of it -- never reaches the caller: a plain field was silently lost, and an owning field was a double free plus a leak, because the field rebind released the caller's buffer through the copy. CE2421 is the same rule for the receiver, and the plain-function form has no such problem, because there the callee OWNS its by-value parameters. Unlike the receiver, this one has an escape that exists today: declare the parameter `poke T` and the write reaches the caller."))

# --- Reference bindings (#300 phase 1) --------------------------------------------------
#
# `foreach(poke r in ...)` and `Own(poke inner)` bind a POINTER into the container's /
# pointee's storage, so a write through the binding reaches the owner. The two codes here
# fence the phase-1 boundary: an iterable whose items have no address (CE2423), and the
# match-pattern position, which waits on the enum payload alignment fix (CE2424).

_add(ErrorMessage("CE2423", Severity.ERROR,
    "a reference binding needs addressable elements; this iterable yields values",
    Category.BORROW, "A `peek`/`poke` foreach binding is a pointer into the container's element storage, so the iterable must HAVE element storage. A range (`0..10`) synthesizes its values, and `HashMap.entries()` synthesizes each `Entry` pair on the fly -- there is no address to bind (issue #300). Iterate a container (`arr.iter()`, `list.iter()`, `map.keys()`, `map.values()`) or drop the marker and take the value."))

_add(ErrorMessage("CE2424", Severity.ERROR,
    "a reference binding in a NESTED match pattern is not supported",
    Category.BORROW, "A top-level `Variant(poke x)` binds a pointer into the scrutinee's payload storage and is supported (issue #300 phase 3, on the aligned enum payload layout). A NESTED pattern is different: extraction walks through temporary copies of the inner enums, so a pointer into one writes to storage nobody reads -- the silently-lost-write class of issue #253. Bind the payload by value in the nested pattern, or restructure to match the inner enum at the top level."))

_add(ErrorMessage("CE2425", Severity.ERROR,
    "a 'peek self'/'poke self' receiver parameter is not valid here",
    Category.BORROW, "The receiver parameter (#327) is the FIRST parameter of an EXTENSION or PERK method: `extend Counter bump(poke self) ~:`. It is not valid in a plain top-level function (a plain function has no receiver -- take `poke T name`), not valid after the first position, and a bare `poke name` that is not `self` is a reference parameter missing its type."))

# --- The `let`-borrow binding (#344) ----------------------------------------------------
#
# The FIFTH read-only receiver, and the last member of the family CE2414, CE2408, CE2421
# and CE2422 close. Its own code rather than a widened CE2414 because the two escapes
# differ: a match/foreach binding is a private DEEP copy, so the only way out is to clone,
# mutate and store back, while a `let`-borrow names storage an owner still holds -- so the
# FIRST answer is "write to the owner".

_add(ErrorMessage("CE2426", Severity.ERROR,
    "cannot write through '{name}': it borrows storage another value still owns",
    Category.BORROW, "A `let` bound from a read THROUGH an owner -- `let v = h.items`, `let v = c.get(0)??` -- BORROWS: it names storage the owner keeps and still frees (issue #242). A write through it is not merely lost, which is what CE2414 says for a match/foreach binding: the binding holds its own copy of the descriptor while the DATA is shared, so a mutating method updates a length nobody reads, a field assignment lands on the private copy, and a `.push()` that reallocates frees the OWNER's buffer -- a double free plus a read of released memory that compiled clean before issue #344. Write to the owner directly (`h.items.push(9)`), or take an independent value with `.clone()`, mutate it, and store it back. CE2412 is the complementary question -- may the OWNER be changed while the binding lives -- not an alternative to this one."))


# --- Borrow by default: the mode markers (docs/design/borrow-model.md) ------------------
#
# A marked mode is written at BOTH ends -- the declaration and the call site -- and the
# unmarked default is written at neither. `peek` and `poke` already had that symmetry, and
# they get it for free: a reference parameter carries a `ReferenceType`, so a missing or
# wrong marker is CE2006, an argument type mismatch. `nom` changes no type, so it needs a
# code of its own.

_add(ErrorMessage("CE2427", Severity.ERROR,
    "argument mode does not match the declared mode of parameter '{name}'",
    Category.BORROW, "A `nom` parameter takes OWNERSHIP of its argument, and that must be visible where the value is handed over: without the marker, `f(s)` would not show whether `s` survives the call, and the reader would have to open the callee to find out (docs/design/borrow-model.md S3). So the marker is written at both ends, or at neither. Add `nom` at the call site to hand the value over, or drop it if the callee only borrows. `.clone()` is the escape when the caller needs to keep its own value: `f(nom s.clone())`."))

_add(ErrorMessage("CE2428", Severity.ERROR,
    "`nom` has no meaning on the foreign parameter '{name}'",
    Category.BORROW, "FFI is outside the mode system. A C callee never receives a Sushi value: the compiler marshals the argument into a fresh C representation that the CALLER owns and frees at scope exit, so there is nothing for a foreign parameter to take ownership of. Declare the parameter without the marker. The four modes describe how a value crosses a SUSHI call boundary (docs/design/borrow-model.md S5)."))


# --- The unbound chained borrow (#352 ruling, #407) --------------------------------------
#
# The SIXTH read-only receiver kind, and the first keyed on SHAPE rather than on the state
# of a name: a write receiver must reach its root through member and index steps only. A
# chain that crosses a call boundary -- a method call, a `??`, a plain call -- holds a
# temporary copy past that point, so there is no name to hold a `BorrowState` on and the
# write could never reach the owner.

_add(ErrorMessage("CE2429", Severity.ERROR,
    "cannot write through an unbound chained borrow",
    Category.BORROW, "The value past a call boundary is a temporary copy, not the owner's storage: `o.get()` is a get-out, so `o.get().items.push(9)` would land on the copy and be lost, while the `Own` keeps and frees the real buffer (issue #407 -- the write compiled, printed the old length, and the leak counters balanced). A FRESH temporary is rejected by the same rule, because the statement discards the value and the write is dead either way. Bind a clone, mutate it, and rebuild the owner -- `let Holder h = o.get().clone()`, `h.items.push(9)`, `o := Own.alloc(h)` -- or, where the `Own` sits in an enum payload, mutate in place through a nested `Own(poke inner)` reference binding (#300)."))


_add(ErrorMessage("CE2430", Severity.ERROR,
    "'{name}' cannot be the source of a bulk write into itself",
    Category.BORROW,
    "A bulk write -- `.extend()`, `.extend_range()` -- borrows its source and grows its "
    "destination. When the two are the same array the growth may REALLOCATE the buffer, "
    "which leaves the source pointer dangling in the middle of the copy. That is a "
    "memory-safety hole rather than a wrong answer, so it is refused rather than defined. "
    "The escape is `.clone()`, or `.ss(start, count)` for a range, either of which gives an "
    "independent source. CE2412 is the neighbouring question -- may the OWNER be changed "
    "while a `let`-borrow of it lives -- and not this one, because here the borrow is a "
    "method argument. A copy that must read what it is writing is not this operation at "
    "all: a DEFLATE back-reference expands a run by reading bytes the same loop just wrote, "
    "and it stays a per-element loop for that reason."))

_add(ErrorMessage("CE2431", Severity.ERROR,
    "cannot clone '{type}': it owns a resource, and a copy of it would be a second handle",
    Category.BORROW, "HANDLES.md ruling R3. `.clone()` is the one deep copy, and a derived clone copies a value FIELD BY FIELD. A type that implements the `Drop` perk owns something no field walk can see -- a file or a socket holds one i32 descriptor -- so a derived clone would copy that number and leave two values holding one descriptor, both of which drop. That is a double close, and the copy verb would hide it. A copy verb must not quietly mean a second handle: Rust hit this with Arc::clone, wrote a lint for it, and then demoted the lint. Sushi gives the second-owner operation its own name instead. Use `.share()`, which is dup(2) and says what it does -- an independent descriptor over a SHARED open file description, so the offset is shared too. For concurrent reads of one file the answer is `read_at`/`write_at`, where the offset is an argument and no state is shared. The refusal reaches a struct that HOLDS a resource type, an array of them and a container of them, because cloning any of those copies the descriptor one level down; it is reported at the INSTANTIATION for a generic body that clones, because one monomorphized body serves every type argument and the argument is what makes it illegal."))

# --- The pattern binding MODES (HANDLES.md ruling R11) ---------------------------------
#
# A pattern binding carries a mode, and the three are the ones parameters already have:
# bare borrows, `poke` writes through, `nom` TAKES. The three codes below fence what
# taking needs -- a scrutinee the match owns (CE2432), a whole variant rather than one
# slot of it (CE2433), and storage that can still free itself afterwards (CE2434).

_add(ErrorMessage("CE2432", Severity.ERROR,
    "cannot take '{name}': this match only borrows its scrutinee",
    Category.BORROW,
    "HANDLES.md ruling R11. A `nom` payload binding takes the value out of the "
    "scrutinee, so the match has to OWN the scrutinee to give it away. A TEMPORARY -- a "
    "call result, a constructor, a `??` -- is owned by construction and needs no marker. "
    "A place expression is not: `match r:` leaves `r` the owner, and `r` still frees the "
    "payload at the end of its scope, so a second owner here would be a double free. "
    "Write `match nom r:` to hand the value to the match; `r` is then consumed exactly "
    "as `f(nom r)` consumes it, and a later mention of it is CE2405. Marked at both ends "
    "or neither is the same rule CE2427 states for a call argument. To keep `r`, drop "
    "the marker and read through the borrow, or bind `poke` to write through it."))

_add(ErrorMessage("CE2433", Severity.ERROR,
    "'{name}' is bound by value while this arm takes another payload of the same variant",
    Category.BORROW,
    "HANDLES.md ruling R11, the all-or-nothing rule. What suppresses the match's free is "
    "the WHOLE scrutinee and not one payload slot, so an arm that takes any payload "
    "leaves every other owning payload of that variant with no owner at all -- taking "
    "one and borrowing its neighbour is a leak of the neighbour, not a dangling read. "
    "Mark every owning binding in the arm `nom`, or none of them. A payload that owns no "
    "heap is unaffected: an i32 beside a taken array stays a plain binding. Discarding "
    "the neighbour with `_` does NOT help -- it is the same slot with no name. A "
    "per-slot take needs a drop flag per payload, which is a later change; today the "
    "variant moves whole."))

_add(ErrorMessage("CE2435", Severity.ERROR,
    "cannot use '{name}': '{method}' consumed it",
    Category.BORROW,
    "HANDLES.md ruling R27. A `nom self` method takes ownership of what it was called "
    "on, so the binding is spent by the call. This is not CE2405 and the difference is "
    "not cosmetic: nothing was transferred to another owner that the reader can point "
    "at, and a receiver's mode is DECLARATION-only, so there is no `nom` marker "
    "anywhere on the page. The diagnostic has to carry what the syntax cannot, which is "
    "why it names the method. One code covers every consuming receiver, and the method "
    "name is what tells the two shapes apart: `close()` releases a descriptor and "
    "answers `~`, so nothing went anywhere, while `lines()` and `into_inner()` hand the "
    "value onward. A `nom` ARGUMENT keeps CE2405, because `eat(nom s)` is a real move "
    "and the marker is visible. To keep using the value, do not call the consuming "
    "method -- an owned handle closes itself when its owner leaves scope, so an "
    "explicit `close()` is only for the caller who has to SEE the failure."))

_add(ErrorMessage("CE2434", Severity.ERROR,
    "a `nom` binding is not valid inside an `Own(...)` pattern",
    Category.BORROW,
    "HANDLES.md ruling R11. `Own@(T)` is a heap cell that owns its pointee. Taking the "
    "pointee out with `nom` would leave the cell itself with nothing to free it, because "
    "the only thing that can suppress the match's free is the whole scrutinee -- so the "
    "malloc'd box would leak while the value inside it moved on. `Own(poke x)` and "
    "`Own(peek x)` stay legal: both bind the heap pointer and take nothing. To move the "
    "value out, take the `Own@(T)` itself -- a `nom` binding on the payload that holds "
    "it -- and read through it at the new owner. Freeing the box alone needs an owner "
    "for the cell that survives the move, which is a later change."))
