"""Result@(T, E) and Maybe@(T) method errors (CE25xx)."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


# Result@(T, E) method errors (CE25xx)
# CE2502 ("realise() requires exactly 1 argument, got {got}") was RETIRED by #799: a
# miscount on `Result.realise()` is CE2009, as on `Maybe.realise()` and every other callee.

_add(ErrorMessage("CE2503", Severity.ERROR,
    "realise() default type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The default value type passed to realise() must match the T type in Result@(T, E)."))

_add(ErrorMessage("CE2505", Severity.ERROR,
    "cannot assign Result@(T, E) to non-Result variable without handling (use .realise() or pattern matching)",
    Category.TYPE, "Result@(T, E) values must be explicitly handled before assigning to non-Result variables."))

_add(ErrorMessage("CE2506", Severity.ERROR,
    "cannot call .realise() on '{ty}' (a '~' success has no value to extract)",
    Category.TYPE, "A `~ | E` function answers Result@(~, E): its success carries no value, so a default for it means nothing. Test the outcome with `match` or `.is_ok()`, or propagate it with `??` in a body with a channel. A bare `~` function answers no Result at all (docs/design/error-channel.md). The same rule holds for a Maybe@(~)."))

# Try operator (??) errors (CE25xx continued)
_add(ErrorMessage("CE2507", Severity.ERROR,
    "`??` takes a `Result@(T, E)`, got '{got}'",
    Category.TYPE, "The ?? operator takes a Result@(T, E) and nothing else. It unwraps the Ok, or it returns the Err from the enclosing body, so the Err must hold an error value that the program made. A Maybe@(T) holds no error value: before #1168, ?? on a None returned an Err built from an undefined value, and the answer changed with --opt. Write the error value at the site with or_err: m.or_err(nom AppError.Empty)??. The ?? operator reads the TYPE of its operand and not its variant names, because type identity is nominal. So a user enum with Ok/Err or Some/None variants is not a Result: its Err payload is out of reach of the rule that an E is an error type. Answer a Result@(T, E) (a callee writes | E), or use the value without ??. The rule is ruling C8 of docs/design/error-conversion.md."))

_add(ErrorMessage("CE2508", Severity.ERROR,
    "`??` is legal only in a body with an error channel ('| E' or a Result@(T, E) return)",
    Category.TYPE, "The ?? operator propagates an error by an early return, so it needs an enclosing body that returns a Result@(T, E): a function, a method or a lambda that writes '| E', or a function that returns an explicit Result@(T, E). This code is the backstop for a `??` that stands outside every body. A `??` in a BARE body is CE0131, and a `??` on an operand that is not a Result@(T, E) is CE2507 (docs/design/error-conversion.md section 4)."))

_add(ErrorMessage("CE2509", Severity.ERROR,
    "operator '+' cannot be used with string types (use string interpolation instead: \"text {{variable}}\")",
    Category.TYPE, "Sushi does not support string concatenation with the + operator. Use string interpolation for combining strings."))

_add(ErrorMessage("CE2510", Severity.ERROR,
    "cannot use operator with mixed numeric types: {left_type} and {right_type} (use 'as' to explicitly cast one operand)",
    Category.TYPE, "Sushi converts no numeric type on its own, so two numeric operands of one operator must have the same type. This covers arithmetic (+ - * / %), the comparisons (== != < <= > >=) and the bitwise & | ^. Use 'as' to cast one operand to the other's type: (low as u32) | wide. A shift is the exception: its right operand is a count, not a second value, so its type is free and the result keeps the type of the left operand. The bitwise half of the rule arrived with #438, where a mixed pair was silently widened or TRUNCATED by the backend and the wrong answer reached the binary."))

_add(ErrorMessage("CE2512", Severity.ERROR,
    "shift count {count} is out of range for {value_type}: a count must be from 0 to {max_count}",
    Category.TYPE, "A shift moves the bits of its left operand, so the width of that operand is what limits the count. A count at or above the width moves every bit out of the type, and a negative count is no shift at all. Neither is a large answer: LLVM makes the result poison and the hardware promises nothing, so the program prints whatever is left behind -- 0x12 << 8 on a u8 answered 32 (#438). Cast the value to a wider type when the shift is meant to reach further: (high as u32) << 8. Only a count the compiler can read is an error. A computed count is defined instead of checked: a shift that empties the type answers 0, and an arithmetic right shift leaves the sign behind, which is Go's rule rather than the masking Java and Rust expose."))

_add(ErrorMessage("CE2511", Severity.ERROR,
    "error type mismatch in propagation: cannot propagate Result@({ok_type}, {inner_err}) to function returning Result@({ok_type}, {outer_err})",
    Category.TYPE, "The ?? operator propagates the error of the inner Result@(T, {inner_err}) into the channel of the enclosing body, Result@(T, {outer_err}). When the two error types are the same type, the error propagates unchanged. A `??` converts an error only through a declared conversion (docs/design/error-conversion.md section 3.3): when the two error types differ, it calls `extend {inner_err} as {outer_err}:` if the program declares it, and the help names that declaration. When no declaration of the pair is legal (a target with no home module, such as `StdError`), the help names `.map_err(f)??` at the site. A conversion is one step, so `A as B` and `B as C` do not give `A` to `C` (ruling C4): the lookup is an exact match on the pair, and a program that wants `A` to `C` declares it. Only the unit that declares the target type may declare the conversion (CE2519). For one site, `r.map_err(f)??` converts with no declaration."))

_add(ErrorMessage("CE2513", Severity.ERROR,
    "cannot compare '{left_type}' with '{right_type}' using operator '{op}'",
    Category.TYPE, "A comparison asks one question of two values, so both operands must be of one type. Sushi converts nothing on its own, and there is no order between a string and a number to fall back on. Cast one operand with 'as' when both are numeric, or compare like with like. Two numeric operands that disagree are CE2510 instead, which says which widths met. This code arrived with #449: every pair the typecheck pass did not look at reached the backend, which then tried to compare a string or a struct value as an i32 and answered with a CE0017 internal error."))

_add(ErrorMessage("CE2514", Severity.ERROR,
    "operator '{op}' cannot compare two values of type '{type_name}'",
    Category.TYPE, "Equality (== !=) reads the predefined perk `Eq`, and an order (< > <= >=) reads `Ord`. A primitive keeps a closed set: equality for the numeric types, bool and string, an order for the numeric types and string, where it reads the bytes. A struct or an enum, `Maybe` and `Result` included, has the equality and the order the compiler DERIVES from what it holds -- every field, or the variant and then its payload -- unless an `extend T with Eq` (or `Ord`) implementation overrides it. A type that holds something with no such contract -- a function value, a `ptr`, a `HashMap` -- has none, and a note names the field that stops it. An array, a `List` and an `Own` compare only where they are held inside a type. A bool is deliberately excluded from the order at the top level: false < true is almost always a typo for != or a missing 'and'. Inside a derived order a bool field orders false before true. This code arrived with #449, where a struct, an enum and an array comparison each reached the backend and became a CE0017 internal error."))

_add(ErrorMessage("CE2515", Severity.ERROR,
    "'{method}' is not a method of '{wrapper}' -- the call before it returns a channel that is still unhandled",
    Category.TYPE, "A method that declares '| E' returns Result@(T, E), and a Maybe@(T) is likewise more than the bare T, so the chain stops until the wrapper is handled (ruling 5 of the UFCS epic). This is a RESOLUTION FALLBACK, not a receiver-kind ban: resolution runs first, a method found on the Result/Maybe enum itself (.realise, .hash) is legal, and this code fires only when the method is missing there but present on the payload type -- which is what tells a typo from an unhandled channel. Append '??' to the call that returns a Result to propagate its Err. A Maybe holds no error value and `??` takes a Result only (CE2507), so the help for a Maybe writes one first: '.or_err(nom e)??'. Or handle the wrapper in place with match or .realise(default). The receiver is a Result or a Maybe by type identity, never by the names of its variants."))

_add(ErrorMessage("CE2517", Severity.ERROR,
    "the '??' binder needs an item that is a Result, and this loop's item is '{ty}'",
    Category.TYPE, "A foreach walks anything whose next() answers Maybe@(T), and the '??' on the BINDER is the short form for the case where T is a Result: it unwraps the Ok and leaves the function on the first Err, exactly as '??' does in every other position. An item that is not a Result has nothing to unwrap, so the marker would mean nothing -- drop it and bind the item itself. This is not CE2515, which is a resolution fallback for a CHAINED call whose channel is still unhandled, and not CE2516, which is a wrapper standing where a bool belongs; here the item is the right shape for the loop and the wrong shape for the marker. A fallible iterator is written by setting T to a Result: next() answers Maybe@(Result@(T, E)), where the outer Maybe says whether there is more and the inner Result says whether reading it worked (HANDLES.md ruling R21). The protocol itself carries no error channel, so a next() that declares '| E' is not a fallible iterator -- it is a method whose own answer is wrapped, and the loop will not accept it."))

_add(ErrorMessage("CE2516", Severity.ERROR,
    "a condition must be a bool, and '{ty}' is an unhandled {wrapper}",
    Category.TYPE, "A condition asks a yes-or-no question, so it takes a bool and nothing else. Sushi gives no other type a truth value: an integer, a string and a Maybe@(T) were all refused with CE2005 from the start. A Result@(T, E) was the one exception. It read the Ok tag, which made 'if (f())' mean 'did the call succeed' -- a second, unwritten rule that only this one type obeyed. The exception is removed with #522, and Result now agrees with Maybe. The reason is that the exception could not be read correctly. A Result@(bool, E) has two meanings in a condition, because both the wrapper and the payload are a legal condition on their own. The compiler took the wrapper in silence, so 'Ok(false)' ran the true branch and the bool was never read. This shipped a wrong answer: io/path's extension() tested a Result@(bool) that was missing its '??', and every path answered with an empty extension (#520). C++ keeps the same design for std::optional and std::expected, and optional<bool> is the standing example of the trap. Rust, Swift, Go, Kotlin and Haskell all refuse the wrapper and make you name the question. Sushi names it too: '.is_ok()' or '.is_some()' asks whether the call succeeded, '??' propagates the error of a Result ('.or_err(nom e)??' gives a Maybe an error value first, because '??' takes a Result only), '.realise(default)' takes the value with a fallback, and match reads both arms. This code covers every condition position: an if, a while, and the operands of and, or, xor and not."))

_add(ErrorMessage("CE2518", Severity.ERROR,
    "operator '{op}' takes a numeric operand, and '{type_name}' is not one",
    Category.TYPE, "Arithmetic combines numbers, so every operand of + - * / % and of the unary minus must be an integer or a float. Nothing else carries the operation. A bool is not 0 and 1 here, an enum is a tag and a payload, and a struct or an array is a value with parts -- add the parts one at a time, and use match to read an enum. An unhandled Result@(T, E) or Maybe@(T) is the common way to land here: for a Result the fault is a missing '??', and the diagnostic names that escape beside '.realise(default)' and match. A Maybe holds no error value and '??' takes a Result only, so for a Maybe the help names '.or_err(nom e)??' in place of '??'. The one carve-out is '+' with a string operand, which is CE2509, because Sushi has no concatenation operator and CE2509 names the interpolation that replaces it. Two numeric operands that disagree in width are CE2510 instead, which says which widths met. This code arrived with #709. The typecheck pass read no arithmetic operand at all, so everything reached the backend: a bool or a wrapper beside a number became a CE0000 internal error out of emit_arithmetic, a struct, an enum or an array operand failed the LLVM IR parse with the same code, and '-true' compiled and printed 'true'. The constant evaluator refused every one of them with CE0110 from the start, so a body and a constant now agree."))

_add(ErrorMessage("CE2519", Severity.ERROR,
    "cannot declare a conversion into '{target}' here: {reason}",
    Category.TYPE, "A conversion `extend <Source> as <Target>:` may be declared only in the unit that declares the TARGET type (docs/design/error-conversion.md section 3.5, ruling C5), the rule that `Drop` has already. So each pair of types has at most one declaration in the program, because the target's unit is unique: two libraries cannot both declare `FileError as AppError`, and no clash must be found across units and libraries. An application lists the errors that its own error type absorbs beside the declaration of that type. For a predefined error type the declaring unit is its HOME module: `FileError` and `IoError` have the home `<io/error>`, so a conversion into `IoError` lives there and nowhere else. A library cannot convert its own error into `IoError`; it declares its own error type and converts `IoError` into it. `StdError` has no home module, so no unit may declare a conversion into it (ruling C12): a program that wants a target for its conversions declares an error type of its own. A note points at the declaration of the target when the target has one."))

_add(ErrorMessage("CE2520", Severity.ERROR,
    "the {side} of a conversion must be a non-generic error type, and '{type_name}' is {kind}",
    Category.TYPE, "A conversion turns one error value into another, for `??` and for `as` (docs/design/error-conversion.md section 3.6, ruling C6). The `E` of every `Result` is an error type (E3), so a source or a target that is not an error type -- a plain enum, a struct, a primitive -- could never meet a `??`. Declare an enum that is an error vocabulary with `error`. Both sides are also NON-GENERIC: a generic error type and an instance of one (`DecodeError@(i32)`) are refused, so the lookup is a pair of names and the library manifest a pair of strings. A generic FUNCTION may still use a conversion: in `fn f@(E)(...) T | AppError` the pair is known for each instance, and each instance looks it up. To convert a generic error, declare a non-generic error type that holds it, or convert at the one site with `map_err`."))

_add(ErrorMessage("CE2521", Severity.ERROR,
    "a conversion from '{type_name}' into itself is refused",
    Category.TYPE, "An identity conversion does nothing that the language does not already do: `??` propagates an error of the same type unchanged, and `e as T` on a value of type `T` is a no-op (docs/design/error-conversion.md section 3.4). A declaration of one would be a second, silent path for the same propagation, so it is refused. Delete the declaration."))

_add(ErrorMessage("CE2522", Severity.ERROR,
    "'or_err' reads a borrowed '{maybe}' through, so its Result must be the operand of '??'",
    Category.TYPE, "A Maybe that is a borrow -- a get-out such as 'xs.get(0)', a parameter, a pattern binding -- keeps its payload with its owner. 'or_err' reads such a Maybe through and does not take it (docs/design/error-conversion.md section 4), so its Result holds a borrowed Ok and an owned Err. Only '??' takes the two apart: the Err moves out to the caller, and the Ok binds a borrow, as 'xs.get(0)??' does. Any other position -- a 'let', a method call such as '.is_ok()', a 'match', an argument, a 'return' -- would hold the Result whole, and no rule frees one arm and keeps the other. So the call stands under '??', or the program takes an owned copy first with '.clone().or_err(...)'. The rule applies when the payload owns a resource. An owned Maybe, and a borrowed Maybe whose payload owns nothing, give a Result that owns both arms, and that Result is legal in every position."))
