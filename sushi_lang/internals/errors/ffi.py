"""FFI / foreign function interface errors (CE5xxx)."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


_add(ErrorMessage("CE5001", Severity.ERROR,
    "external link-name '{symbol}' is declared with another signature",
    Category.FFI, "One C symbol has one signature in one program (#1099). Two `unsafe external` declarations of one link name agree only when each position has the SAME C type: each parameter, the return and `var_arg`. `i32` against `u32` does not agree, because `int` and `unsigned int` are two C types, and a C compiler refuses the pair. A fixed signature against a `var_arg` signature does not agree, also when the fixed parameters are equal. Two spellings of one C type agree: `string` and `Maybe@(string)` are both `char*`, `ptr` and `Maybe@(ptr)` are both `void*`, and a link name written as a constant is the name it folds to. Two external variables of one link name must have the same C type, and a function and a variable cannot share one link name. The rule reads every declaration in the program, in each unit and across units, the standard library units written in Sushi included. The three libc symbols that are the compiler's own, `malloc`, `free` and `exit`, are the first declaration of their names, so a user declaration of one of them must have the compiler's C types. Every other libc symbol that the compiler declares is independent of a user declaration: each call goes through the type of its own declaration. The back end declares a symbol one time, so before this rule the second declaration called through the first one and computed a wrong value with no diagnostic. The note points at the other declaration. Make the two signatures the same, or remove one declaration."))

_add(ErrorMessage("CE5002", Severity.ERROR,
    "public function '{name}' exposes a foreign `ptr` and cannot appear in a library (.slib) public API",
    Category.FFI, "FFI is a private implementation detail of a unit. Externals and any public function whose signature exposes a foreign `ptr` cannot propagate through Nori packages."))

_add(ErrorMessage("CE5003", Severity.ERROR,
    "external signature uses non-C-ABI type '{type}'",
    Category.FFI, "External (FFI) signatures are limited to C-representable types: i8..i64, u8..u64, f32, f64, bool, string (auto-marshalled), ptr, and ~ (void), plus `Maybe@(string)` and `Maybe@(ptr)` at the top level of a parameter or a return, which say that the pointer may be NULL (#1085): a NULL return answers `Maybe.None`, and a `Maybe.None` argument crosses as NULL. A PARAMETER may also be a byte buffer, `u8[]`, `peek u8[]` or `poke u8[]`, which crosses as the pointer to its first byte (#1088); a `u8[]` return is refused, because C cannot answer a Sushi array. Every other Maybe (`Maybe@(i32)`, a nested one), a Result, a struct, any other array (`i32[]`, a fixed `u8[N]`), a reference and a user type cannot cross the C ABI boundary."))

_add(ErrorMessage("CE5004", Severity.ERROR,
    "variadic external '{name}' requires at least one fixed parameter",
    Category.FFI, "A variadic `unsafe external` declaration (trailing `...`) must declare at least one fixed parameter. The C ABI's va_start needs a named argument to anchor the variadic argument list."))

_add(ErrorMessage("CE5005", Severity.ERROR,
    "non-C-ABI type '{type}' passed as variadic argument to external '{name}'",
    Category.FFI, "Each trailing variadic argument to an external call must be C-representable: i8..i64, u8..u64, f32, f64, bool, string (auto-marshalled), or ptr. Result/Maybe, structs, arrays, references, and user types cannot cross the C ABI boundary."))

_add(ErrorMessage("CE5006", Severity.ERROR,
    "public generic '{name}' cannot be exported: it references un-shippable library symbol '{symbol}'",
    Category.FFI, "A public generic is shipped in a library (.slib) and monomorphized at the consumer. Library-private helpers it references ship automatically as part of the export closure (as templates if generic, as linkable signatures if concrete, with values for constants). Two classes of reference cannot cross the boundary: a symbol whose signature exposes a foreign 'ptr' (FFI is a private unit detail, see CE5002), and an 'unsafe external' namespace (foreign bindings cannot be re-declared at the consumer). Wrap the foreign detail behind a private helper with a C-ABI-free signature, or restructure the generic to avoid it."))

_add(ErrorMessage("CE5007", Severity.ERROR,
    "library '{lib}' ships private symbol '{name}' which conflicts with a local definition",
    Category.FFI, "An imported library's exported generics depend on this private helper, which ships in the .slib export closure and must be registered at the consumer under its original name. A local symbol with the same name would silently change what the library's monomorphized bodies call. Rename the local symbol."))

_add(ErrorMessage("CE5008", Severity.ERROR,
    "public {kind} '{name}' exposes a foreign `ptr` in its signature and cannot cross a unit boundary",
    Category.FFI, "FFI is a private implementation detail of a unit. A public declaration whose parameters, return type or error arm contain `ptr` (including inside Result or Maybe) cannot be part of a unit's public API. `{kind}` says which declaration it was: a public generic, an extension method and a perk method are all reached, and each used to compile clean. Keep the function private, or wrap the pointer in a struct (struct fields may carry `ptr` across units)."))

_add(ErrorMessage("CE5009", Severity.ERROR,
    "foreign `ptr` used in a unit with no `unsafe external` block",
    Category.FFI, "The `ptr` type may only be named in a unit that declares an `unsafe external` block - no danger zone, no ptr. This keeps every file that can traffic in raw foreign handles greppable by its `unsafe external` marker. Other units hold handles through wrapper structs declared in the FFI unit. The rule reads two walks, because a body spells the name exactly as a signature does: a local, a `foreach` item, a cast target, a lambda parameter and a call-site type argument are all naming positions. The body half read a field the AST does not have and enforced nothing until #596, so `let ptr p = 0` compiled and the type checker answered CE2002 about the initializer instead."))

_add(ErrorMessage("CE5010", Severity.ERROR,
    "foreign `ptr` cannot be used with operator '{op}'",
    Category.FFI, "A `ptr` is an opaque handle: it has no comparable identity, no arithmetic, and no truthiness. There is nothing to test for null either: a null is never a Sushi value. A C function that may answer NULL is declared `Maybe@(ptr)` and its NULL arrives as `Maybe.None`, while a plain `ptr` return asserts non-null and a NULL there is RE2025 at the call (#1085). The `is_null(ptr)` intrinsic this text promised before is replaced by that rule."))

_add(ErrorMessage("CE5011", Severity.ERROR,
    "foreign `ptr` has no method '.{method}()'",
    Category.FFI, "A `ptr` has one closed set of methods, the foreign-memory methods (#1086): `load_<width>(off)` and `store_<width>(off, v)` for each integer and float width, `load_ptr(off)` (a `Maybe@(ptr)`), `store_ptr(off, q)`, `offset(n)` and `to_string(off)`, each at a byte offset. It has nothing else: no hash, no string form, no extension method. Pass it back to an external function, or wrap it in a struct and attach extension methods to the struct. Before #1086 a `ptr` had no method at all, and this text said so."))

_add(ErrorMessage("CE5013", Severity.ERROR,
    "external link-name '{symbol}' names a symbol this program defines",
    Category.FFI, "An `unsafe external` reaches OUT of the program: it may name a foreign symbol, never one this build defines. A program's units share one LLVM module and a linked library's module is merged into it, so a declaration and a definition of one name UNIFY -- the declaration then enters the program's own body with no ABI check, which is how a library-PRIVATE body could be run from code that may not call it, returning garbage read out of the wrong register (#470). Where the compiler already held a declaration of the name, the same program was an internal error (`DuplicatedNameError`) instead of a diagnostic. Rename the link-name, or call the Sushi function directly. The rule reads every symbol this build defines: the function and constant tables, the linked libraries, and the symbols the standard library GENERATES -- those last are in no semantic table, so the compiler reads the manifest the stdlib build writes beside its bitcode, plus a small reserved set for the ones the backend emits inline. Before that, a generated name built clean and died with a bus error at run time (#472). A generated name is refused whether this program links the unit or not. CE5001 is the neighbouring rule for a built-in extern DECLARATION, which LLVM deduplicates when the signatures match."))

_add(ErrorMessage("CE5014", Severity.ERROR,
    "`errno()` is read in a unit with no `unsafe external` block",
    Category.FFI, "`errno()` answers the calling thread's `errno`, the cause a failed C call leaves behind (#1087). Only a C call can leave one, and only a unit that declares an `unsafe external` block can make a C call, so the built-in has the same confinement as the `ptr` type (CE5009): no danger zone, no errno. A unit's own `fn errno` is an ordinary declaration and wins over the built-in anywhere. Read `errno()` directly after the failed call and before any `close`, `free` or other C call, because those can overwrite it."))

_add(ErrorMessage("CE5015", Severity.ERROR,
    "the link name of external '{name}' is a constant of type {type}, not a string",
    Category.FFI, "The link name after `=` in an `unsafe external` declaration is a string literal or a string constant (#1089): `= \"stat\"`, `= STAT_SYMBOL`, or `= platform.STAT_SYMBOL`. A constant is the form for a symbol that differs per platform, such as `stat$INODE64` on macOS x86_64, and `<sys/platform>` holds those names. The constant is folded in the unit that declares the block, and CE5013 and CE5001 read the folded name. A constant of another type names no symbol."))

_add(ErrorMessage("CE5016", Severity.ERROR,
    "external variable '{name}' is read-only",
    Category.FFI, "A `var` in an `unsafe external` block declares a C global variable (#1090), and Sushi reads it: each read loads the global at that moment. A write to a C global (`libc.optind := 1`) is refused, because nothing yet says who else reads the global or when. A write can come later with `poke` semantics. Call a C function that sets the global, or keep the value in a Sushi variable."))

_add(ErrorMessage("CE5012", Severity.ERROR,
    "foreign `ptr` cannot be a type argument of '{base}'",
    Category.FFI, "Only Result@(ptr, E) and Maybe@(ptr) support carrying a foreign `ptr`. Other generic containers (HashMap, List, user-defined generics) cannot store an opaque handle. Wrap the pointer in a concrete struct and store that instead."))
