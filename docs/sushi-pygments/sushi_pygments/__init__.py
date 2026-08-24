"""A Pygments lexer for the Sushi programming language.

The lexer is not on the compiler's path, so the language can move under it in
silence -- and did, twice. `tests/unit/test_pygments_lexer.py` is the gate: the
grammar's keywords must all be known here, every numeric shape must lex whole,
and no character in the corpus may reach the catch-all rule. The numeric rules
below mirror the grammar terminals deliberately.
"""

from pygments.lexer import RegexLexer, bygroups, include, words
from pygments.token import (
    Comment,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
)

__all__ = ["SushiLexer"]

# Reserved words that introduce declarations, control flow, and modifiers.
_KEYWORDS = (
    "fn", "let", "const", "return", "if", "elif", "else", "while", "foreach",
    "expand", "in", "match", "struct", "enum", "perk", "extend", "with", "use",
    "public", "unsafe", "external", "because", "break", "continue", "as", "new",
    "peek", "poke", "nom",
)

# The match wildcard and the discard binding. A bare `_` only -- `_foo` is a name.
_WILDCARD = ("_",)

# Word-form operators.
_WORD_OPERATORS = ("and", "or", "xor", "not")

# Value keywords.
_KEYWORD_CONSTANTS = ("true", "false", "self")

# Built-in primitive and scalar types.
_TYPES = (
    "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
    "f32", "f64", "bool", "string", "file", "ptr",
)

# Built-in generic types, collections, and error enums.
_BUILTIN_TYPES = (
    "Result", "Maybe", "Option", "Own", "List", "HashMap", "Iterator", "Entry",
    "StdError", "MathError", "FileError", "IoError", "ProcessError", "EnvError",
)

# Built-in free functions / constructors.
_BUILTIN_FUNCS = ("print", "println", "from")

# Built-in I/O stream values.
_BUILTIN_VALUES = ("stdin", "stdout", "stderr")


class SushiLexer(RegexLexer):
    """Syntax highlighting for Sushi (`.sushi`) source files."""

    name = "Sushi"
    aliases = ["sushi"]
    filenames = ["*.sushi"]
    mimetypes = ["text/x-sushi"]
    url = "https://omakase.lubica.net"

    tokens = {
        "root": [
            (r"[ \t]+", Text),
            (r"\n", Text),
            (r"#.*$", Comment.Single),

            # Strings.
            (r'"', String.Double, "dqs"),
            (r"'", String.Single, "sqs"),

            # `fn name` -> highlight the function name.
            (r"(fn)(\s+)([a-zA-Z_]\w*)",
             bygroups(Keyword, Text, Name.Function)),

            # Numeric literals, mirroring the grammar terminals (radix-prefixed
            # first, then float, then int). Like the grammar these match a
            # PERMISSIVE superset: one underscore between two digits is the rule,
            # and the compiler checks placement in its own seam (CE6006). A
            # highlighter that split `1__0` would only hide the error.
            # The float has two shapes -- with a point, and exponent-only (`1e10`).
            (r"0[xX][0-9a-fA-F_]+", Number.Hex),
            (r"0[bB][01_]+", Number.Bin),
            (r"0[oO][0-7_]+", Number.Oct),
            (r"\d[\d_]*\.[\d_]+([eE][+-]?[\d_]+)?|\d[\d_]*[eE][+-]?[\d_]+",
             Number.Float),
            (r"\d[\d_]*", Number.Integer),

            # Identifiers by category.
            (words(_KEYWORDS, suffix=r"\b"), Keyword),
            (words(_WILDCARD, suffix=r"\b"), Keyword),
            (words(_WORD_OPERATORS, suffix=r"\b"), Operator.Word),
            (words(_KEYWORD_CONSTANTS, suffix=r"\b"), Keyword.Constant),
            (words(_TYPES, suffix=r"\b"), Keyword.Type),
            (words(_BUILTIN_TYPES, suffix=r"\b"), Name.Builtin),
            (words(_BUILTIN_FUNCS, suffix=r"\b"), Name.Builtin),
            (words(_BUILTIN_VALUES, suffix=r"\b"), Name.Builtin),

            # Multi-character operators. Order matters: the `...` variadic/bloom
            # spread must precede the range operators, which precede the dot rule.
            # Closures (`|params| expr`, `|~|`) get no dedicated rule -- the `|`
            # disambiguation is positional (the compiler's LALR parser resolves it)
            # and cannot be done robustly in a regex lexer; a lambda still renders
            # acceptably as `|` operators + `Name` params + a `~` operator.
            (r"\?\?|:=|==|!=|<=|>=|->|\.\.\.|\.\.=|\.\.|<<|>>|&&|\|\||\^\^",
             Operator),
            (r"[+\-*/%=<>&|\^~!]", Operator),

            # Member access: `.field` / `.method` / `.Variant`.
            (r"(\.)([a-zA-Z_]\w*)", bygroups(Punctuation, Name.Attribute)),

            # Type-like names start with an uppercase letter.
            (r"[A-Z]\w*", Name.Class),
            (r"[a-zA-Z_]\w*", Name),

            # `@` opens a type-argument list (`List@(i32)`), the 0.11.0 generic
            # form. It delimits, like the parenthesis it always precedes.
            (r"[()\[\]{},:.@;]", Punctuation),
            (r".", Text),
        ],

        # Double-quoted strings support `{expr}` interpolation.
        "dqs": [
            (r"\\.", String.Escape),
            (r"\{", String.Interpol, "interp"),
            (r'"', String.Double, "#pop"),
            (r'[^"\\{]+', String.Double),
            (r".", String.Double),
        ],

        # Interpolated expression inside a double-quoted string. `{...}` holds an
        # ordinary expression, so it is lexed as one: the state used to carry a
        # second, smaller expression lexer that knew neither `[`, `??` nor `==`.
        "interp": [
            (r"\}", String.Interpol, "#pop"),
            include("root"),
        ],

        # Single-quoted strings are literal (no interpolation).
        "sqs": [
            (r"\\.", String.Escape),
            (r"'", String.Single, "#pop"),
            (r"[^'\\]+", String.Single),
            (r".", String.Single),
        ],
    }
