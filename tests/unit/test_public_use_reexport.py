"""`public use` re-exports (#586): the identity gate, and the binary-library limit.

`docs/design/unit-namespaces.md` section 8.1 (Ruling 7). A re-export is a resolution
path and not a type identity: a name reached bare, behind two aliases and through a
two-hop `public use` chain resolves to ONE table entry, and a `Result` over it interns
once. The type model guarantees it; this gate pins it. The second half is rule 3: a
BINARY or HYBRID `.slib` carries no re-export today (#585), so a `public use` in one
is refused at build time.
"""
from __future__ import annotations

import dataclasses
import subprocess
from typing import NamedTuple

import pytest

from sushi_lang.compiler.loader import load_unit_recursively
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.ast import Let
from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
from sushi_lang.semantics.typesys import EnumType, StructType
from sushi_lang.semantics.units import UnitManager
from sushic_path import SUSHIC, SUSHIC_AVAILABLE


GEOMETRY = """\
public struct Vec:
    i32 x
    i32 y

public fn origin() Vec:
    return Result.Ok(Vec(1, 2))
"""

SHAPES = """\
public use "geometry"

public fn area(Vec v) i32:
    return Result.Ok(v.x * v.y)
"""

OUTER = """\
public use "shapes"

public fn describe(Vec v) string:
    return Result.Ok("{v.x}x{v.y}")
"""

# `Vec` bare through the two-hop chain, `g.Vec` behind an alias on its home, `sh.Vec`
# behind an alias on the one-hop re-exporter. `IoError` bare and behind two aliases.
MAIN = """\
use "outer"
use "geometry" as g
use "shapes" as sh
use <io/contracts>
use <io/contracts> as ioa
use <io/contracts> as iob

fn err_bare(i32 n) i32 | IoError:
    return Result.Ok(n)

fn err_a(i32 n) i32 | ioa.IoError:
    return Result.Ok(n)

fn err_b(i32 n) i32 | iob.IoError:
    return Result.Ok(n)

fn main() i32:
    let Vec v1 = Vec(0, 0)
    let g.Vec v2 = g.Vec(1, 1)
    let sh.Vec v3 = sh.Vec(2, 2)
    let IoError e1 = IoError.Other
    let ioa.IoError e2 = ioa.IoError.Other
    let iob.IoError e3 = iob.IoError.Other
    let Vec o = origin().realise(v1)
    println("{area(v2).realise(0)} {area(v3).realise(0)} {describe(o).realise('?')}")
    println("{err_bare(1).realise(0)} {err_a(2).realise(0)} {err_b(3).realise(0)}")
    match e1:
        IoError.Other -> println("other")
        _ -> println("else")
    match e2:
        IoError.Other -> println("other")
        _ -> println("else")
    match e3:
        IoError.Other -> println("other")
        _ -> println("else")
    return Result.Ok(0)
"""


class Analysis(NamedTuple):
    reporter: Reporter
    analyzer: SemanticAnalyzer


def _analyze_program_of(tmp_path, units: dict[str, str], main: str = "main") -> Analysis:
    """The production multi-unit flow: the loader follows every `use`, then one check.

    The `analyze_program` fixture loads ONE unit, so a re-export chain -- three units
    deep here -- needs the loader the compiler itself runs.
    """
    for name, text in units.items():
        (tmp_path / f"{name}.sushi").write_text(text, encoding="utf-8")
    reporter = Reporter(source=units[main], filename=main)
    get_stdlib_registry()
    manager = UnitManager(root_path=tmp_path, reporter=reporter)
    assert load_unit_recursively(manager, main, set(), reporter), reporter.items
    manager.get_compilation_order()
    analyzer = SemanticAnalyzer(reporter, filename=main, unit_manager=manager)
    try:
        analyzer.check(manager.units[main].ast)
    except ValueError:
        pass
    return Analysis(reporter=reporter, analyzer=analyzer)


def _errors(analysis):
    return [d for d in analysis.reporter.items if getattr(d, "kind", None) == "error"]


@pytest.fixture
def identity(tmp_path):
    analysis = _analyze_program_of(tmp_path, {
        "main": MAIN, "geometry": GEOMETRY, "shapes": SHAPES, "outer": OUTER})
    assert not _errors(analysis), \
        f"analysis reported {[getattr(d, 'code', '?') for d in _errors(analysis)]}"
    return analysis


def _nodes(node):
    """Every dataclass node reachable from `node`."""
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        yield node
        children = [getattr(node, f.name) for f in dataclasses.fields(node)]
    elif isinstance(node, (list, tuple)):
        children = list(node)
    else:
        return
    for child in children:
        yield from _nodes(child)


def _let_types(analysis, type_name: str):
    """The type of every `let` in main that names `type_name`, as the typecheck pass
    left it: the pass resolves the written form -- bare or behind a qualifier -- and
    writes the table entry back onto the node."""
    program = analysis.analyzer.unit_manager.units["main"].ast
    return [node.ty for node in _nodes(program)
            if isinstance(node, Let) and getattr(node.ty, "name", None) == type_name]


def test_a_struct_is_one_object_whatever_the_path(identity):
    """Bare through two hops, behind the home's alias, behind a re-exporter's alias.

    The qualifier folds into the bare name before the table lookup, so the three
    written forms resolve to ONE table entry and never to a second `Vec`.
    """
    table_entry = identity.analyzer.structs.by_name["Vec"]
    assert isinstance(table_entry, StructType)
    resolved = _let_types(identity, "Vec")
    assert len(resolved) >= 3, "one `let` per path at least"
    assert all(ty is table_entry for ty in resolved), "a second Vec was built"


def test_a_predefined_enum_is_one_object_whatever_the_path(identity):
    """`IoError`, `ioa.IoError` and `iob.IoError` are the one synthesized enum."""
    table_entry = identity.analyzer.enums.by_name["IoError"]
    assert isinstance(table_entry, EnumType)
    resolved = _let_types(identity, "IoError")
    assert len(resolved) >= 3, "one `let` per path at least"
    assert all(ty is table_entry for ty in resolved), "a second IoError was built"


def test_a_result_over_a_reexported_name_interns_once(identity):
    """Three spellings of the error arm, one `Result<i32, IoError>` in the table."""
    interned = [name for name in identity.analyzer.enums.by_name
                if name.startswith("Result<i32, ") and "IoError" in name]
    assert interned == ["Result<i32, IoError>"], interned
    codes = [getattr(d, "code", None) for d in identity.reporter.items]
    assert "CE0126" not in codes


# --- Rule 3: a binary .slib carries no re-export ------------------------------

LIB_MAIN = """\
public use "helper"

public fn twice(i32 n) i32:
    return Result.Ok(bump(n)?? + n - 1)
"""

LIB_HELPER = """\
public fn bump(i32 n) i32:
    return Result.Ok(n + 1)
"""


def _build(tmp_path, *extra_args):
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    for name, text in (("rexlib", LIB_MAIN), ("helper", LIB_HELPER)):
        (tmp_path / f"{name}.sushi").write_text(text, encoding="utf-8")
    out = tmp_path / "rexlib.slib"
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-version", "1.0.0",
         str(tmp_path / "rexlib.sushi"), "-o", str(out), *extra_args],
        cwd=tmp_path, capture_output=True, text=True)
    return result, out


@pytest.mark.parametrize("kind", ["binary", "hybrid"])
def test_a_compiled_library_refuses_a_public_use(tmp_path, kind):
    result, out = _build(tmp_path, "--lib-kind", kind)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "CE3514" in result.stderr, result.stderr
    # Tier 2: the refusal points at the line, in the unit that wrote it.
    assert "rexlib.sushi:1:" in result.stderr, result.stderr
    assert not out.exists(), "a refused build must write nothing"


def test_a_source_library_carries_a_public_use(tmp_path):
    result, out = _build(tmp_path, "--lib-kind", "source")
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.exists()
