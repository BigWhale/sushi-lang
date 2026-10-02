"""Syntax errors (CE6xxx) -- the parser's own diagnostics."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


# Syntax errors (CE6xxx) -- the parser's own diagnostics
_add(ErrorMessage("CE6001", Severity.ERROR,
    "unexpected token '{token}'",
    Category.SYNTAX, "The parser reached a token that cannot appear here."))

_add(ErrorMessage("CE6002", Severity.ERROR,
    "unexpected character '{char}'",
    Category.SYNTAX, "The character is not part of any Sushi token."))

_add(ErrorMessage("CE6003", Severity.ERROR,
    "unexpected end of file",
    Category.SYNTAX, "The source ended in the middle of a construct."))

_add(ErrorMessage("CE6004", Severity.ERROR,
    "inconsistent indentation: dedent to column {got}, expected column {expected}",
    Category.SYNTAX, "A dedent must return to a column that an enclosing block opened."))

_add(ErrorMessage("CE6005", Severity.ERROR,
    "could not tokenize input",
    Category.SYNTAX, "The lexer failed on this input."))

_add(ErrorMessage("CE6006", Severity.ERROR,
    "malformed numeric literal '{literal}': {reason}",
    Category.SYNTAX, "An underscore groups the digits of a numeric literal. One underscore, "
                     "between two digits -- so not after the base prefix, not doubled, and "
                     "not next to a point or an exponent marker."))

_add(ErrorMessage("CE6010", Severity.ERROR,
    "could not parse the interpolated expression '{expr}'",
    Category.SYNTAX, "The text between {braces} in a string literal must be a valid expression."))

# Documentation blocks (docs/design/documentation.md sections 2 and 7). All three are
# raised from a lexer callback, at lex time, before the AST builder has an opinion.
_add(ErrorMessage("CE6011", Severity.ERROR,
    "a documentation block is opened here and never closed",
    Category.SYNTAX, "A `##:` opens a documentation block, and a `:##` closes it. The "
                     "closer is on the opening line, or it is line-initial. Treating an "
                     "unmatched opener as a comment would let a whole documented API "
                     "vanish from a build with no signal."))

_add(ErrorMessage("CE6012", Severity.ERROR,
    "a documentation block is closed here, but never opened",
    Category.SYNTAX, "A `:##` closes a documentation block that a `##:` opened. The two "
                     "delimiters are asymmetric on purpose: they let the compiler say "
                     "which of the two mistakes was made."))

_add(ErrorMessage("CE6013", Severity.ERROR,
    "a documentation block is opened inside a documentation block",
    Category.SYNTAX, "Documentation blocks do not nest: a block ends at the first closer "
                     "that qualifies. A line-initial `##:` in the interior means the "
                     "enclosing block swallowed the blocks between the two openers. This "
                     "is the signal GCC gives for a `/*` inside a block comment."))

_add(ErrorMessage("CE6101", Severity.ERROR,
    "nested function definitions are not supported",
    Category.SYNTAX, "A function may only be defined at the top level. Use a lambda for a "
                     "local callable."))

_add(ErrorMessage("CE6103", Severity.ERROR,
    "a perk implementation method cannot be marked `public`",
    Category.SYNTAX, "Ruling 2 of `docs/design/visibility.md`: an implementation carries no marker, because it is exactly as visible as the type it is attached to. A private type cannot be named, constructed or received in another unit, so its methods are already unreachable there, and a public type's methods are reachable wherever the type is. The marker used to parse here and be stored on the method, where nothing read it. Remove it, and mark the target type instead."))

_add(ErrorMessage("CE6102", Severity.ERROR,
    "explicit type arguments are only supported on direct function calls",
    Category.SYNTAX, "The `@(...)` type-argument list may appear only on a call to a named "
                     "free function, not on a method call or an indirect call."))

_add(ErrorMessage("CE6104", Severity.ERROR,
    "the named argument '{name}:' is only supported in a struct construction",
    Category.SYNTAX, "A named argument names a FIELD, and a struct declaration is the only "
                     "declaration that gives a name to each position at the call site. A "
                     "function parameter, a method parameter and an enum payload have no "
                     "such name, so the compiler read the arguments by position and "
                     "ignored the labels: `p.moved(dy: 5)` against `(i32 dx, i32 dy)` "
                     "wrote the wrong slot on a program the compiler accepted (#563). The "
                     "grammar takes `name: value` in every argument list, because the "
                     "parser cannot know what the callee is; the rule is refused where the "
                     "callee is known, which is the typecheck pass, as CE6102 is. Write "
                     "the arguments in declaration order."))

_add(ErrorMessage("CE6105", Severity.ERROR,
    "invalid tuple element: {reason}",
    Category.SYNTAX, "A tuple TYPE and a `let` destructure share one list rule, `(elem, elem, ...)`, and the token after the `)` tells them apart: `=` is a destructure, a NAME is a typed binding. The AST builder judges each element by its position. In a TYPE position every element is a type and nothing else: a name (`(i32 quot, i32 rem)`) and a `_` are refused, because a tuple has no named elements (ruling 9 of the tuple design) -- a record with names is a struct. In a DESTRUCTURE position an element is a bare name (`q`, the type is the element type), a typed name (`i32 q`), a `_`, or a nested destructure (`(a, b)`); a type with no name (`i32` alone) binds nothing and is refused. Added with tuples (docs/design/tuples.md)."))

_add(ErrorMessage("CE6106", Severity.ERROR,
    "'.{index}' is not a tuple element index: {reason}",
    Category.SYNTAX, "A tuple element is read with `.N`, where N is a plain decimal number that starts at 0: `t.0`, `t.1`, and `t.0.1` for an element of a nested tuple. The element type depends on the index, so the index is a literal and never a value (`t[i]` is not a tuple access). The lexer reads `t.0.1` as `t` `.` and the number `0.1`, and the builder splits it in two steps; a number with an underscore (`t.0_1`), an exponent (`t.1e3`) or a leading zero (`t.01`) names no element and is refused here. Added with tuples (docs/design/tuples.md)."))

_add(ErrorMessage("CE6107", Severity.ERROR,
    "a tuple element takes no mode: '{mode}'",
    Category.SYNTAX, "A destructure element is a name, a typed name, a `_` or a nested destructure, and it carries no `peek`, `poke` or `nom`: a binder OWNS its element when the destructure takes an owned value (a temporary, or an owned local, which the destructure spends), and BORROWS it when the value is a borrow (a parameter, a field, a binding). So `let (peek i32 a, b) = t` is refused (spec decision D1 of the tuple design; a later change can add a mode). A tuple TYPE takes no mode on an element either: a tuple holds values, and a reference is a parameter or a `let` mode. Added with tuples (docs/design/tuples.md)."))

_add(ErrorMessage("CE6108", Severity.ERROR,
    "{construct} is not supported yet",
    Category.SYNTAX, "The grammar of tuples arrived in one step, and three of its shapes get their meaning in a later step: a `foreach` destructure (`foreach((k, v) in pairs.iter()):`), a destructuring rebind (`(a, b) := (b, a)`), and a tuple pattern or a literal inside a pattern in a `match` arm (`Maybe.Some((a, b)) ->`, `Maybe.Some(0) ->`). Until then each is refused here with its location. Destructure with a `let` instead: `let (k, v) = item`. This code is temporary, and it goes when the last of the three shapes is supported (docs/design/tuples.md)."))
