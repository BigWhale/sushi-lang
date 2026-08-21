"""Differential test: the Sushi msgpack decoder against Python msgpack.

Each case packs one Python value with msgpack.packb, writes it to a file, runs
the compiled tests/stdlib/msgpack/helpers/dump.sushi tool on it, and compares
the tool's stdout against a Python mirror of mp_show.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import msgpack
import pytest

REPO = Path(__file__).parents[2]
DUMP_SRC = REPO / "tests" / "stdlib" / "msgpack" / "helpers" / "dump.sushi"

I64_MAX = 0x7FFFFFFFFFFFFFFF


def mp_show(v) -> str:
    """Mirror of the Sushi mp_show renderer."""
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return f"{v}u" if v > I64_MAX else str(v)
    if isinstance(v, float):
        bits = struct.unpack("<Q", struct.pack("<d", v))[0]
        return f"f64({bits})"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, bytes):
        return "bin(" + ",".join(str(b) for b in v) + ")"
    if isinstance(v, list):
        return "[" + ",".join(mp_show(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(f"{mp_show(k)}:{mp_show(val)}" for k, val in v.items()) + "}"
    raise TypeError(f"no mp_show mirror for {type(v)}")


@pytest.fixture(scope="module")
def dump_tool(tmp_path_factory):
    """Compile the dump helper once for the module."""
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    tmp = tmp_path_factory.mktemp("mpdump")
    out = tmp / "dump"
    result = subprocess.run(
        ["sushic", str(DUMP_SRC), "-o", str(out)],
        cwd=tmp, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return out


def deep_list(depth: int):
    v = 1
    for _ in range(depth):
        v = [v]
    return v


CORPUS = {
    "nil": None,
    "true": True,
    "false": False,
    "fixint_zero": 0,
    "fixint_max": 127,
    "neg_fixint_edge_-1": -1,
    "neg_fixint_edge_-32": -32,
    "int8_-33": -33,
    "uint8_255": 255,
    "uint16_256": 256,
    "uint32": 3735928559,
    "i64_min": -(2 ** 63),
    "i64_max": 2 ** 63 - 1,
    "u64_above_i64": 2 ** 63,
    "u64_max": 2 ** 64 - 1,
    "float64": 3.14,
    "float64_neg": -0.5,
    "str_empty": "",
    "str_short": "Mostly Harmless",
    "str_utf8": "café šuši 🍣",
    "str_len15": "a" * 15,
    "str_len16": "a" * 16,
    "str_len31": "a" * 31,
    "str_len32": "a" * 32,
    "str_len255": "b" * 255,
    "str_len256": "b" * 256,
    "str_len65535": "c" * 65535,
    "str_len65536": "c" * 65536,
    "bin_empty": b"",
    "bin_short": b"\x00\x01\xfe\xff",
    "bin_len255": bytes(range(256))[:255],
    "bin_len256": bytes(range(256)),
    "arr_empty": [],
    "arr_small": [1, 2, 3],
    "arr_len15": list(range(15)),
    "arr_len16": list(range(16)),
    "arr_len65535": [0] * 65535,
    "arr_len65536": [0] * 65536,
    "arr_mixed": [None, True, -1, "x", b"\x01", [2], {"k": 3}],
    "arr_deep": deep_list(30),
    "map_empty": {},
    "map_small": {"k": 42},
    "map_ordered": {"z": 1, "a": 2, "m": 3},
    "map_len15": {f"k{i:02d}": i for i in range(15)},
    "map_len16": {f"k{i:02d}": i for i in range(16)},
    "map_nested": {"outer": {"inner": [1, {"deep": None}]}},
    "map_nonstring_keys": {1: "one", None: "nil-key"},
}


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_decodes_like_python(dump_tool, tmp_path, name):
    value = CORPUS[name]
    blob = msgpack.packb(value, use_bin_type=True)
    case = tmp_path / "case.mp"
    case.write_bytes(blob)
    r = subprocess.run([str(dump_tool), str(case)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout == mp_show(value) + "\n"


def test_float32_widens_to_f64(dump_tool, tmp_path):
    # 1.5 is exact in float32, so the widened f64 bits equal the f64 of 1.5.
    blob = msgpack.packb(1.5, use_single_float=True)
    assert blob[0] == 0xCA
    case = tmp_path / "case.mp"
    case.write_bytes(blob)
    r = subprocess.run([str(dump_tool), str(case)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout == mp_show(1.5) + "\n"
