"""Regression tests for #60: struct value-semantics for heap-owning structs.

A struct that owns heap memory (a dynamic-array `T[]` field) must get an *independent*
buffer whenever it is copied -- taken out of an array via `.get()`/indexing, or passed
by value to a function -- so that exactly one owner frees each allocation. Before the
fix the copy shallow-shared the element's buffer (double-ownership), and by-value struct
parameters were never freed by the callee at all.

These assert behaviour by counting `malloc`/`free` in the generated IR (the bug is a
silent leak / latent double-free at runtime, mirroring the #59 approach in
test_raii_multi_exit.py).
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function


_STRUCT = (
    "struct DataBuffer:\n"
    "    u8[] data\n"
    "    i32 size\n"
    "\n"
)


def test_struct_get_borrows_and_does_not_copy(tmp_path):
    """`let b = arr.get(i)??` BORROWS the element. It does not copy it (#242).

    The array keeps the element and still frees it, so `b` names the same buffer and owns
    nothing. `extract` therefore allocates NOTHING: it only reads a length through the
    borrow.

    This asserted the opposite until #242. `.get()` used to deep-copy at the read, which
    was the compiler inserting a copy the user did not ask for -- and the copy was the
    only thing that made a second owner safe. Now there is no second owner.
    """
    src = _STRUCT + (
        "fn extract(DataBuffer[] bufs) i32:\n"
        "    let DataBuffer b = bufs.get(0)??\n"
        "    return Result.Ok(b.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer[] buffers = from([DataBuffer(from([1 as u8, 2 as u8]), 2)])\n"
        "    let i32 n = extract(buffers).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "extract", "malloc")
    assert mallocs == 0, f"`.get()` must borrow, not copy, got {mallocs} mallocs"


def test_struct_index_borrows_and_does_not_copy(tmp_path):
    """`let b = arr[i]` BORROWS the element. It does not copy it (#242)."""
    src = _STRUCT + (
        "fn extract(DataBuffer[] bufs) i32:\n"
        "    let DataBuffer b = bufs[0]\n"
        "    return Result.Ok(b.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer[] buffers = from([DataBuffer(from([1 as u8, 2 as u8]), 2)])\n"
        "    let i32 n = extract(buffers).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "extract", "malloc")
    assert mallocs == 0, f"`arr[i]` must borrow, not copy, got {mallocs} mallocs"


def test_nom_struct_param_freed_by_callee(tmp_path):
    """A `nom` struct param with a `T[]` field is freed by the callee at scope exit.

    The callee is the one owner, so it must free it. Before the fix struct parameters
    were never registered for cleanup and the callee emitted zero frees.
    """
    src = _STRUCT + (
        "fn consume(nom DataBuffer d) i32:\n"
        "    return Result.Ok(d.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer x = DataBuffer(from([1 as u8, 2 as u8, 3 as u8]), 3)\n"
        "    let i32 n = consume(nom x).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "consume", '@"free"')
    assert frees >= 1, f"callee must free its `nom` struct param's buffer, got {frees} frees"


def test_borrow_struct_param_not_freed_by_callee(tmp_path):
    """The twin, and the flip itself: an UNMARKED struct param is a borrow.

    The caller keeps the value and frees it, so the callee must emit no free at all. The
    two functions differ by one word (docs/design/borrow-model.md S4).
    """
    src = _STRUCT + (
        "fn look(DataBuffer d) i32:\n"
        "    return Result.Ok(d.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer x = DataBuffer(from([1 as u8, 2 as u8, 3 as u8]), 3)\n"
        "    let i32 n = look(x).realise(0)\n"
        "    return Result.Ok(x.data.len())\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "look", '@"free"')
    assert frees == 0, f"a borrow parameter must never be freed by the callee, got {frees}"


def test_byvalue_struct_arg_moved_at_call_site(tmp_path):
    """The call site MOVES a bare owning struct arg into a `nom` slot (#134).

    Pairs with test_nom_struct_param_freed_by_callee: the callee frees the moved-in
    value, and the caller marks the source moved so it does not free it too. Asserted
    relatively: passing the bare Name `consume(x)` (a move) emits FEWER mallocs in `main`
    than passing an explicit `consume(x.clone())` (a copy) -- the move elides the call-site
    deep copy of the struct's `u8[]` buffer. Before #134 the bare-Name form also copied, so
    the two counts were equal.
    """
    body = (
        "fn consume(nom DataBuffer d) i32:\n"
        "    return Result.Ok(d.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer x = DataBuffer(from([1 as u8, 2 as u8, 3 as u8]), 3)\n"
        "    let i32 n = consume(nom {arg}).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    move_mallocs = _count_in_function(_emit_ir(tmp_path, _STRUCT + body.format(arg="x")), "user_main", "malloc")
    clone_mallocs = _count_in_function(_emit_ir(tmp_path, _STRUCT + body.format(arg="x.clone()")), "user_main", "malloc")
    assert move_mallocs < clone_mallocs, (
        f"bare owning struct arg must MOVE (fewer mallocs than an explicit clone): "
        f"move={move_mallocs}, clone={clone_mallocs}"
    )


def test_struct_rebind_moves_not_clones(tmp_path):
    """`let b = a` MOVES an owning struct (#134): no implicit clone of its buffer.

    Compared to the explicit `let b = a.clone()`, the plain rebind emits fewer mallocs in
    `main` -- the deep copy of the struct's `u8[]` buffer is elided (`a` is consumed
    instead). Before #134 the plain rebind also cloned, so the two counts were equal.
    """
    body = (
        "fn main() i32:\n"
        "    let DataBuffer a = DataBuffer(from([1 as u8, 2 as u8, 3 as u8]), 3)\n"
        "    let DataBuffer b = {rhs}\n"
        "    return Result.Ok(b.size)\n"
    )
    move_m = _count_in_function(_emit_ir(tmp_path, _STRUCT + body.format(rhs="a")), "user_main", "malloc")
    clone_m = _count_in_function(_emit_ir(tmp_path, _STRUCT + body.format(rhs="a.clone()")), "user_main", "malloc")
    assert move_m < clone_m, f"`let b = a` must MOVE (fewer mallocs than a.clone()): move={move_m}, clone={clone_m}"


def test_struct_local_freed_on_every_branch_return(tmp_path):
    """if/else, each branch returns: a struct-with-array-field local is freed on every path.

    Completes the #59 every-path RAII for struct-with-array-field locals (was blocked by
    the #60 aliasing). Before the fix the global-once gate emitted the struct cleanup on
    only one path.
    """
    src = _STRUCT + (
        "fn f(i32 t) i32:\n"
        "    let DataBuffer d = DataBuffer(from([1 as u8, 2 as u8]), 2)\n"
        "    if (t == 1):\n"
        "        return Result.Ok(1)\n"
        "    else:\n"
        "        return Result.Ok(2)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f(2).realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", '@"free"')
    assert frees >= 2, f"struct-with-array-field local must be freed on both branch returns, got {frees}"
