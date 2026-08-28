"""Regression tests for #248: using an array constant directly is a CE0000 ICE."""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _function_body, _mentions_symbol


_PRIMES = "const i32[3] PRIMES = [2, 3, 5]\n\n"


def test_index_in_interpolation(tmp_path):
    """`println("{PRIMES[0]}")` -- the shape reported in #248."""
    src = _PRIMES + (
        "fn main() i32:\n"
        '    println("{PRIMES[0]}")\n'
        "    return Result.Ok(0)\n"
    )
    assert _mentions_symbol(_emit_ir(tmp_path, src), "PRIMES")


def test_index_in_let(tmp_path):
    """`let i32 first = PRIMES[0]` -- indexing outside an interpolation."""
    src = _PRIMES + (
        "fn main() i32:\n"
        "    let i32 first = PRIMES[0]\n"
        "    println(first)\n"
        "    return Result.Ok(0)\n"
    )
    assert _mentions_symbol(_emit_ir(tmp_path, src), "PRIMES")


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
    assert _mentions_symbol(_function_body(_emit_ir(tmp_path, src), "third"), "PRIMES")


def test_len(tmp_path):
    """`PRIMES.len()`."""
    src = _PRIMES + (
        "fn main() i32:\n"
        "    println(PRIMES.len())\n"
        "    return Result.Ok(0)\n"
    )
    assert _emit_ir(tmp_path, src)


def test_get_maybe(tmp_path):
    """`PRIMES.get(0)??` -- needs the constant's SEMANTIC type, not just its address."""
    src = _PRIMES + (
        "fn first() i32:\n"
        "    return Result.Ok(PRIMES.get(0)??)\n"
        "\n"
        "fn main() i32:\n"
        "    println(first().realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    assert _mentions_symbol(_emit_ir(tmp_path, src), "PRIMES")


def test_iter_foreach(tmp_path):
    """`foreach(p in PRIMES.iter())` -- pins the second address site, in iterators.py."""
    src = _PRIMES + (
        "fn main() i32:\n"
        "    foreach(p in PRIMES.iter()):\n"
        "        println(p)\n"
        "    return Result.Ok(0)\n"
    )
    assert _mentions_symbol(_emit_ir(tmp_path, src), "PRIMES")


def test_hash(tmp_path):
    """`PRIMES.hash()` -- reaches the address path through emit_receiver_value."""
    src = _PRIMES + (
        "fn main() i32:\n"
        "    println(PRIMES.hash())\n"
        "    return Result.Ok(0)\n"
    )
    assert _emit_ir(tmp_path, src)


def test_local_shadows_constant(tmp_path):
    """A local named like the constant WINS -- the read must not reach the global."""
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
    assert not _mentions_symbol(body, "PRIMES"), \
        "the local shadows the constant; the global must not be read"


def test_read_is_zero_copy(tmp_path):
    """Reading a constant GEPs the global -- it does NOT materialise a local copy."""
    src = _PRIMES + (
        "fn read() i32:\n"
        "    return Result.Ok(PRIMES[1])\n"
        "\n"
        "fn main() i32:\n"
        "    println(read().realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    body = _function_body(_emit_ir(tmp_path, src), "read")
    assert _mentions_symbol(body, "PRIMES")
    assert 'alloca [3 x i32]' not in body, "the constant must not be copied into a local"
    assert 'memcpy' not in body, "the constant must not be copied into a local"
