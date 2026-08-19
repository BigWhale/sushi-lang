"""The HashMap key gate runs for `new`, and the map hashes keys through a perk override.

Issue #272: CE2054 was dead code -- `HashMap.new()` never reached
`_validate_hashmap_new`, and the gate read the backend-populated builtin registry,
which is empty during Pass 2. The .sushi twins in tests/stdlib/generics/hashmap/
pin the diagnostics; this file pins the half stdout cannot show -- that the map's
own key hashing calls the perk implementation instead of the derived inline hash.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SUSHIC = shutil.which("sushic")

PERK_KEY_PROGRAM = """use <collections/hashmap>

perk Hashable:
    fn hash() u64

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return (self.x * 31 + self.y) as u64

fn main() i32:
    let HashMap@(Point, string) m = HashMap.new()
    m.insert(Point(1, 2), "one")
    println(m.get(Point(1, 2)).realise("missing"))
    m.free()
    return Result.Ok(0)
"""


@pytest.mark.skipif(SUSHIC is None, reason="sushic not on PATH (run under `uv run pytest`)")
def test_hashmap_key_hashing_calls_the_perk_implementation(tmp_path):
    """The program never calls .hash() directly, so any call to Point_hash in the IR
    can only come from the map's own key hashing. The derived struct hash is emitted
    inline (no named function), which is what makes the assertion able to tell the
    two apart."""
    (tmp_path / "main.sushi").write_text(PERK_KEY_PROGRAM, encoding="utf-8")
    compiled = subprocess.run(
        ["sushic", "--write-ll", "main.sushi", "-o", "out"],
        cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    ll = (tmp_path / "out.ll").read_text(encoding="utf-8")
    called = re.search(r'call[^\n]*Point_hash', ll)
    assert called, (
        "the map's key hashing did not call the perk hash implementation -- "
        "get_key_hash_method resolved the key's hash without consulting the perk table")
