"""Array and struct initialization helpers for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import ArrayLiteral
    from sushi_lang.semantics.typesys import DynamicArrayType


def initialize_array_literal(
    codegen: 'LLVMCodegen',
    slot: 'ir.AllocaInstr',
    array_literal: 'ArrayLiteral',
    array_type: 'ir.ArrayType',
    element_semantic_type=None
) -> None:
    """Initialize array variable with array literal elements."""
    from llvmlite import ir
    from sushi_lang.backend.destructors import resolve_named_type
    from sushi_lang.backend.ownership import ConsumingUse, consume

    require_builder(codegen)

    resolved_element = (resolve_named_type(codegen, element_semantic_type)
                        if element_semantic_type is not None else None)

    for i, element_expr in enumerate(array_literal.elements):
        element_value = codegen.expressions.emit_expr(element_expr)

        element_value = consume(codegen, element_expr, element_value, resolved_element,
                                ConsumingUse.ARRAY_ELEMENT)

        zero = ir.Constant(codegen.i32, 0)
        index = ir.Constant(codegen.i32, i)
        gep = codegen.builder.gep(slot, [zero, index])

        casted_element = codegen.utils.cast_for_param(element_value, array_type.element)
        codegen.builder.store(casted_element, gep)


def initialize_dynamic_array(
    codegen: 'LLVMCodegen',
    name: str,
    array_type: 'DynamicArrayType',
    constructor_expr
) -> None:
    """Initialize dynamic array variable with constructor or expression."""
    from sushi_lang.semantics.ast import DynamicArrayNew, DynamicArrayFrom
    from llvmlite import ir
    if codegen.dynamic_arrays is None:
        raise_internal_error("CE0014")

    alloca = codegen.dynamic_arrays.declare_dynamic_array(name, array_type)

    current_scope_level = codegen.memory._scope_depth
    codegen.memory._scope_vars[current_scope_level].add(name)

    if name not in codegen.memory._locals:
        codegen.memory._locals[name] = []
    codegen.memory._locals[name].append((current_scope_level, alloca))

    if name not in codegen.memory._types:
        codegen.memory._types[name] = []
    codegen.memory._types[name].append((current_scope_level, array_type))

    if isinstance(constructor_expr, DynamicArrayNew):
        codegen.dynamic_arrays.emit_array_constructor_new(name)
    elif isinstance(constructor_expr, DynamicArrayFrom):
        # Optimized path: array literal with from([...]). A heap-owning element that aliases
        # a live owner is deep-copied so the array and the source each own independent
        # buffers (#139); a fresh temp element is the sole owner and moved in.
        from sushi_lang.backend.types.arrays import emit_array_literal_elements
        elements = emit_array_literal_elements(
            codegen, constructor_expr.elements.elements, array_type.base_type
        )
        codegen.dynamic_arrays.emit_array_constructor_from(name, elements)
    else:
        val = codegen.expressions.emit_expr(constructor_expr)

        if isinstance(val.type, ir.PointerType) and codegen.types.is_dynamic_array_type(val.type.pointee):
            val = codegen.builder.load(val, name=f"{name}_init_value")

        # A bare `T[]` binding goes through the same seam as every other `let`. With no
        # decision here, `let b = a` left two registered owners of one buffer.
        from sushi_lang.backend.ownership import bind, relinquish
        val, owns = bind(codegen, constructor_expr, val, array_type)

        codegen.builder.store(val, alloca)

        if not owns:
            # `let i32[] view = holder.items` BORROWS: the struct still owns the buffer and
            # still frees it, so this binding must not (#242). The descriptor had to be
            # declared and registered above, before the initializer that decides could be
            # emitted, so this is the one `let` path that gives the registration back.
            relinquish(codegen, name)
