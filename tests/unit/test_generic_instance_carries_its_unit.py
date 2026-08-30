"""A monomorphized instance's SYMBOL starts with its declaring unit's prefix (#495).

This is the gate that keeps the epic from regressing, and it reads the EMITTED
symbol, not a table: a wrong instance symbol is a silent miscompile (#494), so the
assertion stands where the miscompile would. Two units each declare a private
`twin@(T)` and instantiate it at i32; the two instances share the mangled base
`twin__i32` and must emit as two unit-prefixed symbols.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from sushic_path import SUSHIC, needs_sushic


HELPER_SRC = (
    "fn twin@(T)(nom T x) T:\n"
    "    println(\"helper twin\")\n"
    "    return Result.Ok(x)\n"
    "\n"
    "public fn twin_use() i32:\n"
    "    return Result.Ok(twin(nom 1)??)\n"
)

MAIN_SRC = (
    'use "helper"\n'
    "\n"
    "fn twin@(T)(nom T x) T:\n"
    "    println(\"main twin\")\n"
    "    return Result.Ok(x)\n"
    "\n"
    "fn main() i32:\n"
    "    let i32 a = twin(nom 7).realise(0)\n"
    "    let i32 b = twin_use().realise(0)\n"
    "    println(\"{a} {b}\")\n"
    "    return Result.Ok(0)\n"
)


@needs_sushic
def test_every_instance_symbol_starts_with_its_unit(tmp_path: Path):
    (tmp_path / "helper.sushi").write_text(HELPER_SRC, encoding="utf-8")
    (tmp_path / "main.sushi").write_text(MAIN_SRC, encoding="utf-8")

    result = subprocess.run(
        [SUSHIC, "main.sushi", "--no-incremental", "--write-ll", "-o", "out"],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "NO_COLOR": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr

    ll = (tmp_path / "out.ll").read_text(encoding="utf-8")
    defined = re.findall(r'^define[^@]*@"?([^"(]+)"?\(', ll, flags=re.MULTILINE)
    instances = sorted(name for name in defined if name.endswith("twin__i32"))

    assert instances == ["helper$twin__i32", "main$twin__i32"], (
        f"an instance symbol lost its unit prefix: {instances}"
    )

    run = subprocess.run(["./out"], cwd=tmp_path, capture_output=True, text=True)
    assert run.stdout == "main twin\nhelper twin\n7 1\n", run.stdout
