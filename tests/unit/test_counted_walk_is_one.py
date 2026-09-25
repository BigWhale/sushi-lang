"""A counted walk over contiguous elements is `emit_container_walk` (#834).

`generics/container_walk.py` is the one `data[0..count)` walk: the index slot in the entry
block, an unsigned compare, the null guard and the visit predicate. A backend function that
builds a loop of its own -- a `*_cond` (or `*_head`) block, a `*_body` block and an index
compare -- is a copy of it, and a copy drifts (a signed compare, an index slot in the
wrong block). So such a function is refused outside `container_walk.py` and the list
below.

KNOWN_HAND_LOOPS may only get SHORTER. It holds the loops that cannot be a walk, each with
its reason above its row (#852): a loop with no count, a decoder with a stride other than one
element, and a user loop body that needs `break` / `continue` targets.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

WALK_MODULE = "generics/container_walk.py"

KNOWN_HAND_LOOPS = {
    # Not a walk: it stops at the NUL byte of a C string, and no count is known.
    ("runtime/strings.py", "_declare_and_define_utf8_char_count"),
    # A user loop body: `break`/`continue` need loop blocks that the walk does not expose.
    ("statements/loops.py", "_emit_hashmap_foreach"),
    # Not a walk: it steps by the byte count of each code point and stops at the first bad one.
    ("types/arrays/methods/utf8_validate.py", "get_or_emit_utf8_validate"),
}


def _block_name_tail(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, str):
            return last.value
    return None


def _is_hand_loop(fn: ast.AST) -> bool:
    names: list[str] = []
    compares = False
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "append_basic_block":
            arg = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg == "name"), None)
            tail = _block_name_tail(arg) if arg is not None else None
            if tail:
                names.append(tail)
        elif node.func.attr in ("icmp_unsigned", "icmp_signed"):
            compares = True
    return (compares
            and any(n.endswith(("_cond", "_head")) for n in names)
            and any(n.endswith("_body") for n in names))


def _hand_loops(source: str) -> list[str]:
    tree = ast.parse(source)
    return [fn.name for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_hand_loop(fn)]


def _all_hand_loops() -> set[tuple[str, str]]:
    found = set()
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel == WALK_MODULE:
            continue
        found.update((rel, name) for name in _hand_loops(path.read_text()))
    return found


def test_no_new_hand_written_counted_loop():
    extra = sorted(_all_hand_loops() - KNOWN_HAND_LOOPS)
    assert not extra, (
        "walk contiguous elements with generics/container_walk.emit_container_walk:\n"
        + "\n".join(f"{rel}: {name}" for rel, name in extra))


def test_the_known_hand_loops_still_exist():
    gone = sorted(KNOWN_HAND_LOOPS - _all_hand_loops())
    assert not gone, (
        "these loops are gone; delete their rows from KNOWN_HAND_LOOPS:\n"
        + "\n".join(f"{rel}: {name}" for rel, name in gone))


def test_the_gate_sees_a_hand_loop():
    source = '''
def copy(b, n):
    head = b.append_basic_block(f"{p}_loop_head")
    body = b.append_basic_block(name="copy_body")
    b.icmp_signed("<", i, n)
'''
    assert _hand_loops(source) == ["copy"]
