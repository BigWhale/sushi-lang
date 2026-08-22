"""AST-builder gate: type-pack params, v2 pack value-params, expand nodes."""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.ast import Expand, Name, Block
from sushi_lang.semantics.typesys import DynamicArrayType, UnknownType


def _funcdef(src: str, name: str):
    program, _tree = parse_to_ast(src)
    fn = next(f for f in program.functions if f.name == name)
    return fn


def test_type_pack_param_and_pack_value_param() -> None:
    src = (
        "fn print_all@(...Ts: Display)(...Ts args) ~:\n"
        "    return Result.Ok(~)\n"
    )
    fn = _funcdef(src, "print_all")

    # One type-pack type-param.
    assert fn.type_params is not None
    assert len(fn.type_params) == 1
    tp = fn.type_params[0]
    assert tp.is_pack is True
    assert tp.name == "Ts"
    assert tp.constraints == ["Display"]

    # One v2 pack value-param.
    assert len(fn.params) == 1
    p = fn.params[0]
    assert p.name == "args"
    assert p.is_pack is True
    assert p.is_variadic is False
    # ty is the bare pack-name reference, NOT a collected dynamic array.
    assert not isinstance(p.ty, DynamicArrayType)
    assert str(p.ty) == "Ts"
    assert getattr(p.ty, "name", None) == "Ts"


def test_v1_native_variadic_unchanged() -> None:
    src = (
        "fn log_all(string prefix, ...i32 values) ~:\n"
        "    return Result.Ok(~)\n"
    )
    fn = _funcdef(src, "log_all")

    # No type params -> every variadic stays v1.
    assert not fn.type_params

    values = next(p for p in fn.params if p.name == "values")
    assert values.is_variadic is True
    assert values.is_pack is False
    assert isinstance(values.ty, DynamicArrayType)
    assert str(values.ty.base_type) == "i32"


def test_unconstrained_type_pack() -> None:
    src = (
        "fn g@(...Ts)(...Ts xs) ~:\n"
        "    return Result.Ok(~)\n"
    )
    fn = _funcdef(src, "g")

    tp = fn.type_params[0]
    assert tp.is_pack is True
    assert tp.constraints == []

    p = fn.params[0]
    assert p.is_pack is True
    assert p.is_variadic is False
    assert isinstance(p.ty, UnknownType)
    assert p.ty.name == "Ts"


def test_expand_statement_builds_expand_node() -> None:
    # Put the expand inside a normal function body for this AST test even though
    # it would not fully type-check; we only assert the built node shape.
    src = (
        "fn h(i32[] args) ~:\n"
        "    expand(a in args):\n"
        "        println(a)\n"
        "    return Result.Ok(~)\n"
    )
    fn = _funcdef(src, "h")

    stmt = fn.body.statements[0]
    assert isinstance(stmt, Expand)
    assert stmt.var == "a"
    assert isinstance(stmt.iterable, Name)
    assert stmt.iterable.id == "args"
    assert isinstance(stmt.body, Block)
    # Body contains the println as a single statement.
    assert len(stmt.body.statements) == 1
