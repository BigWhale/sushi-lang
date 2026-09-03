"""An enum constant is a `{i32 tag, [K x i64] data}` INITIALIZER, never a run-time construction (#551).

The behaviour tests under `tests/constants/enum_constants/` read the values back; this
gate pins the shape: the global carries the tag and the encoded payload, and the reader
loads it instead of building the variant with `insertvalue`.
"""
from __future__ import annotations

import re

from tests.unit.test_ffi import _emit_ir, _function_body


def _initializer_of(ir_text: str, name: str) -> str:
    """The text of the global constant `name`, whichever unit prefix it carries."""
    pattern = re.compile(r'@"(?:[^"]*\$)?' + re.escape(name) + r'" = .*')
    match = pattern.search(ir_text)
    assert match is not None, f"no global for {name}"
    return match.group(0)


_SIGN = (
    "enum Sign:\n"
    "    Plus\n"
    "    Minus\n"
    "\n"
)

_READING = (
    "enum Reading:\n"
    "    Empty\n"
    "    Sample(bool, u8, i64, f64)\n"
    "    Pair(i32, i32)\n"
    "\n"
)

_MAIN_READS = (
    "fn read() u64:\n"
    "    return Result.Ok(X.hash())\n"
    "\n"
    "fn main() i32:\n"
    "    println(read().realise(0))\n"
    "    return Result.Ok(0)\n"
)


def test_payload_free_variant_is_a_tag_over_a_zero_payload(tmp_path):
    """`Sign.Minus` is tag 1 and nothing else: the data words are the zero value."""
    ir_text = _emit_ir(tmp_path, _SIGN + "const Sign X = Sign.Minus\n\n" + _MAIN_READS)
    initializer = _initializer_of(ir_text, "X")
    assert "constant" in initializer, initializer
    assert "{i32 1, [1 x i64] zeroinitializer}" in initializer, initializer


def test_mixed_width_payload_packs_at_the_layout_offsets(tmp_path):
    """bool@0, u8@1, i64@8, f64@16: three words, each the little-endian bytes of its fields."""
    ir_text = _emit_ir(
        tmp_path, _READING + "const Reading X = Reading.Sample(true, 200, -5, 2.5)\n\n" + _MAIN_READS)
    initializer = _initializer_of(ir_text, "X")
    word0 = 1 | (200 << 8)
    word2 = int.from_bytes(bytes.fromhex("0000000000000440"), "little", signed=True)  # 2.5 as f64
    assert f"{{i32 1, [3 x i64] [i64 {word0}, i64 -5, i64 {word2}]}}" in initializer, initializer


def test_narrow_variant_fills_the_widest_variants_words(tmp_path):
    """`Pair(3, -4)` is one word of data in an enum whose widest variant needs three."""
    ir_text = _emit_ir(tmp_path, _READING + "const Reading X = Reading.Pair(3, -4)\n\n" + _MAIN_READS)
    initializer = _initializer_of(ir_text, "X")
    word0 = int.from_bytes((3).to_bytes(4, "little") + (-4).to_bytes(4, "little", signed=True),
                           "little", signed=True)
    assert f"{{i32 2, [3 x i64] [i64 {word0}, i64 0, i64 0]}}" in initializer, initializer


def test_string_payload_is_a_pointer_word(tmp_path):
    """The fat pointer's data word is a `ptrtoint` of the backing bytes, the size beside it."""
    src = (
        "enum Token:\n"
        "    End\n"
        "    Word(string)\n"
        "\n"
        'const Token X = Token.Word("abc")\n\n' + _MAIN_READS
    )
    initializer = _initializer_of(_emit_ir(tmp_path, src), "X")
    assert "ptrtoint" in initializer, initializer
    assert "i64 3]" in initializer, initializer


def test_the_reader_loads_and_never_constructs(tmp_path):
    """The reader loads the global; it emits no `insertvalue` to build the variant itself."""
    ir_text = _emit_ir(tmp_path, _SIGN + "const Sign X = Sign.Minus\n\n" + _MAIN_READS)
    body = _function_body(ir_text, "read")
    assert re.search(r'@"(?:[^"]*\$)?X"', body), body
    # A run-time construction names its values `<Enum>_<Variant>_tag`; the Result wrapper
    # around the hash is an `insertvalue` of its own, so the name is what is pinned.
    assert "Sign_Minus" not in body, body
