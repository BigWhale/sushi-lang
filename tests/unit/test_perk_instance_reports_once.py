"""A perk implementation on a generic target is one source, so it answers one report (#800).

Each instantiation's copy of the implementation carries the TEMPLATE's spans, and the
scope, typecheck and borrow passes walk every copy. The copy's methods are marked as
instances, and each pass tells the reporter whose body it reads, the rule a generic
function already follows (#648).
"""
from __future__ import annotations

HEADER = """struct Bx@(T):
    T v

perk Sz:
    fn sz() i32

extend Bx@(T) with Sz:
    fn sz() i32:
"""

THREE_INSTANCES = """
fn main() i32:
    let Bx@(i32) p = Bx(1)
    let Bx@(bool) q = Bx(true)
    let Bx@(string) r = Bx("a")
    println("{p.sz()} {q.sz()} {r.sz()}")
    return Result.Ok(0)
"""

ONE_INSTANCE = """
fn main() i32:
    let Bx@(i32) p = Bx(1)
    println("{p.sz()}")
    return Result.Ok(0)
"""

UNUSED = HEADER + """        let i32 dead2 = 3
        return 1
"""

UNKNOWN_NAME = HEADER + """        return missing_name
"""

UNKNOWN_FUNCTION = HEADER + """        return missing_fn(1)
"""

BORROW_FAULT = """fn eat(nom i32[] xs) ~:
    println(xs.len())
    return Result.Ok(~)

""" + HEADER + """        let i32[] owned = from([1, 2])
        eat(nom owned)
        eat(nom owned)
        return 1
"""


def count(reporter, code: str) -> int:
    return sum(1 for d in reporter.items if d.code == code)


def test_a_scope_warning_reports_once_for_three_instances(analyze):
    assert count(analyze(UNUSED + THREE_INSTANCES), "CW1001") == 1


def test_a_scope_error_reports_once_for_three_instances(analyze):
    assert count(analyze(UNKNOWN_NAME + THREE_INSTANCES), "CE1001") == 1


def test_a_typecheck_fault_reports_once_for_three_instances(analyze):
    assert count(analyze(UNKNOWN_FUNCTION + THREE_INSTANCES), "CE2008") == 1


def test_a_borrow_fault_reports_once_for_three_instances(analyze):
    assert count(analyze(BORROW_FAULT + THREE_INSTANCES), "CE2405") == 1


def test_one_instance_still_reports(analyze):
    """Control: collapsing a repeat must not swallow the only copy."""
    assert count(analyze(UNUSED + ONE_INSTANCE), "CW1001") == 1
    assert count(analyze(UNKNOWN_FUNCTION + ONE_INSTANCE), "CE2008") == 1
    assert count(analyze(BORROW_FAULT + ONE_INSTANCE), "CE2405") == 1


def test_the_copies_are_marked_as_instances(analyze_program):
    program = analyze_program(UNUSED + THREE_INSTANCES).program
    copies = [impl for impl in program.perk_impls if impl.is_synthesized]
    assert len(copies) == 3, "the gate is vacuous without the copies"
    assert all(method.instance_of for impl in copies for method in impl.methods)
