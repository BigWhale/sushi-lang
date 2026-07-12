"""FFI / foreign function interface errors (CE5xxx).

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


_add(ErrorMessage("CE5001", Severity.ERROR,
    "external link-name '{symbol}' clashes with a built-in extern of a different signature",
    Category.FFI, "A compiler built-in already declares this C symbol with a different signature. LLVM only deduplicates identical declarations. Choose a different link-name or match the reserved signature."))

_add(ErrorMessage("CE5002", Severity.ERROR,
    "public function '{name}' exposes a foreign `ptr` and cannot appear in a library (.slib) public API",
    Category.FFI, "FFI is a private implementation detail of a unit. Externals and any public function whose signature exposes a foreign `ptr` cannot propagate through Nori packages."))

_add(ErrorMessage("CE5003", Severity.ERROR,
    "external signature uses non-C-ABI type '{type}'",
    Category.FFI, "External (FFI) signatures are limited to C-representable types: i8..i64, u8..u64, f32, f64, bool, string (auto-marshalled), ptr, and ~ (void). Result/Maybe, structs, arrays, references, and user types cannot cross the C ABI boundary."))

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
    "public function '{name}' exposes a foreign `ptr` in its signature and cannot cross a unit boundary",
    Category.FFI, "FFI is a private implementation detail of a unit. A `public fn` whose parameters or return type contain `ptr` (including inside Result or Maybe) cannot be part of a unit's public API. Keep the function private, or wrap the pointer in a struct (struct fields may carry `ptr` across units)."))

_add(ErrorMessage("CE5009", Severity.ERROR,
    "foreign `ptr` used in a unit with no `unsafe external` block",
    Category.FFI, "The `ptr` type may only be named in a unit that declares an `unsafe external` block - no danger zone, no ptr. This keeps every file that can traffic in raw foreign handles greppable by its `unsafe external` marker. Other units hold handles through wrapper structs declared in the FFI unit."))

_add(ErrorMessage("CE5010", Severity.ERROR,
    "foreign `ptr` cannot be used with operator '{op}'",
    Category.FFI, "A `ptr` is an opaque handle: it has no comparable identity, no arithmetic, and no truthiness. If null-checking is ever needed it will arrive as an `is_null(ptr)` intrinsic, never as `==`."))

_add(ErrorMessage("CE5011", Severity.ERROR,
    "foreign `ptr` has no methods (attempted '.{method}()')",
    Category.FFI, "A `ptr` is an opaque handle with no hash, no string form, and no methods. Pass it back to an external function, or wrap it in a struct and attach extension methods to the struct."))

_add(ErrorMessage("CE5012", Severity.ERROR,
    "foreign `ptr` cannot be a type argument of '{base}'",
    Category.FFI, "Only Result<ptr, E> and Maybe<ptr> support carrying a foreign `ptr`. Other generic containers (HashMap, List, user-defined generics) cannot store an opaque handle. Wrap the pointer in a concrete struct and store that instead."))
