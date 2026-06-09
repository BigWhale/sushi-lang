"""P0-T3: pack value-parameter fan-out in the monomorphizer.

Phase 0 infrastructure (no surface syntax). A variadic-generic function
conceptually looks like ``fn f<...Ts>(Ts... args)``: a single value-parameter
whose type is a pack type-param. When monomorphized with a concrete pack
``(i32, string, bool)``, that ONE value-parameter fans out into N concrete
value-parameters -- one per pack element. Arity 0 makes the parameter vanish.

These checks drive the real ``Monomorphizer.monomorphize_function`` end-to-end
(so the functions.py wiring is exercised) and also unit-test
``TypeSubstitutor.expand_pack_param`` directly for the empty-pack and non-pack
branches.

Synthetic-fixture pattern mirrors test_p0t1/test_p0t2: the trailing
``TypeParameter`` carries ``is_pack=True`` set via ``object.__setattr__`` (frozen
dataclass); param types are concrete and the body is empty.
"""
import pytest

from sushi_lang.semantics.generics.types import TypeParameter, TypePack
from sushi_lang.semantics.generics.monomorphize.transformer import TypeSubstitutor
from sushi_lang.semantics.typesys import BuiltinType, UnknownType

I32 = BuiltinType.I32
STR = BuiltinType.STRING
BOOL = BuiltinType.BOOL


def _param(name, is_pack=False):
    tp = TypeParameter(name)
    if is_pack:
        object.__setattr__(tp, "is_pack", True)
    return tp


def _variadic_generic():
    """``fn log_all<...Ts>(string prefix, Ts... args)`` synthetic fixture.

    The pack VALUE-parameter ``args`` has a bare ``UnknownType("Ts")`` type --
    the AST-builder shape for a type-parameter reference -- so it is detected
    as pack-typed once ``Ts`` binds to a ``TypePack``.
    """
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.ast import Block, Param

    return GenericFuncDef(
        name="log_all",
        type_params=(_param("Ts", is_pack=True),),
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
# end-to-end fan-out via monomorphize_function
# ---------------------------------------------------------------------------

def test_fanout_arity3():
    mono = _make_mono()
    generic = _variadic_generic()

    fn = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR, BOOL))

    names = [p.name for p in fn.params]
    types = [p.ty for p in fn.params]
    assert names == ["prefix", "args_0", "args_1", "args_2"]
    assert types == [STR, I32, STR, BOOL]
    # Expanded params are ordinary (non-variadic) concrete params.
    assert all(p.is_variadic is False for p in fn.params)


def test_fanout_arity0_param_vanishes():
    mono = _make_mono()
    generic = _variadic_generic()

    fn = mono.function_monomorphizer.monomorphize_function(generic, ())

    # The pack-typed param expands to ZERO params; only the leading one remains.
    assert [p.name for p in fn.params] == ["prefix"]
    assert [p.ty for p in fn.params] == [STR]


def test_fanout_arity1():
    mono = _make_mono()
    generic = _variadic_generic()

    fn = mono.function_monomorphizer.monomorphize_function(generic, (BOOL,))

    assert [p.name for p in fn.params] == ["prefix", "args_0"]
    assert [p.ty for p in fn.params] == [STR, BOOL]


def test_fanout_preserves_spans():
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.ast import Block, Param

    nspan, tspan, loc = object(), object(), object()
    generic = GenericFuncDef(
        name="log_all",
        type_params=(_param("Ts", is_pack=True),),
        params=[
            Param(name="args", ty=UnknownType("Ts"),
                  name_span=nspan, type_span=tspan, loc=loc),
        ],
        ret=None,
        body=Block(loc=None, statements=[]),
    )

    mono = _make_mono()
    fn = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR))

    assert [p.name for p in fn.params] == ["args_0", "args_1"]
    for p in fn.params:
        assert p.name_span is nspan
        assert p.type_span is tspan
        assert p.loc is loc


# ---------------------------------------------------------------------------
# regular (non-pack) generic still 1:1 with identical fields
# ---------------------------------------------------------------------------

def test_regular_generic_one_param_per_param():
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.ast import Block, Param

    generic = GenericFuncDef(
        name="identity",
        type_params=(_param("T"),),
        params=[Param(loc=None, name="x", ty=UnknownType("T"))],
        ret=None,
        body=Block(loc=None, statements=[]),
    )

    mono = _make_mono()
    fn = mono.function_monomorphizer.monomorphize_function(generic, (I32,))

    assert len(fn.params) == 1
    assert fn.params[0].name == "x"
    assert fn.params[0].ty == I32
    assert fn.params[0].is_variadic is False


# ---------------------------------------------------------------------------
# direct expand_pack_param unit tests (empty-pack and non-pack branches)
# ---------------------------------------------------------------------------

def test_expand_pack_param_empty_pack_returns_empty_list():
    from sushi_lang.semantics.ast import Param

    sub = TypeSubstitutor(object())
    param = Param(loc=None, name="args", ty=UnknownType("Ts"))
    out = sub.expand_pack_param(param, {"Ts": TypePack(())})
    assert out == []


def test_expand_pack_param_pack_via_typeparameter_ref():
    from sushi_lang.semantics.ast import Param

    sub = TypeSubstitutor(object())
    # Pack-typed param expressed as a bare TypeParameter reference.
    param = Param(loc=None, name="args", ty=TypeParameter("Ts"))
    out = sub.expand_pack_param(param, {"Ts": TypePack((I32, STR))})
    assert [p.name for p in out] == ["args_0", "args_1"]
    assert [p.ty for p in out] == [I32, STR]


def test_expand_pack_param_non_pack_single_param_identical_fields():
    from sushi_lang.semantics.ast import Param

    sub = TypeSubstitutor(object())
    nspan, tspan, loc = object(), object(), object()
    param = Param(name="x", ty=UnknownType("T"),
                  name_span=nspan, type_span=tspan, loc=loc, is_variadic=True)

    out = sub.expand_pack_param(param, {"T": I32})

    assert len(out) == 1
    p = out[0]
    assert p.name == "x"
    assert p.ty == I32                 # substituted via substitute_type
    assert p.name_span is nspan
    assert p.type_span is tspan
    assert p.loc is loc
    assert p.is_variadic is True       # preserved exactly


def test_expand_pack_param_non_pack_concrete_type_unchanged():
    from sushi_lang.semantics.ast import Param

    sub = TypeSubstitutor(object())
    param = Param(loc=None, name="prefix", ty=STR)
    out = sub.expand_pack_param(param, {"Ts": TypePack((I32,))})
    assert len(out) == 1
    assert out[0].name == "prefix"
    assert out[0].ty == STR
