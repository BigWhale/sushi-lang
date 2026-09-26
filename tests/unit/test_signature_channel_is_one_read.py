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









# -- the reader answers what a caller cannot read off the parsed type ------------

