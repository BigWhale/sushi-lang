"""The built-in-method seam and the typecheck pass validation must recognise the same families."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    ReferenceType,
    StructType,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM = SOURCE_ROOT / "semantics" / "generics" / "builtin_methods.py"
VALIDATION = SOURCE_ROOT / "semantics" / "passes" / "types" / "calls" / "methods.py"

# How a built-in family is recognised in either file.
FAMILY_PREDICATE = re.compile(
    r"\b(is_builtin_\w+_method|has_primitive_method|get_builtin_method)\b"
)

# Families validate_method_call consults that the seam deliberately does not.
#
# Perk implementations are the sanctioned override -- they win at all three layers on
# purpose, and CE2097's help text points users at them. Treating a perk method as a
# shadowing built-in would remove the only escape hatch, since Sushi has no opt-out from
# auto-derivation.
SEAM_EXEMPT: frozenset[str] = frozenset()


def _families(path: Path) -> set[str]:
    return set(FAMILY_PREDICATE.findall(path.read_text(encoding="utf-8")))


def test_seam_covers_every_family_validation_dispatches_on():
    """A family validation knows about but the seam does not means silent shadowing."""
    missing = sorted(_families(VALIDATION) - _families(SEAM) - SEAM_EXEMPT)
    assert not missing, (
        f"validate_method_call recognises these built-in families but the seam does not: "
        f"{missing}. An extension method with one of those names would be compiled and "
        f"then never called -- add it to builtin_method_exists."
    )


def test_seam_claims_no_family_validation_does_not_have():
    """The converse: a seam-only family would reject an extension that in fact works."""
    extra = sorted(_families(SEAM) - _families(VALIDATION))
    assert not extra, (
        f"the seam recognises these built-in families but validate_method_call does not: "
        f"{extra}. CE2097 would reject an extension method that would have dispatched fine."
    )


def test_the_gate_can_actually_see_families():
    """Guard against both sides silently reading as empty (a typo'd path, a renamed file)."""
    assert len(_families(SEAM)) >= 8
    assert len(_families(VALIDATION)) >= 8


# Behaviour, per family

def _list(elem="i32"):
    return StructType(name=f"List<{elem}>", fields=())


@pytest.mark.parametrize("receiver,method", [
    (DynamicArrayType(base_type=BuiltinType.I32), "len"),
    (ArrayType(base_type=BuiltinType.I32, size=3), "get"),
    (BuiltinType.STRING, "trim"),
    (BuiltinType.STRING, "to_str"),
    (BuiltinType.STRING, "hash"),
    (BuiltinType.I32, "to_str"),
    (BuiltinType.I32, "hash"),
    (BuiltinType.F64, "to_bits"),
    (EnumType(name="Result<i32, StdError>", variants=()), "is_ok"),
    (EnumType(name="Maybe<i32>", variants=()), "is_some"),
    (StructType(name="Own<i32>", fields=()), "get"),
    (StructType(name="HashMap<i32, i32>", fields=()), "insert"),
    (_list(), "push"),
])
def test_recognised(receiver, method):
    assert builtin_method_exists(receiver, method) is True


@pytest.mark.parametrize("receiver,method", [
    (DynamicArrayType(base_type=BuiltinType.I32), "squared"),
    (BuiltinType.STRING, "shout"),
    (BuiltinType.I32, "squared"),
    (BuiltinType.I32, "to_bits"),          # float-only
    (BuiltinType.BOOL, "to_bits"),
    (_list(), "sum_all"),                  # the real extension in tests/bugs
    (StructType(name="Point", fields=()), "describe"),
    # EVERY File method is an ordinary extension or perk implementation in <io/fs> now,
    # so the seam must claim none of them -- claiming one would make CE2097 reject the
    # real definition (HANDLES.md Phase 5). `lines()` was the last builtin and Phase 7d
    # removed it: line iteration is BufReader's, walked by the `next()` protocol.
    (StructType(name="File", fields=()), "write"),
    (StructType(name="File", fields=()), "close"),
    (StructType(name="File", fields=()), "lines"),
    (None, "hash"),
])
def test_not_recognised(receiver, method):
    assert builtin_method_exists(receiver, method) is False


def test_reference_receivers_unwrap():
    """Methods on &T are the methods on T, so a borrow must not hide a collision."""
    arr = DynamicArrayType(base_type=BuiltinType.I32)
    assert builtin_method_exists(ReferenceType(referenced_type=arr), "len")


def test_struct_auto_derived_pair_is_recognised(analyze):
    """The one registry-backed family -- registered from semantics in the derive pass."""
    analyze("""
struct P:
    i32 x

fn main() i32:
    let P p = P(1)
    println(p.hash())
    return Result.Ok(0)
""")
    point = StructType(name="P", fields=())
    assert builtin_method_exists(point, "hash") is True
    assert builtin_method_exists(point, "clone") is True
    assert builtin_method_exists(point, "describe") is False
