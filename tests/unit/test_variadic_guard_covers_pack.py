"""CE0115 must guard `is_pack` as well as `is_variadic` (#246).

The grammar cannot declare a type-pack parameter in a perk method or in an
extension method today. The guard must stay correct by construction: if
`parse_params` ever gets `pack_names` on those paths, the guard must already
reject the pack. These tests set `is_pack` on the AST directly, because no
source text can reach that state.
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.collect import CollectorPass


PERK_SRC = (
    "perk Loud:\n"
    "    fn shout(i32 n) ~\n"
    "\n"
    "fn main() i32:\n"
    "    return Result.Ok(0)\n"
)

EXT_SRC = (
    "extend i32 twice(i32 n) i32:\n"
    "    return self + n\n"
    "\n"
    "fn main() i32:\n"
    "    return Result.Ok(0)\n"
)


def _codes(reporter: Reporter) -> set[str]:
    return {d.code for d in reporter.items if d.kind == "error"}


def test_pack_param_in_perk_method_is_CE0115():
    src_program, _tree = parse_to_ast(PERK_SRC)
    src_program.perks[0].methods[0].params[0].is_pack = True
    reporter = Reporter(source=PERK_SRC, filename="main")
    CollectorPass(reporter).run(src_program, unit_name="main")
    assert "CE0115" in _codes(reporter)


def test_pack_param_in_extension_method_is_CE0115():
    src_program, _tree = parse_to_ast(EXT_SRC)
    src_program.extensions[0].params[0].is_pack = True
    reporter = Reporter(source=EXT_SRC, filename="main")
    CollectorPass(reporter).run(src_program, unit_name="main")
    assert "CE0115" in _codes(reporter)


def test_variadic_param_guard_still_fires():
    src_program, _tree = parse_to_ast(PERK_SRC)
    src_program.perks[0].methods[0].params[0].is_variadic = True
    reporter = Reporter(source=PERK_SRC, filename="main")
    CollectorPass(reporter).run(src_program, unit_name="main")
    assert "CE0115" in _codes(reporter)
