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
    "cannot borrow '{name}': variable does not exist",
    Category.BORROW, "Attempted to borrow a variable that was not declared."))

_add(ErrorMessage("CE2401", Severity.ERROR,
    "cannot move/reassign '{name}' while it is borrowed",
    Category.BORROW, "A variable cannot be moved or reassigned while a reference to it is active."))

_add(ErrorMessage("CE2402", Severity.ERROR,
    "cannot destroy '{name}' while it is borrowed",
    Category.BORROW, "A variable cannot be explicitly destroyed (.destroy()) while a reference to it is active."))

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
# dangling read, or silent dead code (BORROW.md section 6).
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
