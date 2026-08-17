"""Phase 2 of borrow-by-default: the surface syntax of the four parameter modes."""
from __future__ import annotations

import pytest
from lark import Lark
from lark.exceptions import UnexpectedInput

from sushi_lang.internals.parser import GRAMMAR_PATH
from sushi_lang.internals.indenter import LangIndenter


def _parser() -> Lark:
    """Build the production LALR parser (same kwargs as parse_to_ast)."""
    return Lark.open(
        str(GRAMMAR_PATH),
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=False,
        postlex=LangIndenter(),
        lexer="basic",
    )


@pytest.fixture(scope="module")
def parser() -> Lark:
    return _parser()


# --------------------------------------------------------------------------- #
# ACCEPT: peek / poke without the `&`, in all six positions
# --------------------------------------------------------------------------- #

ACCEPT_BARE_MODE = [
    # 1. reference parameter
    "fn f(peek i32 x) i32:\n"
    "    return Result.Ok(x)\n",
    "fn f(poke i32 x) ~:\n"
    "    x := 1\n"
    "    return Result.Ok(~)\n",
    # 2. receiver parameter
    "extend Counter bump(poke self) ~:\n"
    "    return ~\n",
    "extend Counter show(peek self) i32:\n"
    "    return 0\n",
    # 3. call-site borrow
    "fn main() i32:\n"
    "    let i32 a = 1\n"
    "    f(peek a)\n"
    "    g(poke a)\n"
    "    return Result.Ok(0)\n",
    # 4. foreach reference binding
    "fn main() i32:\n"
    "    foreach(poke r in rows.iter()):\n"
    "        println(r)\n"
    "    return Result.Ok(0)\n",
    # 5. match / Own reference binding
    "fn main() i32:\n"
    "    match s:\n"
    "        Shape.Poly(poke p) -> println(p)\n"
    "    return Result.Ok(0)\n",
    "fn main() i32:\n"
    "    match o:\n"
    "        Holder.Full(Own(poke x)) -> println(x)\n"
    "    return Result.Ok(0)\n",
    # 6. parameter inside a function type, and the lambda that satisfies it
    "fn main() i32:\n"
    "    let fn(peek i32) -> i32 g = h\n"
    "    return Result.Ok(0)\n",
    "fn main() i32:\n"
    "    let fn(peek i32) -> i32 g = |peek i32 x| x\n"
    "    return Result.Ok(0)\n",
]


@pytest.mark.parametrize("src", ACCEPT_BARE_MODE)
def test_bare_peek_poke_parses(parser, src):
    parser.parse(src)


# --------------------------------------------------------------------------- #
# REJECT: the `&`-prefixed spelling is gone
# --------------------------------------------------------------------------- #

REJECT_AMPERSAND = [
    "fn f(&peek i32 x) i32:\n"
    "    return Result.Ok(x)\n",
    "extend Counter bump(&poke self) ~:\n"
    "    return ~\n",
    "fn main() i32:\n"
    "    f(&peek a)\n"
    "    return Result.Ok(0)\n",
    "fn main() i32:\n"
    "    foreach(&poke r in rows.iter()):\n"
    "        println(r)\n"
    "    return Result.Ok(0)\n",
]


@pytest.mark.parametrize("src", REJECT_AMPERSAND)
def test_ampersand_spelling_is_gone(parser, src):
    with pytest.raises(UnexpectedInput):
        parser.parse(src)


# --------------------------------------------------------------------------- #
# ACCEPT: `nom` in the four positions that carry a parameter mode
# --------------------------------------------------------------------------- #

ACCEPT_NOM = [
    # a parameter of a plain function
    "fn f(nom string name) ~:\n"
    "    return Result.Ok(~)\n",
    # mixed with unmarked and by-pointer parameters
    "fn f(string a, nom string b, peek i32 c) ~:\n"
    "    return Result.Ok(~)\n",
    # a parameter of an extension method
    "extend Sink eat(nom string s) ~:\n"
    "    return ~\n",
    # inside a function type
    "fn main() i32:\n"
    "    let fn(nom string) -> i32 g = h\n"
    "    return Result.Ok(0)\n",
    # a lambda parameter
    "fn main() i32:\n"
    "    let fn(nom string) -> i32 g = |nom string s| 1\n"
    "    return Result.Ok(0)\n",
    # a call site
    "fn main() i32:\n"
    "    let string s = \"x\"\n"
    "    f(nom s)\n"
    "    return Result.Ok(0)\n",
    # a call site, on a temporary
    "fn main() i32:\n"
    "    f(nom make())\n"
    "    return Result.Ok(0)\n",
    # a call site, mixed with the other markers
    "fn main() i32:\n"
    "    f(a, nom b, peek c, poke d)\n"
    "    return Result.Ok(0)\n",
    # an FFI extern parameter parses; CE2428 rejects it in semantics, not here
    "unsafe external \"C\" as libc because \"test\":\n"
    "    fn puts(nom string s) i32 = \"puts\"\n",
]


@pytest.mark.parametrize("src", ACCEPT_NOM)
def test_nom_parses(parser, src):
    parser.parse(src)


# --------------------------------------------------------------------------- #
# REJECT: `nom` is a parameter mode, not a type
# --------------------------------------------------------------------------- #

REJECT_NOM = [
    # not a generic type argument
    "fn main() i32:\n"
    "    let List@(nom string) xs = List.new()\n"
    "    return Result.Ok(0)\n",
    # not a let type
    "fn main() i32:\n"
    "    let nom string s = \"x\"\n"
    "    return Result.Ok(0)\n",
    # not a struct field type
    "struct S:\n"
    "    nom string name\n",
    # not a return type
    "fn f() nom string:\n"
    "    return Result.Ok(\"x\")\n",
    # not a receiver mode
    "extend Counter bump(nom self) ~:\n"
    "    return ~\n",
    # not a variadic marker
    "fn f(nom ...string args) ~:\n"
    "    return Result.Ok(~)\n",
]


@pytest.mark.parametrize("src", REJECT_NOM)
def test_nom_rejected_outside_a_parameter_mode(parser, src):
    with pytest.raises(UnexpectedInput):
        parser.parse(src)


# --------------------------------------------------------------------------- #
# The three words are reserved: no identifier may use them
# --------------------------------------------------------------------------- #

RESERVED = ["peek", "poke", "nom"]


@pytest.mark.parametrize("word", RESERVED)
def test_mode_words_are_reserved(parser, word):
    src = f"fn main() i32:\n    let i32 {word} = 1\n    return Result.Ok(0)\n"
    with pytest.raises(UnexpectedInput):
        parser.parse(src)
