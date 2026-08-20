"""The `??` guard for extension and perk bodies is a Phase 0 gate (#398).

The rule is structural: these bodies return a bare value (CE2091), so a `??`
has nothing to propagate into. Phase 0 collection walks the DECLARATION, so
the guard fires once per declaration, before monomorphization -- an
uninstantiated template cannot slip through, and an instantiated one is not
reported once per copy.
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.collect import CollectorPass


TEMPLATE_SRC = (
    "struct Box@(T):\n"
    "    T value\n"
    "\n"
    "fn tag(i32 n) i32:\n"
    "    return Result.Ok(n)\n"
    "\n"
    "extend Box@(T) tagged() i32:\n"
    "    return tag(1)??\n"
    "\n"
    "fn main() i32:\n"
    "    return Result.Ok(0)\n"
)

PERK_IMPL_SRC = (
    "perk Tagged:\n"
    "    fn tagit() i32\n"
    "\n"
    "fn tag(i32 n) i32:\n"
    "    return Result.Ok(n)\n"
    "\n"
    "struct P:\n"
    "    i32 n\n"
    "\n"
    "extend P with Tagged:\n"
    "    fn tagit() i32:\n"
    "        let i32 a = tag(self.n)??\n"
    "        return tag(a)??\n"
    "\n"
    "fn main() i32:\n"
    "    return Result.Ok(0)\n"
)

INSTANTIATED_SRC = (
    "struct Box@(T):\n"
    "    T value\n"
    "\n"
    "fn tag(i32 n) i32:\n"
    "    return Result.Ok(n)\n"
    "\n"
    "extend Box@(T) tagged() i32:\n"
    "    return tag(1)??\n"
    "\n"
    "fn main() i32:\n"
    "    let Box@(i32) a = Box(1)\n"
    "    let Box@(string) b = Box(\"x\")\n"
    "    println(\"{a.tagged()} {b.tagged()}\")\n"
    "    return Result.Ok(0)\n"
)


def _collect_codes(src: str) -> list[str]:
    program, _tree = parse_to_ast(src)
    reporter = Reporter(source=src, filename="main")
    CollectorPass(reporter).run(program, unit_name="main")
    return [d.code for d in reporter.items if d.kind == "error"]


def test_phase0_rejects_try_in_extension_template():
    assert _collect_codes(TEMPLATE_SRC).count("CE0131") == 1


def test_phase0_rejects_try_in_perk_impl_once_per_occurrence():
    assert _collect_codes(PERK_IMPL_SRC).count("CE0131") == 2


def test_full_analysis_reports_once_not_per_instantiation(analyze):
    reporter = analyze(INSTANTIATED_SRC)
    codes = [d.code for d in reporter.items if d.kind == "error"]
    assert codes.count("CE0131") == 1


def test_ce2508_no_longer_fires_for_extension_bodies(analyze):
    reporter = analyze(INSTANTIATED_SRC)
    codes = [d.code for d in reporter.items if d.kind == "error"]
    assert "CE2508" not in codes

LAMBDA_SRC = (
    "fn tag(i32 n) i32:\n"
    "    return Result.Ok(n)\n"
    "\n"
    "extend i32 fixed() i32:\n"
    "    let fn(i32) -> i32 f = |i32 x| tag(x)??\n"
    "    let i32 r = f(self).realise(0)\n"
    "    return r\n"
    "\n"
    "fn main() i32:\n"
    "    return Result.Ok(0)\n"
)


def test_try_inside_lambda_is_not_ce0131():
    """The walk skips Lambda subtrees (#399): the lambda has its own channel."""
    assert _collect_codes(LAMBDA_SRC).count("CE0131") == 0
