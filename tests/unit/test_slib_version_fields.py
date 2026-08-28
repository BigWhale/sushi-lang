"""A `.slib` states its own version, and which compilers may consume it.

`compiler_version` has always been recorded and never enforced. Version 4 adds
`library_version` (what this library is) and `requires_compiler` (which compilers accept
it), and the load path enforces the second. See `docs/design/libraries.md` section 6.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from sushi_lang.backend.library_errors import LibraryError
from sushi_lang.backend.library_format import LibraryFormat


LIB_SRC = """\
public fn twice(i32 n) i32:
    return Result.Ok(n * 2)
"""

NORI_TOML = """\
[package]
name = "versionlib"
version = "2.5.0"
"""


def _build_lib(tmp_path, *extra_args, nori: str | None = None):
    """Compile LIB_SRC into a .slib and return the CompletedProcess plus the path."""
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    src = tmp_path / "versionlib.sushi"
    src.write_text(LIB_SRC, encoding="utf-8")
    if nori is not None:
        (tmp_path / "nori.toml").write_text(nori, encoding="utf-8")
    out = tmp_path / "versionlib.slib"
    result = subprocess.run(
        ["sushic", "--lib", str(src), "-o", str(out), *extra_args],
        cwd=tmp_path, capture_output=True, text=True)
    return result, out


# --- The manifest carries the new fields ------------------------------------

@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("versionlib")
    result, out = _build_lib(tmp_path, "--lib-version", "1.4.2")
    assert result.returncode == 0, result.stdout + result.stderr
    return LibraryFormat.read_metadata_only(out)


def test_the_protocol_string_is_bumped(manifest):
    # 2.1: the three concrete lists gained an `is_public` gate and `not_exported` grew
    # three kinds. 2.2: every record names its `unit`, and a record with a symbol in the
    # shipped bitcode names that too (`link_symbol`). Either way an older `.slib` has to
    # be rebuilt.
    assert manifest["sushi_lib_version"] == "2.2"


def test_the_manifest_records_its_kind(manifest):
    assert manifest["kind"] in ("source", "binary", "hybrid")


def test_the_manifest_lists_its_units(manifest):
    assert isinstance(manifest["units"], list)


def test_the_manifest_records_the_library_version(manifest):
    assert manifest["library_version"] == "1.4.2"


def test_the_manifest_records_which_compilers_it_accepts(manifest):
    from sushi_lang import __version__
    from sushi_lang.internals.semver import Version, default_compiler_req

    assert manifest["requires_compiler"] == default_compiler_req(Version.parse(__version__))


def test_the_building_compiler_is_still_recorded_verbatim(manifest):
    from sushi_lang import __version__

    assert manifest["compiler_version"] == __version__


# --- Where the library version comes from -----------------------------------

def test_a_nori_manifest_supplies_the_version(tmp_path):
    result, out = _build_lib(tmp_path, nori=NORI_TOML)
    assert result.returncode == 0, result.stdout + result.stderr
    assert LibraryFormat.read_metadata_only(out)["library_version"] == "2.5.0"


def test_a_flag_agreeing_with_the_nori_manifest_is_accepted(tmp_path):
    result, out = _build_lib(tmp_path, "--lib-version", "2.5.0", nori=NORI_TOML)
    assert result.returncode == 0, result.stdout + result.stderr
    assert LibraryFormat.read_metadata_only(out)["library_version"] == "2.5.0"


def test_a_flag_contradicting_the_nori_manifest_is_rejected(tmp_path):
    # Silently preferring one would let a package ship under a version it does not claim.
    result, _out = _build_lib(tmp_path, "--lib-version", "9.9.9", nori=NORI_TOML)
    assert result.returncode == 2
    assert "CE3505" in result.stdout + result.stderr


def test_no_version_anywhere_is_rejected(tmp_path):
    result, _out = _build_lib(tmp_path)
    assert result.returncode == 2
    assert "CE3505" in result.stdout + result.stderr


def test_a_malformed_version_flag_is_rejected(tmp_path):
    result, _out = _build_lib(tmp_path, "--lib-version", "1.2")
    assert result.returncode == 2
    assert "CE3505" in result.stdout + result.stderr


# --- Enforcement of requires_compiler ---------------------------------------

def _check(requires, current="0.11.1", ignore=False):
    from sushi_lang.compiler.pipeline import _check_library_compiler_version

    _check_library_compiler_version(
        {"library_name": "versionlib", "requires_compiler": requires},
        "lib/versionlib", current=current, ignore=ignore)


@pytest.mark.parametrize("requires,current", [
    ("~0.11", "0.11.1"),
    ("~0.11", "0.11.0"),
    ("~0.11", "0.11.99"),
    (">=0.10.0, <1.0.0", "0.11.1"),
    ("0.11.1", "0.11.1"),
])
def test_a_satisfied_requirement_loads(requires, current):
    _check(requires, current)


@pytest.mark.parametrize("requires,current", [
    ("~0.11", "0.12.0"),
    ("~0.11", "0.10.9"),
    ("~0.12", "0.11.1"),
    ("0.11.1", "0.11.2"),
])
def test_an_unsatisfied_requirement_is_rejected(requires, current):
    with pytest.raises(LibraryError) as excinfo:
        _check(requires, current)
    assert excinfo.value.code == "CE3503"


def test_the_override_flag_lifts_the_rejection():
    _check("~0.11", current="0.12.0", ignore=True)


@pytest.mark.parametrize("metadata", [
    {},                                     # a field that is simply absent
    {"requires_compiler": None},
    {"requires_compiler": ""},
    {"requires_compiler": "not-a-constraint"},
])
def test_an_unreadable_requirement_does_not_block(metadata):
    # The gate exists to catch a real incompatibility, never to fail a build over a
    # field it could not parse.
    from sushi_lang.compiler.pipeline import _check_library_compiler_version

    _check_library_compiler_version(metadata, "lib/x", current="0.11.1")


def test_an_unknown_running_compiler_does_not_block():
    # `sushi_lang.__version__` falls back to "unknown" when the package metadata and
    # pyproject.toml are both unreadable. That must not stop a build.
    _check("~0.11", current="unknown")


# --- End to end --------------------------------------------------------------

def test_a_consumer_refuses_a_library_that_excludes_its_compiler(tmp_path):
    result, out = _build_lib(tmp_path, "--lib-version", "1.0.0")
    assert result.returncode == 0, result.stdout + result.stderr

    metadata = LibraryFormat.read_metadata_only(out)
    _meta, bitcode = LibraryFormat.read(out)
    metadata["requires_compiler"] = "~99.0"
    LibraryFormat.write(out, metadata, bitcode)

    consumer = tmp_path / "consumer.sushi"
    consumer.write_text(
        "use <lib/versionlib>\n\n"
        "fn main() i32:\n"
        "    return Result.Ok(twice(2).realise(0))\n",
        encoding="utf-8")
    env_run = subprocess.run(
        ["sushic", str(consumer)], cwd=tmp_path, capture_output=True, text=True,
        env={**__import__("os").environ, "SUSHI_LIB_PATH": str(tmp_path)})
    assert env_run.returncode == 2
    assert "CE3503" in env_run.stdout + env_run.stderr
