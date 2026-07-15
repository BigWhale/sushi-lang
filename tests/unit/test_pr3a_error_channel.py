"""PR3a: false ICEs become real diagnostics; the error channel loses its swallows.

Each condition below used to reach the user as a generic CE0000 ("this is a bug in
the Sushi compiler"), a raw traceback, or nothing at all. They now render as a
specific, registered code. The registry-completeness test (test_error_registry.py)
guards that these codes are registered and no longer dead; these tests guard that the
conditions actually PRODUCE them.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_HAS_SUSHIC = shutil.which("sushic") is not None


# --------------------------------------------------------------------------
# The bare-except sweep -- an acceptance criterion, made a permanent gate
# --------------------------------------------------------------------------

def test_no_bare_except_in_sushi_lang():
    """A bare `except:` catches KeyboardInterrupt/SystemExit/MemoryError.

    PR3a removed the last one (backend/expressions/calls/utils.py). This keeps it gone:
    a new bare except anywhere under sushi_lang/ turns CI red.
    """
    bare = re.compile(r"except\s*:")
    offenders = []
    for path in (REPO / "sushi_lang").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if bare.match(stripped):
                offenders.append(f"{path.relative_to(REPO)}:{i}")
    assert not offenders, f"bare `except:` found (narrow it): {offenders}"


# --------------------------------------------------------------------------
# CE3504 -- cross-platform .slib rejected at load
# --------------------------------------------------------------------------

def _host_platform() -> str:
    from sushi_lang.backend.platform_detect import current_platform_name
    return current_platform_name()


def test_check_library_platform_rejects_a_mismatch():
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.compiler.pipeline import _check_library_platform

    other = "linux" if _host_platform() == "darwin" else "darwin"
    with pytest.raises(LibraryError) as exc:
        _check_library_platform({"platform": other}, "somelib")
    assert exc.value.code == "CE3504"


def test_check_library_platform_allows_the_host_and_unknown():
    from sushi_lang.compiler.pipeline import _check_library_platform

    # Same platform: no raise.
    _check_library_platform({"platform": _host_platform()}, "lib")
    # "unknown" on either side: do not block a build over an undetectable platform.
    _check_library_platform({"platform": "unknown"}, "lib")
    _check_library_platform({}, "lib")


# --------------------------------------------------------------------------
# CE3510 / CE3511 -- truncation, raised with a LITERAL code so the gate sees it
# --------------------------------------------------------------------------

def test_truncated_metadata_section_is_ce3510():
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.backend.library_format import _read_bytes

    stream = io.BytesIO(b"short")
    with pytest.raises(LibraryError) as exc:
        _read_bytes(stream, 100, "lib.slib", "metadata")
    assert exc.value.code == "CE3510"


def test_truncated_bitcode_section_is_ce3511():
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.backend.library_format import _read_bytes

    stream = io.BytesIO(b"short")
    with pytest.raises(LibraryError) as exc:
        _read_bytes(stream, 100, "lib.slib", "bitcode")
    assert exc.value.code == "CE3511"


def test_library_error_renders_through_the_reporter_path():
    """A LibraryError is a SushiError, so the top-level guard renders its own code."""
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.internals.diagnostics import SushiError

    err = LibraryError("CE3504", lib_platform="linux", current_platform="darwin")
    assert isinstance(err, SushiError)
    assert err.code == "CE3504"
    assert "platform mismatch" in err.message


# --------------------------------------------------------------------------
# CE3501 -- main() rejected in --lib mode (end to end)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_SUSHIC, reason="sushic not on PATH")
def test_lib_mode_rejects_main(tmp_path):
    src = tmp_path / "lib.sushi"
    src.write_text(
        "public fn helper(i32 x) i32:\n"
        "    return Result.Ok(x + 1)\n\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["sushic", "--lib", str(src), "-o", str(tmp_path / "lib.slib")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "CE3501" in result.stderr


@pytest.mark.skipif(not _HAS_SUSHIC, reason="sushic not on PATH")
def test_lib_mode_without_main_succeeds(tmp_path):
    src = tmp_path / "lib.sushi"
    src.write_text(
        "public fn helper(i32 x) i32:\n"
        "    return Result.Ok(x + 1)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["sushic", "--lib", str(src), "-o", str(tmp_path / "lib.slib")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------
# CE3507 -- a .slib whose bitcode payload is corrupt (end to end)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_SUSHIC, reason="sushic not on PATH")
def test_corrupt_library_bitcode_is_ce3507(tmp_path):
    libs = tmp_path / "libs"
    libs.mkdir()
    lib_src = tmp_path / "mathlib.sushi"
    lib_src.write_text(
        "public fn add_one(i32 x) i32:\n"
        "    return Result.Ok(x + 1)\n",
        encoding="utf-8",
    )
    slib = libs / "mathlib.slib"
    build = subprocess.run(
        ["sushic", "--lib", str(lib_src), "-o", str(slib)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr

    # Corrupt the bitcode payload in place: the header + metadata + the two length
    # fields stay intact (so the truncation guards pass), but the bitcode bytes are
    # garbage, so llvm.parse_bitcode fails -> CE3507. The last 8-byte length field
    # precedes the bitcode; scribble over everything after it.
    from sushi_lang.backend.library_format import LibraryFormat
    metadata, bitcode = LibraryFormat.read(slib)
    LibraryFormat.write(slib, metadata, b"\x00" * len(bitcode))

    project = tmp_path / "consumer"
    project.mkdir()
    (project / "main.sushi").write_text(
        "use <lib/mathlib>\n\n"
        "fn main() i32:\n"
        "    println(add_one(41).realise(-1))\n"
        "    return Result.Ok(0)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "SUSHI_LIB_PATH": str(libs)}
    result = subprocess.run(
        ["sushic", "main.sushi", "-o", "out"],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "CE3507" in result.stderr
    assert "CE0000" not in result.stderr, "a corrupt library must not read as a compiler bug"
