"""Type, array and struct errors (CE2xxx).

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


# Type Errors
_add(ErrorMessage("CE2001", Severity.ERROR,
    "unknown type '{name}'",
    Category.TYPE, "A declared type is not recognized by the compiler."))

_add(ErrorMessage("CE2002", Severity.ERROR,
    "type mismatch: cannot assign {got} to {expected}",
    Category.TYPE, "The right-hand side expression type does not match the declared or inferred left-hand side type."))

_add(ErrorMessage("CE2003", Severity.ERROR,
    "return type mismatch: got {got}, expected {expected}",
    Category.TYPE, "A function's return expression type does not match its declared return type."))

_add(ErrorMessage("CE2004", Severity.ERROR,
    "invalid operand types for operator '{op}'",
    Category.TYPE, "An operator was applied to operands of incompatible types."))

_add(ErrorMessage("CE2005", Severity.ERROR,
    "condition must be bool",
    Category.TYPE, "If/elif/while conditions require a 'bool' expression; implicit int→bool coercions are not allowed."))

_add(ErrorMessage("CE2006", Severity.ERROR,
    "argument type mismatch at position {index}: expected {expected}, got {got}",
    Category.TYPE, "A function call argument type does not match the corresponding parameter type."))

_add(ErrorMessage("CE2007", Severity.ERROR,
    "missing type annotation for variable '{name}'",
    Category.TYPE, "Variable declaration with 'let' requires an explicit type annotation."))

_add(ErrorMessage("CE2008", Severity.ERROR,
    "undefined function '{name}'",
    Category.TYPE, "Function call references a function that was not declared."))

_add(ErrorMessage("CE2009", Severity.ERROR,
    "function '{name}' expects {expected} arguments, got {got}",
    Category.TYPE, "Function call has wrong number of arguments."))

# Array-specific errors
_add(ErrorMessage("CE2010", Severity.ERROR,
    "array size must be a positive integer literal, got {size}",
    Category.TYPE, "Array type declaration requires a positive integer literal for size."))

_add(ErrorMessage("CE2011", Severity.ERROR,
    "array literal has {got} elements but declared type expects {expected}",
    Category.TYPE, "Array literal element count must match declared array size."))

_add(ErrorMessage("CE2012", Severity.ERROR,
    "array index {index} is out of bounds for array of size {size}",
    Category.TYPE, "Array access with compile-time constant index exceeds array bounds."))

_add(ErrorMessage("CE2013", Severity.ERROR,
    "array element type mismatch: expected {expected}, got {got}",
    Category.TYPE, "Array literal element type does not match declared array element type."))

_add(ErrorMessage("CE2014", Severity.ERROR,
    "invalid cast from '{source}' to '{target}'",
    Category.TYPE, "Type cast is not allowed between the specified types."))

_add(ErrorMessage("CE2015", Severity.ERROR,
    "constant '{name}' cannot use dynamic array type",
    Category.TYPE, "Constants must use compile-time types. Dynamic arrays are not allowed."))

_add(ErrorMessage("CE2016", Severity.ERROR,
    "method '{method}' expects {expected} argument(s), got {got}",
    Category.TYPE, "Built-in Result<T, E> and Maybe<T> methods take a fixed number of arguments."))

# Dynamic array-specific errors (compile-time only)

_add(ErrorMessage("CE2023", Severity.ERROR,
    "dynamic array method argument mismatch for '{method}': expected {expected}, got {got}",
    Category.TYPE, "Dynamic array method called with incorrect argument types."))

_add(ErrorMessage("CE2024", Severity.ERROR,
    "use of destroyed dynamic array '{name}'",
    Category.TYPE, "Attempted to use a dynamic array after it was explicitly destroyed."))

_add(ErrorMessage("CE2026", Severity.ERROR,
    "unterminated interpolation in string literal",
    Category.TYPE, "String interpolation braces must be properly closed with '}'."))

# Struct errors
_add(ErrorMessage("CE2027", Severity.ERROR,
    "struct '{name}' expects {expected} field(s), got {got}",
    Category.TYPE, "Struct constructor must provide exact number of fields."))

_add(ErrorMessage("CE2028", Severity.ERROR,
    "field '{field_name}' expects type '{expected}', got '{got}'",
    Category.TYPE, "Struct constructor field type mismatch."))

# Result type errors
_add(ErrorMessage("CE2030", Severity.ERROR,
    "return statement must use Ok() or Err()",
    Category.TYPE, "All return statements must explicitly wrap values in Ok() or use Err()."))

_add(ErrorMessage("CE2031", Severity.ERROR,
    "Ok() value type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The value inside Ok() must match the function's return type."))

_add(ErrorMessage("CE2032", Severity.ERROR,
    "blank type (~) can only be used as function return type",
    Category.TYPE, "Blank type cannot be used for variables, parameters, or constants."))

_add(ErrorMessage("CE2039", Severity.ERROR,
    "Err() error type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The error value inside Err() must match the function's error type."))

_add(ErrorMessage("CE2033", Severity.ERROR,
    "foreach requires an iterator, got '{got}'",
    Category.TYPE, "The expression in foreach must be an iterator (e.g., from calling .iter() on an array)."))

_add(ErrorMessage("CE2034", Severity.ERROR,
    "foreach item type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The declared item type in foreach does not match the iterator's element type."))

_add(ErrorMessage("CE2035", Severity.ERROR,
    "cannot interpolate expression of type '{type}' into string",
    Category.TYPE, "String interpolation only supports: integers, floats, booleans, and strings."))

_add(ErrorMessage("CE2036", Severity.ERROR,
    "Ok() requires a value. For blank return type use Ok(~)",
    Category.TYPE, "Empty Ok() is not allowed. Use Ok(value) for regular returns or Ok(~) for blank type returns."))

_add(ErrorMessage("CE2037", Severity.ERROR,
    "cannot print Result<T> directly (use .realise() to unwrap first)",
    Category.TYPE, "Result<T> must be explicitly handled before printing. Use .realise(default) to extract the value."))

_add(ErrorMessage("CE2038", Severity.ERROR,
    "empty interpolation in string literal",
    Category.TYPE, "String interpolation braces must contain an expression (e.g., \"{value}\" not \"{}\")."))

# Enum errors
_add(ErrorMessage("CE2040", Severity.ERROR,
    "non-exhaustive match pattern (missing variants: {variants})",
    Category.TYPE, "Match statement must handle all enum variants."))

_add(ErrorMessage("CE2041", Severity.ERROR,
    "duplicate match arm for variant '{variant}'",
    Category.TYPE, "The same enum variant cannot be matched more than once."))

_add(ErrorMessage("CE2042", Severity.ERROR,
    "unreachable match arm",
    Category.TYPE, "This match arm can never be reached because previous arms cover all cases."))

_add(ErrorMessage("CE2043", Severity.ERROR,
    "pattern type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "Pattern binding type does not match the expected type from the enum variant."))

_add(ErrorMessage("CE2044", Severity.ERROR,
    "wrong number of pattern bindings: variant '{variant}' expects {expected}, got {got}",
    Category.TYPE, "Pattern must bind the exact number of variables for the variant's associated data."))

_add(ErrorMessage("CE2045", Severity.ERROR,
    "enum variant '{variant}' not found in enum '{enum}'",
    Category.TYPE, "The specified variant does not exist in the enum type."))

_add(ErrorMessage("CE2046", Severity.ERROR,
    "duplicate enum '{name}'",
    Category.TYPE, "Two enums share the same name in a compilation unit."))

_add(ErrorMessage("CE2047", Severity.ERROR,
    "duplicate variant '{name}' in enum '{enum_name}'",
    Category.TYPE, "An enum declares the same variant name more than once."))

_add(ErrorMessage("CE2048", Severity.ERROR,
    "match scrutinee must be an enum type, got '{got}'",
    Category.TYPE, "Match expressions can only be used with enum types."))

_add(ErrorMessage("CE2049", Severity.ERROR,
    "enum constructor argument type mismatch for variant '{variant}': expected '{expected}', got '{got}'",
    Category.TYPE, "Enum variant constructor argument type does not match the expected associated data type."))

_add(ErrorMessage("CE2050", Severity.ERROR,
    "enum variant '{variant}' expects {expected} argument(s), got {got}",
    Category.TYPE, "Enum variant constructor must provide exact number of arguments for associated data."))

_add(ErrorMessage("CE2051", Severity.ERROR,
    "{message}",
    Category.TYPE, "Struct hashing limitation or error."))

_add(ErrorMessage("CE2052", Severity.ERROR,
    "recursive enum '{name}' requires Own<T> indirection (example: enum IntList: Cons(i32, Own<IntList>))",
    Category.TYPE, "Direct recursion in enums without Own<T> creates infinite size types."))

# List<T> method errors
_add(ErrorMessage("CE2053", Severity.ERROR,
    "List<T>.{method}() expects {expected} argument(s), got {got}",
    Category.TYPE, "List method called with incorrect number of arguments."))

# HashMap<K, V> type errors
_add(ErrorMessage("CE2054", Severity.ERROR,
    "HashMap<K, V> key type '{key_type}' does not support hashing (missing .hash() method)",
    Category.TYPE, "HashMap keys must support hashing. Use types that have .hash() method (primitives, strings, structs with hashable fields, enums, arrays)."))

_add(ErrorMessage("CE2055", Severity.ERROR,
    "HashMap<K, V> key type '{key_type}' does not support equality comparison",
    Category.TYPE, "HashMap keys must support equality comparison (==). This is required for collision resolution."))

_add(ErrorMessage("CE2058", Severity.ERROR,
    "HashMap<K, V> key type '{key_type}' is not comparable (dynamic arrays cannot be HashMap keys)",
    Category.TYPE, "Dynamic arrays are not allowed as HashMap keys due to memory management constraints. Use fixed-size arrays instead (e.g., i32[3] instead of i32[])."))

# Array indexing errors (CE2056-CE2057)
_add(ErrorMessage("CE2056", Severity.ERROR,
    "array index {index} is negative (indices must be >= 0)",
    Category.TYPE, "Array indices must be non-negative. Negative indices are not supported."))

_add(ErrorMessage("CE2057", Severity.ERROR,
    "array index {index} out of bounds for array of size {size}",
    Category.TYPE, "Array index exceeds array bounds. This error is caught at compile-time for constant indices."))

# Generic function call errors (CE2060-CE2069)
_add(ErrorMessage("CE2060", Severity.ERROR,
    "cannot infer type arguments for generic function '{name}': {reason}",
    Category.TYPE, "Type inference failed for generic function call. Type parameters could not be determined from argument types."))

_add(ErrorMessage("CE2061", Severity.ERROR,
    "monomorphized function '{mangled}' not found for '{name}' with type arguments {type_args}",
    Category.INTERNAL, "Internal compiler error: monomorphized function missing from function table."))

# Radix literal errors (CE2070-CE2079)
_add(ErrorMessage("CE2070", Severity.ERROR,
    "{radix} literal {literal} overflows {type}",
    Category.TYPE, "The literal value is too large to fit in the target integer type. Use a wider integer type or reduce the value."))

_add(ErrorMessage("CE2071", Severity.ERROR,
    "C-style octal literal '{literal}' is not supported. Use '0o' prefix instead (e.g., 0o{octal})",
    Category.TYPE, "Leading zero octals (like 077) are ambiguous and error-prone. Use explicit 0o prefix instead."))

_add(ErrorMessage("CE2072", Severity.ERROR,
    "range expression requires integer types for start and end bounds. Got {got}, expected {expected}",
    Category.TYPE, "Range expressions (.. and ..=) can only be used with integer types (i8, i16, i32, i64, u8, u16, u32, u64)."))

_add(ErrorMessage("CE2073", Severity.ERROR,
    "literal {literal} out of range for {type}",
    Category.TYPE, "The literal does not fit the target type's range. Use a wider type, or an explicit 'as' cast if you intend the bit pattern."))

# Named struct constructor errors (CE2080-CE2089)
_add(ErrorMessage("CE2080", Severity.ERROR,
    "unknown field '{field}' for struct '{struct}'",
    Category.TYPE, "Named struct constructor field name does not exist in struct definition."))

_add(ErrorMessage("CE2081", Severity.ERROR,
    "duplicate field '{field}' in struct constructor",
    Category.TYPE, "Field name appears more than once in named struct constructor."))

_add(ErrorMessage("CE2082", Severity.ERROR,
    "missing required field(s) '{fields}' for struct '{struct}'",
    Category.TYPE, "Named struct constructor must provide all required fields."))

_add(ErrorMessage("CE2083", Severity.ERROR,
    "field '{field}' expects type '{expected}', got '{got}'",
    Category.TYPE, "Named struct constructor field type mismatch."))

_add(ErrorMessage("CE2084", Severity.ERROR,
    "error type must be an enum, not '{type_name}'",
    Category.TYPE, "Custom error types (fn foo() T | E) must be enums. Structs and primitives are not allowed as error types."))

_add(ErrorMessage("CE2085", Severity.ERROR,
    "cannot use '| {err_type}' syntax with explicit Result<T, E> return type",
    Category.TYPE, "When using explicit Result<T, E> syntax, the error type is already specified. Remove the '| ErrorType' syntax or use implicit return type."))

# CE2086-CE2089 reserved for future extensions
_add(ErrorMessage("CE2090", Severity.ERROR,
    "type-pack element {index} of type '{ty}' does not satisfy constraint '{perk}'",
    Category.TYPE, "Each element type bound to a perk-constrained type-pack '...Ts: Perk' must implement the required perk."))

_add(ErrorMessage("CE2091", Severity.ERROR,
    "extension/perk method '{name}' must use a bare 'return <value>', not 'return Result.Ok(...)' / 'Result.Err(...)'",
    Category.TYPE, "Extension and perk-implementation methods return the bare value directly (their ABI is unwrapped). Write 'return value' instead of 'return Result.Ok(value)'."))

_add(ErrorMessage("CE2092", Severity.ERROR,
    "function value type mismatch: expected '{expected}', got '{actual}'",
    Category.TYPE, "A first-class function value must match the expected function type exactly: same arity, parameter types, return type, and error type (function types are invariant)."))

_add(ErrorMessage("CE2093", Severity.ERROR,
    "cannot take a function value of '{name}': {reason}",
    Category.TYPE, "In v1 only plain top-level functions are first-class. Extension/perk methods, FFI externals, and generic functions cannot be referenced as function values."))

_add(ErrorMessage("CE2094", Severity.ERROR,
    "illegal closure capture: {reason}",
    Category.TYPE, "Tier 1 closures capture by value (copy) or by move (owned types). Capturing a borrow (&peek/&poke) through a closure is deferred to Tier 2. An owning or variadic function-value parameter type is also rejected in Tier 1 (the indirect-call path has no deep-copy/variadic-collapse yet)."))
