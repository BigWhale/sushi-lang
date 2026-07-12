"""Function and declaration errors (CE01xx).

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


_add(ErrorMessage("CE0100", Severity.ERROR,
    "unsupported expression type: {expr}",
    Category.INTERNAL, "Expression type not supported in this context."))

# Function/header errors
_add(ErrorMessage("CE0101", Severity.ERROR,
    "duplicate function '{name}'",
    Category.FUNC, "Two functions share the same name in a compilation unit."))

_add(ErrorMessage("CE0102", Severity.ERROR,
    "duplicate parameter '{name}'",
    Category.FUNC, "A function declares the same parameter name more than once."))

_add(ErrorMessage("CE0103", Severity.ERROR,
    "missing return type for function '{name}'",
    Category.FUNC, "Function header must specify an explicit return type."))

_add(ErrorMessage("CE0104", Severity.ERROR,
    "missing type annotation for constant '{name}'",
    Category.FUNC, "Constant declarations must specify an explicit type."))

_add(ErrorMessage("CE0105", Severity.ERROR,
    "duplicate constant '{name}'",
    Category.FUNC, "Two constants share the same name in a compilation unit."))

_add(ErrorMessage("CE0106", Severity.ERROR,
    "main() function must return an integer type (i8-i64, u8-u64), got '{type}'",
    Category.FUNC, "The main function must return an integer type to be used as a shell exit code."))

_add(ErrorMessage("CE0107", Severity.ERROR,
    "function '{name}' must return a value on all code paths",
    Category.FUNC, "All functions with a return type must have a return statement."))

# Constant expression evaluation errors
_add(ErrorMessage("CE0108", Severity.ERROR,
    "expression is not a compile-time constant (type: {expr_type})",
    Category.FUNC, "Constant declarations must use compile-time evaluable expressions. Function calls, method calls, and variable references are not allowed."))

_add(ErrorMessage("CE0109", Severity.ERROR,
    "circular constant dependency detected: {chain}",
    Category.FUNC, "Constants cannot depend on themselves directly or indirectly."))

_add(ErrorMessage("CE0110", Severity.ERROR,
    "unsupported operation '{op}' in constant expression",
    Category.FUNC, "This operation cannot be evaluated at compile-time for constants."))

_add(ErrorMessage("CE0111", Severity.ERROR,
    "invalid type cast in constant expression from {from_type} to {to_type}",
    Category.FUNC, "Type cast is not allowed in constant expressions."))

_add(ErrorMessage("CE0112", Severity.ERROR,
    "division by zero in constant expression",
    Category.FUNC, "Constant expressions cannot divide by zero."))

_add(ErrorMessage("CE0113", Severity.ERROR,
    "{message}",
    Category.INTERNAL, "Generic enum constructor requires type annotation from semantic analysis."))

_add(ErrorMessage("CE0114", Severity.ERROR,
    "{message}",
    Category.FUNC, "A variadic '...T' parameter must be the last parameter, a function may declare at most one, and its element type must not be a reference (a dynamic-array element '...T[]' is allowed and moved per element)."))

_add(ErrorMessage("CE0115", Severity.ERROR,
    "variadic '...T' parameter not allowed in {context}",
    Category.FUNC, "Variadic parameters are only permitted in plain function definitions, not in perk methods or extension methods."))

_add(ErrorMessage("CE0116", Severity.ERROR,
    "public function '{name}' is variadic and cannot appear in a library public API",
    Category.FUNC, "Native variadic functions cannot be exported through a .slib public API in v1 (the variadic flag is not serialized into the library format)."))

_add(ErrorMessage("CE0117", Severity.ERROR,
    "{message}",
    Category.FUNC, "A type-pack parameter '...Ts' must be the last parameter, and a function may declare at most one type-pack."))

_add(ErrorMessage("CE0118", Severity.ERROR,
    "{message}",
    Category.FUNC, "A function cannot mix a v2 type-pack parameter '...Ts' with a v1 native variadic '...T'."))

_add(ErrorMessage("CE0119", Severity.ERROR,
    "malformed expand(...): {message}",
    Category.FUNC, "An expand(...) construct is malformed or used outside a type-pack context."))

_add(ErrorMessage("CE0120", Severity.ERROR,
    "{message}",
    Category.FUNC, "A bloom argument 'arr...' requires a variadic '...T' parameter and must be the sole, last trailing argument at the call site."))

_add(ErrorMessage("CE0121", Severity.ERROR,
    "could not resolve the concrete enum type for match pattern '{pattern}' - pattern bindings cannot be extracted",
    Category.INTERNAL, "The backend could not determine the scrutinee's concrete enum type for a match arm with bindings. The type checker should have annotated Match.resolved_scrutinee_type; a miss here would otherwise silently drop the arm's binding locals."))

_add(ErrorMessage("CE0122", Severity.ERROR,
    "generic type '{name}' is infinitely recursive - monomorphization exceeded the maximum depth",
    Category.TYPE, "A generic type instantiation nests without bound (e.g. a type parameter that grows on each self-reference), so monomorphization cannot terminate. A finite self-reference through an opaque pointer (Own<T>) is fine; an ever-growing type argument is not."))

_add(ErrorMessage("CE0123", Severity.ERROR,
    "no hash emitter registered for kind '{kind}'",
    Category.INTERNAL, "Pass 1.8 registered a hash() method whose LLVM emitter the backend never supplied. The backend types modules register their emitter factories at import; one of them failed to load."))

_add(ErrorMessage("CE0124", Severity.ERROR,
    "'??' expression reached codegen without a type annotation from semantic analysis",
    Category.INTERNAL, "Pass 2 annotates every TryExpr it validates (inner type, unwrapped type, success tag). Reaching the backend without one means the expression's type was never inferred - the backend no longer re-infers types, so this is a gap in Pass 2, not a user error."))

_add(ErrorMessage("CE0125", Severity.ERROR,
    "internal error: borrow checker has no arm for expression node '{node}'",
    Category.INTERNAL, "The Expr union grew a member the borrow checker does not dispatch on. This used to be a SILENT fall-through, which meant no borrow checking at all for that node - the root cause of the bloom use-after-free (#174), the unchecked range bound (#175) and the unchecked perk body (#176). tests/unit/test_borrow_dispatch_is_total.py is the CI gate; this is the runtime backstop."))
