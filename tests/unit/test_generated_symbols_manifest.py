"""The stdlib symbol manifest is what CE5013 reads for a generated symbol (#472).

No semantic table holds `string_len` and its 145 siblings: the Python generators emit
them, and a normal compile only ever reads the bitcode they produced. So the build
writes down what it defined, and this gate holds that list to the artifact -- the one
failure mode a hand-kept list has.
"""
from __future__ import annotations

from pathlib import Path

import llvmlite.binding as llvm

from sushi_lang.backend.stdlib_builder import (
    _DIST_DIR, detect_platform, ensure_stdlib_built, read_generated_symbols,
)
from sushi_lang.semantics.externs_manifest import (
    GENERATED_INLINE_SYMBOLS, RESERVED_EXTERNS,
)


def _bitcode_definitions(platform_dir: Path) -> set[str]:
    """Every function the shipped bitcode DEFINES, read from the bitcode itself."""
    names: set[str] = set()
    for bc_path in sorted(platform_dir.rglob("*.bc")):
        module = llvm.parse_bitcode(bc_path.read_bytes())
        names |= {fn.name for fn in module.functions if not fn.is_declaration}
    return names


def test_manifest_matches_the_bitcode() -> None:
    """The list and the artifact agree. A renamed generator moves both or fails here."""
    platform = detect_platform()
    ensure_stdlib_built(platform)

    manifest = read_generated_symbols(platform)
    assert manifest, "the manifest is empty: nothing would be refused"
    assert manifest == _bitcode_definitions(_DIST_DIR / platform)


def test_manifest_holds_the_symbol_that_crashed() -> None:
    """`string_len` is the measured case: a clean build and a bus error (#472)."""
    ensure_stdlib_built()
    assert "string_len" in read_generated_symbols()


def test_the_two_halves_are_disjoint() -> None:
    """An inline symbol is in no bitcode file -- that is what makes it a second list.

    The day a generator starts DEFINING one of these in a unit, the inline set is the
    wrong home for it and this says so.
    """
    ensure_stdlib_built()
    assert not (GENERATED_INLINE_SYMBOLS & read_generated_symbols())


def test_a_reserved_extern_is_not_a_generated_symbol() -> None:
    """The two sets in the manifest module are opposites, and must not overlap.

    A libc name in RESERVED_EXTERNS may be declared with a matching signature; a
    generated name may not be declared at all. One name in both would make CE5013
    and CE5001 disagree about the same declaration.
    """
    ensure_stdlib_built()
    reserved = frozenset(RESERVED_EXTERNS)
    assert not (reserved & GENERATED_INLINE_SYMBOLS)
    assert not (reserved & read_generated_symbols())
