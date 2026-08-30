"""Result@(T, E) and Maybe@(T) method errors (CE25xx)."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


# Result@(T) method errors (CE25xx)
_add(ErrorMessage("CE2502", Severity.ERROR,
    "realise() requires exactly 1 argument, got {got}",
    Category.TYPE, "The realise() method on Result@(T) must be called with exactly one default value argument."))

_add(ErrorMessage("CE2503", Severity.ERROR,
    "realise() default type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The default value type passed to realise() must match the T type in Result@(T)."))

_add(ErrorMessage("CE2505", Severity.ERROR,
    "cannot assign Result@(T) to non-Result variable without handling (use .realise() or pattern matching)",
    Category.TYPE, "Result@(T) values must be explicitly handled before assigning to non-Result variables."))

_add(ErrorMessage("CE2506", Severity.ERROR,
    "cannot call .realise() on Result@(~) (blank type has no value to extract)",
    Category.TYPE, "Blank functions return Result@(~) which has no meaningful value. Use if statement to check success/failure instead."))

# Try operator (??) errors (CE25xx continued)
_add(ErrorMessage("CE2507", Severity.ERROR,
    "?? operator requires Result@(T), Maybe@(T), or result-like enum (with Ok/Err or Some/None variants), got '{got}'",
    Category.TYPE, "The ?? operator requires an enum with Ok/Err variants (e.g., Result@(T), FileResult) or Some/None variants (e.g., Maybe@(T))."))

_add(ErrorMessage("CE2508", Severity.ERROR,
    "?? operator can only be used in functions returning a result-like enum (with Ok/Err variants)",
    Category.TYPE, "The ?? operator propagates errors by early return, so it requires the enclosing function to return a result-like enum (e.g., Result@(T), FileResult). Note: Maybe@(T) can be used with ??, but it propagates as Result.Err()."))

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
    Category.TYPE, "The ?? operator requires error types to match exactly. Inner function returns Result@(T, {inner_err}) but outer function returns Result@(T, {outer_err}). Error type conversion is not supported yet."))

_add(ErrorMessage("CE2513", Severity.ERROR,
    "cannot compare '{left_type}' with '{right_type}' using operator '{op}'",
    Category.TYPE, "A comparison asks one question of two values, so both operands must be of one type. Sushi converts nothing on its own, and there is no order between a string and a number to fall back on. Cast one operand with 'as' when both are numeric, or compare like with like. Two numeric operands that disagree are CE2510 instead, which says which widths met. This code arrived with #449: every pair the typecheck pass did not look at reached the backend, which then tried to compare a string or a struct value as an i32 and answered with a CE0017 internal error."))

_add(ErrorMessage("CE2514", Severity.ERROR,
    "operator '{op}' cannot compare two values of type '{type_name}'",
    Category.TYPE, "Equality is defined for the numeric types, bool and string. An order (< > <= >=) is defined for the numeric types and string, where it reads the bytes. Nothing else carries a comparison. Use match to ask which variant an enum holds, and compare the fields of a struct one at a time. A bool is deliberately excluded from the order: false < true is almost always a typo for != or a missing 'and', and Rust and Go both accept it where Sushi does not. This code arrived with #449, where a struct, an enum and an array comparison each reached the backend and became a CE0017 internal error."))

_add(ErrorMessage("CE2515", Severity.ERROR,
    "'{method}' is not a method of '{wrapper}' -- the call before it returns a channel that is still unhandled",
    Category.TYPE, "A method that declares '| E' returns Result@(T, E), and a Maybe@(T) is likewise more than the bare T, so the chain stops until the wrapper is handled (ruling 5 of the UFCS epic). This is a RESOLUTION FALLBACK, not a receiver-kind ban: resolution runs first, a method found on the Result/Maybe enum itself (.realise, .hash) is legal, and this code fires only when the method is missing there but present on the payload type -- which is what tells a typo from an unhandled channel. Append '??' to the call that returns the wrapper to propagate its Err/None, or handle it in place with match or .realise(default)."))
