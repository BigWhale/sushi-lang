"""String Search Operations"""

from dataclasses import dataclass
from typing import Callable, Optional

import llvmlite.ir as ir
from ..intrinsics import declare_utf8_count_intrinsic
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types, get_maybe_type
from sushi_lang.backend.memory.allocas import entry_alloca


def emit_string_starts_with(module: ir.Module) -> ir.Function:
    """Emit `i8 string_starts_with({i8*, i32} str, {i8*, i32} prefix)`."""
    func_name = "string_starts_with"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    fn_ty = ir.FunctionType(i8, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "prefix"

    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    loop_cond_block = func.append_basic_block("loop_cond")
    loop_body_block = func.append_basic_block("loop_body")
    mismatch_block = func.append_basic_block("mismatch")
    match_block = func.append_basic_block("match")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    prefix_data = builder.extract_value(func.args[1], 0, name="prefix_data")
    prefix_size = builder.extract_value(func.args[1], 1, name="prefix_size")
    builder.branch(size_check_block)

    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", prefix_size, str_size, name="size_ok")
    builder.cbranch(size_ok, loop_cond_block, mismatch_block)

    builder = ir.IRBuilder(loop_cond_block)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    cond = builder.icmp_unsigned("<", i_phi, prefix_size, name="cond")
    builder.cbranch(cond, loop_body_block, match_block)

    builder = ir.IRBuilder(loop_body_block)
    str_ptr = builder.gep(str_data, [i_phi], name="str_ptr")
    prefix_ptr = builder.gep(prefix_data, [i_phi], name="prefix_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    prefix_ch = builder.load(prefix_ptr, name="prefix_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, prefix_ch, name="chars_equal")

    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, loop_body_block)

    builder.cbranch(chars_equal, loop_cond_block, mismatch_block)

    builder = ir.IRBuilder(mismatch_block)
    builder.ret(ir.Constant(i8, 0))

    builder = ir.IRBuilder(match_block)
    builder.ret(ir.Constant(i8, 1))

    return func


def emit_string_ends_with(module: ir.Module) -> ir.Function:
    """Emit `i8 string_ends_with({i8*, i32} str, {i8*, i32} suffix)`."""
    func_name = "string_ends_with"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    fn_ty = ir.FunctionType(i8, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "suffix"

    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    loop_cond_block = func.append_basic_block("loop_cond")
    loop_body_block = func.append_basic_block("loop_body")
    mismatch_block = func.append_basic_block("mismatch")
    match_block = func.append_basic_block("match")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    suffix_data = builder.extract_value(func.args[1], 0, name="suffix_data")
    suffix_size = builder.extract_value(func.args[1], 1, name="suffix_size")
    builder.branch(size_check_block)

    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", suffix_size, str_size, name="size_ok")
    offset = builder.sub(str_size, suffix_size, name="offset")
    builder.cbranch(size_ok, loop_cond_block, mismatch_block)

    builder = ir.IRBuilder(loop_cond_block)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    cond = builder.icmp_unsigned("<", i_phi, suffix_size, name="cond")
    builder.cbranch(cond, loop_body_block, match_block)

    builder = ir.IRBuilder(loop_body_block)
    str_index = builder.add(offset, i_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    suffix_ptr = builder.gep(suffix_data, [i_phi], name="suffix_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    suffix_ch = builder.load(suffix_ptr, name="suffix_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, suffix_ch, name="chars_equal")

    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, loop_body_block)

    builder.cbranch(chars_equal, loop_cond_block, mismatch_block)

    builder = ir.IRBuilder(mismatch_block)
    builder.ret(ir.Constant(i8, 0))

    builder = ir.IRBuilder(match_block)
    builder.ret(ir.Constant(i8, 1))

    return func


_Found = Callable[[ir.IRBuilder, "_Scan", Callable[[], ir.Value]], None]
_Match = Callable[[ir.IRBuilder, "_Scan"], tuple[ir.Value, ir.Value]]
_Exhausted = Callable[[ir.IRBuilder, Optional[ir.Value]], None]


@dataclass(frozen=True)
class _Scan:
    """The values of one search that a hook can read."""

    str_data: ir.Value
    str_size: ir.Value
    needle_size: ir.Value
    pos: ir.PhiInstr
    carry: Optional[ir.PhiInstr]


def _defined(module: ir.Module, func_name: str) -> Optional[ir.Function]:
    func = module.globals.get(func_name)
    if isinstance(func, ir.Function) and not func.is_declaration:
        return func
    return None


def _emit_search(
    module: ir.Module,
    func_name: str,
    return_type: ir.Type,
    *,
    on_exhausted: _Exhausted,
    on_found: Optional[_Found] = None,
    on_match: Optional[_Match] = None,
    carry: Optional[tuple[str, ir.Constant]] = None,
    reverse: bool = False,
) -> ir.Function:
    """Emit `return_type func_name({i8*, i32} str, {i8*, i32} needle)` as a naive byte search.

    `on_found` ends the search at the first match, and an empty needle is a match at the
    start of the scan. `on_match` continues the search at the position it gives, with
    the next `carry` value, and an empty needle goes to `on_exhausted` with the initial
    `carry`. Give exactly one of the two. `reverse` scans from the last start position
    down to 0.
    """
    if (on_found is None) == (on_match is None):
        raise ValueError("_emit_search takes exactly one of on_found and on_match")

    _i8, _i8_ptr, i32, _i64, string_type = get_string_types()

    fn_ty = ir.FunctionType(return_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "needle"

    entry_block = func.append_basic_block("entry")
    empty_needle_check = func.append_basic_block("empty_needle_check")
    size_check_block = func.append_basic_block("size_check")
    outer_loop_cond = func.append_basic_block("outer_loop_cond")
    outer_loop_body = func.append_basic_block("outer_loop_body")
    inner_loop_cond = func.append_basic_block("inner_loop_cond")
    inner_loop_body = func.append_basic_block("inner_loop_body")
    if on_match is not None:
        match_block = func.append_basic_block("inner_loop_match")
    inner_loop_mismatch = func.append_basic_block("inner_loop_mismatch")
    if on_found is not None:
        match_block = func.append_basic_block("found")
        exhausted_block = func.append_basic_block("not_found")
        empty_block = match_block
    else:
        exhausted_block = func.append_basic_block("return_count")
        empty_block = exhausted_block

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    needle_data = builder.extract_value(func.args[1], 0, name="needle_data")
    needle_size = builder.extract_value(func.args[1], 1, name="needle_size")
    builder.branch(empty_needle_check)

    builder = ir.IRBuilder(empty_needle_check)
    is_empty = builder.icmp_unsigned("==", needle_size, ir.Constant(i32, 0), name="is_empty")
    builder.cbranch(is_empty, empty_block, size_check_block)

    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", needle_size, str_size, name="size_ok")
    max_start = builder.sub(str_size, needle_size, name="max_start")
    builder.cbranch(size_ok, outer_loop_cond, exhausted_block)

    builder = ir.IRBuilder(outer_loop_cond)
    pos_phi = builder.phi(i32, name="pos")
    pos_phi.add_incoming(max_start if reverse else ir.Constant(i32, 0), size_check_block)
    carry_phi = None
    if carry is not None:
        carry_name, carry_initial = carry
        carry_phi = builder.phi(i32, name=carry_name)
        carry_phi.add_incoming(carry_initial, size_check_block)
    if reverse:
        pos_ok = builder.icmp_signed(">=", pos_phi, ir.Constant(i32, 0), name="pos_ok")
    else:
        pos_ok = builder.icmp_unsigned("<=", pos_phi, max_start, name="pos_ok")
    builder.cbranch(pos_ok, outer_loop_body, exhausted_block)

    scan = _Scan(str_data, str_size, needle_size, pos_phi, carry_phi)

    builder = ir.IRBuilder(outer_loop_body)
    builder.branch(inner_loop_cond)

    builder = ir.IRBuilder(inner_loop_cond)
    j_phi = builder.phi(i32, name="j")
    j_phi.add_incoming(ir.Constant(i32, 0), outer_loop_body)
    j_ok = builder.icmp_unsigned("<", j_phi, needle_size, name="j_ok")
    builder.cbranch(j_ok, inner_loop_body, match_block)

    builder = ir.IRBuilder(inner_loop_body)
    str_index = builder.add(pos_phi, j_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    needle_ptr = builder.gep(needle_data, [j_phi], name="needle_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    needle_ch = builder.load(needle_ptr, name="needle_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, needle_ch, name="chars_equal")

    j_next = builder.add(j_phi, ir.Constant(i32, 1), name="j_next")
    j_phi.add_incoming(j_next, inner_loop_body)

    builder.cbranch(chars_equal, inner_loop_cond, inner_loop_mismatch)

    if on_match is not None:
        builder = ir.IRBuilder(match_block)
        next_pos, next_carry = on_match(builder, scan)
        pos_phi.add_incoming(next_pos, match_block)
        if carry_phi is not None:
            carry_phi.add_incoming(next_carry, match_block)
        builder.branch(outer_loop_cond)

    builder = ir.IRBuilder(inner_loop_mismatch)
    if reverse:
        pos_step = builder.sub(pos_phi, ir.Constant(i32, 1), name="pos_prev")
    else:
        pos_step = builder.add(pos_phi, ir.Constant(i32, 1), name="pos_next")
    pos_phi.add_incoming(pos_step, inner_loop_mismatch)
    if carry_phi is not None:
        carry_phi.add_incoming(carry_phi, inner_loop_mismatch)
    builder.branch(outer_loop_cond)

    if on_found is not None:
        builder = ir.IRBuilder(match_block)
        empty_pos = str_size if reverse else ir.Constant(i32, 0)

        def found_pos() -> ir.Value:
            phi = builder.phi(i32, name="found_pos")
            phi.add_incoming(empty_pos, empty_needle_check)
            phi.add_incoming(pos_phi, inner_loop_cond)
            return phi

        on_found(builder, scan, found_pos)

    builder = ir.IRBuilder(exhausted_block)
    final_carry = None
    if carry_phi is not None:
        final_carry = builder.phi(i32, name=f"final_{carry_phi.name}")
        if empty_block is exhausted_block:
            final_carry.add_incoming(carry_initial, empty_needle_check)
        final_carry.add_incoming(carry_initial, size_check_block)
        final_carry.add_incoming(carry_phi, outer_loop_cond)
    on_exhausted(builder, final_carry)

    return func


def _ret_maybe_some(builder: ir.IRBuilder, maybe_type: ir.LiteralStructType, value: ir.Value) -> None:
    _i8, i8_ptr, i32, _i64, _string_type = get_string_types()
    data_array_ty = maybe_type.elements[1]
    undef_maybe = ir.Constant(maybe_type, ir.Undefined)
    maybe_with_tag = builder.insert_value(undef_maybe, ir.Constant(i32, 0), 0, name="maybe_some_tag")

    temp_alloca = entry_alloca(builder, data_array_ty, name="data_temp")
    builder.store(ir.Constant(data_array_ty, None), temp_alloca)
    data_ptr_i8 = builder.bitcast(temp_alloca, i8_ptr, name="data_ptr_i8")
    data_ptr_i32 = builder.bitcast(data_ptr_i8, ir.PointerType(i32), name="data_ptr_i32")
    builder.store(value, data_ptr_i32)
    packed_data = builder.load(temp_alloca, name="packed_data")
    maybe_complete = builder.insert_value(maybe_with_tag, packed_data, 1, name="maybe_some_data")
    builder.ret(maybe_complete)


def _ret_maybe_none(builder: ir.IRBuilder, maybe_type: ir.LiteralStructType) -> None:
    i32 = ir.IntType(32)
    data_array_ty = maybe_type.elements[1]
    undef_maybe_none = ir.Constant(maybe_type, ir.Undefined)
    maybe_none_with_tag = builder.insert_value(undef_maybe_none, ir.Constant(i32, 1), 0, name="maybe_none_tag")
    zero_data = ir.Constant(data_array_ty, None)
    maybe_none_complete = builder.insert_value(maybe_none_with_tag, zero_data, 1, name="maybe_none_data")
    builder.ret(maybe_none_complete)


def emit_string_contains(module: ir.Module) -> ir.Function:
    """Emit `i8 string_contains({i8*, i32} str, {i8*, i32} needle)`."""
    func = _defined(module, "string_contains")
    if func is not None:
        return func
    i8 = ir.IntType(8)
    return _emit_search(
        module, "string_contains", i8,
        on_found=lambda builder, _scan, _found_pos: builder.ret(ir.Constant(i8, 1)),
        on_exhausted=lambda builder, _carried: builder.ret(ir.Constant(i8, 0)),
    )


def _emit_string_find_at(module: ir.Module, func_name: str, reverse: bool) -> ir.Function:
    """Emit a search that answers the CHARACTER index of its match as `Maybe@(i32)`."""
    func = _defined(module, func_name)
    if func is not None:
        return func
    maybe_type = get_maybe_type(ir.IntType(32))
    utf8_count = declare_utf8_count_intrinsic(module)

    def on_found(builder: ir.IRBuilder, scan: _Scan, found_pos: Callable[[], ir.Value]) -> None:
        char_index = builder.call(utf8_count, [scan.str_data, found_pos()], name="char_index")
        _ret_maybe_some(builder, maybe_type, char_index)

    return _emit_search(
        module, func_name, maybe_type,
        on_found=on_found,
        on_exhausted=lambda builder, _carried: _ret_maybe_none(builder, maybe_type),
        reverse=reverse,
    )


def emit_string_find(module: ir.Module) -> ir.Function:
    """Emit `{i32, [1 x i64]} string_find({i8*, i32} str, {i8*, i32} needle)`."""
    return _emit_string_find_at(module, "string_find", reverse=False)


def emit_string_find_last(module: ir.Module) -> ir.Function:
    """Emit `{i32, [1 x i64]} string_find_last({i8*, i32} str, {i8*, i32} needle)`."""
    return _emit_string_find_at(module, "string_find_last", reverse=True)


def emit_string_count(module: ir.Module) -> ir.Function:
    """Emit `i32 string_count({i8*, i32} str, {i8*, i32} needle)`; matches do not overlap."""
    func = _defined(module, "string_count")
    if func is not None:
        return func
    i32 = ir.IntType(32)

    def on_match(builder: ir.IRBuilder, scan: _Scan) -> tuple[ir.Value, ir.Value]:
        if scan.carry is None:
            raise ValueError("string_count carries a count")
        count_incremented = builder.add(scan.carry, ir.Constant(i32, 1), name="count_incremented")
        pos_skip = builder.add(scan.pos, scan.needle_size, name="pos_skip")
        return pos_skip, count_incremented

    return _emit_search(
        module, "string_count", i32,
        on_match=on_match,
        carry=("count", ir.Constant(i32, 0)),
        on_exhausted=_ret_carried,
    )


def _ret_carried(builder: ir.IRBuilder, carried: Optional[ir.Value]) -> None:
    if carried is None:
        raise ValueError("string_count carries a count")
    builder.ret(carried)
