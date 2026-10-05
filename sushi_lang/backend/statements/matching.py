"""Pattern matching statement emission for the Sushi language compiler."""
from __future__ import annotations
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils, gep_utils
from sushi_lang.backend.utils import require_both_initialized
from sushi_lang.backend.statements.loops import _emit_block
from sushi_lang.backend.statements.control_flow import close_merge_block

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Match, Expr, MatchArm
    from sushi_lang.semantics.typesys import EnumType, Type


def emit_match(codegen: 'LLVMCodegen', stmt: 'Match') -> None:
    """Emit match statement with exhaustive pattern matching."""

    builder, func = require_both_initialized(codegen)
    codegen.utils.ensure_open_block()

    # An integer match (#415) switches on the scrutinee VALUE, not on an enum
    # tag; the typecheck pass stamps `integer_match_type` on exactly those matches.
    if getattr(stmt, 'integer_match_type', None) is not None:
        _emit_integer_match(codegen, stmt)
        return

    # A tuple match and a string match have no tag to switch on: each arm tests its
    # pattern in order. The stamp is read BEFORE the scrutinee is emitted, because a
    # tuple-literal scrutinee builds no tuple (ruling 3 of the tuple design).
    from sushi_lang.semantics.generics.tuples import is_tuple_type
    from sushi_lang.semantics.typesys import BuiltinType
    if (is_tuple_type(stmt.resolved_scrutinee_type)
            or stmt.resolved_scrutinee_type == BuiltinType.STRING):
        _emit_sequential_match(codegen, stmt)
        return

    scrutinee_value = codegen.expressions.emit_expr(stmt.scrutinee)

    # The typecheck pass stamps every enum match it accepts, and the stamp is the one
    # source (#838): a second derivation here could disagree with it.
    from sushi_lang.semantics.typesys import EnumType
    scrutinee_type = stmt.resolved_scrutinee_type
    if not isinstance(scrutinee_type, EnumType):
        raise_internal_error("CE0121", pattern=_first_arm_pattern(stmt))

    # `match nom r:` (ruling R11) hands the local to the match, which then owns it exactly
    # as it owns a temporary. The seam marks `r` moved, so no exit path frees it twice.
    if stmt.consumes_scrutinee:
        from sushi_lang.backend import ownership
        scrutinee_value = ownership.consume(codegen, stmt.scrutinee, scrutinee_value,
                                            scrutinee_type,
                                            ownership.ConsumingUse.MATCH_SCRUTINEE)

    # An UNBOUND scrutinee is a temporary nothing owns, and the arm bindings only BORROW
    # its payload, so an owning payload was never freed (#159). It becomes an ordinary
    # owning local in a scope around the whole match -- an ordinary local rather than a new
    # temp registry, so every exit path and the move guard already work.
    scope = _MatchScope()
    scrutinee_slot = _own_scrutinee(codegen, stmt, stmt.scrutinee, scrutinee_value,
                                    scrutinee_type, [arm.pattern for arm in stmt.arms], scope)

    tag = enum_utils.extract_enum_tag(codegen, scrutinee_value, name="match_tag")

    end_bb = codegen.func.append_basic_block(name="match.end")
    arm_blocks = []
    for i, _arm in enumerate(stmt.arms):
        arm_bb = codegen.func.append_basic_block(name=f"match.arm{i}")
        arm_blocks.append(arm_bb)

    wildcard_bb = _find_wildcard_block(stmt, arm_blocks)

    switch, unreachable_bb = _create_switch_instruction(codegen, tag, wildcard_bb)

    _add_switch_cases(codegen, stmt, arm_blocks, switch, scrutinee_type)

    root = _Root(stmt.scrutinee, scrutinee_value, scrutinee_type, scrutinee_slot)
    end_reached = _emit_match_arms(codegen, stmt, arm_blocks, root, end_bb)

    if unreachable_bb is not None:
        codegen.builder.position_at_end(unreachable_bb)
        codegen.builder.unreachable()

    close_merge_block(codegen, end_bb, end_reached)

    # Close the synthetic scope owning an unbound scrutinee. Emitted at match.end, this is the
    # fall-through free; the early-exit paths (return / break / ??) already freed it through the
    # same registry before branching away. An arm that TOOK a payload cleared the drop flag,
    # so the free here reads it and does nothing on that path.
    scope.close(codegen)


def _emit_integer_match(codegen: 'LLVMCodegen', stmt: 'Match') -> None:
    """Emit a match on an integer scrutinee (#415): one LLVM switch on the value.

    The typecheck pass guarantees the shape: every arm is a LiteralPattern except a trailing
    wildcard, which becomes the switch default. Integers are plain values, so
    there is no temp-scrutinee ownership and no binding extraction; the shared
    `_emit_match_arms` skips both for non-Pattern arms.
    """
    from llvmlite import ir
    from sushi_lang.semantics.ast import LiteralPattern

    scrutinee_value = codegen.expressions.emit_expr(stmt.scrutinee)

    end_bb = codegen.func.append_basic_block(name="match.end")
    arm_blocks = [codegen.func.append_basic_block(name=f"match.arm{i}")
                  for i in range(len(stmt.arms))]

    wildcard_bb = _find_wildcard_block(stmt, arm_blocks)
    switch, unreachable_bb = _create_switch_instruction(codegen, scrutinee_value, wildcard_bb)

    for arm, arm_bb in zip(stmt.arms, arm_blocks, strict=True):
        if isinstance(arm.pattern, LiteralPattern):
            case_value = ir.Constant(scrutinee_value.type, arm.pattern.value)
            switch.add_case(case_value, arm_bb)

    end_reached = _emit_match_arms(codegen, stmt, arm_blocks, None, end_bb)

    if unreachable_bb is not None:
        codegen.builder.position_at_end(unreachable_bb)
        codegen.builder.unreachable()

    close_merge_block(codegen, end_bb, end_reached)


def _emit_sequential_match(codegen: 'LLVMCodegen', stmt: 'Match') -> None:
    """Emit a match with no tag to switch on: each arm tests its whole pattern, in order.

    A tuple and a string take this path. A tuple-literal scrutinee builds no tuple
    (ruling 3): each element is one root, evaluated once and from left to right, with
    the ownership rules of a named scrutinee. Any other scrutinee is one root. A failed
    test goes to the next arm; after the last arm is the RE2023 backstop, which
    exhaustiveness makes unreachable.
    """
    from sushi_lang.semantics.ast import TupleLiteral
    from sushi_lang.semantics.generics.tuples import tuple_elements

    scrutinee_type = stmt.resolved_scrutinee_type
    patterns = [arm.pattern for arm in stmt.arms]
    scope = _MatchScope()
    roots: list[_Root] = []
    if isinstance(stmt.scrutinee, TupleLiteral):
        element_types = tuple_elements(scrutinee_type)
        values = [codegen.expressions.emit_expr(element)
                  for element in stmt.scrutinee.elements]
        for index, (element, value, element_type) in enumerate(
                zip(stmt.scrutinee.elements, values, element_types, strict=True)):
            value = _consume_if_handed_over(codegen, stmt, element, value, element_type)
            items = [_element_item(pattern, index) for pattern in patterns]
            slot = _own_scrutinee(codegen, stmt, element, value, element_type, items, scope)
            roots.append(_Root(element, value, element_type, slot))
    else:
        value = codegen.expressions.emit_expr(stmt.scrutinee)
        value = _consume_if_handed_over(codegen, stmt, stmt.scrutinee, value, scrutinee_type)
        slot = _own_scrutinee(codegen, stmt, stmt.scrutinee, value, scrutinee_type, patterns,
                              scope)
        roots.append(_Root(stmt.scrutinee, value, scrutinee_type, slot))

    end_bb = codegen.func.append_basic_block(name="match.end")
    arm_blocks = [codegen.func.append_basic_block(name=f"match.arm{i}")
                  for i in range(len(stmt.arms))]
    codegen.builder.branch(arm_blocks[0])

    end_reached = False
    for index, (arm, arm_bb) in enumerate(zip(stmt.arms, arm_blocks, strict=True)):
        codegen.builder.position_at_end(arm_bb)
        codegen.memory.push_scope()
        if len(roots) == 1:
            positions = [(arm.pattern, roots[0])]
        else:
            positions = [(_element_item(arm.pattern, i), root) for i, root in enumerate(roots)]
        next_bb = arm_blocks[index + 1] if index + 1 < len(arm_blocks) else None
        _extract_pattern_bindings(codegen, positions, _failure_target(codegen, arm, next_bb))
        end_reached = _emit_arm_body(codegen, arm, end_bb) or end_reached

    close_merge_block(codegen, end_bb, end_reached)
    scope.close(codegen)


def _element_item(pattern: object, index: int) -> object:
    """The item of a top-level tuple pattern that reads element `index`; `_` for a `_` arm."""
    from sushi_lang.semantics.ast import TuplePattern
    if isinstance(pattern, TuplePattern):
        return pattern.elements[index]
    return "_"


def _consume_if_handed_over(codegen: 'LLVMCodegen', stmt: 'Match', scrutinee: 'Expr',
                            value: 'ir.Value', semantic_type: 'Type') -> 'ir.Value':
    """`match nom r:` and `match nom (a, b):` hand each scrutinee to the match (R11)."""
    if not stmt.consumes_scrutinee:
        return value
    from sushi_lang.backend import ownership
    return ownership.consume(codegen, scrutinee, value, semantic_type,
                             ownership.ConsumingUse.MATCH_SCRUTINEE)


# A counter, not a fixed name: two matches in one function would otherwise register the same
# name twice and the inner scope's free would shadow the outer's.
_TEMP_SCRUTINEE_SEQ = itertools.count()


class Scrutinee:
    """The storage a match gives its own scrutinee, and whether it also owns it.

    Three things need it and each needs a different part. A `poke` binding needs an
    ADDRESS to point into (ruling R11 lifted CE2404 for a scrutinee the match owns). A
    `nom` binding needs the NAME, to clear the drop flag through the ownership seam. The
    fall-through free needs to know whether a scope was pushed at all.
    """

    def __init__(self, slot=None, name: 'str | None' = None, owns: bool = False) -> None:
        """Record the slot, the registered name, and whether a scope was pushed."""
        self.slot = slot
        self.name = name
        self.owns = owns


class _MatchScope:
    """The one scope around a match that holds the scrutinees the match owns."""

    def __init__(self) -> None:
        """No scope until a scrutinee needs one."""
        self.pushed = False

    def open(self, codegen: 'LLVMCodegen') -> None:
        """Push the scope once, before the first owned scrutinee is registered."""
        if not self.pushed:
            codegen.memory.push_scope()
            self.pushed = True

    def close(self, codegen: 'LLVMCodegen') -> None:
        """Pop the scope at match.end: the fall-through free of each owned scrutinee."""
        if self.pushed:
            codegen.memory.pop_scope()


@dataclass
class _Root:
    """One value a match reads: the scrutinee, or one element of a tuple-literal one."""
    expr: 'Expr'
    value: 'ir.Value'
    semantic_type: 'Type'
    storage: Scrutinee


def _pattern_takes(item: object) -> bool:
    """Does this pattern item, at any depth, take a value out of the scrutinee?"""
    from sushi_lang.semantics.ast import NomBinding, Pattern, TuplePattern
    if isinstance(item, NomBinding):
        return True
    if isinstance(item, Pattern):
        return any(_pattern_takes(b) for b in item.bindings)
    if isinstance(item, TuplePattern):
        return any(_pattern_takes(e) for e in item.elements)
    return False


def _pattern_binds_a_reference(item: object) -> bool:
    """Does this pattern item bind a `peek` / `poke` reference into the scrutinee?"""
    from sushi_lang.semantics.ast import Pattern, RefBinding, TuplePattern
    if isinstance(item, RefBinding):
        return True
    if isinstance(item, Pattern):
        return any(_pattern_binds_a_reference(b) for b in item.bindings)
    if isinstance(item, TuplePattern):
        return any(_pattern_binds_a_reference(e) for e in item.elements)
    return False


def _own_scrutinee(codegen: 'LLVMCodegen', stmt: 'Match', scrutinee: 'Expr',
                   scrutinee_value: 'ir.Value', scrutinee_type: 'Type | None',
                   items: list, scope: _MatchScope) -> Scrutinee:
    """Give a scrutinee the match owns its storage, and an owner where it needs one.

    `items` are the pattern items that read this scrutinee, one for each arm.
    """
    from sushi_lang.backend.expressions.memory import expression_is_temporary
    from sushi_lang.backend.destructors import needs_cleanup, resolve_named_type

    # A TEMPORARY is owned by construction; `match nom r:` says the local was handed over.
    # The same predicate the borrow pass makes, so the two sides cannot disagree.
    if not (expression_is_temporary(codegen, scrutinee) or stmt.consumes_scrutinee):
        return Scrutinee()

    # `needs_cleanup` is table-free -- an unresolved UnknownType answers False, which is how a
    # Result's owning payload escaped every RAII predicate in #179. Resolve first.
    resolved = resolve_named_type(codegen, scrutinee_type) if scrutinee_type is not None else None
    if resolved is not None and needs_cleanup(codegen, resolved):
        scope.open(codegen)
        name = f"__match_temp_{next(_TEMP_SCRUTINEE_SEQ)}"
        slot = codegen.memory.create_local(name, scrutinee_value.type, scrutinee_value,
                                           resolved, register_cleanup=False)
        codegen.memory.register_owning_value(name, resolved, slot)
        if any(_pattern_takes(item) for item in items):
            # One arm takes the payload and another may not, so whether this local still
            # owns its value at match.end is a RUN-TIME fact -- the same drop flag a
            # conditional move of an ordinary local gets (#414).
            codegen.moves.arm(slot)
        return Scrutinee(slot, name, owns=True)

    # Nothing to free, but a reference binding still needs somewhere to point.
    if not any(_pattern_binds_a_reference(item) for item in items):
        return Scrutinee()
    slot = codegen.memory.entry_alloca(scrutinee_value.type, "match_scrutinee")
    codegen.builder.store(scrutinee_value, slot)
    return Scrutinee(slot)


def _first_arm_pattern(stmt: 'Match') -> str:
    """The first arm's pattern as written, for the CE0121 text."""
    from sushi_lang.semantics.ast import Pattern
    pattern = stmt.arms[0].pattern if stmt.arms else None
    if isinstance(pattern, Pattern):
        return f"{pattern.enum_name}.{pattern.variant_name}"
    return "_"


def _find_wildcard_block(stmt: 'Match', arm_blocks: list['ir.Block']) -> 'ir.Block | None':
    """Find the block corresponding to a wildcard pattern, if any."""
    from sushi_lang.semantics.ast import WildcardPattern

    for i, arm in enumerate(stmt.arms):
        if isinstance(arm.pattern, WildcardPattern):
            return arm_blocks[i]
    return None


def _find_next_arm_with_same_tag(codegen: 'LLVMCodegen', stmt: 'Match', arm_blocks: list['ir.Block'], scrutinee_type: 'EnumType', current_arm_index: int) -> 'ir.Block | None':
    """Find the next arm that has the same outer tag as the current arm."""
    from sushi_lang.semantics.ast import Pattern, WildcardPattern

    current_arm = stmt.arms[current_arm_index]
    if not isinstance(current_arm.pattern, Pattern):
        return None

    current_tag = scrutinee_type.get_variant_index(current_arm.pattern.variant_name)
    if current_tag is None:
        return None

    for i in range(current_arm_index + 1, len(stmt.arms)):
        next_arm = stmt.arms[i]

        if isinstance(next_arm.pattern, WildcardPattern):
            return arm_blocks[i]

        if isinstance(next_arm.pattern, Pattern):
            next_tag = scrutinee_type.get_variant_index(next_arm.pattern.variant_name)
            if next_tag == current_tag:
                return arm_blocks[i]

    return None


def _create_switch_instruction(codegen: 'LLVMCodegen', tag: 'ir.Value', wildcard_bb: 'ir.Block | None') -> tuple['ir.Instruction', 'ir.Block | None']:
    """Create the switch instruction for pattern matching."""
    if wildcard_bb is None:
        unreachable_bb = codegen.func.append_basic_block(name="match.unreachable")
        return codegen.builder.switch(tag, unreachable_bb), unreachable_bb
    else:
        return codegen.builder.switch(tag, wildcard_bb), None


def _add_switch_cases(codegen: 'LLVMCodegen', stmt: 'Match', arm_blocks: list['ir.Block'], switch: 'ir.Instruction', scrutinee_type: 'EnumType') -> None:
    """Add switch cases for each match arm."""
    from llvmlite import ir
    from sushi_lang.semantics.ast import Pattern, WildcardPattern

    added_tags = set()

    for _i, (arm, arm_bb) in enumerate(zip(stmt.arms, arm_blocks, strict=True)):
        if isinstance(arm.pattern, WildcardPattern):
            continue
        if not isinstance(arm.pattern, Pattern):
            continue

        variant_index = scrutinee_type.get_variant_index(arm.pattern.variant_name)
        if variant_index is not None and variant_index not in added_tags:
            tag_value = ir.Constant(codegen.types.i32, variant_index)
            switch.add_case(tag_value, arm_bb)
            added_tags.add(variant_index)


def _emit_match_arms(
    codegen: 'LLVMCodegen',
    stmt: 'Match',
    arm_blocks: list['ir.Block'],
    root: '_Root | None',
    end_bb: 'ir.Block',
) -> bool:
    """Emit the arms of a switch match. Answer whether an arm branches to `end_bb`."""
    from sushi_lang.semantics.ast import Pattern
    from sushi_lang.semantics.typesys import EnumType

    end_reached = False
    for i, (arm, arm_bb) in enumerate(zip(stmt.arms, arm_blocks, strict=True)):
        codegen.builder.position_at_end(arm_bb)
        codegen.memory.push_scope()

        if isinstance(arm.pattern, Pattern):
            # Only an enum match has Pattern arms, and emit_match refuses one with no type.
            if root is None or not isinstance(root.semantic_type, EnumType):
                raise_internal_error("CE0121", pattern=_first_arm_pattern(stmt))
            # The switch tested the outer tag. A test inside the payload that fails goes
            # to the next arm of the same tag.
            next_arm_bb = _find_next_arm_with_same_tag(codegen, stmt, arm_blocks,
                                                       root.semantic_type, i)
            _extract_pattern_bindings(codegen, [(arm.pattern, root)],
                                      _failure_target(codegen, arm, next_arm_bb),
                                      tag_known=True)

        end_reached = _emit_arm_body(codegen, arm, end_bb) or end_reached

    return end_reached


def _emit_arm_body(codegen: 'LLVMCodegen', arm: 'MatchArm', end_bb: 'ir.Block') -> bool:
    """Emit an arm's body and close its scope. Answer whether it branches to `end_bb`."""
    from sushi_lang.semantics.ast import Block

    if isinstance(arm.body, Block):
        _emit_block(codegen, arm.body)
    else:
        codegen.expressions.emit_expr(arm.body)

    codegen.memory.pop_scope()

    if codegen.builder.block.terminator is None:
        codegen.builder.branch(end_bb)
        return True
    return False


def _failure_target(codegen: 'LLVMCodegen', arm: 'MatchArm',
                    next_bb: 'ir.Block | None') -> Callable[[], 'ir.Block']:
    """Where a failed test of `arm` goes: the next candidate arm, or the RE2023 backstop."""
    from sushi_lang.semantics.ast import pattern_source

    made: list = []

    def target() -> 'ir.Block':
        if next_bb is not None:
            return next_bb
        if not made:
            here = codegen.builder.block
            block = codegen.func.append_basic_block(name="match.no_arm")
            codegen.builder.position_at_end(block)
            codegen.runtime.errors.emit_runtime_error(
                "RE2023", pattern=pattern_source(arm.pattern))
            codegen.builder.unreachable()
            codegen.builder.position_at_end(here)
            made.append(block)
        return made[0]

    return target


@dataclass
class _Position:
    """One position of the value a pattern reads: its value, its type, and its address.

    `address` computes a pointer into the scrutinee's own storage, or is None where the
    position has none; a `poke` binding asks it, after every test passed.
    """
    value: 'ir.Value'
    semantic_type: 'Type'
    address: Optional[Callable[[], 'ir.Value']]


@dataclass
class _Bind:
    """A binding to make once the whole pattern matched."""
    kind: str                 # "value" | "nom" | "ref" | "own_value" | "own_ref"
    name: str
    position: _Position
    mode: Optional[str] = None
    own_value: Optional['ir.Value'] = None


def _root_address(codegen: 'LLVMCodegen', root: _Root) -> Callable[[], 'ir.Value']:
    """The address of a root: the slot the match parked it in, or the place it names.

    A temporary has no expression to take an address of, and ruling R11 made that shape
    legal by giving it storage for the whole statement. A place is a name, or a member or
    index chain off one (the borrow pass refuses anything else with CE2404).
    """
    def address() -> 'ir.Value':
        if root.storage.slot is not None:
            return root.storage.slot
        from sushi_lang.backend.expressions.calls.utils import emit_receiver_as_pointer
        return emit_receiver_as_pointer(codegen, root.expr)
    return address


def _extract_pattern_bindings(codegen: 'LLVMCodegen', positions: list,
                              on_fail: Callable[[], 'ir.Block'],
                              tag_known: bool = False) -> None:
    """Test each pattern item against its root, then bind, in that order.

    Every test comes first, so a failed test leaves the arm before anything is bound or
    taken. Then an arm that TAKES a value clears its root's drop flag (ruling R11): the
    free at match.end reads that flag, and only this path stores 0 into it. Then the
    bindings are made. `tag_known` says the switch already tested the outer tag.
    """
    from sushi_lang.backend.destructors import resolve_named_type

    binds: list[_Bind] = []
    for item, root in positions:
        position = _Position(root.value, resolve_named_type(codegen, root.semantic_type),
                             _root_address(codegen, root))
        _test_item(codegen, item, position, on_fail, binds, tag_known=tag_known)

    for item, root in positions:
        if root.storage.name is not None and _pattern_takes(item):
            from sushi_lang.backend.ownership import relinquish_temp
            relinquish_temp(codegen, root.storage.name)

    for bind in binds:
        _make_binding(codegen, bind)


def _branch_unless(codegen: 'LLVMCodegen', matches: 'ir.Value',
                   on_fail: Callable[[], 'ir.Block']) -> None:
    """Go on in a new block when `matches` holds; go to the failure target when not."""
    go_on = codegen.func.append_basic_block(name="match.test_ok")
    codegen.builder.cbranch(matches, go_on, on_fail())
    codegen.builder.position_at_end(go_on)


def _test_item(codegen: 'LLVMCodegen', item: object, position: _Position,
               on_fail: Callable[[], 'ir.Block'], binds: list,
               tag_known: bool = False) -> None:
    """Emit the tests of one pattern item, and collect the bindings it makes."""
    from llvmlite import ir
    from sushi_lang.semantics.ast import (
        LiteralPattern, NomBinding, OwnPattern, Pattern, RefBinding, TuplePattern,
    )

    if isinstance(item, str):
        if item != "_":
            binds.append(_Bind("value", item, position))
    elif isinstance(item, NomBinding):
        binds.append(_Bind("nom", item.name, position))
    elif isinstance(item, RefBinding):
        binds.append(_Bind("ref", item.name, position, mode=item.mode))
    elif isinstance(item, LiteralPattern) and isinstance(item.value, str):
        # The pattern is a .rodata constant (owned = 0): nothing to free.
        from sushi_lang.backend.types.contracts import emit_value_eq
        expected = codegen.runtime.strings.emit_string_literal(item.value)
        _branch_unless(codegen, emit_value_eq(codegen, position.value, expected,
                                              position.semantic_type), on_fail)
    elif isinstance(item, LiteralPattern):
        expected = ir.Constant(position.value.type, item.value)
        _branch_unless(codegen, codegen.builder.icmp_signed("==", position.value, expected,
                                                            name="literal_matches"), on_fail)
    elif isinstance(item, TuplePattern):
        _test_tuple(codegen, item, position, on_fail, binds)
    elif isinstance(item, Pattern):
        _test_variant(codegen, item, position, on_fail, binds, tag_known)
    elif isinstance(item, OwnPattern):
        _test_own(codegen, item, position, on_fail, binds)


def _test_tuple(codegen: 'LLVMCodegen', pattern, position: _Position,
                on_fail: Callable[[], 'ir.Block'], binds: list) -> None:
    """A tuple pattern: test each element, from left to right."""
    from sushi_lang.backend.destructors import resolve_named_type
    from sushi_lang.semantics.generics.tuples import tuple_elements

    for index, (item, element_type) in enumerate(
            zip(pattern.elements, tuple_elements(position.semantic_type), strict=True)):
        value = codegen.builder.extract_value(position.value, index, name=f"elem{index}")
        _test_item(codegen, item, _Position(value, resolve_named_type(codegen, element_type),
                                            _field_address(codegen, position, index)),
                   on_fail, binds)


def _field_address(codegen: 'LLVMCodegen', position: _Position,
                   index: int) -> Optional[Callable[[], 'ir.Value']]:
    """The address of element `index` of a tuple position, when the tuple has one."""
    parent = position.address
    if parent is None:
        return None
    return lambda: gep_utils.gep_struct_field(codegen, parent(), index, f"elem{index}_ptr")


def _test_variant(codegen: 'LLVMCodegen', pattern, position: _Position,
                  on_fail: Callable[[], 'ir.Block'], binds: list, tag_known: bool) -> None:
    """An enum pattern: test the tag, then each payload position."""
    from sushi_lang.backend.destructors import resolve_named_type
    from sushi_lang.semantics.typesys import EnumType

    enum_type = position.semantic_type
    if not isinstance(enum_type, EnumType):
        raise_internal_error("CE0121", pattern=f"{pattern.enum_name}.{pattern.variant_name}")
    variant = enum_type.get_variant(pattern.variant_name)
    tag = enum_type.get_variant_index(pattern.variant_name)
    if variant is None or tag is None:
        raise_internal_error("CE0121", pattern=f"{pattern.enum_name}.{pattern.variant_name}")

    if not tag_known:
        _branch_unless(codegen, enum_utils.check_enum_variant(
            codegen, position.value, tag, signed=True, name="variant_matches"), on_fail)

    if not pattern.bindings:
        return

    # ENTRY block, not the current position: a match inside a loop would otherwise
    # allocate again every iteration and never release it (BUGS.md B1). Reuse is safe
    # because every value binding is loaded OUT of this copy, and a reference binding
    # points into the scrutinee instead (#253).
    data_array = enum_utils.extract_enum_data(codegen, position.value, name="match_data")
    storage = codegen.memory.entry_alloca(position.value.type.elements[1], "match_data_storage")
    codegen.builder.store(data_array, storage)
    data_ptr = codegen.builder.bitcast(storage, codegen.types.str_ptr, name="data_ptr")

    # Each payload sits at its offset from the ONE layout authority (#300 phase 2); the
    # offsets are naturally aligned and the payload base is 8-aligned.
    offsets = codegen.types.payload_field_offsets(variant.associated_types)
    for item, payload_type, offset in zip(pattern.bindings, variant.associated_types,
                                          offsets, strict=True):
        resolved = resolve_named_type(codegen, payload_type)
        value = _load_payload(codegen, data_ptr, offset, resolved)
        _test_item(codegen, item, _Position(value, resolved,
                                            _payload_address(codegen, position, offset,
                                                             resolved)),
                   on_fail, binds)


def _load_payload(codegen: 'LLVMCodegen', data_ptr: 'ir.Value', offset: int,
                  payload_type: 'Type') -> 'ir.Value':
    """Load one payload value out of the arm's copy of the enum data."""
    from llvmlite import ir
    field_ptr = codegen.builder.gep(data_ptr, [ir.Constant(codegen.types.i32, offset)],
                                    name="field_ptr")
    typed = codegen.builder.bitcast(field_ptr, ir.PointerType(codegen.types.ll_type(payload_type)),
                                    name="field_ptr_typed")
    return codegen.builder.load(typed, name="field_value")


def _payload_address(codegen: 'LLVMCodegen', position: _Position, offset: int,
                     payload_type: 'Type') -> Optional[Callable[[], 'ir.Value']]:
    """The address of a payload inside the scrutinee's own storage, never the copy.

    A pointer into the copy makes every write silently lost (#253).
    """
    from llvmlite import ir
    parent = position.address
    if parent is None:
        return None

    def address() -> 'ir.Value':
        data = gep_utils.gep_struct_field(codegen, parent(), 1, "scrutinee_data_ptr")
        data_i8 = codegen.builder.bitcast(data, codegen.types.str_ptr, name="scrutinee_data_i8")
        field = codegen.builder.gep(data_i8, [ir.Constant(codegen.types.i32, offset)],
                                    name="ref_binding_field_i8")
        return codegen.builder.bitcast(
            field, ir.PointerType(codegen.types.ll_type(payload_type)),
            name="ref_binding_field_ptr")
    return address


def _test_own(codegen: 'LLVMCodegen', pattern, position: _Position,
              on_fail: Callable[[], 'ir.Block'], binds: list) -> None:
    """An `Own(...)` pattern: read through the heap cell to the pointee."""
    from sushi_lang.backend.destructors import resolve_named_type
    from sushi_lang.backend.generics import own as own_module
    from sushi_lang.semantics.generics.own import get_own_element_type
    from sushi_lang.semantics.type_predicates import is_instance_of
    from sushi_lang.semantics.typesys import StructType

    own_type = position.semantic_type
    if not isinstance(own_type, StructType) or not is_instance_of(own_type, "Own"):
        raise_internal_error("CE0022", type=str(own_type))
    element_type = resolve_named_type(codegen, get_own_element_type(own_type))
    unwrapped = own_module.emit_own_get(codegen, position.value, element_type)

    inner = pattern.inner_pattern
    if isinstance(inner, str):
        if inner == "_":
            return
        # `Own(poke x)` binds the heap POINTER, so a write lands in the allocation the Own
        # owns. A bare binding BORROWS the pointee, which the Own@(T) still owns.
        kind = "own_ref" if pattern.inner_borrow is not None else "own_value"
        binds.append(_Bind(kind, inner, _Position(unwrapped, element_type, None),
                           mode=pattern.inner_borrow, own_value=position.value))
        return
    _test_item(codegen, inner, _Position(unwrapped, element_type, None), on_fail, binds)


def _make_binding(codegen: 'LLVMCodegen', bind: _Bind) -> None:
    """Make one binding, after every test of the arm passed."""
    position = bind.position
    llvm_type = codegen.types.ll_type(position.semantic_type)
    if bind.kind == "nom":
        # `Variant(nom x)` (ruling R11): the arm TAKES the value, so it IS registered --
        # through `register_owning_value`, the complete registry router (#382). What
        # stops the second free is the scrutinee's drop flag, cleared before the bindings.
        slot = codegen.memory.create_local(bind.name, llvm_type, position.value,
                                           position.semantic_type, register_cleanup=False)
        codegen.memory.register_owning_value(bind.name, position.semantic_type, slot)
    elif bind.kind == "ref":
        from sushi_lang.backend.statements.loops import bind_element_reference
        if position.address is None:
            raise_internal_error("CE0121", pattern=f"{bind.mode} {bind.name}")
        bind_element_reference(codegen, bind.name, bind.mode, position.semantic_type,
                               position.address())
    elif bind.kind == "own_ref":
        from sushi_lang.semantics.param_modes import borrow_mode
        from sushi_lang.semantics.typesys import ReferenceType
        pointee_ptr = codegen.builder.extract_value(bind.own_value, 0, name="own_ptr")
        ref_type = ReferenceType(position.semantic_type, borrow_mode(bind.mode))
        codegen.memory.create_local(bind.name, pointee_ptr.type, pointee_ptr, ref_type,
                                    register_cleanup=False)
    else:
        # A bare binding BORROWS the value the scrutinee owns, so it is NOT registered for
        # its own free -- the owner frees it, and registering both double-frees (#139).
        codegen.memory.create_local(bind.name, llvm_type, position.value,
                                    position.semantic_type, register_cleanup=False)
