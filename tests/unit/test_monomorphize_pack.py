"""P0-TA: consolidated pack-monomorphization verification suite.

An INDEPENDENT, whole-contract check of the Phase-0 "pack-aware
monomorphization core" (no surface syntax). Authored by the verification
agent; it reuses the synthetic-fixture patterns from test_p0t1/t2/t3 but
focuses on the contract-level properties the plan calls out -- with emphasis
on the cache-identity and mangling-collision invariants:

1. Regression: a regular (non-pack) generic monomorphizes 1:1 with the
   historical mangled names and unchanged params; no-pack mangling unaffected.
2. 0/1/3-element packs produce pairwise-distinct mangled names end-to-end
   through monomorphize_function (not just mangle directly), none == base.
3. Cache identity: same (name, type_args) -> SAME FuncDef object and exactly
   one cache entry; two different arities -> two distinct cache entries.
4. Pairwise-unique mangling across an adversarial collision matrix.
5. Arity/placement validation raises ValueError (pack-not-last, two packs,
   no-pack arity mismatch with the historical message).
6. Scalar-position guard: a pack binding in scalar position raises.

These drive the REAL Monomorphizer.monomorphize_function end-to-end where the
property is about wiring (2, 3), and call the lower-level helpers directly
where the property is local (1's substitution, 4, 5, 6).
"""
import pytest

from sushi_lang.semantics.generics.types import TypeParameter, TypePack
from sushi_lang.semantics.generics.monomorphize.transformer import TypeSubstitutor
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.typesys import BuiltinType, UnknownType

I32 = BuiltinType.I32
STR = BuiltinType.STRING
BOOL = BuiltinType.BOOL


# ---------------------------------------------------------------------------
# fixture helpers (mirror test_p0t1/t2/t3)
# ---------------------------------------------------------------------------

def _param(name, is_pack=False):
    """A type-PARAMETER; the trailing one may carry the pack marker.

    TypeParameter is a frozen dataclass, so the Phase-0 ``is_pack`` marker is
    set via object.__setattr__ on synthetic fixtures (ast.py stays untouched).
    """
    tp = TypeParameter(name)
    if is_pack:
        object.__setattr__(tp, "is_pack", True)
    return tp


def _identity_generic():
    """``fn identity<T>(T x)`` -- a plain 1:1 generic (no pack)."""
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.ast import Block, Param

    return GenericFuncDef(
        name="identity",
        type_params=(_param("T"),),
        params=[Param(loc=None, name="x", ty=UnknownType("T"))],
        ret=None,
        body=Block(loc=None, statements=[]),
    )


def _pack_generic():
    """``fn f<T, ...Ts>(string prefix, Ts... args)`` synthetic fixture.

    A leading 1:1 type-param ``T``, a trailing pack ``Ts``, and a pack VALUE
    parameter ``args`` typed as a bare ``UnknownType("Ts")`` so it fans out.
    """
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.ast import Block, Param

    return GenericFuncDef(
        name="f",
        type_params=(_param("T"), _param("Ts", is_pack=True)),
        params=[
            Param(loc=None, name="prefix", ty=STR),
            Param(loc=None, name="args", ty=UnknownType("Ts")),
        ],
        ret=None,
        body=Block(loc=None, statements=[]),
    )


def _make_mono():
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.generics.monomorphize import Monomorphizer

    return Monomorphizer(reporter=Reporter())


# ---------------------------------------------------------------------------
# 1. Regression -- regular generic unchanged
# ---------------------------------------------------------------------------

def test_regular_generic_monomorphizes_to_historical_names_and_params():
    """identity<T> at i32/string yields the historical mangled names and a
    single 1:1 param whose type is the concrete substitution (no fan-out,
    no pack marker)."""
    mono = _make_mono()
    generic = _identity_generic()

    fn_i32 = mono.function_monomorphizer.monomorphize_function(generic, (I32,))
    assert fn_i32.name == "identity__i32"
    assert [p.name for p in fn_i32.params] == ["x"]
    assert [p.ty for p in fn_i32.params] == [I32]
    assert all(p.is_variadic is False for p in fn_i32.params)
    assert "." not in fn_i32.name  # no pack marker on a regular generic

    fn_str = mono.function_monomorphizer.monomorphize_function(generic, (STR,))
    assert fn_str.name == "identity__string"
    assert [p.ty for p in fn_str.params] == [STR]


def test_no_pack_mangling_output_unaffected():
    """The no-pack mangling path is byte-for-byte the historical output."""
    assert mangle_function_name("identity", (I32,)) == "identity__i32"
    assert mangle_function_name("swap", (I32, STR)) == "swap__i32_string"
    assert mangle_function_name("f", ()) == "f"
    # The keyword default must behave exactly like the positional no-pack call.
    assert mangle_function_name("f", (I32, STR), pack_arity=None) == "f__i32_string"


# ---------------------------------------------------------------------------
# 2. 0/1/3-element packs -> distinct mangled names (end-to-end)
# ---------------------------------------------------------------------------

def test_pack_arities_yield_distinct_names_end_to_end():
    """Driving the REAL monomorphize_function at pack arities 0, 1, 3 produces
    three pairwise-distinct symbols, none equal to the bare base name 'f'."""
    mono = _make_mono()
    generic = _pack_generic()  # f<T, ...Ts>: leading T, then the pack

    # arity 0: only leading T=i32, pack empty
    fn0 = mono.function_monomorphizer.monomorphize_function(generic, (I32,))
    # arity 1: leading T=i32, pack (string,)
    fn1 = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR))
    # arity 3: leading T=i32, pack (string, bool, i32)
    fn3 = mono.function_monomorphizer.monomorphize_function(
        generic, (I32, STR, BOOL, I32)
    )

    names = [fn0.name, fn1.name, fn3.name]
    assert len(set(names)) == 3, f"expected 3 distinct names, got {names}"
    assert all(n != "f" for n in names), names
    # The .pack{N} marker reflects the absorbed-arity, not the flat arg count.
    assert fn0.name == "f__i32.pack0"
    assert fn1.name == "f__i32_string.pack1"
    assert fn3.name == "f__i32_string_bool_i32.pack3"


# ---------------------------------------------------------------------------
# 3. Cache identity
# ---------------------------------------------------------------------------

def test_cache_returns_same_object_and_grows_by_one():
    """Same (name, type_args) twice through ONE Monomorphizer -> the identical
    FuncDef object, and func_cache grows by exactly one entry."""
    mono = _make_mono()
    generic = _pack_generic()
    cache = mono.func_cache

    before = len(cache)
    fn_a = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR))
    after_first = len(cache)
    fn_b = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR))
    after_second = len(cache)

    assert fn_a is fn_b, "second call must return the cached FuncDef object"
    assert after_first - before == 1, "first call adds exactly one cache entry"
    assert after_second == after_first, "cache hit must not add an entry"
    assert (generic.name, (I32, STR)) in cache


def test_distinct_arities_create_two_cache_entries():
    """Two DIFFERENT arities of the same function -> two distinct cache entries
    and two different FuncDef objects with different names."""
    mono = _make_mono()
    generic = _pack_generic()
    cache = mono.func_cache

    before = len(cache)
    fn1 = mono.function_monomorphizer.monomorphize_function(generic, (I32,))
    fn3 = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR, BOOL))
    after = len(cache)

    assert after - before == 2, "two distinct instantiations -> two cache entries"
    assert fn1 is not fn3
    assert fn1.name != fn3.name
    assert (generic.name, (I32,)) in cache
    assert (generic.name, (I32, STR, BOOL)) in cache


# ---------------------------------------------------------------------------
# 4. Pairwise-unique mangling collision matrix
# ---------------------------------------------------------------------------

class _NamedType:
    """A type whose ``str()`` is a plain CNAME-shaped identifier -- models a
    user type literally named e.g. ``pack2``, the adversarial worst case for
    the pack marker."""

    def __init__(self, name):
        self._name = name

    def __str__(self):
        return self._name


def test_mangling_matrix_is_pairwise_unique():
    """A stress matrix mixing: same base + different flat args; same flat args +
    different pack_arity; pack vs no-pack with the same base+args; and an
    adversarial type literally named 'pack2' as a no-pack arg vs a real
    pack_arity=2 instantiation. ALL produced symbols must be pairwise-unique."""
    pack2_named = _NamedType("pack2")

    cases = [
        # same base, different flat args (no pack)
        ("f", (I32,), None),
        ("f", (I32, STR), None),
        ("f", (STR, I32), None),
        # same flat args, different pack_arity
        ("f", (I32,), 0),
        ("f", (I32,), 1),
        ("f", (I32,), 2),
        # pack vs no-pack on the same base + empty args
        ("f", (), None),
        ("f", (), 0),
        ("f", (), 2),
        # adversarial: a type whose str() is literally "pack2" as a no-pack arg
        ("f", (pack2_named,), None),
        # ...versus a REAL arity-2 instantiation (empty flat args)
        # already covered by ("f", (), 2) above; add the flat-arg variant too
        ("f", (I32, STR), 2),
    ]

    symbols = [mangle_function_name(base, args, pack_arity=pa)
               for (base, args, pa) in cases]

    assert len(set(symbols)) == len(symbols), (
        "collision detected among mangled symbols:\n  "
        + "\n  ".join(f"{c} -> {s}" for c, s in zip(cases, symbols))
    )

    # Spot-check the adversarial structural separation explicitly.
    forged = mangle_function_name("f", (pack2_named,))      # "f__pack2"
    real = mangle_function_name("f", (), pack_arity=2)      # "f.pack2"
    assert forged == "f__pack2"
    assert real == "f.pack2"
    assert "." not in forged
    assert forged != real


# ---------------------------------------------------------------------------
# 5. Arity / placement validation raises
# ---------------------------------------------------------------------------

def _generic_with_typeparams(name, type_params):
    """Minimal GenericFuncDef carrying only the type_params we want to exercise
    in build_substitution (params/body irrelevant for the substitution check)."""
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.ast import Block

    return GenericFuncDef(
        name=name,
        type_params=tuple(type_params),
        params=[],
        ret=None,
        body=Block(loc=None, statements=[]),
    )


def test_pack_not_last_raises():
    mono = _make_mono()
    generic = _generic_with_typeparams(
        "f", [_param("Ts", is_pack=True), _param("T")]
    )
    with pytest.raises(ValueError):
        mono.function_monomorphizer.build_substitution(generic, (I32, STR))


def test_more_than_one_pack_raises():
    mono = _make_mono()
    generic = _generic_with_typeparams(
        "f", [_param("As", is_pack=True), _param("Bs", is_pack=True)]
    )
    with pytest.raises(ValueError):
        mono.function_monomorphizer.build_substitution(generic, (I32,))


def test_no_pack_arity_mismatch_raises_with_historical_message():
    mono = _make_mono()
    generic = _generic_with_typeparams("f", [_param("T"), _param("E")])
    with pytest.raises(ValueError) as exc:
        mono.function_monomorphizer.build_substitution(generic, (I32,))
    assert str(exc.value).startswith("Type argument count mismatch")


# ---------------------------------------------------------------------------
# 6. Scalar-position guard
# ---------------------------------------------------------------------------

def test_substitute_type_rejects_pack_in_scalar_position():
    """A bare pack reference in a scalar type position must raise: a TypePack is
    not a scalar Type and must never silently flow into substitute_type."""
    sub = TypeSubstitutor(_make_mono())
    mapping = {"Ts": TypePack((I32, STR))}

    with pytest.raises(ValueError):
        sub.substitute_type(TypeParameter("Ts"), mapping)
    with pytest.raises(ValueError):
        sub.substitute_type(UnknownType("Ts"), mapping)
