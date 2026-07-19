"""Pass 1.5 must infer a generic call's argument types for every expression shape
Pass 2 can (issues #171, #191).

Pass 1.5 (the instantiation collector) kept its own thin type inferrer, parallel to
Pass 2's. When they disagreed, Pass 1.5 recorded no instantiation, the monomorphizer
produced no symbol, and Pass 2 -- which *could* infer the call -- mangled a name that
did not exist and raised CE2061. Any expression shape Pass 2 infers and Pass 1.5 does
not reproduces this:

- #171: `identity(self)` inside an extension body -- Pass 1.5 never bound `self`.
- #191: `identity(x.foo())`, `identity(p.field)`, `identity(arr[0])`, `identity(-n)`,
  `identity(f()??)` -- Pass 1.5's inferrer had no arm for these.

These tests pin the fix at the collector level: the exact instantiation key must fall
out of `InstantiationCollector.run`. They are the fast, precise counterpart to the
`.sushi` regression tests in tests/bugs/.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.collect import CollectorPass
from sushi_lang.semantics.generics.instantiate import InstantiationCollector
from sushi_lang.semantics.typesys import BuiltinType

I32 = BuiltinType.I32


def _collect(src: str):
    """Run Pass 0/1 + Pass 1.5 over one program; return the function-instantiation set."""
    program, _ = parse_to_ast(src)
    tables = CollectorPass(Reporter()).run(program)
    inst = InstantiationCollector(
        struct_table=tables.structs.by_name,
        enum_table=tables.enums.by_name,
        generic_structs=tables.generic_structs.by_name,
        generic_funcs=tables.generic_funcs.by_name,
        func_table=tables.funcs.by_name,
        tables=tables,
    )
    _types, funcs = inst.run(program)
    return funcs


_IDENTITY = """
fn identity@(T)(T value) T:
    return Result.Ok(value)
"""


# --------------------------------------------------------------------------
# #191 -- argument shapes, in a plain function (no self, no extension)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("body,label", [
    ("let i32 r = identity(p.get_x())??",   "method-call arg"),
    ("let i32 r = identity(p.x)??",         "field-access arg"),
    ("let i32 r = identity(arr[0])??",      "index arg"),
    ("let i32 r = identity(-n)??",          "unary arg"),
    ("let i32 r = identity(make()??)??",    "call arg"),
])
def test_generic_call_argument_shapes_are_collected(body, label):
    src = _IDENTITY + f"""
struct P:
    i32 x

extend P get_x() i32:
    return self.x

fn make() i32:
    return Result.Ok(7)

fn use_it() i32:
    let P p = P(3)
    let i32[] arr = from([3, 4])
    let i32 n = 5
    {body}
    return Result.Ok(r)

fn main() i32:
    println(use_it()??)
    return Result.Ok(0)
"""
    assert ("identity", (I32,)) in _collect(src), f"missing instantiation for {label}"


# --------------------------------------------------------------------------
# #171 -- self as a generic-call argument, in an extension and a perk impl
# --------------------------------------------------------------------------

def test_self_arg_in_extension_body_is_collected():
    src = _IDENTITY + """
extend i32 doubled() i32:
    return identity(self).realise(0) * 2

fn main() i32:
    let i32 n = 21
    println(n.doubled())
    return Result.Ok(0)
"""
    assert ("identity", (I32,)) in _collect(src)


def test_self_arg_in_perk_impl_body_is_collected():
    src = _IDENTITY + """
perk Doubling:
    fn dbl() i32

struct Box:
    i32 v

extend Box with Doubling:
    fn dbl() i32:
        return identity(self.v).realise(0) * 2

fn main() i32:
    let Box b = Box(21)
    println(b.dbl())
    return Result.Ok(0)
"""
    assert ("identity", (I32,)) in _collect(src)


# --------------------------------------------------------------------------
# Regression guard: the shapes Pass 1.5 already handled must keep working
# --------------------------------------------------------------------------

@pytest.mark.parametrize("arg", ["1", '"s"', "true", "n", "1 as i64"])
def test_simple_argument_shapes_still_collected(arg):
    from sushi_lang.semantics.typesys import BuiltinType as BT
    expected = {
        "1": (BT.I32,), '"s"': (BT.STRING,), "true": (BT.BOOL,),
        "n": (BT.I32,), "1 as i64": (BT.I64,),
    }[arg]
    src = _IDENTITY + f"""
fn use_it() i32:
    let i32 n = 5
    let i32 unused = identity({arg})?? as i32
    return Result.Ok(unused)

fn main() i32:
    println(use_it()??)
    return Result.Ok(0)
"""
    funcs = _collect(src)
    assert ("identity", expected) in funcs
