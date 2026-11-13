"""The backend narrows the i64 of an index position to an i32 only in `LLVMUtils`.

An index position arrives as an i64 (docs/adr/0001). A bare `trunc` of it to i32 is not
sound: 2^32 reads as 0. `backend/llvm_utils.py` holds the three narrowings:
`index_as_i32` (a saturation), `count_as_i32` (a trap above the i32 range, RE2033) and
`checked_as_i32` (a truncation of a value that a bounds check proved). So a `trunc` call
outside that module is refused, except in the functions below.

KNOWN_TRUNCS may only get SHORTER. Each row is a truncation of a value that is no index
position, with its reason above it.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

SEAM_MODULE = "llvm_utils.py"

KNOWN_TRUNCS = {
    # A hash: the low 32 bits of a 64-bit hash pick the bucket.
    ("generics/hashmap/types.py", "emit_key_hash_i32"),
    # The answer of a bit count (`count_ones` and its siblings), at most 64.
    ("types/primitives/bit_ops.py", "_count_to_i32"),
    # The amount of a shift, which the caller compared with the width first.
    ("expressions/operators.py", "_emit_shift"),
    # The hash of an integer wider than 64 bits, cut to u64.
    ("types/primitives/hashing.py", "emitter"),
    # The needle of a search, cut to the element type that the typecheck pass allowed.
    ("types/arrays/methods/search.py", "_normalize_needle"),
    # An `as` cast: a truncation is the rule of the cast.
    ("expressions/casts.py", "cast_int_to_int"),
    # The size of a type, which is a small constant.
    ("expressions/memory.py", "get_element_size_constant"),
    # The i64 answer of a `main`, cut to the C `int` of the exit code.
    ("functions/main_wrapper.py", "_return_exit_code"),
    # The i64 counter of a range as a value, cut to the item type: the item of a range is in
    # the range of its type.
    ("runtime/iterators.py", "body"),
    # The stored i64 size of a string, or length or capacity of a `T[]` or a `List@(T)`,
    # read as the surface i32 of `size()`, `len()` and `capacity()`. Each write extends an
    # i32, so the value is in the i32 range (the expand step of docs/adr/0001; the contract
    # step removes the row).
    ("stored_width.py", "as_surface_i32"),
}


def _truncs(source: str) -> set[str]:
    """The names of the functions that hold a `.trunc(` call; the innermost one."""
    found: set[str] = set()

    def visit(node: ast.AST, owner: str | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "trunc"):
            found.add(owner or "<module>")
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(ast.parse(source), None)
    return found


def _all_truncs() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for path in BACKEND.rglob("*.py"):
        relative = path.relative_to(BACKEND).as_posix()
        if relative == SEAM_MODULE:
            continue
        rows |= {(relative, name) for name in _truncs(path.read_text(encoding="utf-8"))}
    return rows


def test_no_trunc_outside_the_narrowing_seam():
    extra = _all_truncs() - KNOWN_TRUNCS
    assert not extra, (
        "a trunc outside backend/llvm_utils.py: narrow an index position with "
        f"index_as_i32, count_as_i32 or checked_as_i32: {sorted(extra)}")


def test_the_known_list_has_no_stale_row():
    stale = KNOWN_TRUNCS - _all_truncs()
    assert not stale, f"remove the rows that hold no trunc any more: {sorted(stale)}"
