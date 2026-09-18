"""A perk CONTRACT writes types, and they are checked (#667).

The contract is a callable with no body, so the per-declaration loop never reaches it.
`EXPECT_ERROR_CODE` is a substring check and cannot pin "one fault, one diagnostic", so
the code SET is pinned here on the analyze fixture.
"""
from __future__ import annotations

CONTRACT_RETURN = """
perk Source:
    fn read_one(peek self) NoSuchType

fn main() i32:
    return Result.Ok(0)
"""

CONTRACT_PARAMETER = """
perk Sink:
    fn put(peek self, NoSuchParam v) i32

fn main() i32:
    return Result.Ok(0)
"""


def _codes(reporter):
    return [d.code for d in reporter.items if d.kind == "error"]


def test_the_walk_yields_every_contract_signature_position():
    """The fault is a position the PASS ignored, not one the walk omitted.

    `signature_types()` already yields the return, the channel and every parameter of a
    contract method, which is why the leak fence and the `ptr` quarantine reach them
    already. Measuring it keeps a later reader from adding a second walk for what this
    one covers.
    """
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.semantics.ast_walk import signature_types

    program, _tree = parse_to_ast(CONTRACT_PARAMETER)
    positions = {site.position for site in signature_types(program)
                 if site.kind == "perk method"}
    assert {"return", "error", "parameter"} <= positions


def test_a_contract_return_that_names_nothing_is_one_ce2001(analyze):
    assert _codes(analyze(CONTRACT_RETURN)) == ["CE2001"]


def test_a_contract_parameter_that_names_nothing_is_one_ce2001(analyze):
    assert _codes(analyze(CONTRACT_PARAMETER)) == ["CE2001"]
