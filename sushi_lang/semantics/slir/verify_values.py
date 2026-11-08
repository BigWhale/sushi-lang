"""The verifier rules of a function value (docs/design/ir.md 8.6, 8.13).

Each `Convention` of an `Indirect` call matches the parameter modes of the `FunctionType` of
its operand, because the modes are part of the type. A function value is the one owning value
beside a `string` that may come as a copy: a named function owns no heap, and `borrow` adopts
it (`copies_with_no_transfer`). In a function that lowering cut from a lambda, a capture place
is never moved out of, and the environment is never written as a whole: a caller can call the
closure two times or more (`capture_places`). The `Indirect` call of an `IteratorValue` walk
has an iterator as its operand, and no argument. A local that holds an iterator with a stack
state never reaches `_0` or an `Owned` argument (`stack_iterators`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.param_modes import CalleeKind, effective_modes
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import SlirFunction
from sushi_lang.semantics.slir.ids import LocalId
from sushi_lang.semantics.slir.verify_rules import CAPTURE_PLACES, CONVENTIONS, STACK_ITERATOR
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, IteratorType, Type, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.verify import Check


def copies_with_no_transfer(ty: Optional[Type]) -> bool:
    """A value of this type can own nothing while its type says that it owns: a `string` from a
    literal, and a function value of a named function."""
    return ty == BuiltinType.STRING or isinstance(ty, FunctionType)


def indirect_conventions(check: Check, stmt: st.Call, callee: st.Indirect) -> None:
    ty = check.operand_type(callee.operand, stmt.span)
    called = None if ty is None else deref_type(ty)
    if isinstance(called, IteratorType):
        if stmt.args:
            check.fail(CONVENTIONS, "the step of an iterator takes no argument", stmt.span)
        return
    if not isinstance(called, FunctionType):
        check.fail(CONVENTIONS, f"the operand of an indirect call has the type {ty}, which is "
                                f"no function type", stmt.span)
    wanted = tuple(mode.convention
                   for mode in effective_modes(called.modes, CalleeKind.INDIRECT))
    given = tuple(convention for _, convention in stmt.args)
    if len(given) != len(wanted):
        check.fail(CONVENTIONS, f"the indirect call has {len(given)} arguments, and its function "
                                f"type declares {len(wanted)}", stmt.span)
    for index, (have, want) in enumerate(zip(given, wanted, strict=True)):
        if have is not want:
            check.fail(CONVENTIONS, f"argument {index} of the indirect call is @{have.value}, "
                                    f"and its parameter is @{want.value}", stmt.span)


def capture_places(check: Check, function: SlirFunction) -> None:
    """No `Move` has its root in the environment, and no statement writes the environment."""
    env = function.environment
    if env is None:
        return
    root = p.Local(env)
    for block in function.blocks:
        for stmt in block.statements:
            if isinstance(stmt, st.Assign):
                operands, written = r.operands_of(stmt.rvalue), stmt.place
            elif isinstance(stmt, st.Call):
                operands, written = tuple(operand for operand, _ in stmt.args), stmt.dest
            else:
                continue
            if written == root:
                check.fail(CAPTURE_PLACES, f"writes the environment _{env.index}", stmt.span)
            for operand in operands:
                if isinstance(operand, p.Move) and p.locals_read(operand.place)[:1] == (env,):
                    check.fail(CAPTURE_PLACES, f"moves {operand.place} out of the environment",
                               stmt.span)


def stack_iterators(check: Check, function: SlirFunction) -> None:
    """Each local that an `AggIterator` with a stack state writes, and each local that it moves,
    copies or puts into an aggregate, stays in the function."""
    held = _stack_state_locals(function)
    for block in function.blocks:
        for stmt in block.statements:
            if isinstance(stmt, st.Call):
                for operand, convention in stmt.args:
                    if convention.consumes and _reads_one_of(operand, held):
                        check.fail(STACK_ITERATOR, f"{operand} gives a stack state to an owned "
                                                   f"argument", stmt.span)
            if (isinstance(stmt, st.Assign) and stmt.place == p.Local(LocalId(0))
                    and _carries(stmt.rvalue, held)):
                check.fail(STACK_ITERATOR, "a stack state reaches the return slot", stmt.span)


def _stack_state_locals(function: SlirFunction) -> set[LocalId]:
    held: set[LocalId] = set()
    grew = True
    while grew:
        grew = False
        for block in function.blocks:
            for stmt in block.statements:
                if (isinstance(stmt, st.Assign) and isinstance(stmt.place, p.Local)
                        and stmt.place.id not in held and _carries(stmt.rvalue, held)):
                    held.add(stmt.place.id)
                    grew = True
    return held


def _carries(value: r.Rvalue, held: set[LocalId]) -> bool:
    """Whether `value` makes a stack state, reads a local that holds one, or holds one as a
    part of an aggregate."""
    if isinstance(value, r.Aggregate) and isinstance(value.kind, r.AggIterator):
        return not value.kind.heap
    if isinstance(value, r.Aggregate):
        return any(_reads_one_of(part, held) for part in value.parts)
    return isinstance(value, r.Use) and _reads_one_of(value.operand, held)


def _reads_one_of(operand: p.Operand, held: set[LocalId]) -> bool:
    return (isinstance(operand, (p.Copy, p.Move)) and isinstance(operand.place, p.Local)
            and operand.place.id in held)
