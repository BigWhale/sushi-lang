"""Pattern matching statement emission for the Sushi language compiler."""
from __future__ import annotations
import itertools
from typing import TYPE_CHECKING
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils, gep_utils
from sushi_lang.backend.utils import require_both_initialized

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Match, Expr, Pattern, OwnPattern
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

    scrutinee_value = codegen.expressions.emit_expr(stmt.scrutinee)

    # Prefer the typecheck pass's resolved enum type: the backend re-derivation below cannot cover
    # every scrutinee form, and a miss silently drops the arm's bindings.
    from sushi_lang.semantics.typesys import EnumType
    scrutinee_type = getattr(stmt, 'resolved_scrutinee_type', None)
    if not isinstance(scrutinee_type, EnumType):
        scrutinee_type = _get_scrutinee_type(codegen, stmt.scrutinee)

    # An UNBOUND scrutinee is a temporary nothing owns, and the arm bindings only BORROW
    # its payload, so an owning payload was never freed (#159). It becomes an ordinary
    # owning local in a scope around the whole match -- an ordinary local rather than a new
    # temp registry, so every exit path and the move guard already work.
    owns_temp_scrutinee = _register_temp_scrutinee(codegen, stmt.scrutinee, scrutinee_value, scrutinee_type)

    tag = enum_utils.extract_enum_tag(codegen, scrutinee_value, name="match_tag")

    end_bb = codegen.func.append_basic_block(name="match.end")
    arm_blocks = []
    for i, _arm in enumerate(stmt.arms):
        arm_bb = codegen.func.append_basic_block(name=f"match.arm{i}")
        arm_blocks.append(arm_bb)

    wildcard_bb = _find_wildcard_block(stmt, arm_blocks)

    switch, unreachable_bb = _create_switch_instruction(codegen, tag, wildcard_bb)

    _add_switch_cases(codegen, stmt, arm_blocks, switch, scrutinee_type)

    _emit_match_arms(codegen, stmt, arm_blocks, scrutinee_value, scrutinee_type, end_bb)

    if unreachable_bb is not None:
        codegen.builder.position_at_end(unreachable_bb)
        codegen.builder.unreachable()

    codegen.builder.position_at_end(end_bb)

    # Close the synthetic scope owning an unbound scrutinee. Emitted at match.end, this is the
    # fall-through free; the early-exit paths (return / break / ??) already freed it through the
    # same registry before branching away.
    if owns_temp_scrutinee:
        codegen.memory.pop_scope()


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

    _emit_match_arms(codegen, stmt, arm_blocks, scrutinee_value, None, end_bb)

    if unreachable_bb is not None:
        codegen.builder.position_at_end(unreachable_bb)
        codegen.builder.unreachable()

    codegen.builder.position_at_end(end_bb)


# A counter, not a fixed name: two matches in one function would otherwise register the same
# name twice and the inner scope's free would shadow the outer's.
_TEMP_SCRUTINEE_SEQ = itertools.count()


def _register_temp_scrutinee(codegen: 'LLVMCodegen', scrutinee: 'Expr', scrutinee_value: 'ir.Value',
                             scrutinee_type: 'EnumType | None') -> bool:
    """Own an unbound match scrutinee for the duration of the match. Returns True if a scope was
    pushed.
    """
    from sushi_lang.backend.expressions.memory import expression_is_temporary
    from sushi_lang.backend.destructors import needs_cleanup, resolve_named_type
    from sushi_lang.semantics.typesys import EnumType

    if not expression_is_temporary(codegen, scrutinee):
        return False

    # `needs_cleanup` is table-free -- an unresolved UnknownType answers False, which is how a
    # Result's owning payload escaped every RAII predicate in #179. Resolve first.
    resolved = resolve_named_type(codegen, scrutinee_type) if scrutinee_type is not None else None
    if not isinstance(resolved, EnumType) or not needs_cleanup(codegen, resolved):
        return False

    codegen.memory.push_scope()
    name = f"__match_temp_{next(_TEMP_SCRUTINEE_SEQ)}"
    codegen.memory.create_local(name, scrutinee_value.type, scrutinee_value, resolved)
    return True


def _get_scrutinee_type(codegen: 'LLVMCodegen', scrutinee: 'Expr') -> 'EnumType | None':
    """Get the EnumType of the scrutinee expression."""
    from sushi_lang.semantics.ast import Name, DotCall, MethodCall, MemberAccess
    from sushi_lang.semantics.typesys import EnumType, StructType

    if isinstance(scrutinee, Name):
        var_type = codegen.memory.find_semantic_type(scrutinee.id)

        if isinstance(var_type, EnumType):
            return var_type

        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(var_type, GenericTypeRef):
            if var_type.base_name == "Result" and len(var_type.type_args) == 2:
                from sushi_lang.semantics.generics.results import ensure_result_type_in_table
                from sushi_lang.semantics.type_resolution import resolve_unknown_type

                ok_type = resolve_unknown_type(
                    var_type.type_args[0],
                    codegen.struct_table.by_name,
                    codegen.enum_table.by_name
                )
                err_type = resolve_unknown_type(
                    var_type.type_args[1],
                    codegen.struct_table.by_name,
                    codegen.enum_table.by_name
                )

                result_enum = ensure_result_type_in_table(
                    codegen.enum_table,
                    ok_type,
                    err_type, struct_table=codegen.struct_table.by_name)
                return result_enum
            else:
                type_args_str = ", ".join(str(arg) for arg in var_type.type_args)
                concrete_name = f"{var_type.base_name}<{type_args_str}>"
                if concrete_name in codegen.enum_table.by_name:
                    return codegen.enum_table.by_name[concrete_name]

    if isinstance(scrutinee, MemberAccess):
        if isinstance(scrutinee.receiver, Name):
            receiver_type = codegen.memory.find_semantic_type(scrutinee.receiver.id)
            if isinstance(receiver_type, StructType):
                for field_name, field_type in receiver_type.fields:
                    if field_name == scrutinee.member:
                        from sushi_lang.semantics.generics.types import GenericTypeRef
                        if isinstance(field_type, GenericTypeRef):
                            if field_type.base_name == "Result" and len(field_type.type_args) == 2:
                                from sushi_lang.semantics.generics.results import ensure_result_type_in_table
                                from sushi_lang.semantics.type_resolution import resolve_unknown_type

                                ok_type = resolve_unknown_type(
                                    field_type.type_args[0],
                                    codegen.struct_table.by_name,
                                    codegen.enum_table.by_name
                                )
                                err_type = resolve_unknown_type(
                                    field_type.type_args[1],
                                    codegen.struct_table.by_name,
                                    codegen.enum_table.by_name
                                )

                                result_enum = ensure_result_type_in_table(
                                    codegen.enum_table,
                                    ok_type,
                                    err_type, struct_table=codegen.struct_table.by_name)
                                return result_enum
                            else:
                                type_args_str = ", ".join(str(arg) for arg in field_type.type_args)
                                concrete_name = f"{field_type.base_name}<{type_args_str}>"
                                if concrete_name in codegen.enum_table.by_name:
                                    return codegen.enum_table.by_name[concrete_name]
                        elif isinstance(field_type, EnumType):
                            return field_type
        return None

    # A Call scrutinee needs no branch: the typecheck pass stamps `resolved_scrutinee_type`, which
    # emit_match reads first. Re-inferring it here swallowed misses and dropped bindings.

    if isinstance(scrutinee, (DotCall, MethodCall)):
        if hasattr(scrutinee, 'inferred_return_type') and isinstance(scrutinee.inferred_return_type, EnumType):
            return scrutinee.inferred_return_type

    if hasattr(scrutinee, 'inferred_type') and isinstance(scrutinee.inferred_type, EnumType):
        return scrutinee.inferred_type

    return None


def _find_wildcard_block(stmt: 'Match', arm_blocks: list['ir.Block']) -> 'ir.Block | None':
    """Find the block corresponding to a wildcard pattern, if any."""
    from sushi_lang.semantics.ast import WildcardPattern

    for i, arm in enumerate(stmt.arms):
        if isinstance(arm.pattern, WildcardPattern):
            return arm_blocks[i]
    return None


def _find_next_arm_with_same_tag(codegen: 'LLVMCodegen', stmt: 'Match', arm_blocks: list['ir.Block'], scrutinee_type: 'EnumType | None', current_arm_index: int) -> 'ir.Block | None':
    """Find the next arm that has the same outer tag as the current arm."""
    from sushi_lang.semantics.ast import Pattern, WildcardPattern

    current_arm = stmt.arms[current_arm_index]
    if not isinstance(current_arm.pattern, Pattern):
        return None

    enum_type = scrutinee_type
    if enum_type is None and hasattr(codegen, 'enum_table'):
        enum_type = codegen.enum_table.by_name.get(current_arm.pattern.enum_name)

    if enum_type is None:
        return None

    current_tag = enum_type.get_variant_index(current_arm.pattern.variant_name)
    if current_tag is None:
        return None

    for i in range(current_arm_index + 1, len(stmt.arms)):
        next_arm = stmt.arms[i]

        if isinstance(next_arm.pattern, WildcardPattern):
            return arm_blocks[i]

        if isinstance(next_arm.pattern, Pattern):
            next_enum_type = scrutinee_type
            if next_enum_type is None and hasattr(codegen, 'enum_table'):
                next_enum_type = codegen.enum_table.by_name.get(next_arm.pattern.enum_name)

            if next_enum_type is not None:
                next_tag = next_enum_type.get_variant_index(next_arm.pattern.variant_name)
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


def _add_switch_cases(codegen: 'LLVMCodegen', stmt: 'Match', arm_blocks: list['ir.Block'], switch: 'ir.Instruction', scrutinee_type: 'EnumType | None') -> None:
    """Add switch cases for each match arm."""
    from llvmlite import ir
    from sushi_lang.semantics.ast import Pattern, WildcardPattern

    added_tags = set()

    for _i, (arm, arm_bb) in enumerate(zip(stmt.arms, arm_blocks, strict=True)):
        if isinstance(arm.pattern, WildcardPattern):
            continue
        if not isinstance(arm.pattern, Pattern):
            continue

        enum_type = scrutinee_type
        if enum_type is None and hasattr(codegen, 'enum_table'):
            enum_type = codegen.enum_table.by_name.get(arm.pattern.enum_name)

        if enum_type is not None:
            variant_index = enum_type.get_variant_index(arm.pattern.variant_name)
            if variant_index is not None and variant_index not in added_tags:
                tag_value = ir.Constant(codegen.types.i32, variant_index)
                switch.add_case(tag_value, arm_bb)
                added_tags.add(variant_index)


def _emit_match_arms(
    codegen: 'LLVMCodegen',
    stmt: 'Match',
    arm_blocks: list['ir.Block'],
    scrutinee_value: 'ir.Value',
    scrutinee_type: 'EnumType | None',
    end_bb: 'ir.Block'
) -> None:
    """Emit all match arms."""
    from sushi_lang.semantics.ast import Pattern, Block

    for i, (arm, arm_bb) in enumerate(zip(stmt.arms, arm_blocks, strict=True)):
        codegen.builder.position_at_end(arm_bb)
        codegen.memory.push_scope()

        # Bracket `variable_types` per ARM: it is a FLAT per-function dict, so an arm's
        # binding shadowed a same-named outer local for the rest of the function -- wrong
        # type for a value binding, and a double deref for a reference one (#300).
        saved_variable_types = dict(codegen.variable_types)

        if isinstance(arm.pattern, Pattern):
            next_arm_bb = _find_next_arm_with_same_tag(codegen, stmt, arm_blocks, scrutinee_type, i)

            # Extract and bind pattern variables. The scrutinee EXPRESSION rides along
            # for reference bindings (#300 phase 3), which need a pointer into the
            # scrutinee's own storage rather than into the arm's temporary copy.
            _extract_pattern_bindings(codegen, arm.pattern, scrutinee_value, scrutinee_type, next_arm_bb,
                                      scrutinee_expr=stmt.scrutinee)

        try:
            if isinstance(arm.body, Block):
                _emit_block(codegen, arm.body)
            else:
                codegen.expressions.emit_expr(arm.body)
        finally:
            codegen.variable_types.clear()
            codegen.variable_types.update(saved_variable_types)

        codegen.memory.pop_scope()

        if codegen.builder.block.terminator is None:
            codegen.builder.branch(end_bb)


def _extract_pattern_bindings(codegen: 'LLVMCodegen', pattern: 'Pattern', scrutinee_value: 'ir.Value', scrutinee_type: 'EnumType | None', next_arm_bb: 'ir.Block | None' = None, scrutinee_expr: 'Expr | None' = None) -> None:
    """Extract and bind pattern variables from enum data."""
    from llvmlite import ir
    from sushi_lang.semantics.ast import Pattern as PatternNode, OwnPattern
    from sushi_lang.semantics.ast import RefBinding as RefBindingNode

    if not pattern.bindings:
        return

    # The fallback looks up the pattern's BASE enum name, which cannot match a
    # monomorphized key, so a generic scrutinee needs scrutinee_type. Fail loud if it is
    # missing while the pattern binds: returning drops the locals and surfaces as CE0055.
    enum_type = scrutinee_type
    if enum_type is None and hasattr(codegen, 'enum_table'):
        enum_type = codegen.enum_table.by_name.get(pattern.enum_name)

    if not enum_type:
        raise_internal_error(
            "CE0121",
            pattern=f"{pattern.enum_name}.{pattern.variant_name}",
        )

    variant = enum_type.get_variant(pattern.variant_name)
    if not variant or not variant.associated_types:
        return

    data_array = enum_utils.extract_enum_data(codegen, scrutinee_value, name="match_data")

    data_array_type = scrutinee_value.type.elements[1]
    # ENTRY block, not the current position: a match inside a loop would otherwise
    # allocate again every iteration and never release it (BUGS.md B1). Reuse is safe
    # because every value binding is loaded OUT of this copy below, and a reference
    # binding deliberately points into the scrutinee instead (#253).
    temp_alloca = codegen.memory.entry_alloca(data_array_type, "match_data_storage")
    codegen.builder.store(data_array, temp_alloca)

    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Extract each binding at its offset from the ONE layout authority (#300 phase 2);
    # the offsets are naturally aligned and the payload base is 8-aligned, so the loads
    # need no `align=1` any more.
    field_offsets = codegen.types.payload_field_offsets(variant.associated_types)
    for (binding_item, binding_type), field_offset in zip(
            zip(pattern.bindings, variant.associated_types, strict=True), field_offsets,
            strict=True):
        binding_llvm_type = codegen.types.ll_type(binding_type)

        # A reference binding points into the SCRUTINEE'S own payload storage, never this
        # arm's temporary copy -- a pointer into the copy makes every write silently lost
        # (#253). The payload base is 8-aligned, so the interior pointer is naturally
        # aligned; the borrow pass guarantees the scrutinee is a bare name (CE2404).
        if isinstance(binding_item, RefBindingNode):
            from sushi_lang.backend.expressions.calls.utils import emit_receiver_as_pointer
            from sushi_lang.backend.statements.loops import bind_element_reference
            scrutinee_ptr = emit_receiver_as_pointer(codegen, scrutinee_expr)
            orig_data_ptr = gep_utils.gep_struct_field(
                codegen, scrutinee_ptr, 1, "scrutinee_data_ptr")
            orig_data_i8 = codegen.builder.bitcast(
                orig_data_ptr, codegen.types.str_ptr, name="scrutinee_data_i8")
            payload_field_ptr = codegen.builder.gep(
                orig_data_i8, [ir.Constant(codegen.types.i32, field_offset)],
                name="ref_binding_field_i8")
            payload_field_typed = codegen.builder.bitcast(
                payload_field_ptr, ir.PointerType(binding_llvm_type),
                name="ref_binding_field_ptr")
            bind_element_reference(codegen, binding_item.name, binding_item.mode,
                                   binding_type, payload_field_typed)
            continue

        field_ptr_i8 = codegen.builder.gep(data_ptr, [ir.Constant(codegen.types.i32, field_offset)], name="field_ptr")
        field_ptr_typed = codegen.builder.bitcast(field_ptr_i8, ir.PointerType(binding_llvm_type), name="field_ptr_typed")
        field_value = codegen.builder.load(field_ptr_typed, name="field_value")

        if isinstance(binding_item, str):
            # The binding BORROWS the enum's payload, so it is NOT registered for its own
            # RAII free -- the enum frees it, and registering both double-frees (#139).
            if binding_item != "_":
                codegen.memory.create_local(binding_item, binding_llvm_type, field_value, binding_type, register_cleanup=False)
                codegen.variable_types[binding_item] = binding_type
        elif isinstance(binding_item, PatternNode):
            _extract_nested_pattern(codegen, binding_item, field_value, binding_type, next_arm_bb)
        elif isinstance(binding_item, OwnPattern):
            _extract_own_pattern(codegen, binding_item, field_value, binding_type, next_arm_bb)


def _extract_nested_pattern(codegen: 'LLVMCodegen', nested_pattern: 'Pattern', enum_value: 'ir.Value', enum_type: 'Type', next_arm_bb: 'ir.Block | None' = None) -> None:
    """Extract and validate a nested pattern from an enum value."""
    from sushi_lang.semantics.typesys import EnumType

    concrete_enum_type = enum_type
    if not isinstance(concrete_enum_type, EnumType):
        if hasattr(codegen, 'enum_table'):
            concrete_enum_type = codegen.enum_table.by_name.get(nested_pattern.enum_name)

    if not isinstance(concrete_enum_type, EnumType):
        return

    variant = concrete_enum_type.get_variant(nested_pattern.variant_name)
    if not variant:
        return

    expected_tag = concrete_enum_type.get_variant_index(nested_pattern.variant_name)
    if expected_tag is None:
        return

    tag_matches = enum_utils.check_enum_variant(
        codegen, enum_value, expected_tag, signed=True, name="nested_tag_matches"
    )

    match_bb = codegen.func.append_basic_block(name="nested_pattern_match")
    mismatch_bb = codegen.func.append_basic_block(name="nested_pattern_mismatch")

    codegen.builder.cbranch(tag_matches, match_bb, mismatch_bb)

    codegen.builder.position_at_end(mismatch_bb)
    if next_arm_bb is not None:
        codegen.builder.branch(next_arm_bb)
    else:
        codegen.runtime.errors.emit_runtime_error(
            "RE2023",
            pattern=f"{nested_pattern.enum_name}.{nested_pattern.variant_name}",
        )
        codegen.builder.unreachable()

    codegen.builder.position_at_end(match_bb)

    _extract_pattern_bindings(codegen, nested_pattern, enum_value, concrete_enum_type)


def _extract_own_pattern(codegen: 'LLVMCodegen', own_pattern: 'OwnPattern', own_value: 'ir.Value', own_type: 'Type', next_arm_bb: 'ir.Block | None' = None) -> None:
    """Extract and bind an Own<T> pattern by auto-unwrapping."""
    from sushi_lang.semantics.ast import Pattern as PatternNode
    from sushi_lang.semantics.typesys import StructType
    from sushi_lang.backend.generics import own as own_module
    from sushi_lang.semantics.generics.own import get_own_element_type

    if not isinstance(own_type, StructType) or not own_type.name.startswith("Own<"):
        raise_internal_error("CE0022", type=str(own_type))

    element_type = get_own_element_type(own_type)

    unwrapped_value = own_module.emit_own_get(codegen, own_value, element_type)

    element_llvm_type = codegen.types.ll_type(element_type)

    inner_pattern = own_pattern.inner_pattern
    if isinstance(inner_pattern, str):
        # The binding BORROWS the Own's pointee, which the Own<T> still owns, so it must
        # NOT be registered -- the same rule as the plain arm binding above. It was latent
        # until #162/#183 stopped the enum destructor no-opping on an Own payload.
        if inner_pattern != "_":
            if own_pattern.inner_borrow is not None:
                # `Own(poke x)` binds the heap POINTER, not a copy of the pointee, so a
                # write lands in the allocation the Own owns. The slot mimics a reference
                # parameter's `T**`, and the `ReferenceType` flips every deref consumer.
                from sushi_lang.semantics.typesys import BorrowMode, ReferenceType
                pointee_ptr = codegen.builder.extract_value(own_value, 0, name="own_ptr")
                mode = (BorrowMode.POKE if own_pattern.inner_borrow == "poke"
                        else BorrowMode.PEEK)
                ref_type = ReferenceType(element_type, mode)
                codegen.memory.create_local(inner_pattern, pointee_ptr.type, pointee_ptr,
                                            ref_type, register_cleanup=False)
                codegen.variable_types[inner_pattern] = ref_type
            else:
                codegen.memory.create_local(inner_pattern, element_llvm_type, unwrapped_value,
                                            element_type, register_cleanup=False)
    elif isinstance(inner_pattern, PatternNode):
        _extract_nested_pattern(codegen, inner_pattern, unwrapped_value, element_type, next_arm_bb)


def _emit_block(codegen: 'LLVMCodegen', block) -> None:
    """Helper to emit a block of statements."""
    from sushi_lang.backend.statements import StatementEmitter
    emitter = StatementEmitter(codegen)
    emitter.emit_block(block)
