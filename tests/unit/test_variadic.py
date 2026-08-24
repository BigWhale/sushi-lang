"""Unit tests for native variadic '...T' internals the .sushi corpus cannot pin."""
from __future__ import annotations

from sushi_lang.internals.report import Reporter

# Reuse the production IR-emit + function-body helpers from the FFI unit tests.
from tests.unit.test_ffi import (
    _emit_ir,
    _count_in_function,
    _ensure_newline,
    _make_unit,
    _StubAnalyzer,
)


def test_variadic_array_freed_exactly_once_in_callee(tmp_path):
    """The synthesized variadic T[] is freed exactly once, inside the callee."""
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

    # Emits and returns. The raise this used to assert reached the top-level guard and
    # printed a spurious CE0000 over the diagnostic (#436).
    gen._extract_public_functions([unit])
    assert any(item.code == "CE0116" for item in reporter.items)


# E2 (#71) coverage note: the instantiate pass's inference of an enum/struct-constructor DotCall
# pack argument (`Color.Red()`) is done by the typecheck pass's shared inferrer. The three unit tests
# that used to pin the thin parallel inferrer's DotCall arm directly were removed with that
# inferrer in #214; the behaviour is covered by test_shared_type_arg_inference.py,
# test_pack_inference.py, and the end-to-end pack tests under tests/generics/.
