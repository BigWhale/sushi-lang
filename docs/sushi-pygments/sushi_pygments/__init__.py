"""A Pygments lexer for the Sushi programming language (targets Sushi 0.10.0 syntax)."""

from pygments.lexer import RegexLexer, bygroups, words
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
    "peek", "poke",
)

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

            # Numeric literals (radix-prefixed first, then float, then int).
            (r"0[xX][0-9a-fA-F][0-9a-fA-F_]*", Number.Hex),
            (r"0[bB][01][01_]*", Number.Bin),
            (r"0[oO][0-7][0-7_]*", Number.Oct),
            (r"\d[\d_]*\.\d[\d_]*([eE][+-]?\d+)?", Number.Float),
            (r"\d[\d_]*", Number.Integer),

            # Identifiers by category.
            (words(_KEYWORDS, suffix=r"\b"), Keyword),
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

            (r"[()\[\]{},:.]", Punctuation),
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

        # Interpolated expression inside a double-quoted string.
        "interp": [
            (r"\}", String.Interpol, "#pop"),
            (r"'[^']*'", String.Single),
            (r"\d[\d_]*", Number),
            (r"[a-zA-Z_]\w*", Name),
            (r"[.,()]", Punctuation),
            (r"[+\-*/%]", Operator),
            (r"\s+", Text),
            (r".", Text),
        ],

        # Single-quoted strings are literal (no interpolation).
        "sqs": [
            (r"\\.", String.Escape),
            (r"'", String.Single, "#pop"),
            (r"[^'\\]+", String.Single),
            (r".", String.Single),
        ],
    }
