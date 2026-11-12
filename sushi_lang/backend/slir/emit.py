"""One `SlirFunction` into the LLVM function that the declaration round made for its symbol.

Every local is a slot in the entry block (`entry_alloca`, the one seam), except a `peek`
or a `poke` parameter: its local IS the incoming address (docs/design/ir.md 8.3). An
address local, which a `Ref` writes, has a slot of a pointer, and its storage is the
address in that slot (8.2). A value parameter is stored into its slot at the entry. `_0` is the return slot, and `return`
loads it. The signature comes from `call_signature`: a value that moves by address crosses
as a pointer, the callee copies a parameter into its slot, and `return` copies `_0` into
the slot of the caller. Each SLIR block is one LLVM block, in the order of the function. A statement
class or a terminator class that the emitter does not handle is an internal error. `StorageLive`, `StorageDead`, `BeginBorrow` and `EndBorrow` emit
nothing. A drop goes through the
one destroy seam of the backend.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from llvmlite import ir

from sushi_lang.backend.destructors import emit_value_destructor
from sushi_lang.backend.expressions.calls.stdlib import STDLIB_EMITTERS
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.functions.signature import CallSignature, call_signature
from sushi_lang.backend.runtime.closures import emit_indirect_call_into
from sushi_lang.backend.runtime.iterators import emit_next
from sushi_lang.backend.slir.externs import emit_extern
from sushi_lang.backend.slir.intrinsics import INTRINSICS
from sushi_lang.backend.slir.methods import emit_method
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.types.arrays.runs import emit_aggregate_copy
from sushi_lang.internals.errors import message_for, raise_internal_error
from sushi_lang.internals.errors.runtime import printf_conversions
from sushi_lang.backend.slir.values import (
    assign, operand_address, operand_value, place_address, type_of_operand,
    type_of_place,
)
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import BuiltinMethod, Intrinsic
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import (
    SlirBlock, SlirFunction, SlirLocal, address_locals)
from sushi_lang.semantics.slir.lower import RETURN_SLOT
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, IteratorType, deref_type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_function(codegen: LLVMCodegen, function: SlirFunction, *,
                  filename: Optional[str]) -> ir.Function:
    """Define the LLVM function of `function`, and answer it.

    The stage entry of the emitter: no pytest test may reach it (`compiler_stage_gate`).
    """
    llvm_fn = codegen.module.globals.get(function.symbol)
    if not isinstance(llvm_fn, ir.Function) or not llvm_fn.is_declaration:
        raise_internal_error("CE0000", filename=filename,
                             detail=f"the module holds no declaration of "
                                    f"'{function.symbol}' to define")
    entry = llvm_fn.append_basic_block(name="entry")
    state = FunctionState(codegen, function, llvm_fn, ir.IRBuilder(entry), filename)
    state.blocks = {block.id: llvm_fn.append_basic_block(name=_block_name(block))
                    for block in function.blocks}
    saved = codegen.builder, codegen.func
    codegen.builder, codegen.func = state.builder, llvm_fn
    try:
        _open(state)
        state.builder.branch(state.block(function.entry))
        for block in function.blocks:
            state.builder.position_at_end(state.block(block.id))
            for statement in block.statements:
                state.span = statement.span
                _statement(state, statement)
            state.span = block.terminator.span
            _terminator(state, block.terminator)
    finally:
        codegen.builder, codegen.func = saved
    return llvm_fn


#: The name of the LLVM block that ends in a trap of a bounds check, after its number.
_TRAP_BLOCKS = {"RE2020": "bounds_fail", "RE2028": "bounds_fail"}


def _block_name(block: SlirBlock) -> str:
    """`bb<n>`; the block of a bounds trap is `bb<n>_bounds_fail`, so the IR names it."""
    term = block.terminator
    suffix = _TRAP_BLOCKS.get(term.code) if isinstance(term, tm.Trap) else None
    return f"bb{block.id.index}" if suffix is None else f"bb{block.id.index}_{suffix}"


def _open(state: FunctionState) -> None:
    """The storage of each local, and the value parameters stored into their slots. The
    signature that the declaration made must agree with the locals and the conventions."""
    function, llvm_fn = state.function, state.llvm_fn
    signature = call_signature(_ll(state, function.locals[0]), [
        _ll(state, function.locals[local.index]).as_pointer() if convention.by_address
        else _ll(state, function.locals[local.index])
        for local, convention in function.params])
    declared = llvm_fn.function_type
    if declared != signature.function_type:
        state.fault(f"the declaration {declared} does not agree with the SLIR header "
                    f"{signature.function_type} (answers {declared.return_type} and "
                    f"{signature.function_type.return_type})")
    if signature.answer_by_address:
        state.answer = llvm_fn.args[-1]
    params = {local: (index, convention)
              for index, (local, convention) in enumerate(function.params)}
    addresses = address_locals(function)
    for local in function.locals:
        ll = _ll(state, local)
        param = params.get(local.id)
        if local.id in addresses:
            state.addresses[local.id] = entry_alloca(state.builder, ll.as_pointer(),
                                                     _name(local))
            continue
        if param is None:
            state.storage[local.id] = entry_alloca(state.builder, ll, _name(local))
            continue
        arg = llvm_fn.args[param[0]]
        if param[1].by_address:
            state.storage[local.id] = arg
            continue
        slot = entry_alloca(state.builder, ll, _name(local))
        if signature.by_address[param[0]]:
            emit_aggregate_copy(state.codegen, slot, arg, ll)
        else:
            state.builder.store(arg, slot)
        state.storage[local.id] = slot


def _write_address(state: FunctionState, place: p.Place, ref: r.Ref) -> None:
    """`_n = ref <mode> <place>`: the address of the place goes into the slot of the
    address local `_n`, which is its storage from here on (ir.md 8.2)."""
    slot = state.addresses.get(place.id) if isinstance(place, p.Local) else None
    if slot is None:
        state.fault(f"a Ref writes {place}, which is no address local")
    state.builder.store(place_address(state, ref.place), slot)


def _ll(state: FunctionState, local: SlirLocal) -> ir.Type:
    return state.codegen.types.ll_type(local.ty)


def _name(local: SlirLocal) -> str:
    return local.name or f"_{local.id.index}"


# -- statements ------------------------------------------------------------------------

def _statement(state: FunctionState, statement: st.SlirStatement) -> None:
    """`StorageLive` and `StorageDead` emit nothing: the emitter keeps every local in one
    slot for the whole function. A borrow region is a fact for the verifier alone."""
    if isinstance(statement, st.Assign) and isinstance(statement.rvalue, r.Ref):
        _write_address(state, statement.place, statement.rvalue)
    elif isinstance(statement, st.Assign):
        assign(state, statement.place, statement.rvalue)
    elif isinstance(statement, st.Call):
        _call(state, statement)
    elif isinstance(statement, st.Drop):
        _drop(state, statement.place)
    elif isinstance(statement, st.DropIfSet):
        _drop_if_set(state, statement)
    elif not isinstance(statement, (st.StorageLive, st.StorageDead, st.BeginBorrow,
                                    st.EndBorrow)):
        state.unhandled(statement)


def _drop(state: FunctionState, place: p.Place) -> None:
    """The destroy seam frees what the value owns: a string only when its owned flag is
    set, an enum through the destroy half of its lifecycle handler."""
    emit_value_destructor(state.codegen, place_address(state, place), type_of_place(state, place))


def _drop_if_set(state: FunctionState, statement: st.DropIfSet) -> None:
    """The drop runs only while the flag local holds `true`."""
    if state.type_of(statement.flag) != BuiltinType.BOOL:
        state.fault(f"the drop flag _{statement.flag.index} is no bool")
    flag = state.builder.load(state.storage_of(statement.flag))
    with state.builder.if_then(state.codegen.utils.as_i1(flag)):
        _drop(state, statement.place)


def _call(state: FunctionState, statement: st.Call) -> None:
    """The callee kind is SLIR lowering's answer: one row of `CALL_EMITTERS` for each."""
    CALL_EMITTERS[type(statement.callee)](state, statement)


def _direct(state: FunctionState, statement: st.Call) -> None:
    """A value for an `Owned` or a `Guaranteed` argument, the address of the place for an
    `InGuaranteed` or an `Inout` one. The argument types must agree with the callee. A C
    function, which the external table holds under its link name and its name, crosses
    the C boundary (`externs.py`)."""
    direct = statement.callee
    if not isinstance(direct, st.Direct):
        state.fault(f"the row of a direct call got a {type(direct).__name__} callee")
    if direct.module is not None:
        _stdlib(state, statement, direct)
        return
    extern = state.codegen.external_table.by_link_name(direct.symbol, direct.name)
    if extern is not None:
        emit_extern(state, statement, direct, extern)
        return
    llvm_fn = state.codegen.module.globals.get(direct.symbol)
    if not isinstance(llvm_fn, ir.Function):
        state.fault(f"the module declares no function '{direct.symbol}'")
    signature = _signature(state, statement)
    args = _arguments(state, statement, signature)
    dest = place_address(state, statement.dest)
    if signature.answer_by_address:
        args.append(dest)
    if [arg.type for arg in args] != [param.type for param in llvm_fn.args]:
        state.fault(f"the call of '{direct.symbol}' does not agree with its "
                    f"declaration {llvm_fn.function_type}")
    value = state.call(llvm_fn, args)
    if not signature.answer_by_address:
        state.builder.store(value, dest)


def _stdlib(state: FunctionState, statement: st.Call, direct: st.Direct) -> None:
    """A function of a registry stdlib module, through the row of its module in
    `STDLIB_EMITTERS`, with the value and the type of each operand. Each operand is
    `Guaranteed` (the verifier checks it), and every operand is evaluated before the row
    emits anything."""
    emit = STDLIB_EMITTERS.get(direct.module or "")
    if emit is None:
        state.fault(f"no stdlib row with operands emits the module '{direct.module}'")
    args = [(operand_value(state, operand), type_of_operand(state, operand))
            for operand, _ in statement.args]
    value = emit(state.codegen, direct.name, direct.symbol, args)
    state.builder.store(value, place_address(state, statement.dest))


def _builtin(state: FunctionState, statement: st.Call) -> None:
    """An intrinsic, through its row of `INTRINSICS`, and a method, through its family
    (`emit_method`). An intrinsic with no row is an internal error."""
    builtin = statement.callee
    if not isinstance(builtin, st.Builtin):
        state.fault(f"the row of a builtin call got a {type(builtin).__name__} callee")
    if isinstance(builtin.method, BuiltinMethod):
        value = emit_method(state, builtin.method, statement)
        if value is not None:
            state.builder.store(value, place_address(state, statement.dest))
        return
    if not isinstance(builtin.method, Intrinsic):
        state.unhandled(builtin.method)
    emit = INTRINSICS.get(builtin.method.name)
    if emit is None:
        state.unhandled(f"intrinsic {builtin.method.name}")
    state.builder.store(emit(state, statement), place_address(state, statement.dest))


def _indirect(state: FunctionState, statement: st.Call) -> None:
    """A call of a function value: the emitter reads the semantic type of the operand, loads
    `fn_ptr` and `env_ptr`, and passes the environment first."""
    callee = statement.callee
    if not isinstance(callee, st.Indirect):
        state.fault(f"the row of an indirect call got a {type(callee).__name__} callee")
    called = deref_type(type_of_operand(state, callee.operand))
    if isinstance(called, IteratorType):
        emit_next(state.codegen, operand_value(state, callee.operand), called.element_type,
                  place_address(state, statement.dest))
        return
    if not isinstance(called, FunctionType):
        state.fault(f"an indirect call of a value of {called}")
    value = operand_value(state, callee.operand)
    args = _arguments(state, statement, _signature(state, statement))
    emit_indirect_call_into(state.codegen, value, called, args,
                            place_address(state, statement.dest))


#: One row for each variant of `SlirCallee` (gate: `test_slir_call_dispatch_is_a_table.py`).
CALL_EMITTERS: dict[type, Callable[[FunctionState, st.Call], None]] = {
    st.Direct: _direct,
    st.Builtin: _builtin,
    st.Indirect: _indirect,
}


def _signature(state: FunctionState, statement: st.Call) -> CallSignature:
    """The signature of the callee at this call, from the types of the operands and of the
    destination: the one rule that the declaration of the callee reads too."""
    params = []
    for operand, convention in statement.args:
        ll = state.codegen.types.ll_type(type_of_operand(state, operand))
        params.append(ll.as_pointer() if convention.by_address else ll)
    return call_signature(state.codegen.types.ll_type(type_of_place(state, statement.dest)),
                          params)


def _arguments(state: FunctionState, statement: st.Call,
               signature: CallSignature) -> list[ir.Value]:
    """The LLVM arguments of a call, the answer slot not included. A value argument that
    moves by address is the address of its place: the callee copies the bytes into its own
    slot at its entry, so the place is read before anything changes it."""
    return [_argument(state, operand, convention, by_address)
            for (operand, convention), by_address
            in zip(statement.args, signature.by_address, strict=True)]


def _argument(state: FunctionState, operand: p.Operand, convention: Convention,
              by_address: bool) -> ir.Value:
    if not convention.by_address:
        return operand_address(state, operand) if by_address else operand_value(state, operand)
    if isinstance(operand, p.Move):
        state.fault(f"a {convention.value} argument is a move")
    return operand_address(state, operand)


# -- terminators -----------------------------------------------------------------------

def _terminator(state: FunctionState, terminator: tm.SlirTerminator) -> None:
    builder = state.builder
    if isinstance(terminator, tm.Goto):
        builder.branch(state.block(terminator.target))
    elif isinstance(terminator, tm.SwitchInt):
        _switch(state, terminator)
    elif isinstance(terminator, tm.Return):
        _return(state)
    elif isinstance(terminator, tm.Unreachable):
        builder.unreachable()
    elif isinstance(terminator, tm.Trap):
        _trap(state, terminator)
    else:
        state.unhandled(terminator)


def _return(state: FunctionState) -> None:
    """`ret` of the value in `_0`. An answer that moves by address is copied from `_0` into
    the slot of the caller, and the function answers `void`."""
    builder = state.builder
    if state.answer is None:
        builder.ret(builder.load(state.storage_of(RETURN_SLOT)))
        return
    emit_aggregate_copy(state.codegen, state.answer, state.storage_of(RETURN_SLOT),
                          _ll(state, state.function.locals[0]))
    builder.ret_void()


def _trap(state: FunctionState, terminator: tm.Trap) -> None:
    """The registry text is the printf format. A string operand fills `%.*s`: its size as
    an `int`, then its data. The error emitter exits, so the block ends in `unreachable`."""
    builder = state.builder
    values: list[ir.Value] = []
    for conversion, operand in zip(printf_conversions(message_for(terminator.code)),
                                   terminator.args, strict=True):
        value = operand_value(state, operand)
        if conversion == "%.*s":
            values += [builder.extract_value(value, 1), builder.extract_value(value, 0)]
        else:
            values.append(value)
    state.codegen.runtime.errors.emit_runtime_error_with_values(terminator.code, *values)
    builder.unreachable()


def _switch(state: FunctionState, terminator: tm.SwitchInt) -> None:
    """`otherwise: None` goes to a block of its own that holds `unreachable`."""
    value = operand_value(state, terminator.operand)
    if terminator.otherwise is None:
        default = state.llvm_fn.append_basic_block(name="unreachable")
        ir.IRBuilder(default).unreachable()
    else:
        default = state.block(terminator.otherwise)
    switch = state.builder.switch(value, default)
    for case, target in terminator.targets:
        switch.add_case(ir.Constant(value.type, case), state.block(target))
