"""The monomorphize step reports its refusals in source order, under every hash seed (#899).

The instantiate pass collects each instantiation into a set, and the monomorphizer walked
that set, so a CE2090, a CE4006 or a CE2062 came out in the hash order of the type
tuples. One program gave a different output from one run to the next. The step now
orders its work by the first site that named each instantiation.

`PYTHONHASHSEED` is fixed per process, so each seed runs in its own subprocess.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEEDS = ("0", "1", "2", "3", "4", "5", "6", "7")

FUNCTION_CONSTRAINTS = """perk Named:
    fn name() string

extend i32 with Named:
    fn name() string:
        return "i32"

fn many@(...Ts: Named)(...Ts xs) ~:
    expand(a in xs):
        println(a.name())
    return Result.Ok(~)

fn one@(T: Named)(T x) ~:
    return Result.Ok(~)

fn main() i32:
    many(5, true)
    one(true)
    return Result.Ok(0)
"""

TYPE_CONSTRAINTS = """perk Named:
    fn name() string

extend i32 with Named:
    fn name() string:
        return "i32"

struct Box@(T: Named):
    T v

struct Bag@(T: Named):
    T v

fn main() i32:
    let Box@(bool) a = Box(true)
    let Bag@(f64) b = Bag(1.5)
    let Box@(u8) c = Box(3 as u8)
    return Result.Ok(0)
"""

TYPE_ARITY = """struct Box@(T):
    T v

struct Bag@(T, U):
    T v

fn main() i32:
    let Box@(i32, bool) a = Box(1)
    let Bag@(f64) b = Bag(1.5)
    let Box@(u8, u8, u8) c = Box(3 as u8)
    return Result.Ok(0)
"""

PROGRAMS = {
    "function_constraints": (FUNCTION_CONSTRAINTS, [["CE2090", 17], ["CE4006", 18]]),
    "type_constraints": (TYPE_CONSTRAINTS, [["CE4006", 15], ["CE4006", 16], ["CE4006", 17]]),
    "type_arity": (TYPE_ARITY, [["CE2062", 8], ["CE2062", 9], ["CE2062", 10]]),
}

_ANALYZE = """
import json, pathlib, sys, tempfile
from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
from sushi_lang.semantics.units import UnitManager

answers = {}
for name, text in json.loads(sys.stdin.read()).items():
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "main.sushi").write_text(text, encoding="utf-8")
    reporter = Reporter(source=text, filename="main")
    program, _tree = parse_to_ast(text, reporter=reporter)
    get_stdlib_registry()
    units = UnitManager(root_path=root, reporter=reporter)
    units.load_unit("main", program)
    units.get_compilation_order()
    try:
        SemanticAnalyzer(reporter, filename="main", unit_manager=units).check()
    except ValueError:
        pass
    answers[name] = [[d.code, d.span.line if d.span else None]
                     for d in reporter.items if d.kind == "error"]
print(json.dumps(answers))
"""


def _errors_under(seed: str) -> dict:
    """The (code, line) list of every program, analyzed in one process under `seed`."""
    sources = {name: text for name, (text, _expected) in PROGRAMS.items()}
    env = {**os.environ, "PYTHONHASHSEED": seed,
           "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")]))}
    result = subprocess.run(
        [sys.executable, "-c", _ANALYZE], input=json.dumps(sources),
        cwd=REPO, capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_every_seed_gives_the_source_order():
    expected = {name: codes for name, (_text, codes) in PROGRAMS.items()}
    answers = {seed: _errors_under(seed) for seed in SEEDS}
    wrong = {seed: got for seed, got in answers.items() if got != expected}
    assert not wrong, f"expected {expected}, got {wrong}"
