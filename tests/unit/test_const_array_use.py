"""Regression tests for #248: using an array constant directly is a CE0000 ICE.

`emit_name` knows about global constants -- it looks up `codegen.constants[name]` and
loads it -- but every backend path that needs a constant's ADDRESS rather than its value
went straight to `MemoryManager.find_local_slot`, which knows only local allocas. So
`let i32[3] copy = PRIMES` worked (the only shape that never asks for the address) while
`PRIMES[0]`, `PRIMES.len()`, `PRIMES.get(0)??`, `PRIMES.iter()` and `PRIMES.hash()` all
died with `KeyError: 'undefined name: PRIMES'` rendered as CE0000.

These assert at the IR level, because the failure was a compile-time crash: reaching IR
at all is most of the proof. The two that assert more than "it compiled" are the ones
that pin a DECISION rather than a fix -- that a local shadowing a constant still wins,
and that the read GEPs the global instead of materialising a hidden local copy.
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _function_body


_PRIMES = "const i32[3] PRIMES = [2, 3, 5]\n\n"


def test_index_in_interpolation(tmp_path):
    """`println("{PRIMES[0]}")` -- the shape reported in #248."""
    src = _PRIMES + (
        "fn main() i32:\n"
        '    println("{PRIMES[0]}")\n'
        "    return Result.Ok(0)\n"
    )
    assert "@\"PRIMES\"" in _emit_ir(tmp_path, src)


def test_index_in_let(tmp_path):
    """`let i32 first = PRIMES[0]` -- indexing outside an interpolation."""
    src = _PRIMES + (
        "fn main() i32:\n"
        "    let i32 first = PRIMES[0]\n"
        "    println(first)\n"
        "    return Result.Ok(0)\n"
    )
    assert "@\"PRIMES\"" in _emit_ir(tmp_path, src)


def test_index_in_non_main_function(tmp_path):
    """The read works in an ordinary function, not just in main()."""
    src = _PRIMES + (
        "fn third() i32:\n"
        "    return Result.Ok(PRIMES[2])\n"
        "\n"
        "fn main() i32:\n"
        "    println(third().realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    assert "@\"PRIMES\"" in _function_body(_emit_ir(tmp_path, src), "third")


def test_len(tmp_path):
    """`PRIMES.len()`.

    `len` is also a builtin List/HashMap method name, so `calls/generics.py`'s List and
    HashMap probes run first and asked for the receiver's address before the array
    dispatcher was ever reached.
    """
    src = _PRIMES + (
        "fn main() i32:\n"
        "    println(PRIMES.len())\n"
        "    return Result.Ok(0)\n"
    )
    assert _emit_ir(tmp_path, src)


def test_get_maybe(tmp_path):
    """`PRIMES.get(0)??` -- needs the constant's SEMANTIC type, not just its address.

    `emit_fixed_array_get_maybe` is the one fixed-array method that consumes
    `semantic_type`, and a constant has no entry in the memory manager's semantic-type
    cache; it has to come from the const table.
    """
    src = _PRIMES + (
        "fn first() i32:\n"
        "    return Result.Ok(PRIMES.get(0)??)\n"
        "\n"
        "fn main() i32:\n"
        "    println(first().realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    assert "@\"PRIMES\"" in _emit_ir(tmp_path, src)


def test_iter_foreach(tmp_path):
    """`foreach(p in PRIMES.iter())` -- pins the second address site, in iterators.py."""
    src = _PRIMES + (
        "fn main() i32:\n"
        "    foreach(p in PRIMES.iter()):\n"
        "        println(p)\n"
        "    return Result.Ok(0)\n"
    )
    assert "@\"PRIMES\"" in _emit_ir(tmp_path, src)


def test_hash(tmp_path):
    """`PRIMES.hash()` -- reaches the address path through emit_receiver_value."""
    src = _PRIMES + (
        "fn main() i32:\n"
        "    println(PRIMES.hash())\n"
        "    return Result.Ok(0)\n"
    )
    assert _emit_ir(tmp_path, src)


def test_local_shadows_constant(tmp_path):
    """A local named like the constant WINS -- the read must not reach the global.

    This is the regression risk of the whole fix: resolving constants before locals
    would read the global and silently produce wrong values rather than crash. `emit_name`
    already resolves locals first, and the shared resolver must keep that order.
    """
    src = _PRIMES + (
        "fn shadowed() i32:\n"
        "    let i32[3] PRIMES = [7, 8, 9]\n"
        "    return Result.Ok(PRIMES[0])\n"
        "\n"
        "fn main() i32:\n"
        "    println(shadowed().realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    body = _function_body(_emit_ir(tmp_path, src), "shadowed")
    assert 'alloca [3 x i32]' in body, "the local array must still be allocated"
    assert '@"PRIMES"' not in body, "the local shadows the constant; the global must not be read"


def test_read_is_zero_copy(tmp_path):
    """Reading a constant GEPs the global -- it does NOT materialise a local copy.

    Constants are meant to be zero-cost: one `.rodata` object, no per-use stack copy.
    Pins the design against the alternative fix (alloca + store the loaded global on
    first use), which would compile just as well and be silently more expensive.
    """
    src = _PRIMES + (
        "fn read() i32:\n"
        "    return Result.Ok(PRIMES[1])\n"
        "\n"
        "fn main() i32:\n"
        "    println(read().realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    body = _function_body(_emit_ir(tmp_path, src), "read")
    assert '@"PRIMES"' in body
    assert 'alloca [3 x i32]' not in body, "the constant must not be copied into a local"
    assert 'memcpy' not in body, "the constant must not be copied into a local"
