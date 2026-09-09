"""A compiled .slib carries its units' re-exports (#585).

`public use X` in unit U makes X's public names U's own, so U's importers get them
where U's own names land (`docs/design/unit-namespaces.md` section 8.1). A SOURCE
library needs no record: its units arrive as text and the consumer's `namespaces`
pass reads the statement. A BINARY or hybrid library ships records, so the manifest
has to carry one -- the path of each `public use`, keyed by the unit that wrote it --
and the consumer composes the unit's namespace from its own records plus the
providers of what it re-exports.

The parity standard is the source library, measured: a source `.slib` whose façade
says `public use <io/fs>` gives its consumer `open()`, `File` and `FileMode` bare.
A binary one must give the same. So the record drives three readers -- the provider
(a name behind the alias, and in the flat scope), the source-stdlib injection and
the stdlib bitcode link.

CE3514 refused the statement at build time until this record existed. It is retired.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sushi_lang.backend.library_format import LibraryFormat
from sushic_path import SUSHIC, SUSHIC_AVAILABLE


# A façade over a sibling: the shape `docs/libraries.md` recommends for giving a
# multi-unit library one namespace. The façade declares one function of its own, so
# the manifest's `public_functions` names both units.
FACADE = """\
public use "helper"

public fn twice(i32 n) i32:
    return Result.Ok(bump(n)?? + n - 1)
"""

HELPER = """\
public struct Bump:
    i32 by

public fn bump(i32 n) i32:
    return Result.Ok(n + 1)
"""

# A façade that declares NOTHING of its own: every public name it has is re-exported.
# The manifest's `public_functions` cannot name this unit at all, so the `units`
# index is the only place a consumer can find it.
THIN_FACADE = """\
public use "worker"
"""

WORKER = """\
public fn work() i32:
    return Result.Ok(7)
"""

# A two-hop chain: `chain` re-exports `middle`, `middle` re-exports `tail`.
CHAIN = """\
public use "middle"

public fn chain_tag() string:
    return Result.Ok("chain")
"""

MIDDLE = """\
public use "tail"
"""

TAIL = """\
public fn tail_value() i32:
    return Result.Ok(11)
"""

# A stdlib re-export: the shape `<io/contracts>` uses to hand on `IoError`.
STDLIB_FACADE = """\
public use <io/fs>

public fn tag() string:
    return Result.Ok("facade")
"""

# No re-export at all: the manifest must grow by nothing.
PLAIN = """\
public fn plain() i32:
    return Result.Ok(3)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_lib(tmp_path: Path, units: dict[str, str], main: str, name: str,
               kind: str = "binary"):
    """Build a multi-unit library as a .slib under tmp_path/libs.

    Returns (CompletedProcess, env for a consumer, the .slib path).
    """
    if not SUSHIC_AVAILABLE:
        pytest.skip(f"no compiler driver at {SUSHIC}")
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(exist_ok=True)
    for unit_name, text in units.items():
        _write(tmp_path / f"{unit_name}.sushi", text)
    slib = libs_dir / f"{name}.slib"
    env = {**os.environ, "SUSHI_LIB_PATH": str(libs_dir)}
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "1.0.0",
         str(tmp_path / f"{main}.sushi"), "-o", str(slib)],
        cwd=tmp_path, capture_output=True, text=True, env=env)
    return result, env, slib


def _consume(tmp_path: Path, env: dict, program: str, name: str = "prog"):
    project = tmp_path / name
    _write(project / "main.sushi", program)
    return subprocess.run([SUSHIC, "main.sushi", "-o", "out"],
                          cwd=project, capture_output=True, text=True, env=env)


def _run(tmp_path: Path, name: str = "prog"):
    """Run the built program IN its own directory: a relative path it opens is there."""
    return subprocess.run([str(tmp_path / name / "out")], cwd=tmp_path / name,
                          capture_output=True, text=True)


def _reexports(metadata: dict) -> set[tuple[str, str, str]]:
    return {(r["unit"], r["path"], r["kind"])
            for r in metadata.get("reexports") or ()}


# --- The manifest record ------------------------------------------------------------

@pytest.fixture(scope="module")
def facade_lib(tmp_path_factory):
    """One binary library, a façade over a sibling, built once."""
    tmp_path = tmp_path_factory.mktemp("facade")
    build, env, slib = _build_lib(
        tmp_path, {"rexlib": FACADE, "helper": HELPER}, "rexlib", "rexlib")
    assert build.returncode == 0, build.stdout + build.stderr
    return tmp_path, env, LibraryFormat.read_metadata_only(slib)


def test_a_binary_library_builds_with_a_public_use(facade_lib):
    """CE3514 is retired: the statement is recorded, not refused."""
    _tmp, _env, metadata = facade_lib
    assert metadata["kind"] == "binary"
    assert sorted(metadata["units"]) == ["helper", "rexlib"]


def test_the_manifest_names_the_re_exported_unit(facade_lib):
    _tmp, _env, metadata = facade_lib
    assert _reexports(metadata) == {("rexlib", "helper", "unit")}


def test_a_stdlib_re_export_is_recorded_as_one(tmp_path):
    _build, _env, slib = _build_lib(
        tmp_path, {"sfacade": STDLIB_FACADE}, "sfacade", "sfacade")
    metadata = LibraryFormat.read_metadata_only(slib)
    assert _reexports(metadata) == {("sfacade", "io/fs", "stdlib")}


def test_a_library_with_no_re_export_carries_no_key(tmp_path):
    """An ordinary library grows by nothing, as every optional section does."""
    _build, _env, slib = _build_lib(tmp_path, {"plain": PLAIN}, "plain", "plain")
    metadata = LibraryFormat.read_metadata_only(slib)
    assert "reexports" not in metadata


@pytest.mark.parametrize("kind", ["binary", "hybrid"])
def test_both_compiled_kinds_carry_the_record(tmp_path, kind):
    build, _env, slib = _build_lib(
        tmp_path, {"rexlib": FACADE, "helper": HELPER}, "rexlib", "rexlib", kind=kind)
    assert build.returncode == 0, build.stdout + build.stderr
    metadata = LibraryFormat.read_metadata_only(slib)
    assert _reexports(metadata) == {("rexlib", "helper", "unit")}


def test_a_bundled_units_re_export_is_not_the_librarys(tmp_path):
    """Only the author's own units (#594). `<io/fs>` re-exports on its own account."""
    _build, _env, slib = _build_lib(
        tmp_path, {"sfacade": STDLIB_FACADE}, "sfacade", "sfacade")
    metadata = LibraryFormat.read_metadata_only(slib)
    assert {r["unit"] for r in metadata["reexports"]} == {"sfacade"}


# --- The consumer: a re-exported sibling --------------------------------------------

FLAT_PROGRAM = """\
use <lib/rexlib>

fn main() i32:
    println("{twice(4).realise(0)} {bump(1).realise(0)}")
    return Result.Ok(0)
"""

ALIASED_PROGRAM = """\
use <lib/rexlib> as r

fn main() i32:
    println("{r.twice(4).realise(0)} {r.bump(1).realise(0)}")
    return Result.Ok(0)
"""

ALIASED_TYPE_PROGRAM = """\
use <lib/rexlib> as r

fn main() i32:
    let r.Bump b = r.Bump(2)
    println("{b.by}")
    return Result.Ok(0)
"""


def test_a_re_exported_siblings_name_is_reachable_flat(facade_lib):
    tmp_path, env, _metadata = facade_lib
    build = _consume(tmp_path, env, FLAT_PROGRAM, name="flat")
    assert build.returncode == 0, build.stdout + build.stderr
    assert _run(tmp_path, "flat").stdout == "8 2\n"


def test_a_re_exported_siblings_name_is_reachable_behind_the_alias(facade_lib):
    """The gap the record closes: the alias binds the unit, and the unit re-exports."""
    tmp_path, env, _metadata = facade_lib
    build = _consume(tmp_path, env, ALIASED_PROGRAM, name="aliased")
    assert build.returncode == 0, build.stdout + build.stderr
    assert _run(tmp_path, "aliased").stdout == "8 2\n"


def test_a_re_exported_siblings_type_is_reachable_behind_the_alias(facade_lib):
    tmp_path, env, _metadata = facade_lib
    build = _consume(tmp_path, env, ALIASED_TYPE_PROGRAM, name="aliastype")
    assert build.returncode == 0, build.stdout + build.stderr
    assert _run(tmp_path, "aliastype").stdout == "2\n"


THIN_PROGRAM = """\
use <lib/thin> as t

fn main() i32:
    println("{t.work().realise(0)}")
    return Result.Ok(0)
"""


def test_a_facade_that_declares_nothing_is_still_found(tmp_path):
    """The `units` index is the only place a unit with no public record appears."""
    build, env, _slib = _build_lib(
        tmp_path, {"thin": THIN_FACADE, "worker": WORKER}, "thin", "thin")
    assert build.returncode == 0, build.stdout + build.stderr
    consume = _consume(tmp_path, env, THIN_PROGRAM)
    assert consume.returncode == 0, consume.stdout + consume.stderr
    assert _run(tmp_path).stdout == "7\n"


CHAIN_PROGRAM = """\
use <lib/chain> as c

fn main() i32:
    let string tag = c.chain_tag().realise("?")
    println("{tag} {c.tail_value().realise(0)}")
    return Result.Ok(0)
"""


def test_a_two_hop_chain_composes(tmp_path):
    build, env, slib = _build_lib(
        tmp_path, {"chain": CHAIN, "middle": MIDDLE, "tail": TAIL}, "chain", "chain")
    assert build.returncode == 0, build.stdout + build.stderr
    metadata = LibraryFormat.read_metadata_only(slib)
    assert _reexports(metadata) == {("chain", "middle", "unit"),
                                    ("middle", "tail", "unit")}
    consume = _consume(tmp_path, env, CHAIN_PROGRAM)
    assert consume.returncode == 0, consume.stdout + consume.stderr
    assert _run(tmp_path).stdout == "chain 11\n"


# --- The consumer: a re-exported stdlib module --------------------------------------

STDLIB_FLAT_PROGRAM = """\
use <lib/sfacade>

fn main() i32:
    let File f = open("in.txt", FileMode.Read()).realise(stdin)
    let string name = tag().realise("?")
    let string body = f.read_all().realise("?")
    println("{name} {body}")
    return Result.Ok(0)
"""

STDLIB_ALIASED_PROGRAM = """\
use <lib/sfacade> as s

fn main() i32:
    let s.File f = s.open("in.txt", s.FileMode.Read()).realise(s.stdin)
    let string name = s.tag().realise("?")
    let string body = f.read_all().realise("?")
    println("{name} {body}")
    return Result.Ok(0)
"""


@pytest.fixture(scope="module")
def stdlib_lib(tmp_path_factory):
    """A binary library whose façade re-exports `<io/fs>`."""
    tmp_path = tmp_path_factory.mktemp("stdlibfacade")
    build, env, _slib = _build_lib(
        tmp_path, {"sfacade": STDLIB_FACADE}, "sfacade", "sfacade")
    assert build.returncode == 0, build.stdout + build.stderr
    return tmp_path, env


def test_a_re_exported_stdlib_module_is_in_the_consumers_flat_scope(stdlib_lib):
    """The parity standard: a source library gives exactly this, measured."""
    tmp_path, env = stdlib_lib
    _write(tmp_path / "flat" / "in.txt", "Mostly Harmless")
    build = _consume(tmp_path, env, STDLIB_FLAT_PROGRAM, name="flat")
    assert build.returncode == 0, build.stdout + build.stderr
    assert _run(tmp_path, "flat").stdout == "facade Mostly Harmless\n"


def test_a_re_exported_stdlib_module_is_reachable_behind_the_alias(stdlib_lib):
    tmp_path, env = stdlib_lib
    _write(tmp_path / "aliased" / "in.txt", "Mostly Harmless")
    build = _consume(tmp_path, env, STDLIB_ALIASED_PROGRAM, name="aliased")
    assert build.returncode == 0, build.stdout + build.stderr
    assert _run(tmp_path, "aliased").stdout == "facade Mostly Harmless\n"


# --- The consumer: a re-exported LIBRARY -------------------------------------------

# A compiled library that re-exports a SOURCE library. The producer of a compiled
# library cannot consume a BINARY one at all today -- that build ICEs with CE0000 on
# an unknown function, which predates this record -- so a source dependency is the
# shape that can be built.
LIB_A = """\
public fn a_one() i32:
    return Result.Ok(1)
"""

LIB_B = """\
public use <lib/liba>

public fn b_two() i32:
    return Result.Ok(a_one()?? + 1)
"""

LIBRARY_PROGRAM = """\
use <lib/libb> as b
use <lib/liba>

fn main() i32:
    println("{b.b_two().realise(0)} {b.a_one().realise(0)}")
    return Result.Ok(0)
"""

UNSTATED_PROGRAM = """\
use <lib/libb> as b

fn main() i32:
    println("{b.a_one().realise(0)}")
    return Result.Ok(0)
"""


@pytest.fixture(scope="module")
def library_reexport(tmp_path_factory):
    """A binary `libb` that re-exports the source `liba`."""
    if not SUSHIC_AVAILABLE:
        pytest.skip(f"no compiler driver at {SUSHIC}")
    tmp_path = tmp_path_factory.mktemp("librex")
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir()
    env = {**os.environ, "SUSHI_LIB_PATH": str(libs_dir)}
    _write(tmp_path / "liba.sushi", LIB_A)
    _write(tmp_path / "libb.sushi", LIB_B)
    for name, kind in (("liba", "source"), ("libb", "binary")):
        result = subprocess.run(
            [SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "1.0.0",
             str(tmp_path / f"{name}.sushi"), "-o", str(libs_dir / f"{name}.slib")],
            cwd=tmp_path, capture_output=True, text=True, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
    slib = libs_dir / "libb.slib"
    return tmp_path, env, LibraryFormat.read_metadata_only(slib)


def test_a_library_re_export_is_recorded_as_one(library_reexport):
    _tmp, _env, metadata = library_reexport
    assert _reexports(metadata) == {("libb", "lib/liba", "library")}


def test_a_re_exported_librarys_name_is_reachable(library_reexport):
    """Limitation 1 holds: the consumer states `liba` for itself, and then gets it."""
    tmp_path, env, _metadata = library_reexport
    build = _consume(tmp_path, env, LIBRARY_PROGRAM, name="both")
    assert build.returncode == 0, build.stdout + build.stderr
    assert _run(tmp_path, "both").stdout == "2 1\n"


def test_a_consumer_that_states_neither_hears_the_ordinary_refusal(library_reexport):
    """No transitive dependency, and no silence either: CE2008 names the call."""
    tmp_path, env, _metadata = library_reexport
    build = _consume(tmp_path, env, UNSTATED_PROGRAM, name="one")
    assert build.returncode == 2, build.stdout + build.stderr
    assert "CE2008" in build.stderr, build.stderr
