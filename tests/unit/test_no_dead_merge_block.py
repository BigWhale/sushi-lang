"""An `if` or a `match` whose every arm returns leaves no merge block (#849).

CE0107 refuses a body that can reach its end, so every path of a `fn` body ends in a
`return`. The `if` and `match` emitters used to append the merge block before they knew
whether an arm falls through. No branch reached it, and the function emitter closed it
with `emit_default_return`: a `Result.Err` that no source line produces. The emitters
now remove a merge block that no arm branches to, and `emit_default_return` is an
internal error that no program reaches (maintainer ruling, 2026-09-24: there is no
implicit `Result.Ok(~)`).
"""
from __future__ import annotations

import re
import subprocess
from types import SimpleNamespace

import pytest

from sushic_path import SUSHIC, needs_sushic
from sushi_lang.backend.functions.helpers import FunctionHelpers
from sushi_lang.internals.diagnostics import InternalCompilerError

PROGRAM = """enum Sign:
    Minus
    Plus

fn sign(i32 x) i32:
    if (x < 0):
        return Result.Ok(-1)
    else:
        return Result.Ok(1)

fn nested(i32 x) i32:
    if (x < 0):
        if (x < -10):
            return Result.Ok(-2)
        else:
            return Result.Ok(-1)
    elif (x == 0):
        return Result.Ok(0)
    else:
        return Result.Ok(1)

fn pick(Sign s) i32:
    match s:
        Sign.Minus ->
            return Result.Ok(-1)
        Sign.Plus ->
            if (true):
                return Result.Ok(1)
            else:
                return Result.Ok(2)

fn digit(i32 x) i32:
    match x:
        0 ->
            return Result.Ok(0)
        _ ->
            return Result.Ok(1)

fn main() i32:
    let i32 a = sign(5).realise(9)
    let i32 b = nested(-20).realise(9)
    let i32 c = pick(Sign.Plus).realise(9)
    let i32 d = digit(3).realise(9)
    println("{a} {b} {c} {d}")
    if (a > 0):
        return Result.Ok(0)
    else:
        return Result.Ok(1)
"""

FUNCTIONS = ("sign", "nested", "pick", "digit", "user_main")

_DEFINE = re.compile(r'^define\s.*?@"?([^"(]+)"?\(')
_LABEL = re.compile(r'^"?([^":]+)"?:')
_TARGET = re.compile(r'label %"?([^",\s\]]+)"?')


def _blocks_without_predecessor(ir_text: str) -> dict[str, list[str]]:
    """Per function: every block after the first that no branch in it names."""
    found: dict[str, list[str]] = {}
    function = None
    blocks: list[str] = []
    targets: set[str] = set()
    for line in ir_text.splitlines():
        define = _DEFINE.match(line)
        if define is not None:
            function, blocks, targets = define.group(1), [], set()
            continue
        if function is None:
            continue
        if line.startswith("}"):
            found[function] = [b for b in blocks[1:] if b not in targets]
            function = None
            continue
        label = _LABEL.match(line)
        if label is not None:
            blocks.append(label.group(1))
            continue
        targets.update(_TARGET.findall(line))
    return found


def _user_function(found: dict[str, list[str]], name: str) -> list[str]:
    for symbol, dead in found.items():
        if symbol == name or symbol.endswith(f"${name}"):
            return dead
    raise AssertionError(f"the module defines no {name}: {sorted(found)}")


@needs_sushic
def test_no_function_keeps_a_dead_merge_block(tmp_path):
    (tmp_path / "main.sushi").write_text(PROGRAM, encoding="utf-8")
    built = subprocess.run(
        [SUSHIC, "--write-ll", "--opt", "none", "--no-incremental", "main.sushi", "-o", "out"],
        cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stdout + built.stderr
    found = _blocks_without_predecessor((tmp_path / "out.ll").read_text(encoding="utf-8"))
    dead = {name: _user_function(found, name) for name in FUNCTIONS}
    assert all(not blocks for blocks in dead.values()), dead
    ran = subprocess.run([str(tmp_path / "out")], capture_output=True, text=True, timeout=60)
    assert ran.stdout == "1 -2 1 1\n", ran.stdout + ran.stderr


def test_the_detector_sees_a_dead_block():
    """The control: a detector that finds nothing would pass the row above for free."""
    text = ('define i32 @"u$f"()\n{\nentry:\n  br label %"start"\nstart:\n'
            '  ret i32 0\nif.end:\n  ret i32 1\n}\n')
    assert _blocks_without_predecessor(text) == {"u$f": ["if.end"]}


def test_the_default_return_is_an_internal_error():
    helpers = FunctionHelpers(SimpleNamespace())  # type: ignore[arg-type]
    fn = SimpleNamespace(name="sign", ret=object())
    with pytest.raises(InternalCompilerError) as caught:
        helpers.emit_default_return(fn)  # type: ignore[arg-type]
    assert caught.value.code == "CE0015"
