"""P1-T5: pack-aware type-argument inference (Pass 1.5 shared helper).

Exercises ``pack_inference.infer_flat_type_args`` directly with synthetic generic
functions + concrete argument types, asserting the FLAT instantiation key:

  - arity 0 / 1 / 2 packs (no leading type-params)
  - a leading-param + pack case
  - a NON-pack generic delegates and returns the legacy result unchanged

The leading-inference callback is a tiny stand-in that mirrors the real Pass 1.5
unification just enough for the shared helper's contract (it never sees the pack
value-param). Real `Type`s (BuiltinType i32/string/bool) are used, matching the
existing pack unit tests.
"""
import pytest

from sushi_lang.semantics.generics.types import TypeParameter, TypePack
from sushi_lang.semantics.generics.pack_inference import (
    infer_flat_type_args,
    has_pack_value_param,
)
from sushi_lang.semantics.typesys import BuiltinType, UnknownType

I32 = BuiltinType.I32
STR = BuiltinType.STRING
BOOL = BuiltinType.BOOL


def _tp(name, is_pack=False):
    tp = TypeParameter(name)
    if is_pack:
        object.__setattr__(tp, "is_pack", True)
    return tp


def _param(name, ty, is_pack=False):
    from sushi_lang.semantics.ast import Param
    return Param(loc=None, name=name, ty=ty, is_pack=is_pack)


def _generic(type_params, params):
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.ast import Block
    return GenericFuncDef(
        name="f",
        type_params=tuple(type_params),
        params=list(params),
        ret=None,
        body=Block(loc=None, statements=[]),
    )


def _leading(generic_func, leading_arg_types):
    """Minimal leading inference: unify non-pack params (UnknownType refs) 1:1.

    Returns leading type-args in non-pack type_param order, or None.
    """
    non_pack_params = [p for p in generic_func.params if not p.is_pack]
    if len(leading_arg_types) != len(non_pack_params):
        return None
    bindings = {}
    for arg_ty, param in zip(leading_arg_types, non_pack_params):
        if isinstance(param.ty, UnknownType):
            name = str(param.ty)
            if name in bindings and bindings[name] != arg_ty:
                return None
            bindings[name] = arg_ty
        elif param.ty != arg_ty:
            return None
    out = []
    for tp in generic_func.type_params:
        if getattr(tp, "is_pack", False):
            continue
        if tp.name not in bindings:
            return None
        out.append(bindings[tp.name])
    return tuple(out)


def _infer(generic_func, arg_types):
    return infer_flat_type_args(generic_func, arg_types, infer_leading=_leading)


# ---------------------------------------------------------------------------
# pack-only generic: fn f@(...Ts)(...Ts args)
# ---------------------------------------------------------------------------

def _pack_only():
    return _generic(
        type_params=[_tp("Ts", is_pack=True)],
        params=[_param("args", UnknownType("Ts"), is_pack=True)],
    )


def test_pack_arity2():
    g = _pack_only()
    assert _infer(g, [I32, STR]) == (I32, STR)


def test_pack_arity1():
    g = _pack_only()
    assert _infer(g, [BOOL]) == (BOOL,)


def test_pack_arity0_allowed():
    g = _pack_only()
    assert _infer(g, []) == ()


def test_has_pack_value_param():
    assert has_pack_value_param(_pack_only()) is True


# ---------------------------------------------------------------------------
# leading param + pack: fn f@(T, ...Ts)(T head, ...Ts rest)
# ---------------------------------------------------------------------------

def _leading_plus_pack():
    return _generic(
        type_params=[_tp("T"), _tp("Ts", is_pack=True)],
        params=[
            _param("head", UnknownType("T")),
            _param("rest", UnknownType("Ts"), is_pack=True),
        ],
    )


def test_leading_plus_pack():
    g = _leading_plus_pack()
    # f(1, "a", true) -> T=i32 (from head), trailing (string, bool)
    assert _infer(g, [I32, STR, BOOL]) == (I32, STR, BOOL)


def test_leading_plus_pack_empty_tail():
    g = _leading_plus_pack()
    # f(1) -> T=i32, empty pack
    assert _infer(g, [I32]) == (I32,)


def test_leading_plus_pack_too_few_args():
    g = _leading_plus_pack()
    # No leading arg for `head` -> inference fails.
    assert _infer(g, []) is None


# ---------------------------------------------------------------------------
# non-pack generic delegates unchanged: fn f@(T, U)(T a, U b)
# ---------------------------------------------------------------------------

def _non_pack():
    return _generic(
        type_params=[_tp("T"), _tp("U")],
        params=[
            _param("a", UnknownType("T")),
            _param("b", UnknownType("U")),
        ],
    )


def test_non_pack_delegates():
    g = _non_pack()
    # Identical to calling _leading directly with all args -> legacy result.
    assert _infer(g, [I32, STR]) == _leading(g, [I32, STR]) == (I32, STR)


def test_non_pack_no_value_param_flag():
    assert has_pack_value_param(_non_pack()) is False


def test_non_pack_arg_count_mismatch():
    g = _non_pack()
    assert _infer(g, [I32]) is None


# ---------------------------------------------------------------------------
# end-to-end: real front-end + Pass 1.5 collector discovers the pack key
# ---------------------------------------------------------------------------

_PROBE_SRC = """\
perk Display:
    fn display() string

extend i32 display() string:
    return Result.Ok("int")

extend string display() string:
    return Result.Ok(self)

fn print_all@(...Ts: Display)(...Ts args) ~:
    return Result.Ok(~)

fn main() i32:
    print_all(42, "hi")
    return Result.Ok(0)
"""


def test_collector_discovers_pack_instantiation():
    """Pass 1.5 must produce the flat key ('print_all', (i32, string))."""
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.passes.collect import CollectorPass
    from sushi_lang.semantics.generics.instantiate import InstantiationCollector

    program, _ = parse_to_ast(_PROBE_SRC)

    reporter = Reporter()
    collector = CollectorPass(reporter)
    tables = collector.run(program)

    inst = InstantiationCollector(
        struct_table=tables.structs.by_name,
        enum_table=tables.enums.by_name,
        generic_structs=tables.generic_structs.by_name,
        generic_funcs=tables.generic_funcs.by_name,
        tables=tables,
    )
    _type_inst, func_inst = inst.run(program)

    assert ("print_all", (I32, STR)) in func_inst
