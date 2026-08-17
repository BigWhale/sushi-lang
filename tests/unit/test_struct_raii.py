"""Regression tests for #60: struct value-semantics for heap-owning structs."""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function


_STRUCT = (
    "struct DataBuffer:\n"
    "    u8[] data\n"
    "    i32 size\n"
    "\n"
)


def test_struct_get_borrows_and_does_not_copy(tmp_path):
    """`let b = arr.get(i)??` BORROWS the element. It does not copy it (#242)."""
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
    """A `nom` struct param with a `T[]` field is freed by the callee at scope exit."""
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
    """The twin, and the flip itself: an UNMARKED struct param is a borrow."""
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
    """The call site MOVES a bare owning struct arg into a `nom` slot (#134)."""
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
    """`let b = a` MOVES an owning struct (#134): no implicit clone of its buffer."""
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
    """if/else, each branch returns: a struct-with-array-field local is freed on every path."""
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
