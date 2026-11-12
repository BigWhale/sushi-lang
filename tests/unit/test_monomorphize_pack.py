"""Pack monomorphization: the substitution model, the mangled names and the cache.

One file holds the whole rule. A trailing pack parameter takes the trailing arguments into a
`TypePack`; a `TypePack` never stands in a scalar position; a pack instance has a name with a
`.pack{N}` marker, and no no-pack name can equal it. A generic with no pack keeps its
historical names and its 1:1 substitution.
"""
import types as _pytypes

import pytest

from sushi_lang.semantics.ast import Block, Param
from sushi_lang.semantics.generics.monomorphize.functions import FunctionMonomorphizer
from sushi_lang.semantics.generics.monomorphize.transformer import TypeSubstitutor
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.generics.types import TypeParameter, TypePack
from sushi_lang.semantics.typesys import BuiltinType, UnknownType

I32 = BuiltinType.I32
STR = BuiltinType.STRING
BOOL = BuiltinType.BOOL
F32 = BuiltinType.F32


# Fixture helpers

class _FakeMono:
    """Minimal stand-in for Monomorphizer."""

    def __init__(self):
        self.constraint_calls = []

    def _validate_type_constraints(self, params, args, key=None, template_file=None,
                                   error_params=None):
        # The seam answers whether every constraint held (#579); the fake refuses nothing.
        self.constraint_calls.append((tuple(params), tuple(args)))
        return True


def _generic(name, type_params):
    """A stand-in `GenericFuncDef` for `build_substitution` with the fake monomorphizer."""
    g = _pytypes.SimpleNamespace()
    g.name = name
    g.type_params = list(type_params)
    # The signature and the body of a `GenericFuncDef`, with its defaults: the
    # constraint check reads them for the E positions (E3).
    g.params = []
    g.ret = None
    g.body = Block(statements=[], loc=None)
    g.err_type = None
    g.name_span = None
    g.ret_span = None
    g.err_span = None
    return g


def _param(name, is_pack=False):
    """A type PARAMETER; the trailing one may carry the pack marker."""
    tp = TypeParameter(name)
    if is_pack:
        # The collect pass sets the pack marker as an attribute on synthetic fixtures;
        # ast.py is intentionally untouched. object.__setattr__ because
        # TypeParameter is a frozen dataclass.
        object.__setattr__(tp, "is_pack", True)
    return tp


def _identity_generic():
    """``fn identity<T>(T x)``: a plain 1:1 generic (no pack)."""
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef

    return GenericFuncDef(
        name="identity",
        type_params=(_param("T"),),
        params=[Param(loc=None, name="x", ty=UnknownType("T"))],
        ret=None,
        body=Block(loc=None, statements=[]),
    )


def _pack_generic():
    """``fn f<T, ...Ts>(string prefix, Ts... args)``: a synthetic fixture."""
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef

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


# The substitution: a regular generic is 1:1

def test_regular_generic_builds_1to1_substitution():
    mono = _FakeMono()
    fm = FunctionMonomorphizer(mono)
    generic = _generic("f", [_param("T"), _param("E")])

    sub = fm.build_substitution(generic, (I32, STR))

    assert sub == {"T": I32, "E": STR}
    # Perk constraints validated exactly as before: full param/arg lists, 1:1.
    assert mono.constraint_calls == [
        ((generic.type_params[0], generic.type_params[1]), (I32, STR))
    ]


def test_regular_generic_arity_mismatch_raises():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("T"), _param("E")])
    with pytest.raises(ValueError, match="Type argument count mismatch"):
        fm.build_substitution(generic, (I32,))


# The substitution: a trailing pack absorbs the trailing arguments

def test_trailing_pack_absorbs_all_trailing_args():
    mono = _FakeMono()
    fm = FunctionMonomorphizer(mono)
    # f<T, ...Ts>: leading T binds 1:1, Ts is a pack.
    generic = _generic("f", [_param("T"), _param("Ts", is_pack=True)])

    sub = fm.build_substitution(generic, (I32, STR, F32))

    assert sub["T"] == I32
    assert sub["Ts"] == TypePack((STR, F32))
    # Every parameter and every argument: the pack's elements are checked too (#797).
    assert mono.constraint_calls == [((generic.type_params[0], generic.type_params[1]),
                                      (I32, STR, F32))]


def test_pack_absorbs_zero_trailing_args():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("T"), _param("Ts", is_pack=True)])

    sub = fm.build_substitution(generic, (I32,))

    assert sub["T"] == I32
    assert sub["Ts"] == TypePack(())  # empty pack is valid


def test_pack_only_generic():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("Ts", is_pack=True)])

    sub = fm.build_substitution(generic, (I32, STR))

    assert sub == {"Ts": TypePack((I32, STR))}


def test_pack_not_last_raises():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("Ts", is_pack=True), _param("T")])
    with pytest.raises(ValueError, match="not the\\s+last"):
        fm.build_substitution(generic, (I32, STR))


def test_more_than_one_pack_raises():
    fm = FunctionMonomorphizer(_FakeMono())
    generic = _generic("f", [_param("As", is_pack=True), _param("Bs", is_pack=True)])
    with pytest.raises(ValueError, match="at most one"):
        fm.build_substitution(generic, (I32,))


def test_too_few_args_for_leading_params_raises():
    fm = FunctionMonomorphizer(_FakeMono())
    # f<A, B, ...Ts>: needs at least 2 leading args.
    generic = _generic("f", [_param("A"), _param("B"), _param("Ts", is_pack=True)])
    with pytest.raises(ValueError, match="at least 2"):
        fm.build_substitution(generic, (I32,))


# The scalar-position guard

def test_substitute_type_rejects_pack_in_scalar_position_typeparam():
    sub = TypeSubstitutor(_make_mono())
    mapping = {"Ts": TypePack((I32, STR))}
    with pytest.raises(ValueError, match="scalar type position"):
        sub.substitute_type(TypeParameter("Ts"), mapping)


def test_substitute_type_rejects_pack_in_scalar_position_unknowntype():
    sub = TypeSubstitutor(_make_mono())
    mapping = {"Ts": TypePack((I32,))}
    with pytest.raises(ValueError, match="scalar type position"):
        sub.substitute_type(UnknownType("Ts"), mapping)


def test_substitute_type_scalar_binding_unchanged():
    sub = TypeSubstitutor(_FakeMono())
    mapping = {"T": I32}
    assert sub.substitute_type(TypeParameter("T"), mapping) == I32
    assert sub.substitute_type(UnknownType("T"), mapping) == I32


# The public shape of TypePack

def test_typepack_str_and_hash():
    p = TypePack((I32, STR))
    assert str(p) == "pack(i32, string)"
    # frozen dataclass: hashable + value equality
    assert p == TypePack((I32, STR))
    assert hash(p) == hash(TypePack((I32, STR)))
    assert {p}  # usable in a set


# The mangled names: the no-pack path is the historical output

def test_no_pack_mangling_output_unaffected():
    """The no-pack mangling path is byte-for-byte the historical output."""
    assert mangle_function_name("identity", (I32,)) == "identity__i32"
    assert mangle_function_name("swap", (I32, STR)) == "swap__i32_string"
    assert mangle_function_name("f", (I32, STR)) == "f__i32_string"
    assert mangle_function_name("f", ()) == "f"
    # The keyword default must behave exactly like the positional no-pack call.
    assert mangle_function_name("f", (I32, STR), pack_arity=None) == "f__i32_string"


def test_regular_generic_monomorphizes_to_historical_names_and_params():
    """identity<T> at i32/string yields the historical mangled names and a single 1:1 param whose
    type is the concrete substitution (no fan-out, no pack marker).
    """
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


# The mangled names: a pack has a marker, and the arity is in it

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


def test_pack_symbol_never_equals_non_pack_same_flat_args():
    flat = (I32, STR)
    non_pack = mangle_function_name("f", flat)
    for arity in (0, 1, 2):
        assert mangle_function_name("f", flat, pack_arity=arity) != non_pack


def test_determinism():
    assert mangle_function_name("f", (I32, STR)) == mangle_function_name("f", (I32, STR))
    assert (mangle_function_name("f", (I32, STR), pack_arity=2)
            == mangle_function_name("f", (I32, STR), pack_arity=2))


def test_negative_pack_arity_raises():
    with pytest.raises(ValueError, match="pack_arity must be >= 0"):
        mangle_function_name("f", (I32,), pack_arity=-1)


class _NamedType:
    """A type whose ``str()`` is a plain CNAME-shaped identifier: it models a user type
    literally named e.g. ``pack2``, the adversarial worst case for the pack marker.
    """

    def __init__(self, name):
        self._name = name

    def __str__(self):
        return self._name


def test_mangling_matrix_is_pairwise_unique():
    """A stress matrix mixing: same base + different flat args; same flat args + different
    pack_arity; pack vs no-pack with the same base+args; and an adversarial type literally named
    'pack2' as a no-pack arg vs a real pack_arity=2 instantiation. ALL produced symbols must be
    pairwise-unique.
    """
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
        + "\n  ".join(f"{c} -> {s}" for c, s in zip(cases, symbols, strict=True))
    )

    # Spot-check the adversarial structural separation explicitly: the "." marker
    # separator cannot be forged by a type name.
    forged = mangle_function_name("f", (pack2_named,))      # "f__pack2"
    real = mangle_function_name("f", (), pack_arity=2)      # "f.pack2"
    assert forged == "f__pack2"
    assert real == "f.pack2"
    assert "." not in forged
    assert forged != real


# The names that the real monomorphizer gives

def test_pack_arities_yield_distinct_names_end_to_end():
    """Driving the REAL monomorphize_function at pack arities 0, 1, 2, 3 produces pairwise-distinct
    symbols, none equal to the bare base name 'f'.
    """
    mono = _make_mono()
    generic = _pack_generic()  # f<T, ...Ts>: leading T, then the pack

    # arity 0: only leading T=i32, pack empty
    fn0 = mono.function_monomorphizer.monomorphize_function(generic, (I32,))
    # arity 1: leading T=i32, pack (string,)
    fn1 = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR))
    # arity 2: leading T=i32, pack (string, bool)
    fn2 = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR, BOOL))
    # arity 3: leading T=i32, pack (string, bool, i32)
    fn3 = mono.function_monomorphizer.monomorphize_function(
        generic, (I32, STR, BOOL, I32)
    )

    names = [fn0.name, fn1.name, fn2.name, fn3.name]
    assert len(set(names)) == 4, f"expected 4 distinct names, got {names}"
    assert all(n != "f" for n in names), names
    # The .pack{N} marker reflects the absorbed-arity, not the flat arg count.
    assert fn0.name == "f__i32.pack0"
    assert fn1.name == "f__i32_string.pack1"
    assert fn2.name == "f__i32_string_bool.pack2"
    assert fn3.name == "f__i32_string_bool_i32.pack3"


# The cache

def test_cache_returns_same_object_and_grows_by_one():
    """Same (name, type_args) twice through ONE Monomorphizer -> the identical FuncDef object, and
    func_cache grows by exactly one entry.
    """
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
    assert (generic.unit_name, generic.name, (I32, STR)) in cache


def test_distinct_arities_create_two_cache_entries():
    """Two DIFFERENT arities of the same function -> two distinct cache entries and two different
    FuncDef objects with different names.
    """
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
    assert (generic.unit_name, generic.name, (I32,)) in cache
    assert (generic.unit_name, generic.name, (I32, STR, BOOL)) in cache
