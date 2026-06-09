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
import pytest

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
    assert mangle_function_name("f", (), pack_arity=0) == "f.pack0"


def test_pack_marker_examples():
    assert mangle_function_name("f", (I32, STR, BOOL), pack_arity=2) == "f__i32_string_bool.pack2"
    assert mangle_function_name("f", (I32,), pack_arity=0) == "f__i32.pack0"


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


# ---------------------------------------------------------------------------
# (D) structural collision-freedom: the adversarial "pack2"-named-type case
# ---------------------------------------------------------------------------

class _NamedType:
    """A stand-in type whose ``str()`` is a plain CNAME-shaped identifier.

    Models a user type literally named ``pack2`` -- the worst case for the old
    ``__pack{N}`` marker, which shared the [A-Za-z0-9_] alphabet of sanitized
    type args and could thus be reproduced by the no-pack path.
    """

    def __init__(self, name):
        self._name = name

    def __str__(self):
        return self._name


def test_pack_named_type_cannot_forge_a_pack_symbol():
    # No-pack symbol for a single arg literally named "pack2".
    forged = mangle_function_name("f", (_NamedType("pack2"),))
    assert forged == "f__pack2"  # "__" separator, NOT ".pack2"

    # The real arity-2 empty-arg pack symbol uses the "." marker separator and
    # therefore CANNOT equal the forged no-pack symbol -- structurally.
    real_pack = mangle_function_name("f", (), pack_arity=2)
    assert real_pack == "f.pack2"
    assert "." not in forged
    assert forged != real_pack


# ---------------------------------------------------------------------------
# negative-arity guard
# ---------------------------------------------------------------------------

def test_negative_pack_arity_raises():
    with pytest.raises(ValueError, match="pack_arity must be >= 0"):
        mangle_function_name("f", (I32,), pack_arity=-1)


# ---------------------------------------------------------------------------
# end-to-end wiring: monomorphize_function names a trailing-pack generic
# ---------------------------------------------------------------------------

def _pack_generic():
    """Build a minimal generic ``f<T, ...Ts>(string prefix)`` fixture.

    Mirrors the synthetic-pack approach from test_p0t1_pack_substitution.py:
    the last TypeParameter carries ``is_pack=True`` set via object.__setattr__
    (frozen dataclass). Param types are concrete and the body is empty so the
    substitutor/body-walk are trivial passthroughs and only the naming path is
    exercised.
    """
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.generics.types import TypeParameter
    from sushi_lang.semantics.ast import Block, Param

    def param(name, is_pack=False):
        tp = TypeParameter(name)
        if is_pack:
            object.__setattr__(tp, "is_pack", True)
        return tp

    return GenericFuncDef(
        name="log_all",
        type_params=(param("T"), param("Ts", is_pack=True)),
        params=[Param(loc=None, name="prefix", ty=STR)],
        ret=None,
        body=Block(loc=None, statements=[]),
    )


def test_monomorphize_function_emits_dot_pack_marker():
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.generics.monomorphize import Monomorphizer

    generic = _pack_generic()
    mono = Monomorphizer(reporter=Reporter())

    # f<T, ...Ts> with flat args (i32, string, bool): leading T=i32, pack arity 2.
    fn3 = mono.function_monomorphizer.monomorphize_function(
        generic, (I32, STR, BOOL)
    )
    assert fn3.name == "log_all__i32_string_bool.pack2"

    # Arity-0 pack still distinct, still marked, does not collapse to leading.
    fn1 = mono.function_monomorphizer.monomorphize_function(generic, (I32,))
    assert fn1.name == "log_all__i32.pack0"
