"""Unit tests for native variadic '...T' internals the .sushi corpus cannot pin.

Covers:
- The move handshake / no-leak property: the synthesized variadic T[] is freed
  exactly once -- inside the callee (RAII), and NOT in the caller (which moved it).
- CE0116: a public variadic function aborts the .slib manifest.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter

# Reuse the production IR-emit + function-body helpers from the FFI unit tests.
from tests.unit.test_ffi import (
    _emit_ir,
    _function_body,
    _count_in_function,
    _ensure_newline,
    _make_unit,
    _StubAnalyzer,
)


def test_variadic_array_freed_exactly_once_in_callee(tmp_path):
    """The synthesized variadic T[] is freed exactly once, inside the callee.

    The caller synthesizes an owned i32[] from the trailing args and *moves* it into
    the callee, marking its temp moved so the caller does NOT free it. The callee owns
    the array and frees it via the normal dynamic-array RAII drain at scope exit.

    Guards the move handshake: exactly one free total, and it lives in `sum`, not in
    `main`/`user_main`. A double-free (caller also freeing) or a leak (no free) both
    break this.
    """
    src = (
        "fn sum(...i32 nums) i32:\n"
        "    let i32 total = 0\n"
        "    foreach(n in nums.iter()):\n"
        "        total := total + n\n"
        "    return Result.Ok(total)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 s = sum(1, 2, 3).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)

    # Exactly one free in the whole module: the callee's RAII cleanup.
    total_frees = ir_text.count('call void @"free"')
    assert total_frees == 1, f"expected exactly one free (no double-free, no leak), got {total_frees}"

    # That free lives in the callee `sum`, not in the caller `user_main`/`main`.
    frees_in_sum = _count_in_function(ir_text, "sum", 'call void @"free"')
    assert frees_in_sum == 1, f"callee must free the moved array, got {frees_in_sum}"

    frees_in_main = _count_in_function(ir_text, "user_main", 'call void @"free"')
    assert frees_in_main == 0, (
        f"caller must NOT free the moved array (it was moved into the callee), "
        f"got {frees_in_main}"
    )


def test_empty_variadic_call_has_no_free_in_caller(tmp_path):
    """A zero-arg variadic call still moves an (empty) array; caller frees nothing."""
    src = (
        "fn count(...i32 nums) i32:\n"
        "    return Result.Ok(nums.len() as i32)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 c = count().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees_in_main = _count_in_function(ir_text, "user_main", 'call void @"free"')
    assert frees_in_main == 0, f"caller must not free the moved (empty) array, got {frees_in_main}"


def test_ce0116_public_variadic_aborts_manifest(tmp_path):
    """A public native variadic function cannot be exported through a .slib API."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable

    src = (
        "public fn sum(...i32 nums) i32:\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    reporter = Reporter(source=_ensure_newline(src), filename="lib")
    unit = _make_unit(tmp_path, src)

    gen = LibraryManifestGenerator(_StubAnalyzer(reporter, StructTable(), EnumTable()))

    with pytest.raises(ValueError):
        gen._extract_public_functions([unit])
    assert any(item.code == "CE0116" for item in reporter.items)


# ---------------------------------------------------------------------------
# E2 (#71): Pass 1.5 must infer the type of an enum/struct-constructor *DotCall*
# pack argument. `Color.Red()` parses to a DotCall (not yet an EnumConstructor),
# so without a DotCall case the shared inferrer returns None and the pack
# instantiation is never registered -> Pass 2 raises CE2061. These tests pin the
# shared inferrer directly; they fail (return None) before the fix.
# ---------------------------------------------------------------------------

def _make_inferrer(struct_table=None, enum_table=None):
    from sushi_lang.semantics.generics.instantiate.types import TypeInferrer
    return TypeInferrer(
        variable_types={},
        struct_table=struct_table or {},
        enum_table=enum_table or {},
    )


def _dotcall(receiver_name, method):
    from sushi_lang.semantics.ast import DotCall, Name
    return DotCall(loc=None, receiver=Name(loc=None, id=receiver_name), method=method, args=[])


def test_infer_enum_constructor_dotcall_arg():
    """`Color.Red()` (a DotCall) infers to the Color enum type, not None."""
    from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo

    color = EnumType(
        name="Color",
        variants=(
            EnumVariantInfo(name="Red", associated_types=()),
            EnumVariantInfo(name="Green", associated_types=()),
        ),
    )
    inferrer = _make_inferrer(enum_table={"Color": color})
    assert inferrer.infer_simple_expr_type(_dotcall("Color", "Red")) == color


def test_infer_struct_constructor_dotcall_arg():
    """A struct-constructor DotCall (e.g. via a static-like form) infers the struct type."""
    from sushi_lang.semantics.typesys import StructType, BuiltinType

    point = StructType(name="Point", fields=(("x", BuiltinType.I32), ("y", BuiltinType.I32)))
    inferrer = _make_inferrer(struct_table={"Point": point})
    assert inferrer.infer_simple_expr_type(_dotcall("Point", "new")) == point


def test_infer_unknown_receiver_dotcall_still_none():
    """A DotCall whose receiver is not a known enum/struct stays uninferable (None)."""
    inferrer = _make_inferrer()
    assert inferrer.infer_simple_expr_type(_dotcall("Unknown", "thing")) is None
