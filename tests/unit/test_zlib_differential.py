"""Differential test: the Sushi DEFLATE codec against Python zlib.

Two directions, and the second is the one that matters:

- Python compresses, the Sushi tool inflates, and the bytes must come back.
- The Sushi tool compresses, Python zlib inflates, and the bytes must come
  back. A stream that a real zlib cannot read is the failure this catches.

Each direction runs over both containers -- the RFC 1950 wrapper and a bare
RFC 1951 stream -- and over the shapes that break a DEFLATE codec: nothing, one
byte, a maximal match, a run longer than the window, incompressible bytes, and
real files from the repository.
"""
from __future__ import annotations

import subprocess
import zlib
from pathlib import Path

import pytest
from sushic_path import SUSHIC, SUSHIC_AVAILABLE

REPO = Path(__file__).parents[2]
DEFLATE_SRC = REPO / "tests" / "stdlib" / "zlib" / "helpers" / "zdeflate.sushi"
INFLATE_SRC = REPO / "tests" / "stdlib" / "zlib" / "helpers" / "zinflate.sushi"

LEVELS = (0, 1, 6, 9)


def _build(src: Path, tmp_path_factory, name: str) -> Path:
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    tmp = tmp_path_factory.mktemp(name)
    out = tmp / name
    result = subprocess.run(
        [SUSHIC, str(src), "-o", str(out)],
        cwd=tmp, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return out


@pytest.fixture(scope="module")
def deflate_tool(tmp_path_factory):
    """Compile the compressor once for the module."""
    return _build(DEFLATE_SRC, tmp_path_factory, "zdeflate")


@pytest.fixture(scope="module")
def inflate_tool(tmp_path_factory):
    """Compile the decompressor once for the module."""
    return _build(INFLATE_SRC, tmp_path_factory, "zinflate")


def _repo_file(name: str) -> bytes:
    return (REPO / name).read_bytes()


# Deterministic pseudo-random bytes: a test must not depend on os.urandom.
def _noise(n: int) -> bytes:
    out = bytearray(n)
    x = 0x9E3779B9
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        out[i] = (x >> 16) & 0xFF
    return bytes(out)


CORPUS = {
    "empty": b"",
    "one": b"A",
    "two": b"AB",
    "three": b"abc",
    "run_300": b"a" * 300,
    "run_100k": b"a" * 100000,
    "match_258": b"y" * 258,
    "match_259": b"y" * 259,
    "window_32768": (b"q" * 32768) + b"Z" + (b"q" * 32768),
    "window_32769": (b"w" * 32769) + b"Z" + (b"w" * 32769),
    "alternating": b"ab" * 40000,
    "ramp": bytes(range(256)),
    "ramp_x400": bytes(range(256)) * 400,
    "nulls": bytes(50000),
    "incompressible": _noise(32768),
    "prose": b"the quick brown fox jumps over the lazy dog. " * 2000,
    "readme": _repo_file("README.md"),
    "style": _repo_file("STYLE.md"),
    # A large file with ordinary redundancy is the case a corpus of runs and short
    # strings misses. A run of one byte compresses through few loop iterations,
    # because one match consumes up to 258 bytes; real text takes one iteration per
    # few bytes. That difference hid the stack leak in BUGS.md B1, which crashed the
    # encoder above ~53 KB while every repetitive payload here still passed.
    "changelog": _repo_file("CHANGELOG.md"),
    "large_text": _repo_file("CHANGELOG.md") + _repo_file("STYLE.md") + _repo_file("README.md"),
}

# Entries with enough redundancy that the encoder must beat the input size.
COMPRESSIBLE = (
    "run_300", "run_100k", "window_32768", "window_32769",
    "alternating", "ramp_x400", "nulls", "prose", "readme", "style",
    "changelog", "large_text",
)


def _run(tool: Path, args: list[str]) -> None:
    r = subprocess.run([str(tool)] + args, capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="replace")


def _sushi_deflate(tool, tmp_path, data, level, raw):
    src = tmp_path / "in.bin"
    dst = tmp_path / "out.z"
    src.write_bytes(data)
    _run(tool, [str(src), str(dst), str(level)] + (["--raw"] if raw else []))
    return dst.read_bytes()


def _sushi_inflate(tool, tmp_path, blob, raw):
    src = tmp_path / "in.z"
    dst = tmp_path / "out.bin"
    src.write_bytes(blob)
    _run(tool, [str(src), str(dst)] + (["--raw"] if raw else []))
    return dst.read_bytes()


def _py_deflate_raw(data: bytes, level: int) -> bytes:
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def _py_inflate_raw(blob: bytes) -> bytes:
    d = zlib.decompressobj(-15)
    return d.decompress(blob) + d.flush()


# --------------------------------------------------------------------------
# Python compresses, Sushi decompresses.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_sushi_inflates_what_python_deflated(inflate_tool, tmp_path, name, level):
    data = CORPUS[name]
    blob = zlib.compress(data, level)
    assert _sushi_inflate(inflate_tool, tmp_path, blob, raw=False) == data


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_sushi_inflates_raw_stream_from_python(inflate_tool, tmp_path, name, level):
    data = CORPUS[name]
    blob = _py_deflate_raw(data, level)
    assert _sushi_inflate(inflate_tool, tmp_path, blob, raw=True) == data


# --------------------------------------------------------------------------
# Sushi compresses, Python decompresses. This is the test that matters.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_python_inflates_what_sushi_deflated(deflate_tool, tmp_path, name, level):
    data = CORPUS[name]
    blob = _sushi_deflate(deflate_tool, tmp_path, data, level, raw=False)
    assert zlib.decompress(blob) == data


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_python_inflates_raw_stream_from_sushi(deflate_tool, tmp_path, name, level):
    data = CORPUS[name]
    blob = _sushi_deflate(deflate_tool, tmp_path, data, level, raw=True)
    assert _py_inflate_raw(blob) == data


# --------------------------------------------------------------------------
# Properties that a silent fallback to stored blocks would otherwise hide.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", COMPRESSIBLE)
def test_compression_actually_happens(deflate_tool, tmp_path, name):
    """A redundant payload must come out smaller than it went in."""
    data = CORPUS[name]
    blob = _sushi_deflate(deflate_tool, tmp_path, data, 6, raw=True)
    assert len(blob) < len(data), f"{name}: {len(blob)} >= {len(data)}"


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_output_never_much_exceeds_input(deflate_tool, tmp_path, name):
    """Incompressible input falls back to stored, so growth stays bounded."""
    data = CORPUS[name]
    blob = _sushi_deflate(deflate_tool, tmp_path, data, 6, raw=True)
    budget = len(data) + 5 * (1 + len(data) // 65535)
    assert len(blob) <= budget, f"{name}: {len(blob)} > {budget}"


def test_level_out_of_range_is_rejected(deflate_tool, tmp_path):
    src = tmp_path / "in.bin"
    dst = tmp_path / "out.z"
    src.write_bytes(b"Mostly Harmless")
    r = subprocess.run([str(deflate_tool), str(src), str(dst), "10"],
                       capture_output=True)
    assert r.returncode == 1
    assert b"level" in r.stderr


def test_round_trip_through_both_sushi_tools(deflate_tool, inflate_tool, tmp_path):
    """Sushi to Sushi, with no Python in the middle."""
    data = CORPUS["readme"]
    blob = _sushi_deflate(deflate_tool, tmp_path, data, 9, raw=False)
    assert _sushi_inflate(inflate_tool, tmp_path, blob, raw=False) == data
