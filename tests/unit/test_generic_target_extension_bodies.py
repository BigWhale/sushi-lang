"""Each instantiation of a generic-target extension owns its body, and is checked (#391).

`extend Box@(T)` is monomorphized into one `ExtendDef` per instantiation of `Box`. They used
to SHARE one body AST, which made three facts collide on the same nodes:

- Pass 2 stamps a resolved type on every copy it validates, so the last instantiation won
  and the function emitted for the others returned a value of the wrong shape.
- Pass 3 checked the TEMPLATE once, with `self` abstract, so an owning field handed out of
  the body was classified PLAIN and no ownership decision was stamped -- a compile-clean
  DOUBLE FREE at an owning instantiation, where the plain-target twin is CE2411.
- A body annotation naming `T` reached Pass 2 as written and was CE2001.

The three tests below pin the three halves of the answer: an own body, a per-instantiation
check, and ONE report per finding rather than one per instantiation.
"""
from __future__ import annotations

_BOX = """\
struct Box@(T):
    T value

"""


def _monomorphized(analysis, name: str):
    """Every monomorphized copy of one generic-target extension method."""
    analyzer = analysis.analyzer
    assert analyzer is not None, "analysis produced no analyzer"
    return [e for e in analyzer.monomorphized_extensions if e.name == name]


def test_each_instantiation_gets_its_own_body(analyze_program):
    """Two instantiations, two bodies -- not one object stamped twice."""
    src = _BOX + """\
extend Box@(T) peeked() Maybe@(T):
    return Maybe.Some(self.value.clone())

fn main() i32:
    let Box@(i32) a = Box(1)
    let Box@(string) b = Box("marvin")
    println(a.peeked().realise(0))
    println(b.peeked().realise("none"))
    return Result.Ok(0)
"""
    analysis = analyze_program(src, name="own_body")

    copies = _monomorphized(analysis, "peeked")
    assert len(copies) == 2, f"expected one copy per instantiation, got {len(copies)}"

    bodies = {id(copy.body) for copy in copies}
    assert len(bodies) == 2, (
        "both instantiations share one body AST. Pass 2 stamps the resolved type on those "
        "nodes per instantiation, so the last one wins and the other emits invalid IR."
    )

    # And the substitution reached the signature, which is the same operation.
    returns = {str(copy.ret) for copy in copies}
    assert returns == {"Maybe<i32>", "Maybe<string>"}, (
        f"the copies returned {sorted(returns)}; the type parameter was not substituted"
    )


def test_an_owning_consume_is_diagnosed_at_the_owning_instantiation(analyze_program):
    """`return self.value` is CE2411 at `Box@(string)` and legal at `Box@(i32)`."""
    src = _BOX + """\
extend Box@(T) unwrap() T:
    return self.value

fn main() i32:
    let string base = "marvin"
    let Box@(string) b = Box("{base} the robot")
    println(b.unwrap())
    return Result.Ok(0)
"""
    analysis = analyze_program(src, name="owning_consume")

    codes = [d.code for d in analysis.reporter.items if d.kind == "error"]
    assert "CE2411" in codes, (
        f"an owning field handed out of a borrowed `self` was accepted (codes: {codes}). "
        "It compiles into a double free -- the plain-target twin is CE2411."
    )

    # The SAME body over a plain type argument stays legal: one shared answer could not
    # serve both, which is the whole reason the check moved onto the copies.
    plain = analyze_program(_BOX + """\
extend Box@(T) unwrap() T:
    return self.value

fn main() i32:
    let Box@(i32) b = Box(42)
    println(b.unwrap())
    return Result.Ok(0)
""", name="plain_consume")
    assert not plain.reporter.has_errors, (
        "a plain type argument owns no heap, so handing the field out transfers nothing: "
        + "; ".join(f"{d.code} {d.message}" for d in plain.reporter.items)
    )


def test_one_body_error_is_reported_once(analyze_program):
    """A finding in a template body is one report, not one per instantiation."""
    src = _BOX + """\
extend Box@(T) broken() i32:
    let string s = 42
    return 1

fn main() i32:
    let Box@(i32) a = Box(1)
    let Box@(string) b = Box("marvin")
    println(a.broken() + b.broken())
    return Result.Ok(0)
"""
    analysis = analyze_program(src, name="one_report")

    mismatches = [d for d in analysis.reporter.items if d.code == "CE2002"]
    assert len(mismatches) == 1, (
        f"one line of source produced {len(mismatches)} identical reports. Every "
        "instantiation checks the body, so the report has to be collapsed."
    )
