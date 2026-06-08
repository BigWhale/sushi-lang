"""P0-T2: pack-aware generic name mangling.

Focused checks for ``mangle_function_name``'s optional ``pack_arity`` signal:

- (A) regression: the no-pack path is byte-for-byte unchanged;
- (B) arity distinctness: arity 0/1/3 packs yield pairwise-distinct symbols and
  an arity-0 pack does not collapse to the bare base name;
- (C) determinism: a pure function of its inputs;
- (D) collision-free vs regular generics: a pack symbol never equals the symbol
  a same-base same-flat-args non-pack instantiation would produce.

Real ``BuiltinType`` values exercise the sanitizer (the function uses
``str(arg)``: i32/string/bool).
"""
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.typesys import BuiltinType

I32 = BuiltinType.I32
STR = BuiltinType.STRING
BOOL = BuiltinType.BOOL


# ---------------------------------------------------------------------------
# (A) regression: no-pack path unchanged
# ---------------------------------------------------------------------------

def test_no_pack_two_args_unchanged():
    # Golden value captured from the historical implementation.
    assert mangle_function_name("f", (I32, STR)) == "f__i32_string"


def test_no_pack_empty_args_returns_base():
    assert mangle_function_name("f", ()) == "f"


def test_no_pack_keyword_default_matches_positional():
    assert mangle_function_name("f", (I32, STR), pack_arity=None) == "f__i32_string"


# ---------------------------------------------------------------------------
# (B) arity distinctness / no base collapse
# ---------------------------------------------------------------------------

def test_arity_distinctness():
    s0 = mangle_function_name("f", (), pack_arity=0)
    s1 = mangle_function_name("f", (I32,), pack_arity=1)
    s3 = mangle_function_name("f", (I32, STR, BOOL), pack_arity=3)
    assert len({s0, s1, s3}) == 3


def test_arity0_does_not_collapse_to_base():
    assert mangle_function_name("f", (), pack_arity=0) != "f"
    assert mangle_function_name("f", (), pack_arity=0) == "f__pack0"


def test_pack_marker_examples():
    assert mangle_function_name("f", (I32, STR, BOOL), pack_arity=2) == "f__i32_string_bool__pack2"
    assert mangle_function_name("f", (I32,), pack_arity=0) == "f__i32__pack0"


# ---------------------------------------------------------------------------
# (D) collision-free vs regular generics
# ---------------------------------------------------------------------------

def test_pack_symbol_never_equals_non_pack_same_flat_args():
    flat = (I32, STR)
    non_pack = mangle_function_name("f", flat)
    for arity in (0, 1, 2):
        assert mangle_function_name("f", flat, pack_arity=arity) != non_pack


def test_pack_symbol_never_equals_empty_non_pack():
    assert mangle_function_name("f", (), pack_arity=0) != mangle_function_name("f", ())


# ---------------------------------------------------------------------------
# (C) determinism
# ---------------------------------------------------------------------------

def test_determinism_no_pack():
    a = mangle_function_name("f", (I32, STR))
    b = mangle_function_name("f", (I32, STR))
    assert a == b


def test_determinism_pack():
    a = mangle_function_name("f", (I32, STR), pack_arity=2)
    b = mangle_function_name("f", (I32, STR), pack_arity=2)
    assert a == b
