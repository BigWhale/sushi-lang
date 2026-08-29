"""Type, array and struct errors (CE2xxx)."""
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
    Category.TYPE, "The operand rule of a bitwise operator, which is the only site that emits this: & | ^ ~ << >> combine or move BITS, so every operand must be an integer. A string has none, and a float keeps its own behind f64.to_bits()/f32.to_bits() -- the escape is to convert first, operate on the integer, and go back through from_bits(). The gate used to ask for a numeric type, which let a float through to the backend and turned a user's program into a CE0000 internal error."))

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
    Category.TYPE, "Function call references a function that was not declared. This is for a name that no unit and no linked library declares: a name a library declares and keeps is CE3005, on either library kind (#469)."))

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
    Category.TYPE, "Built-in Result@(T, E) and Maybe@(T) methods take a fixed number of arguments."))

_add(ErrorMessage("CE2017", Severity.ERROR,
    "invalid repeat count in an array literal: {reason}",
    Category.TYPE, "A repeated element is 'value; count', and the count is a count of elements: a positive integer the compiler can read. That is a literal in any base, the name of an integer constant, or an expression of them -- the same reader a fixed array size uses. One code carries every way it can go wrong, because they share one rule and one fix, which is the precedent CE2099 sets for an array size. A count of zero spells nothing and Sushi has no zero-length array, so the lower bound is one. The count is read at the typecheck pass, so unlike an array size it may name a constant of ANOTHER unit."))

# CE2018 ("a repeated element cannot be of type '{type}', which owns heap memory") was
# RETIRED by #478, Ruling 7. It refused '[towel; 3]' because N copies of an owning value
# would need N-1 deep copies and the compiler never inserts one. That stopped being true
# when #479 gave '.fill()' a per-slot 'copy_out': the language then answered one question
# two ways, since 'a.fill(towel)' was legal beside 'from([towel; 2])', which was not. A
# repeated value is now a BORROW and each slot takes its own copy. Its own doc anticipated
# this -- "no case needed it when Ruling 2 went in, and the rule relaxes without breaking a
# program". The const path has no case left either: a constant cannot name a local, so a run
# there can only repeat a literal, and a constant array of string literals already worked.

_add(ErrorMessage("CE2019", Severity.ERROR,
    "invalid range in an array literal: {reason}",
    Category.TYPE, "A range element fills the slots it spans: '0..5' is five elements and '0..=5' is six, and the direction follows `foreach`, so '5..0' descends. One code carries every way a range cannot fill slots, the way CE2017 carries every bad repeat count, because they share one rule and one fix. Two ways: a bound the compiler cannot read in a position that needs a readable LENGTH -- a fixed array, whose length is part of its type, and a constant, whose evaluator needs the values -- and a readable range that yields nothing, because Sushi has no zero-length array and '3..3' spells nothing. The escape for the first is `from()`, which carries its length in the descriptor and accepts any i32 expression as a bound. A range yields i32 (CE2002 for anything else), and it cannot carry a repeat count (CE2020)."))

_add(ErrorMessage("CE2020", Severity.ERROR,
    "a range element cannot carry a repeat count",
    Category.TYPE, "'value; count' repeats ONE value, and a range is not one value: it is already a sequence. '[0..2; 3]' has no reading a person would agree on -- three copies of the span, or a span of three -- so it is refused rather than given one. Write the repeat out as a plain element ('[0, 1, 0, 1]'), or use a range alone. CE2017 is the code for a count that is wrong; this one is for a count that has nothing to repeat."))

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
    "cannot print Result@(T) directly (use .realise() to unwrap first)",
    Category.TYPE, "Result@(T) must be explicitly handled before printing. Use .realise(default) to extract the value."))

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
    "match scrutinee must be an enum or integer type, got '{got}'",
    Category.TYPE, "A match dispatches on an enum's variants, or (since #415) on an integer's value with literal arms. Other types have no match semantics."))

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
    "recursive enum '{name}' requires Own@(T) indirection (example: enum IntList: Cons(i32, Own@(IntList)))",
    Category.TYPE, "Direct recursion in enums without Own@(T) creates infinite size types."))

# List@(T) method errors
_add(ErrorMessage("CE2053", Severity.ERROR,
    "List@(T).{method}() expects {expected} argument(s), got {got}",
    Category.TYPE, "List method called with incorrect number of arguments."))

# HashMap@(K, V) type errors
_add(ErrorMessage("CE2054", Severity.ERROR,
    "HashMap@(K, V) key type '{key_type}' does not support hashing (missing .hash() method)",
    Category.TYPE, "HashMap keys must support hashing. Use types that have .hash() method (primitives, strings, structs with hashable fields, enums, arrays)."))

_add(ErrorMessage("CE2055", Severity.ERROR,
    "HashMap@(K, V) key type '{key_type}' does not support equality comparison",
    Category.TYPE, "HashMap keys must support equality comparison (==). This is required for collision resolution."))

_add(ErrorMessage("CE2058", Severity.ERROR,
    "HashMap@(K, V) key type '{key_type}' is not comparable (dynamic arrays cannot be HashMap keys)",
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

_add(ErrorMessage("CE2062", Severity.ERROR,
    "generic function '{name}' expects {expected} type argument(s), got {got}",
    Category.TYPE, "The explicit `@(...)` type-argument list does not match the function's "
                   "type parameters. Explicit type arguments are all-or-nothing."))

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

# Integer literal match arms (#415)
_add(ErrorMessage("CE2074", Severity.ERROR,
    "non-exhaustive integer match (add a trailing '_' arm)",
    Category.TYPE, "A match on an integer scrutinee cannot enumerate every value, so it must end with a wildcard arm. Introduced with integer literal match arms (#415)."))

_add(ErrorMessage("CE2075", Severity.ERROR,
    "duplicate literal match arm: value {value} is already matched by arm '{first}'",
    Category.TYPE, "Two literal arms match the same VALUE, whatever their radix: 0x2a and 42 are the same arm. The second arm is unreachable."))

_add(ErrorMessage("CE2076", Severity.ERROR,
    "match arm does not fit the scrutinee: {arm_kind} arm on a '{scrutinee_type}' scrutinee",
    Category.TYPE, "A literal arm needs an integer scrutinee; an enum pattern arm needs an enum scrutinee. One match cannot mix the two arm kinds (#415)."))

# Compile-time overflow (Ruling 1 of docs/design/compile-time-evaluation.md)
_add(ErrorMessage("CE2077", Severity.ERROR,
    "operator '{op}' gives {value}, which is out of range for {type}",
    Category.TYPE, "An expression whose value the compiler reads is computed at the declared width, and a result that leaves the type is reported. Sushi follows Rust here: C is the only language that truncates in silence, and truncation made the evaluator disagree with the machine -- a u8 constant of '200 + 100' held 300, so a widening cast read 300 while the program printed 44. The overflow-checked operators are + - * / % and unary minus; & | ^ ~ << >> are width-defined and never report, because the bits that leave the width are lost by design. The escape is a wider type, or an explicit 'as' cast when the bit pattern is what you want. Run time does not change: two locals still wrap."))

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
    "cannot use '| {err_type}' syntax with explicit Result@(T, E) return type",
    Category.TYPE, "When using explicit Result@(T, E) syntax, the error type is already specified. Remove the '| ErrorType' syntax or use implicit return type."))

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
    Category.TYPE, "Tier 1 closures capture by value (copy) or by move (owned types). Capturing a borrow (peek/poke) through a closure is deferred to Tier 2. An owning or variadic function-value parameter type is also rejected in Tier 1 (the indirect-call path has no deep-copy/variadic-collapse yet)."))

_add(ErrorMessage("CE2095", Severity.ERROR,
    "recursive type '{name}' has infinite size: {chain}",
    Category.TYPE, "A type that contains itself by value has no finite size. Every hop in the reported chain stores its target inline -- a struct field, a fixed-size array element, or an enum payload. Break the cycle with indirection: Own@(T) for a single value, or a dynamic array / List@(T) for many. Compare Rust's E0072 and Go's \"invalid recursive type\"."))

_add(ErrorMessage("CE2096", Severity.ERROR,
    "cannot {what} constant '{name}': constants are immutable",
    Category.TYPE, "A constant is emitted as a read-only global (.rodata), so a write that would reach it -- an in-place method, or an indexed assignment -- cannot target one; the store would be undefined behaviour rather than a diagnostic. Copy the constant into a local first and mutate that."))

_add(ErrorMessage("CE2099", Severity.ERROR,
    "invalid size '{size}' for a fixed array: {reason}",
    Category.TYPE, "A fixed array's size is a count of elements, so it must be a positive integer the compiler can read: a literal in any base (256, 0x100, 0b1_0000_0000, 0o400) or the name of an integer constant. One code carries every way it can go wrong, because they share one rule and one fix. The size is read while the unit's AST is built, so the constant must be declared in the SAME unit -- a constant next door is reachable as a value but not as a size (#440). Before this code existed, hex and a name did not parse at all (CE6001, unexpected token) and a zero size left the type unbuilt, which surfaced as CE2007, a missing type annotation on a line that has one (#439)."))

_add(ErrorMessage("CE2098", Severity.ERROR,
    "extension target '{target}' mixes concrete type arguments with type parameters",
    Category.TYPE, "An extension target names either every type parameter -- `extend Box@(T)`, which applies to every instantiation -- or a concrete type for every argument -- `extend Box@(i32)`, which applies to that instantiation alone. A partial form such as `extend Pair@(i32, U)` is partial specialization, and Sushi has none. Rejecting it is what keeps an ordering rule from ever being needed: two fully-concrete targets cannot overlap and template-versus-concrete is strictly ordered, so `Pair@(i32, U)` against `Pair@(T, string)` -- equally specific, neither more so -- cannot arise. That ambiguity is where Rust's specialization has stalled for years. Name every parameter, make every argument concrete, or implement a perk on the concrete target."))

_add(ErrorMessage("CE2100", Severity.ERROR,
    "'{method}' needs an element type with equality: '{element}' has none",
    Category.TYPE, "contains() and index_of() compare the needle against each element with '==', and equality is a CLOSED set: the numeric types, bool, and string (CE2514 is the operator half of the same rule). A struct, an enum, an array or a closure element has no '==', so a search over it has no meaning the compiler could supply. Write the loop by hand and compare what identifies an element -- a field, or a match on the variant -- or search an array of that identifying part instead."))

_add(ErrorMessage("CE2097", Severity.ERROR,
    "extension method '{name}()' conflicts with the built-in '{type}.{name}()'",
    Category.TYPE, "Method resolution always considers built-in methods before extension methods -- during type validation, during type inference, and again during code generation -- so an extension method whose name collides with one is compiled and then never called. The built-in families are: the hash() and clone() the compiler derives for every struct and enum; the primitive and string methods (to_str, hash, to_bits, len, trim, ...); the array methods; and the methods of the built-in containers Result, Maybe, Own, List and HashMap. A perk implementation is the supported way to replace a built-in: it takes precedence at every layer, by design."))
