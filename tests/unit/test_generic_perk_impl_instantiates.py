"""A perk implementation on a generic target is a TEMPLATE, instantiated per target."""
from __future__ import annotations


SOURCE = """\
perk Show:
    fn show() string

struct Box@(T):
    T item

extend Box@(T) with Show:
    fn show() string:
        return "boxed {self.item}"

fn main() i32:
    let Box@(i32) n = Box(7)
    let Box@(string) s = Box("towel")
    println(n.show())
    println(s.show())
    return Result.Ok(0)
"""

CONCRETE = """\
perk Show:
    fn show() string

struct Box@(T):
    T item

extend Box@(i32) with Show:
    fn show() string:
        return "boxed {self.item}"

fn main() i32:
    let Box@(i32) n = Box(7)
    println(n.show())
    return Result.Ok(0)
"""


def test_a_generic_target_leaves_perk_impls_and_becomes_one_copy_per_instantiation(
        analyze_program):
    analysis = analyze_program(SOURCE)
    analyzer, program, reporter = analysis.analyzer, analysis.program, analysis.reporter
    assert not [item for item in reporter.items if item.code.startswith("CE")], \
        [item.code for item in reporter.items]

    # The template itself is re-filed: every later walk over `perk_impls` -- the
    # typecheck pass, the backend, the fingerprint -- assumes a concrete `self`.
    assert len(program.generic_perk_impls) == 1
    assert str(program.generic_perk_impls[0].target_type) == "Box<T>"

    targets = {str(impl.target_type) for impl in program.perk_impls}
    assert targets == {"Box<i32>", "Box<string>"}, targets

    for name in ("Box<i32>", "Box<string>"):
        assert analyzer.perk_impls.implements(name, "Show"), name
        method = analyzer.perk_impls.get(name, "Show").methods[0]
        assert method.name == "show"


def test_a_concrete_instantiation_target_stays_where_it_was_written(analyze_program):
    analysis = analyze_program(CONCRETE)
    analyzer, program, reporter = analysis.analyzer, analysis.program, analysis.reporter
    assert not [item for item in reporter.items if item.code.startswith("CE")], \
        [item.code for item in reporter.items]
    assert program.generic_perk_impls == []
    assert [str(impl.target_type) for impl in program.perk_impls] == ["Box<i32>"]
    assert analyzer.perk_impls.implements("Box<i32>", "Show")


def test_an_uninstantiated_template_costs_nothing(analyze_program):
    src = SOURCE.replace('    let Box@(string) s = Box("towel")\n', "") \
                .replace("    println(s.show())\n", "")
    analysis = analyze_program(src)
    analyzer, program = analysis.analyzer, analysis.program
    targets = {str(impl.target_type) for impl in program.perk_impls}
    assert targets == {"Box<i32>"}, targets
    assert not analyzer.perk_impls.implements("Box<string>", "Show")
