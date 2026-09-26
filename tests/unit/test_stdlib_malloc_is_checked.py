"""A stdlib allocation is checked before its pointer is used (#931).

`emit_checked_malloc` (`sushi_stdlib/src/string_helpers.py`) is the one seam: it calls
`malloc`, compares the answer with `null`, and branches to the one RE2021 block of the
function. This gate generates the IR of every stdlib bitcode unit and reads each
`call @malloc` in it: the first instruction that uses the answer must be the `icmp eq`
with `null`.

`UNCHECKED` names the functions that still allocate with no check. It is a ratchet: it may
only shrink, and a name that is checked now must go.
"""
from __future__ import annotations

import importlib
import re

from sushi_lang.sushi_stdlib.build import STDLIB_BITCODE_UNITS

# The string methods of collections/strings/methods/ still call malloc directly, and
# allocate_and_copy_bytes stays unchecked while string_char_at names its calling block
# in a phi.
UNCHECKED: frozenset[str] = frozenset({
    "string_cap",
    "string_char_at",
    "string_concat",
    "string_join",
    "string_pad_left",
    "string_pad_right",
    "string_repeat",
    "string_replace",
    "string_reverse",
    "string_s",
    "string_sleft",
    "string_split",
    "string_sright",
    "string_ss",
    "string_strip_prefix",
    "string_strip_suffix",
    "string_tleft",
    "string_to_bytes",
    "string_to_f64",
    "string_to_i32",
    "string_to_i64",
    "string_tright",
    "string_trim",
})

_DEFINE = re.compile(r'^define .*?@"?([A-Za-z0-9_.$]+)"?\(')
_MALLOC = re.compile(r'^\s*(%"[^"]+"|%[A-Za-z0-9_.$]+) = call i8\* @"?malloc"?\(')


def _functions(text: str) -> dict[str, list[str]]:
    bodies: dict[str, list[str]] = {}
    name = None
    for line in text.splitlines():
        match = _DEFINE.match(line)
        if match:
            name = match.group(1)
            bodies[name] = []
        elif line.startswith("}"):
            name = None
        elif name is not None:
            bodies[name].append(line)
    return bodies


def _uses(line: str, value: str) -> bool:
    return re.search(re.escape(value) + r'(?![A-Za-z0-9_.$"])', line) is not None


def _first_use_is_a_null_check(body: list[str], index: int, value: str) -> bool:
    for line in body[index + 1:]:
        if _uses(line, value):
            return re.match(r'^\s*%\S+ = icmp eq i8\* ' + re.escape(value) + r', null$',
                            line) is not None
    return False


def _unchecked_functions() -> set[str]:
    found: set[str] = set()
    for row in STDLIB_BITCODE_UNITS:
        text = str(importlib.import_module(row.generator).generate_module_ir())
        for name, body in _functions(text).items():
            for index, line in enumerate(body):
                match = _MALLOC.match(line)
                if match and not _first_use_is_a_null_check(body, index, match.group(1)):
                    found.add(name)
    return found


def test_the_gate_sees_a_malloc_call():
    text = str(importlib.import_module(STDLIB_BITCODE_UNITS[0].generator).generate_module_ir())
    assert any(_MALLOC.match(line) for line in text.splitlines())


def test_every_stdlib_malloc_is_checked_before_use():
    found = _unchecked_functions()
    assert found <= UNCHECKED, sorted(found - UNCHECKED)
    assert UNCHECKED <= found, f"checked now, remove from UNCHECKED: {sorted(UNCHECKED - found)}"
