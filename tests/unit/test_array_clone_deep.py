"""Regression tests for #158: `Array.clone()` must deep-copy owning elements.

`emit_dynamic_array_clone` copied elements with a raw per-element load/store, so a cloned
owning element (a struct with a `T[]` field, a heap-payload enum, a string) shared the
source element's buffer. Both arrays then freed it at scope exit and the process aborted
with a double free. The fix routes the per-element copy through `emit_value_clone` by
delegating to `clone_dynamic_array_value`.

These assert behaviour by counting `malloc` in the generated IR (the bug is a latent
double-free, mirroring the #59/#60 approach in test_raii_multi_exit.py / test_struct_raii.py):
cloning an owning element must allocate the array buffer *and* an independent buffer per
element, while a copyable element must allocate the array buffer only.
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function

_MALLOC = 'call i8* @"malloc"'


def test_clone_deep_copies_owning_struct_elements(tmp_path):
    """`.clone()` of a `Buf[]` (Buf owns an `i32[]`) allocates per element, not just the buffer.

    Before the fix `cloner` had exactly one malloc -- the new array buffer -- and the element
    copy was a shallow load/store, so clone and source shared each `data` buffer.
    """
    src = (
        "struct Buf:\n"
        "    i32[] data\n"
        "\n"
        "fn cloner(Buf[] a) i32:\n"
        "    let Buf[] b = a.clone()\n"
        "    return Result.Ok(b.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let Buf[] a = from([Buf(data: from([1, 2]))])\n"
        "    let i32 n = cloner(a).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "cloner", _MALLOC)
    assert mallocs >= 2, (
        "clone of an owning-struct element must allocate the array buffer AND an "
        f"independent element buffer, got {mallocs} mallocs (shallow copy)"
    )


def test_clone_deep_copies_owning_enum_elements(tmp_path):
    """`.clone()` of an array of a recursive owning enum deep-copies each variant payload."""
    src = (
        "enum Msg:\n"
        "    Num(i32)\n"
        "    Arr(Msg[])\n"
        "\n"
        "fn cloner(Msg[] a) i32:\n"
        "    let Msg[] b = a.clone()\n"
        "    return Result.Ok(b.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let Msg[] a = from([Msg.Arr(from([Msg.Num(1)])), Msg.Num(2)])\n"
        "    let i32 n = cloner(a).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "cloner", _MALLOC)
    assert mallocs >= 2, (
        "clone of a heap-payload enum element must deep-copy the payload, "
        f"got {mallocs} mallocs (shallow copy)"
    )


def test_clone_of_copyable_elements_allocates_only_the_buffer(tmp_path):
    """`.clone()` of an `i32[]` allocates once: a primitive element needs no per-element copy.

    Guards the other direction -- routing through the deep-clone helper must not start
    allocating for element types whose shallow copy is already correct.
    """
    src = (
        "fn cloner(i32[] a) i32:\n"
        "    let i32[] b = a.clone()\n"
        "    return Result.Ok(b.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let i32[] a = from([1, 2])\n"
        "    let i32 n = cloner(a).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "cloner", _MALLOC)
    assert mallocs == 1, f"clone of a primitive element array must allocate once, got {mallocs}"
