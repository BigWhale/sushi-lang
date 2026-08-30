"""A rejected library build reports its diagnostic and nothing else (#436).

Three producer sites used to emit their diagnostic and then `raise ValueError` to stop
the build. Nothing caught it, so it reached the top-level guard in `compiler/cli.py`,
which renders any unexpected exception as CE0000 with the "this is a bug in the Sushi
compiler" note. The user got a correct rejection of their own program followed by an
invitation to file a compiler bug.

What is asserted here is what the user sees: the real code, no CE0000, no `.slib`, and no
success line. The reproductions are the ones recorded in the issue.
"""
from __future__ import annotations

import subprocess

import pytest
from sushic_path import SUSHIC, SUSHIC_AVAILABLE


# A public native `...T` variadic cannot ship: it collects into one concrete function,
# so there is no template for the consumer to monomorphize.
VARIADIC_LIB = """\
public fn total(...i32 xs) i32:
    return Result.Ok(0)
"""

# A public signature exposing a foreign `ptr` cannot ship: `ptr` is quarantined.
PTR_LIB = """\
unsafe external "C" as libc because "test":
    fn malloc(i64 n) ptr = "malloc"

public fn make_handle(i64 n) ptr:
    return Result.Ok(libc.malloc(n))
"""

# An exported generic reaching an un-shippable symbol: the template would carry a
# reference to `libc` that the consumer cannot resolve.
UNSHIPPABLE_GENERIC_LIB = """\
unsafe external "C" as libc because "test":
    fn abs(i32 v) i32 = "abs"

public fn twice@(T)(nom T x) i32:
    return Result.Ok(libc.abs(-2))
"""

# What a CLI build can actually reach. The producer's CE5002 site is NOT here: the
# typecheck pass's `_check_public_fn_ptr_fence` (passes/types/signatures.py) tests the
# identical condition -- a public function with a foreign `ptr` in its return or its
# parameters -- and CE5008 exits before codegen, so CE5002 is shadowed at the CLI. The
# site is still reached by a direct call, which is what the unit-level test below covers.
REJECTIONS = {
    "CE0116": VARIADIC_LIB,
    "CE5006": UNSHIPPABLE_GENERIC_LIB,
}

# The issue measured every case on both kinds. `source` is the default.
KINDS = ["source", "binary"]


def _build(tmp_path, src: str, kind: str):
    """Compile `src` as a library of `kind`, and return (CompletedProcess, out_path)."""
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    (tmp_path / "rejlib.sushi").write_text(src, encoding="utf-8")
    out = tmp_path / "rejlib.slib"
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-version", "1.0.0", "--lib-kind", kind,
         str(tmp_path / "rejlib.sushi"), "-o", str(out)],
        cwd=tmp_path, capture_output=True, text=True)
    return result, out


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", sorted(REJECTIONS))
def test_the_real_code_is_reported(tmp_path, code, kind):
    result, _out = _build(tmp_path, REJECTIONS[code], kind)
    assert code in result.stderr, (code, kind, result.stderr)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", sorted(REJECTIONS))
def test_no_internal_compiler_error_follows_it(tmp_path, code, kind):
    """The defect itself: a legitimate rejection presented as a compiler crash."""
    result, _out = _build(tmp_path, REJECTIONS[code], kind)
    assert "CE0000" not in result.stderr, (code, kind, result.stderr)
    assert "internal compiler error" not in result.stderr, (code, kind, result.stderr)
    assert "this is a bug in the Sushi compiler" not in result.stderr, (
        code, kind, result.stderr)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", sorted(REJECTIONS))
def test_the_rejection_exits_two(tmp_path, code, kind):
    result, _out = _build(tmp_path, REJECTIONS[code], kind)
    assert result.returncode == 2, (code, kind, result.returncode, result.stderr)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", sorted(REJECTIONS))
def test_no_slib_is_written(tmp_path, code, kind):
    """`generate()` must not write a container for a library it just rejected."""
    _result, out = _build(tmp_path, REJECTIONS[code], kind)
    assert not out.exists(), f"{code}/{kind} wrote {out}"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", sorted(REJECTIONS))
def test_no_success_line_is_printed(tmp_path, code, kind):
    result, _out = _build(tmp_path, REJECTIONS[code], kind)
    assert "Success!" not in result.stdout, (code, kind, result.stdout)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", sorted(REJECTIONS))
def test_the_code_is_reported_exactly_once(tmp_path, code, kind):
    """One rejection, one diagnostic. The raise used to guarantee this; the gate must."""
    result, _out = _build(tmp_path, REJECTIONS[code], kind)
    assert result.stderr.count(code) == 1, (kind, result.stderr)


def test_a_public_ptr_signature_is_caught_before_the_producer(tmp_path):
    """CE5008 shadows the producer's CE5002 at the CLI, and that is the correct order.

    Pinned so that a missing CE5002 here is never read as a regression: the typecheck
    pass rejects the same program earlier and more cheaply.
    """
    result, out = _build(tmp_path, PTR_LIB, "source")
    assert "CE5008" in result.stderr, result.stderr
    assert result.returncode == 2
    assert not out.exists()
    assert "CE0000" not in result.stderr, result.stderr


# --- The producer emits and returns, rather than raising ---------------------

def _generator(tmp_path, src: str, name: str):
    """A LibraryManifestGenerator over one unit built from `src`."""
    from types import SimpleNamespace

    from sushi_lang.backend.library_manifest import LibraryManifestGenerator
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.passes.collect import EnumTable, StructTable
    from sushi_lang.semantics.units import Unit

    file_path = tmp_path / f"{name}.sushi"
    file_path.write_text(src, encoding="utf-8")
    program, _tree = parse_to_ast(src)
    unit = Unit(name=name, file_path=file_path, ast=program,
                dependencies=[], public_symbols={})
    reporter = Reporter(source=src, filename=name)
    gen = LibraryManifestGenerator(SimpleNamespace(
        reporter=reporter, structs=StructTable(), enums=EnumTable()))
    return gen, unit, reporter


def test_the_producer_does_not_raise_for_a_variadic_public_fn(tmp_path):
    """No exception escapes, and the diagnostic is still there. CE0116."""
    gen, unit, reporter = _generator(tmp_path, VARIADIC_LIB, "varlib")

    gen._extract_public_functions([unit])

    assert any(item.code == "CE0116" for item in reporter.items)
    assert reporter.has_errors


def test_the_producer_does_not_raise_for_a_public_ptr_signature(tmp_path):
    """CE5002, the site a CLI build cannot reach but a direct call can."""
    gen, unit, reporter = _generator(tmp_path, PTR_LIB, "ptrlib")

    gen._extract_public_functions([unit])

    assert any(item.code == "CE5002" for item in reporter.items)
    assert reporter.has_errors


def test_the_producer_does_not_raise_for_an_unshippable_generic(tmp_path):
    """CE5006, raised from inside the export-closure walk."""
    gen, unit, reporter = _generator(
        tmp_path, UNSHIPPABLE_GENERIC_LIB, "genlib")

    gen._extract_templates([unit])

    assert any(item.code == "CE5006" for item in reporter.items)
    assert reporter.has_errors


def test_every_public_rejection_is_reported_not_just_the_first(tmp_path):
    """Two bad public functions give two diagnostics.

    The raise stopped at the first one, so a library with several rejected exports took
    one build per problem. The loop now reports them all.
    """
    src = (
        "public fn total(...i32 xs) i32:\n"
        "    return Result.Ok(0)\n"
        "\n"
        "public fn count(...i32 ys) i32:\n"
        "    return Result.Ok(0)\n"
    )
    gen, unit, reporter = _generator(tmp_path, src, "twolib")

    gen._extract_public_functions([unit])

    codes = [item.code for item in reporter.items]
    assert codes.count("CE0116") == 2, codes
