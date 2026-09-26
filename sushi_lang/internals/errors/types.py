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
    Category.TYPE, "A condition takes a 'bool' and nothing else. Sushi converts no type to a truth value: an integer is not true when it is not zero, and a string is not true when it holds bytes. Write the question instead -- 'n != 0', 's.len() > 0'. This covers every condition position: an if, an elif, a while, and the operands of the logical operators and, or, xor and not. The operators were the hole until #532. They checked no operand at all, so 'not 5' answered 0 with C truthiness while 'if (5)' was refused, and a string, a float, a struct, an enum or an array operand reached the backend and became a CE0017 internal error -- the same shape #449 removed from the comparisons. A Result@(T, E) or a Maybe@(T) in one of these positions is CE2516 instead, which names the predicate that answers for it."))

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
    "wrong number of arguments: '{name}' expects {expected}, got {got}",
    Category.TYPE, "A call has the wrong number of arguments: a function, a method, a static or a built-in. The text names the callee as written and no noun, because one code serves every callee kind, and it states the counts with no noun, so the text agrees in number for a count of one (#764). The four bulk-copy array methods (`extend`, `extend_range`, `s`, `ss`) reported this fault with the internal CE0023 until #764. The built-in `List@(T)`, `HashMap@(K, V)`, `Own@(T)`, `Maybe@(T)` and `Result@(T, E)` methods, and the `List` and `HashMap` statics, reported it with CE2053, CE2016 and CE2502 until #799; their counts are one table per family now (`MethodFamily.arity`)."))

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

# CE2016 ("method '{method}' expects {expected} argument(s), got {got}") was RETIRED by
# #799. It answered a miscount on a built-in HashMap, Own, Maybe or Result method, while
# an array, a string, a derived method and a function answered the same fault with CE2009.
# One fault is one code: a built-in method miscount is CE2009, read from the family's
# count table (`MethodFamily.arity`).

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
    Category.TYPE, "Every return in a body that answers a Result spells its constructor: 'return Result.Ok(value)' or 'return Result.Err(e)'. The rule holds for a function, a lambda block body, and an extension or perk-impl method with a '| E' channel alike (#848): nothing wraps a bare value, and a '~' success is 'return Result.Ok(~)'. A BARE method (no '| E') is the other way round (CE2091)."))

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
    "foreach needs something to walk, and '{got}' is neither an iterator nor a type with next()",
    Category.TYPE, "Two things are walkable. An ITERATOR, which is what .iter() on an array or a List answers, what .keys() / .values() / .entries() answer on a HashMap, and what a range is. Or any type carrying a method 'next()' that answers Maybe@(T): the loop calls it until it answers None, and that is the whole protocol -- there is no type to implement and no perk to name (HANDLES.md ruling R21). So the fix is one of three: call .iter() on the container, give this type a next(), or check the spelling of the next() it has. Three spellings are refused, each because the loop must be able to call the method repeatedly and read a stop out of its answer: a next() answering a bare T rather than a Maybe@(T) cannot say when to stop; one declaring '| E' answers a Result and not a Maybe; one taking arguments has nothing to be handed; and a 'nom self' receiver answers once and spends the iterator. A fallible iterator puts the failure IN the item instead: Maybe@(Result@(T, E))."))

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
    "cannot print Result@(T, E) directly (use .realise() to unwrap first)",
    Category.TYPE, "Result@(T, E) must be explicitly handled before printing. Use .realise(default) to extract the value."))

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
    Category.TYPE, "The specified variant does not exist in the enum type. Since #542 an enum's dot holds TWO kinds of member -- a variant, and a static method -- so the help names both escapes: add the variant, or declare the name as a static. It is still one namespace: a variant and a static of one name on one enum is CE2103, because the variant would always win."))

_add(ErrorMessage("CE2046", Severity.ERROR,
    "duplicate enum '{name}'",
    Category.TYPE, "Two enums share the same name in a compilation unit."))

_add(ErrorMessage("CE2047", Severity.ERROR,
    "duplicate variant '{name}' in enum '{enum_name}'",
    Category.TYPE, "An enum declares the same variant name more than once."))

_add(ErrorMessage("CE2048", Severity.ERROR,
    "match scrutinee must be an enum or integer type, got '{got}'",
    Category.TYPE, "A match dispatches on an enum's variants, or (since #415) on an integer's value with literal arms. Other types have no match semantics. This is the SCRUTINEE's rule and nothing else. It answered four faults over seven emit sites until #741: an arm or a nested pattern that names another enum (CE2107), a nested pattern over a payload that is not an enum (CE2108), and the three `Own(...)` pattern refusals (CE2109). Three of those sites filled the quoted type slot with a whole sentence, so a user read 'got 'Own(...) pattern requires Own@(T) type, got i32''. The slot takes a type and only a type."))

_add(ErrorMessage("CE2049", Severity.ERROR,
    "enum constructor argument type mismatch for variant '{variant}': expected '{expected}', got '{got}'",
    Category.TYPE, "Enum variant constructor argument type does not match the expected associated data type."))

_add(ErrorMessage("CE2050", Severity.ERROR,
    "enum variant '{variant}' expects {expected} argument(s), got {got}",
    Category.TYPE, "Enum variant constructor must provide exact number of arguments for associated data."))

# CE2051 ("{message}", a hashing limitation) was RETIRED by #804. Its one emit site refused
# `.hash()` on an array of arrays, and nothing could reach it: `i32[][]` cannot be written,
# the hashability gate refuses a nested array before a hash is registered, and the array
# family answers an array's `.hash()` before the derived method is asked.

# CE2052 ("recursive enum '{name}' requires Own@(T) indirection") was RETIRED by the ruling
# on #677 (2026-09-14). The derive pass found an enum cycle in its topological sort and said
# so with a file name and no caret, once per member, and once more for every instance a call
# site solved late, because that sort re-ran per intern. A struct cycle was one CE2095 with
# the chain and the span. One fault class has one code now: the finite-types pass owns every
# inline cycle -- a struct field, a fixed-array element, an enum payload -- and an enum cycle
# reads CE2095 at its first member. The number is not reused.

# List@(T) method errors
# CE2053 ("List@(T).{method}() expects {expected} argument(s), got {got}") was RETIRED by
# #799: a miscount on a List@(T) method or static is CE2009, as on every other callee.

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
    Category.TYPE,
    "Type inference failed for a generic call. A generic FREE function solves its type "
    "parameters from its arguments, and a parameter named only in the return has no "
    "source -- spell the type arguments (`f@(i32)()`). A generic STATIC solves them in "
    "two steps, as one resolution (#573): from the arguments, for every target type "
    "parameter a parameter names, then from the declared type at the binding site for "
    "the rest. A parameter neither step reaches is this error, and the text names both "
    "sources and the parameter. History: from #542 to #573 a static read the stamp "
    "alone, which left a `| E` static unwritable -- a Result-valued call is never "
    "stamped."))

_add(ErrorMessage("CE2061", Severity.ERROR,
    "monomorphized function '{mangled}' not found for '{name}' with type arguments {type_args}",
    Category.INTERNAL, "Internal compiler error: monomorphized function missing from function table."))

_add(ErrorMessage("CE2062", Severity.ERROR,
    "generic '{name}' expects {expected} type argument(s), got {got}",
    Category.TYPE, "A `@(...)` type-argument list does not give the generic the count it "
                   "declares. One rule for every position: an explicit call-site list "
                   "(`id@(i32, i32)(1)`; explicit type arguments are all-or-nothing), a "
                   "written type (`let Box@(i32, i32) b`, a parameter, a field, a payload), "
                   "and an extension or perk-implementation target (`extend Box@(T, U)`). "
                   "Before #796 a written type answered CE2001 'unknown type' and a target "
                   "reached an internal error or was accepted in silence."))

# Radix literal errors (CE2070-CE2079)
_add(ErrorMessage("CE2070", Severity.ERROR,
    "{radix} literal {literal} overflows {type}",
    Category.TYPE, "The literal value is too large to fit in the target integer type. Use a wider integer type or reduce the value."))

_add(ErrorMessage("CE2071", Severity.ERROR,
    "C-style octal literal '{literal}' is not supported. Use '0o' prefix instead (e.g., 0o{octal})",
    Category.TYPE, "Leading zero octals (like 077) are ambiguous and error-prone. Use explicit 0o prefix instead."))

_add(ErrorMessage("CE2072", Severity.ERROR,
    "range expression requires integer types for start and end bounds. Got {got}, expected {expected}",
    Category.TYPE, "A range bound is an i32 position, as an index is (#870). This code is for a bound that is not a number at all (a string, a bool). A number of another type -- an i8, an i64, an f64 -- is CE2002 with the help 'as i32'; a bare literal takes i32 from the position."))

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
    Category.TYPE, "An expression whose value the compiler reads is computed at the declared width, and a result that leaves the type is reported. C is the only language that truncates in silence, and truncation made the evaluator disagree with the machine -- a u8 constant of '200 + 100' held 300, so a widening cast read 300 while the program printed 44. The overflow-checked operators are + - * / % and unary minus; & | ^ ~ << >> are width-defined and never report, because the bits that leave the width are lost by design. The escape is a wider type, or an explicit 'as' cast when the bit pattern is what you want. Run time does not change: two locals still wrap."))

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
    Category.TYPE, "Custom error types (fn foo() T | E) must be enums. Structs and primitives are not allowed as error types. ONE rule over the four kinds that write a channel: a free function, an extension method, a perk contract and a perk implementation (#663). A name that spells nothing stops at CE2001, which already says everything a reader can act on. A GENERIC enum qualifies: `| MyErr@(i32)` is an enum and is legal, which this rule denied on every kind until #668 -- the written instantiation is resolved before the kind is asked."))

_add(ErrorMessage("CE2085", Severity.ERROR,
    "cannot use '| {err_type}' syntax with explicit Result@(T, E) return type",
    Category.TYPE, "When using explicit Result@(T, E) syntax, the error type is already specified. Remove the '| ErrorType' syntax or use implicit return type."))

_add(ErrorMessage("CE2086", Severity.ERROR,
    "error type cannot be a wrapper: '{type_name}'",
    Category.TYPE, "A built-in wrapper is an enum, so CE2084 does not catch it, and it is still not an error vocabulary. `Result@(T, E)` models failure and `Maybe@(T)` models absence; the Err arm of the channel already says that something went wrong, so `fn f() i32 | Maybe@(string)` asks a reader to read an absence as a failure and says nothing they can act on. It also nests: the declared return becomes `Result@(i32, Maybe@(string))`. Write a plain enum that names the failures. A user's GENERIC enum is fine -- `| MyErr@(i32)` is legal (#668); this rule reads the OUTERMOST name only, so it refuses `Maybe` and `Result` and nothing else."))

# CE2087-CE2089 reserved for future extensions
_add(ErrorMessage("CE2090", Severity.ERROR,
    "type-pack element {index} of type '{ty}' does not satisfy constraint '{perk}'",
    Category.TYPE, "Each element type bound to a perk-constrained type-pack '...Ts: Perk' must implement the required perk."))

_add(ErrorMessage("CE2091", Severity.ERROR,
    "method '{name}' must use a bare 'return <value>', not 'return Result.Ok(...)' or 'return Result.Err(...)'",
    Category.TYPE, "A BARE extension or perk-impl method (no '| E') has an unwrapped ABI: it answers the value itself and no Result, so both Result constructors are refused. A method with a '| E' channel has the free function's rule instead: it spells 'return Result.Ok(x)' and 'return Result.Err(e)', and a bare 'return x' there is CE2030. Until #848 a channel body returned its success bare and the compiler wrapped it into Ok in silence (ruling 6 of the UFCS epic, reversed); CE2091 then also refused 'Result.Ok(...)' in a channel body."))

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
    "{kind} target '{target}' mixes concrete type arguments with type parameters",
    Category.TYPE, "An extension target names either every type parameter -- `extend Box@(T)`, which applies to every instantiation -- or a concrete type for every argument -- `extend Box@(i32)`, which applies to that instantiation alone. A partial form such as `extend Pair@(i32, U)` is partial specialization, and Sushi has none. Rejecting it is what keeps an ordering rule from ever being needed: two fully-concrete targets cannot overlap and template-versus-concrete is strictly ordered, so `Pair@(i32, U)` against `Pair@(T, string)` -- equally specific, neither more so -- cannot arise. That ambiguity is where Rust's specialization has stalled for years. Name every parameter, make every argument concrete, or implement a perk on the concrete target. A perk implementation's target reads the same rule and the same code (#860)."))

_add(ErrorMessage("CE2100", Severity.ERROR,
    "'{method}' needs an element type with equality: '{element}' has none",
    Category.TYPE, "contains() and index_of() compare the needle against each element with '==', and equality is a CLOSED set: the numeric types, bool, and string (CE2514 is the operator half of the same rule). A struct, an enum, an array or a closure element has no '==', so a search over it has no meaning the compiler could supply. Write the loop by hand and compare what identifies an element -- a field, or a match on the variant -- or search an array of that identifying part instead."))

_add(ErrorMessage("CE2101", Severity.ERROR,
    "invalid element '{element}' in an array extension target",
    Category.TYPE, "An array extension target's element position takes exactly two spellings: a bare undeclared name, which binds a type parameter (`extend T[]` applies to every element type), and the name of a plain declared type (`extend i32[]`, `extend Crate[]`), which applies to that array type alone. A generic instantiation (`extend Maybe@(T)[]`) has no meaning here -- the parameter would bind through two layers -- and a nested array element is not an expressible type at all (Known Limitation 2). Before this code, the generic-element spelling fell through to the concrete path and reported a false CE2001 for a type nobody had to declare."))

_add(ErrorMessage("CE2097", Severity.ERROR,
    "extension method '{name}()' conflicts with the built-in '{type}.{name}()'",
    Category.TYPE, "Method resolution always considers built-in methods before extension methods -- during type validation, during type inference, and again during code generation -- so an extension method whose name collides with one is compiled and then never called. The built-in families are: the hash() and clone() the compiler derives for every struct and enum; the primitive and string methods (to_str, hash, to_bits, len, trim, ...); the array methods; and the methods of the built-in containers Result, Maybe, Own, List and HashMap. A perk implementation is the supported way to replace a built-in: it takes precedence at every layer, by design."))

_add(ErrorMessage("CE2063", Severity.ERROR,
    "cannot infer method type parameter{plural} {names} for '{method}' from this call",
    Category.TYPE, "A method-level type parameter (`extend List@(T) mapv@(U)(...)`) is inference-only in v1: there is no call-site `@(...)` slot on a method call, so every parameter must be solvable from the arguments. The one shape that cannot be solved is the bare-param lambda (Known Limitation 7): `xs.mapv(|x| x * 2)` gives the lambda no type of its own, so nothing unifies against `fn(T) -> U`. The escape is to annotate the lambda's parameter -- `xs.mapv(|i32 x| x * 2)` -- or to pass a named function."))

_add(ErrorMessage("CE2064", Severity.ERROR,
    "method type parameter '{name}' shadows a type parameter of the extension target",
    Category.TYPE, "The receiver target's bare names (`extend Box@(T)`, `extend T[]`) and the method's own `@(...)` list share one namespace inside the body, so a repeated name would make `T` mean two types in one signature. Rename the method-level parameter. The rule mirrors CE2097's spirit: a declaration that could only ever mislead is refused where it is written."))

_add(ErrorMessage("CE2102", Severity.ERROR,
    "'{type}' has no static method '{method}'",
    Category.TYPE, "A name behind a type's dot is a MEMBER of that type (#542, ruling Q1): a variant, or a static method. This type declares neither of that name. Before statics existed the struct spelling answered CE1001 'use of undeclared identifier' for a type that IS declared -- wrong about the one thing it named, because the fault is the POSITION and not the name. Declare the method as `extend {type} static {method}(...)`, or call an instance method on a value of the type."))

_add(ErrorMessage("CE2103", Severity.ERROR,
    "static method '{method}' collides with a variant of enum '{enum}'",
    Category.TYPE, "One namespace sits behind a type's dot (#542, ruling Q1): a name there is a variant or a static method, never both. A variant always wins at the call site, so a static of that name is compiled and then never called -- the hazard CE2097 refuses for a built-in. Relational: the primary sits at the static declaration and a note at the variant. Rename the static."))

_add(ErrorMessage("CE2104", Severity.ERROR,
    "a static method cannot be declared on an array target",
    Category.TYPE, "A static is called on the TYPE name, and an array type has no spelling in an expression position: `i32[].two()` is a parse error, and there is no form that would reach `extend i32[] static two()` or `extend T[] static two()`. The declaration would compile and never be callable, which is the hazard CE2097 refuses for a colliding built-in -- if the situation cannot possibly do what the user wrote, it is an error and not a warning. Write a free function, or a static on a struct that holds the array."))

_add(ErrorMessage("CE2105", Severity.ERROR,
    "'{name}' is a type, not a value",
    Category.TYPE, "A type name is written in a TYPE position -- a declaration, an annotation, a constraint -- and behind its own dot, where it names a member (#542, ruling Q1). It is not a value, so a value position cannot hold it. An enum name used to pass every semantic pass and reach the emitter, where it answered CE0055 'unknown variable or constant' with the note that says the fault is a compiler bug: no file, no line, no caret, and the blame on the wrong person (#600). A struct name answered CE1001 'use of undeclared identifier' about a type that IS declared, which is the answer CE2102 already retired for the receiver position: the fault is the POSITION and not the name. One ladder answers both now (`semantics/name_ladder.py`). Write a value of the type: a variant for an enum, a construction or a static for a struct."))

_add(ErrorMessage("CE2106", Severity.ERROR,
    "'{type}' has no field '{field}'",
    Category.TYPE, "A name behind a VALUE's dot is a field of that value's type, and this type declares no such field -- CE2102 is the same rule one position over, behind a TYPE's dot. The typecheck pass used to let an unknown field through untouched: the read reached codegen, and the backend was the first thing to notice, answering CE0029 with the note that says the fault is a bug in the compiler -- tier 1, no file, no line, no caret, and the blame on the wrong person for what is a typo (#630). The four backend CE0029 sites stay as the internal backstop they read as. A METHOD is not a field: `v.name` with no parentheses reads a field, and a bound-method value is deferred to Tier 2, so write the call. #630 answered only a STRUCT receiver. A receiver that carries NO field -- an array, a primitive, a string, a closure, a `ptr` -- still reached the backend, where the SHAPE of the read picked the internal code: CE0031 off a name or an assignment target, CE0044 through a field, CE0043 through an array element (#661). One rule answers them all, and `builtin_method_exists` is what tells a compiler-defined method from a typo, so `s.len` reads the same note a struct's method read does. An ENUM receiver reads it too (#666). #661 left that one alone, and it was worse than an internal error: a `Maybe@(T)` is an ordinary interned enum, so the backend unwrapped the receiver to its payload struct and read field 0, which is the TAG -- `pts.get(0).x` compiled clean and printed 0 where the element held 11, and a test fixture had frozen the wrong number. An enum carries variants, and a variant is reached by a pattern and not by a dot, so the help says to take the value first: `??`, `.realise(default)` or `match` for a wrapper, `match` for a user enum. A `Maybe@(T)` gets no implicit unwrap, for the reason a condition is a bool and nothing else (#522/#532), and because the `None` arm has no answer."))

_add(ErrorMessage("CE2110", Severity.ERROR,
    "a function type cannot be an extension or perk-implementation target: '{target}'",
    Category.TYPE, "An extension names a type that the extension table can key on: a primitive, an array, a struct or an enum. A function type is structural, and an extension on one is not designed -- the table key, the symbol, a perk implementation on it and a closure as `self` all have no rule. The declaration was accepted and then dropped in silence until #771, so a call answered CE2008 'undefined function' and a `self` in the body answered CE2008 'undefined function 'self''. It is refused at the target now, and it is the one diagnostic: the body is not checked and a call of the method adds nothing. A perk implementation follows the same rule (#864): `extend fn(i32) -> i32 with P` compiled, and a call of the perk method on a function value ran. A method a function value carries is built in (`.clone()`). Write a free function that takes the function value as a parameter."))

# The three match-pattern refusals CE2048 used to answer (#741). CE2048 stays the
# SCRUTINEE's rule, and these three are the PATTERN's.
_add(ErrorMessage("CE2107", Severity.ERROR,
    "pattern matches enum '{got}', but the value has type '{expected}'",
    Category.TYPE, "A pattern names the enum it destructures, and the value it reads is of another type. This is the outer arm's rule and the nested pattern's rule alike: `Other.Alpha ->` against a `Shape` scrutinee, and `Outer.Wrap(Other.Alpha)` against a payload the variant declares as `Inner`. It is relational, so the note points at the value -- the scrutinee for an outer arm, the variant that declares the payload for a nested one. Both sites answered CE2048 until #741, which reads 'match scrutinee must be an enum or integer type, got 'Other''. That sentence was false twice over: 'Other' IS an enum, and the enum the slot named was the PATTERN's and not the scrutinee's."))

_add(ErrorMessage("CE2108", Severity.ERROR,
    "nested pattern needs an enum value, got '{got}'",
    Category.TYPE, "A nested pattern destructures a variant's payload, so the payload must be an enum. `Box.Held(Other.Alpha)` over a `Held(i32)` reads this, and so does the same shape one level down inside an `Own(...)` pattern -- one rule, two positions. Both answered CE2048 until #741, where the Own position put a whole sentence in the quoted type slot: 'got 'Nested pattern inside Own(...) requires enum type, got i32''."))

_add(ErrorMessage("CE2109", Severity.ERROR,
    "Own(...) pattern needs an Own@(T) value, got '{got}'",
    Category.TYPE, "An `Own(...)` pattern reads through an owning pointer, so the value it reads must be an `Own@(T)`. A plain payload is not one, and neither is a malformed `Own@(i32, i32)`, whose payload cannot be read -- that one arrives behind a CE2001 for the type itself. It answered CE2048 until #741, which put the whole explanation inside the quoted type slot: 'got 'Own(...) pattern requires Own@(T) type, got i32''."))

_add(ErrorMessage("CE2111", Severity.ERROR,
    "cannot infer the element type of an empty {form}",
    Category.TYPE, "An empty `from([])` or a `new()` spells no element, so it takes the element type of its POSITION: a `let`, a field, a payload, a parameter, a `.realise()` default, a return (#544). A position with no type -- a method receiver, an index base, a `println` argument -- gives it nothing, and no element can be read. It reached the backend with no stamp, and the backend answered the internal error CE0042 with the note that says the fault is in the compiler (#868); `new()` did the same until #889. Declare the array first: `let i32[] xs = from([])`, then use `xs`."))
