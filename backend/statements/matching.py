"""
Pattern matching statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for match statements with
exhaustive pattern matching on enum types.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from internals.errors import raise_internal_error
from backend import enum_utils
from backend.utils import require_both_initialized

if TYPE_CHECKING:
    from llvmlite import ir
    from backend.codegen_llvm import LLVMCodegen
    from semantics.ast import Match


def emit_match(codegen: 'LLVMCodegen', stmt: 'Match') -> None:
    """Emit match statement with exhaustive pattern matching.

    Compiles a match statement by:
    1. Evaluating the scrutinee
    2. Extracting the tag (discriminant)
    3. Comparing tag against each variant
    4. Extracting associated data and binding to pattern variables
    5. Executing the matching arm's body

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The match statement node to emit.
    """
    from llvmlite import ir

    builder, func = require_both_initialized(codegen)
    codegen.utils.ensure_open_block()

    # Emit the scrutinee and get its enum value
    scrutinee_value = codegen.expressions.emit_expr(stmt.scrutinee)

    # Get the scrutinee's type for pattern variable extraction
    scrutinee_type = _get_scrutinee_type(codegen, stmt.scrutinee)

    # Extract the tag (discriminant) from the enum struct: {i32 tag, [N x i8] data}
    tag = enum_utils.extract_enum_tag(codegen, scrutinee_value, name="match_tag")

    # Create basic blocks for each arm and the merge block
    end_bb = codegen.func.append_basic_block(name="match.end")
    arm_blocks = []
    for i, arm in enumerate(stmt.arms):
        arm_bb = codegen.func.append_basic_block(name=f"match.arm{i}")
        arm_blocks.append(arm_bb)

    # Find wildcard pattern (if any) to use as default case
    wildcard_bb = _find_wildcard_block(stmt, arm_blocks)

    # Create switch instruction and unreachable block if needed
    switch, unreachable_bb = _create_switch_instruction(codegen, tag, wildcard_bb)

    # Add cases for each arm (skip wildcard - it's the default)
    _add_switch_cases(codegen, stmt, arm_blocks, switch, scrutinee_type)

    # Emit each arm
    _emit_match_arms(codegen, stmt, arm_blocks, scrutinee_value, scrutinee_type, end_bb)

    # Emit unreachable block if no wildcard (after all arms)
    if unreachable_bb is not None:
        codegen.builder.position_at_end(unreachable_bb)
        codegen.builder.unreachable()

    # Position at end block
    codegen.builder.position_at_end(end_bb)


def _get_scrutinee_type(codegen: 'LLVMCodegen', scrutinee: 'Expr') -> 'EnumType | None':
    """Get the EnumType of the scrutinee expression.

    For match expressions, this function determines the concrete enum type of the
    scrutinee, which is crucial for proper pattern variable extraction. This is
    especially important for generic enums like Maybe<T> and Result<T>.

    Args:
        codegen: The main LLVMCodegen instance.
        scrutinee: The scrutinee expression.

    Returns:
        The monomorphized EnumType of the scrutinee, or None if not found.
    """
    from semantics.ast import Name, DotCall, MethodCall, Call, MemberAccess
    from semantics.typesys import EnumType, ResultType, StructType

    # Try to get type from variable table if it's a Name
    if isinstance(scrutinee, Name):
        var_type = codegen.memory.find_semantic_type(scrutinee.id)

        # Handle EnumType directly
        if isinstance(var_type, EnumType):
            return var_type

        # Handle GenericTypeRef("Result", [T, E]) - resolve to Result<T, E> enum
        from semantics.generics.types import GenericTypeRef
        if isinstance(var_type, GenericTypeRef):
            if var_type.base_name == "Result" and len(var_type.type_args) == 2:
                from backend.generics.results import ensure_result_type_in_table
                from semantics.type_resolution import resolve_unknown_type

                # Resolve type arguments
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

                # Ensure Result<T, E> exists and return it
                result_enum = ensure_result_type_in_table(
                    codegen.enum_table,
                    ok_type,
                    err_type
                )
                return result_enum
            else:
                # Other generic type - look up in enum table
                type_args_str = ", ".join(str(arg) for arg in var_type.type_args)
                concrete_name = f"{var_type.base_name}<{type_args_str}>"
                if concrete_name in codegen.enum_table.by_name:
                    return codegen.enum_table.by_name[concrete_name]

        # Handle ResultType - ensure it's in the enum table
        elif isinstance(var_type, ResultType):
            from backend.generics.results import ensure_result_type_in_table
            result_enum = ensure_result_type_in_table(
                codegen.enum_table,
                var_type.ok_type,
                var_type.err_type
            )
            return result_enum

    # For MemberAccess (struct field access like op.result), infer the field type
    if isinstance(scrutinee, MemberAccess):
        # Get the struct type of the receiver
        if isinstance(scrutinee.receiver, Name):
            receiver_type = codegen.memory.find_semantic_type(scrutinee.receiver.id)
            if isinstance(receiver_type, StructType):
                # Find the field type
                for field_name, field_type in receiver_type.fields:
                    if field_name == scrutinee.member:
                        # Handle GenericTypeRef("Result", [T, E]) - resolve to Result<T, E> enum
                        from semantics.generics.types import GenericTypeRef
                        if isinstance(field_type, GenericTypeRef):
                            if field_type.base_name == "Result" and len(field_type.type_args) == 2:
                                from backend.generics.results import ensure_result_type_in_table
                                from semantics.type_resolution import resolve_unknown_type

                                # Resolve type arguments
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

                                # Ensure Result<T, E> exists and return it
                                result_enum = ensure_result_type_in_table(
                                    codegen.enum_table,
                                    ok_type,
                                    err_type
                                )
                                return result_enum
                            else:
                                # Other generic type - look up in enum table
                                type_args_str = ", ".join(str(arg) for arg in field_type.type_args)
                                concrete_name = f"{field_type.base_name}<{type_args_str}>"
                                if concrete_name in codegen.enum_table.by_name:
                                    return codegen.enum_table.by_name[concrete_name]
                        # If field type is ResultType, ensure it's in the enum table
                        elif isinstance(field_type, ResultType):
                            from backend.generics.results import ensure_result_type_in_table
                            result_enum = ensure_result_type_in_table(
                                codegen.enum_table,
                                field_type.ok_type,
                                field_type.err_type
                            )
                            return result_enum
                        elif isinstance(field_type, EnumType):
                            return field_type
        return None

    # For Call nodes (function calls like simple_result()), infer the return type
    # which is Result<T> for most user functions
    if isinstance(scrutinee, Call):
        try:
            from backend.expressions.operators import _infer_call_return_type
            return _infer_call_return_type(codegen, scrutinee)
        except Exception:
            # If inference fails, fall through to other methods
            pass

    # For DotCall or MethodCall (method calls like "hello".find()), check if the type
    # checker already inferred and annotated the return type (Phase 2)
    if isinstance(scrutinee, (DotCall, MethodCall)):
        # The type checker annotates these nodes with inferred_return_type during Pass 2
        if hasattr(scrutinee, 'inferred_return_type') and isinstance(scrutinee.inferred_return_type, EnumType):
            return scrutinee.inferred_return_type

    # Try to infer from scrutinee's type annotation if available (generic fallback)
    # This is set by the type checker in some cases
    if hasattr(scrutinee, 'inferred_type') and isinstance(scrutinee.inferred_type, EnumType):
        return scrutinee.inferred_type

    return None


def _find_wildcard_block(stmt: 'Match', arm_blocks: list['ir.Block']) -> 'ir.Block | None':
    """Find the block corresponding to a wildcard pattern, if any.

    Args:
        stmt: The match statement.
        arm_blocks: List of basic blocks for each arm.

    Returns:
        The wildcard block, or None if no wildcard pattern exists.
    """
    from semantics.ast import WildcardPattern

    for i, arm in enumerate(stmt.arms):
        if isinstance(arm.pattern, WildcardPattern):
            return arm_blocks[i]
    return None


def _find_next_arm_with_same_tag(codegen: 'LLVMCodegen', stmt: 'Match', arm_blocks: list['ir.Block'], scrutinee_type: 'EnumType | None', current_arm_index: int) -> 'ir.Block | None':
    """Find the next arm that has the same outer tag as the current arm.

    This is used for nested pattern fallthrough. When a nested pattern doesn't match,
    we want to fallthrough to the next arm that matches the same outer variant.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The match statement.
        arm_blocks: List of basic blocks for each arm.
        scrutinee_type: The EnumType of the scrutinee.
        current_arm_index: Index of the current arm.

    Returns:
        The block of the next arm with the same tag, or None if no such arm exists.
    """
    from semantics.ast import Pattern, WildcardPattern

    # Get the current arm's pattern
    current_arm = stmt.arms[current_arm_index]
    if not isinstance(current_arm.pattern, Pattern):
        return None

    # Get the current arm's variant tag
    enum_type = scrutinee_type
    if enum_type is None and hasattr(codegen, 'enum_table'):
        enum_type = codegen.enum_table.by_name.get(current_arm.pattern.enum_name)

    if enum_type is None:
        return None

    current_tag = enum_type.get_variant_index(current_arm.pattern.variant_name)
    if current_tag is None:
        return None

    # Look for the next arm with the same outer tag
    for i in range(current_arm_index + 1, len(stmt.arms)):
        next_arm = stmt.arms[i]

        # Wildcard pattern matches any tag - use it as fallthrough
        if isinstance(next_arm.pattern, WildcardPattern):
            return arm_blocks[i]

        # Check if it's a pattern with the same outer tag
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
    """Create the switch instruction for pattern matching.

    Args:
        codegen: The main LLVMCodegen instance.
        tag: The discriminant tag value.
        wildcard_bb: The wildcard block (default case), or None.

    Returns:
        Tuple of (switch instruction, unreachable block or None).
    """
    # Default case: wildcard if present, otherwise unreachable
    if wildcard_bb is None:
        unreachable_bb = codegen.func.append_basic_block(name="match.unreachable")
        return codegen.builder.switch(tag, unreachable_bb), unreachable_bb
    else:
        return codegen.builder.switch(tag, wildcard_bb), None


def _add_switch_cases(codegen: 'LLVMCodegen', stmt: 'Match', arm_blocks: list['ir.Block'], switch: 'ir.Instruction', scrutinee_type: 'EnumType | None') -> None:
    """Add switch cases for each match arm.

    For nested patterns, multiple arms may have the same outer variant tag.
    In this case, we only add the first arm with that tag to the switch,
    and the nested pattern checking happens inside the arm via runtime checks.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The match statement.
        arm_blocks: List of basic blocks for each arm.
        switch: The switch instruction.
        scrutinee_type: The EnumType of the scrutinee (monomorphized for generics).
    """
    from llvmlite import ir
    from semantics.ast import Pattern, WildcardPattern

    # Track which variant indices have already been added to avoid duplicates
    # (needed for nested patterns where multiple patterns can have the same outer variant)
    added_tags = set()

    for i, (arm, arm_bb) in enumerate(zip(stmt.arms, arm_blocks)):
        if isinstance(arm.pattern, WildcardPattern):
            # Skip wildcard - already set as default
            continue
        if not isinstance(arm.pattern, Pattern):
            continue

        # Use scrutinee_type if available (handles generic enums), otherwise fall back to pattern lookup
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
    """Emit all match arms.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The match statement.
        arm_blocks: List of basic blocks for each arm.
        scrutinee_value: The scrutinee enum value.
        scrutinee_type: The EnumType of the scrutinee (monomorphized for generics).
        end_bb: The end block to branch to.
    """
    from semantics.ast import Pattern, Block, WildcardPattern

    for i, (arm, arm_bb) in enumerate(zip(stmt.arms, arm_blocks)):
        codegen.builder.position_at_end(arm_bb)
        codegen.memory.push_scope()

        if isinstance(arm.pattern, Pattern):
            # Find the next arm with the same outer tag (for nested pattern fallthrough)
            next_arm_bb = _find_next_arm_with_same_tag(codegen, stmt, arm_blocks, scrutinee_type, i)

            # Extract and bind pattern variables
            _extract_pattern_bindings(codegen, arm.pattern, scrutinee_value, scrutinee_type, next_arm_bb)

        # Emit the arm body
        if isinstance(arm.body, Block):
            _emit_block(codegen, arm.body)
        else:
            # Expression body
            codegen.expressions.emit_expr(arm.body)

        codegen.memory.pop_scope()

        # Branch to end if not already terminated
        if codegen.builder.block.terminator is None:
            codegen.builder.branch(end_bb)


def _extract_pattern_bindings(codegen: 'LLVMCodegen', pattern: 'Pattern', scrutinee_value: 'ir.Value', scrutinee_type: 'EnumType | None', next_arm_bb: 'ir.Block | None' = None) -> None:
    """Extract and bind pattern variables from enum data.

    Supports nested patterns by recursively extracting and matching nested enum values.
    Supports Own<T> patterns by auto-unwrapping via Own<T>.get().

    Args:
        codegen: The main LLVMCodegen instance.
        pattern: The pattern with bindings.
        scrutinee_value: The scrutinee enum value.
        scrutinee_type: The EnumType of the scrutinee (monomorphized for generics).
    """
    from llvmlite import ir
    from semantics.ast import Pattern as PatternNode, OwnPattern

    if not pattern.bindings:
        return

    # Use scrutinee_type if available (handles generic enums), otherwise fall back to pattern lookup
    enum_type = scrutinee_type
    if enum_type is None and hasattr(codegen, 'enum_table'):
        enum_type = codegen.enum_table.by_name.get(pattern.enum_name)

    if not enum_type:
        return

    variant = enum_type.get_variant(pattern.variant_name)
    if not variant or not variant.associated_types:
        return

    # Extract the data field: [N x i8] array
    data_array = enum_utils.extract_enum_data(codegen, scrutinee_value, name="match_data")

    # Allocate storage for the data array
    data_array_type = scrutinee_value.type.elements[1]
    temp_alloca = codegen.builder.alloca(data_array_type, name="match_data_storage")
    codegen.builder.store(data_array, temp_alloca)

    # Cast to i8* for accessing fields
    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Extract each binding
    offset = 0
    for binding_item, binding_type in zip(pattern.bindings, variant.associated_types):
        # Get the LLVM type for this binding
        binding_llvm_type = codegen.types.ll_type(binding_type)

        # Load the value from the data field
        field_ptr_i8 = codegen.builder.gep(data_ptr, [ir.Constant(codegen.types.i32, offset)], name="field_ptr")
        field_ptr_typed = codegen.builder.bitcast(field_ptr_i8, ir.PointerType(binding_llvm_type), name="field_ptr_typed")
        field_value = codegen.builder.load(field_ptr_typed, name="field_value")

        # Handle simple bindings (strings), nested patterns (Pattern), and Own patterns (OwnPattern)
        if isinstance(binding_item, str):
            # Simple binding: create local variable (skip wildcards "_")
            if binding_item != "_":
                codegen.memory.create_local(binding_item, binding_llvm_type, field_value, binding_type)
                # Also register in variable_types for member access and other operations
                codegen.variable_types[binding_item] = binding_type
        elif isinstance(binding_item, PatternNode):
            # Nested pattern: recursively extract and validate
            _extract_nested_pattern(codegen, binding_item, field_value, binding_type, next_arm_bb)
        elif isinstance(binding_item, OwnPattern):
            # Own pattern: unwrap Own<T> via .get() and bind inner pattern
            _extract_own_pattern(codegen, binding_item, field_value, binding_type, next_arm_bb)

        # Calculate offset for next field using semantic type size (accounts for padding/alignment)
        offset += codegen.types.get_type_size_bytes(binding_type)


def _extract_nested_pattern(codegen: 'LLVMCodegen', nested_pattern: 'Pattern', enum_value: 'ir.Value', enum_type: 'Type', next_arm_bb: 'ir.Block | None' = None) -> None:
    """Extract and validate a nested pattern from an enum value.

    This function:
    1. Extracts the tag from the nested enum
    2. Validates it matches the expected variant
    3. Recursively extracts bindings from the nested pattern
    4. If the tag doesn't match and next_arm_bb is provided, branches to next arm (fallthrough)

    Args:
        codegen: The main LLVMCodegen instance.
        nested_pattern: The nested pattern to match.
        enum_value: The enum value to extract from.
        enum_type: The semantic type of the enum.
        next_arm_bb: The next arm block to branch to on mismatch (for fallthrough support).
    """
    from llvmlite import ir
    from semantics.typesys import EnumType

    # Resolve the enum type (handle generics)
    concrete_enum_type = enum_type
    if not isinstance(concrete_enum_type, EnumType):
        # Try to resolve from pattern name
        if hasattr(codegen, 'enum_table'):
            concrete_enum_type = codegen.enum_table.by_name.get(nested_pattern.enum_name)

    if not isinstance(concrete_enum_type, EnumType):
        return

    # Get the expected variant
    variant = concrete_enum_type.get_variant(nested_pattern.variant_name)
    if not variant:
        return

    expected_tag = concrete_enum_type.get_variant_index(nested_pattern.variant_name)
    if expected_tag is None:
        return

    # Extract and validate the tag matches (emit runtime check)
    tag_matches = enum_utils.check_enum_variant(
        codegen, enum_value, expected_tag, signed=True, name="nested_tag_matches"
    )

    # Create blocks for tag match and tag mismatch
    match_bb = codegen.func.append_basic_block(name="nested_pattern_match")
    mismatch_bb = codegen.func.append_basic_block(name="nested_pattern_mismatch")

    codegen.builder.cbranch(tag_matches, match_bb, mismatch_bb)

    # Mismatch block: Branch to next arm if available (fallthrough), otherwise emit error
    codegen.builder.position_at_end(mismatch_bb)
    if next_arm_bb is not None:
        # Fallthrough to next arm with same outer tag
        codegen.builder.branch(next_arm_bb)
    else:
        # No fallthrough available - this is a runtime error
        error_msg = f"nested pattern mismatch: expected {nested_pattern.enum_name}.{nested_pattern.variant_name}"
        codegen.runtime.errors.emit_runtime_error("RE9999", error_msg)
        codegen.builder.unreachable()

    # Match block: Extract bindings from nested pattern
    codegen.builder.position_at_end(match_bb)

    # Recursively extract bindings from the nested pattern
    _extract_pattern_bindings(codegen, nested_pattern, enum_value, concrete_enum_type)


def _extract_own_pattern(codegen: 'LLVMCodegen', own_pattern: 'OwnPattern', own_value: 'ir.Value', own_type: 'Type', next_arm_bb: 'ir.Block | None' = None) -> None:
    """Extract and bind an Own<T> pattern by auto-unwrapping.

    This function:
    1. Unwraps Own<T> via Own<T>.get() to get the owned value
    2. Binds the inner pattern to the unwrapped value

    Syntax example:
        match expr:
            Expr.BinOp(Own(left), Own(right), op) ->
                # left and right are auto-unwrapped to Expr

    Args:
        codegen: The main LLVMCodegen instance.
        own_pattern: The Own pattern node.
        own_value: The Own<T> struct value to unwrap.
        own_type: The semantic type Own<T>.
        next_arm_bb: The next arm block to branch to on mismatch (for fallthrough support).
    """
    from semantics.ast import Pattern as PatternNode
    from semantics.typesys import StructType
    from backend.generics import own as own_module

    # Verify this is actually an Own<T> type
    if not isinstance(own_type, StructType) or not own_type.name.startswith("Own<"):
        # Type validation should have caught this already
        raise_internal_error("CE0022", type=str(own_type))

    # Get element type T from Own<T>
    element_type = own_module.get_own_element_type(own_type)

    # Unwrap Own<T> via .get() to get the owned value
    unwrapped_value = own_module.emit_own_get(codegen, own_value, element_type)

    # Get LLVM type for the unwrapped value
    element_llvm_type = codegen.types.ll_type(element_type)

    # Bind the inner pattern
    inner_pattern = own_pattern.inner_pattern
    if isinstance(inner_pattern, str):
        # Simple binding: create local variable for the unwrapped value (skip wildcards "_")
        if inner_pattern != "_":
            codegen.memory.create_local(inner_pattern, element_llvm_type, unwrapped_value, element_type)
    elif isinstance(inner_pattern, PatternNode):
        # Nested pattern: recursively extract and validate the unwrapped value
        _extract_nested_pattern(codegen, inner_pattern, unwrapped_value, element_type, next_arm_bb)


def _emit_block(codegen: 'LLVMCodegen', block) -> None:
    """Helper to emit a block of statements.

    Args:
        codegen: The main LLVMCodegen instance.
        block: The block AST node containing statements.
    """
    # Import here to avoid circular dependency
    from backend.statements import StatementEmitter
    emitter = StatementEmitter(codegen)
    emitter.emit_block(block)
