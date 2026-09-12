"""Gate: one reader answers the return type and the `| E` channel (#643).

A free function, an extension method and a perk contract method all write the pair as
`")" type ["|" type]`. The channel is ONE language rule -- CE0133 refuses a contract and
an implementation that declare different channels -- so a second reader can let the rule
disagree with itself before that check runs. The channel got its third reader after the
free function had it, and that copy had to be brought into line by hand.

`read_signature_types` is the one read. The grammar half of this gate says the three
positions still spell the same pair, and the behaviour half says the three parsers
answer the same type and the same spans for it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sushi_lang.internals.parser import build_parser
from sushi_lang.semantics.ast_builder import ASTBuilder
from sushi_lang.semantics.ast_builder.declarations.signatures import (
    read_signature_types)

ROOT = Path(__file__).resolve().parents[2]
GRAMMAR = ROOT / "sushi_lang" / "grammar.lark"
DECLARATIONS = ROOT / "sushi_lang" / "semantics" / "ast_builder" / "declarations"

# The rule each parser reads, and the module that reads it.
SIGNATURE_RULES = {
    "function_def": "functions.py",
    "extend_def": "extensions.py",
    "perk_method": "perks.py",
}

# One channel, written at the same column in all three positions, so a span read that
# points at the return type instead of the channel cannot pass.
CHANNEL_SOURCES = {
    "function": (
        "enum OddError:\n"
        "    TooOdd\n"
        "\n"
        "fn halved(i32 v) i32 | OddError:\n"
        "    return Result.Ok(v)\n"
    ),
    "extension": (
        "enum OddError:\n"
        "    TooOdd\n"
        "\n"
        "struct Half:\n"
        "    i32 n\n"
        "\n"
        "extend Half take(peek self) i32 | OddError:\n"
        "    return self.n\n"
    ),
    "perk method": (
        "enum OddError:\n"
        "    TooOdd\n"
        "\n"
        "perk Source:\n"
        "    fn read_one(peek self) i32 | OddError\n"
    ),
}

BARE_SOURCES = {
    "function": "fn halved(i32 v) i32:\n    return Result.Ok(v)\n",
    "extension": (
        "struct Half:\n"
        "    i32 n\n"
        "\n"
        "extend Half take(peek self) i32:\n"
        "    return self.n\n"
    ),
    "perk method": "perk Source:\n    fn read_one(peek self) i32\n",
}

CONTRACT_AND_IMPLEMENTATION = (
    "enum OddError:\n"
    "    TooOdd\n"
    "\n"
    "perk Source:\n"
    "    fn read_one(peek self) i32 | OddError\n"
    "\n"
    "struct Counter:\n"
    "    i32 value\n"
    "\n"
    "extend Counter with Source:\n"
    "    fn read_one(peek self) i32 | OddError:\n"
    "        return self.value\n"
)

_PARSER = build_parser()


def _declaration(source: str, position: str):
    """The one declaration of `position` that this unit holds."""
    program = ASTBuilder().build(_PARSER.parse(source))
    found = {
        "function": program.functions,
        "extension": program.extensions,
        "perk method": program.perks[0].methods if program.perks else [],
    }[position]
    assert len(found) == 1
    return found[0]


def _rule_body(rule: str) -> str:
    text = GRAMMAR.read_text()
    if rule == "extend_def":
        line = re.search(r"^\s+\|\s+(STATIC\?.+)-> extend_def$", text, re.MULTILINE)
    else:
        line = re.search(rf"^{rule}: (.+)$", text, re.MULTILINE)
    assert line is not None, f"no rule named {rule}"
    return line.group(1)


# -- the three positions spell one pair -----------------------------------------

@pytest.mark.parametrize("rule", sorted(SIGNATURE_RULES))
def test_every_signature_position_spells_the_same_channel(rule: str) -> None:
    """A position that spells the channel differently needs a reader of its own."""
    assert '["|" type]' in _rule_body(rule)


@pytest.mark.parametrize("rule", sorted(SIGNATURE_RULES))
def test_only_a_function_may_omit_its_return_type(rule: str) -> None:
    """A contract and an extension method must state what they answer."""
    assert ("type? [" in _rule_body(rule)) is (rule == "function_def")


@pytest.mark.parametrize("rule", sorted(SIGNATURE_RULES))
def test_the_parser_of_each_position_calls_the_one_reader(rule: str) -> None:
    """A parser that reads the pair itself is the second reader this gate refuses."""
    source = (DECLARATIONS / SIGNATURE_RULES[rule]).read_text()
    assert source.count("read_signature_types(") == 1


# -- the three positions answer one channel --------------------------------------

def test_a_channel_reads_the_same_type_wherever_it_is_written() -> None:
    """The three positions answer one channel. A drift between readers shows here."""
    read = [_declaration(source, position)
            for position, source in sorted(CHANNEL_SOURCES.items())]

    assert read[0].err_type is not None
    assert read[0].err_type.name == "OddError"
    for other in read[1:]:
        assert other.err_type == read[0].err_type
        assert other.ret == read[0].ret


def test_a_signature_with_no_channel_has_no_error_type() -> None:
    read = [_declaration(source, position)
            for position, source in sorted(BARE_SOURCES.items())]

    for declaration in read:
        assert declaration.err_type is None
        assert declaration.ret == read[0].ret


@pytest.mark.parametrize("position", ["extension", "perk method"])
def test_the_channel_span_covers_the_channel_and_not_the_return_type(
        position: str) -> None:
    """The two nodes that record a channel span point it at the channel."""
    declaration = _declaration(CHANNEL_SOURCES[position], position)

    assert declaration.err_span is not None
    assert declaration.err_span.col > declaration.ret_span.col
    assert declaration.err_span.end_col - declaration.err_span.col == len("OddError")


def test_a_contract_and_its_implementation_read_one_channel() -> None:
    """What CE0133 compares. Two readers can make these differ; one cannot."""
    program = ASTBuilder().build(_PARSER.parse(CONTRACT_AND_IMPLEMENTATION))
    contract = program.perks[0].methods[0]
    implementation = program.perk_impls[0].methods[0]

    assert contract.err_type == implementation.err_type
    assert contract.ret == implementation.ret
    assert contract.self_mode == implementation.self_mode


# -- the reader answers what a caller cannot read off the parsed type ------------

def test_a_missing_return_type_is_told_apart_from_one_that_did_not_parse() -> None:
    """`declares_return` answers the WRITTEN shape, which `ret` cannot.

    The type parser gives None for a malformed type too, so a caller that makes the
    return type mandatory has to ask whether one was written at all.
    """
    written = read_signature_types(
        _PARSER.parse("fn f() i32:\n    return Result.Ok(1)\n")
        .children[0].children[0].children,
        ASTBuilder())
    omitted = read_signature_types(
        _PARSER.parse("fn f():\n    println(\"a\")\n")
        .children[0].children[0].children,
        ASTBuilder())

    assert written.declares_return is True
    assert omitted.declares_return is False
    assert omitted.ret is None
    assert omitted.ret_span is None
    assert omitted.err is None
    assert omitted.err_span is None
