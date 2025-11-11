"""
Protocol interfaces for backend components to reduce circular dependencies.

This module defines Protocol classes that represent the interface contracts
for the main backend components. Using Protocol instead of concrete classes
allows for:
- Reduced circular dependencies
- Better separation of concerns
- Easier testing with mock objects
- More flexible refactoring

Usage:
    from backend.interfaces import CodegenProtocol

    def my_function(codegen: CodegenProtocol) -> ir.Value:
        # Use codegen.builder, codegen.types, etc.
        pass
"""
from __future__ import annotations
from typing import Protocol, Dict, Optional, Any

from llvmlite import ir
from semantics.typesys import Type as SemanticType


class TypeSystemProtocol(Protocol):
    """Protocol for LLVM type system operations."""

    i32: ir.IntType
    i8: ir.IntType
    i64: ir.IntType
    i16: ir.IntType
    i1: ir.IntType
    f32: ir.FloatType
    f64: ir.DoubleType

    def ll_type(self, ty: SemanticType) -> ir.Type:
        """Convert semantic type to LLVM type."""
        ...

    def is_dynamic_array_type(self, ty: ir.Type) -> bool:
        """Check if type is a dynamic array struct."""
        ...


class MemoryManagerProtocol(Protocol):
    """Protocol for memory management operations."""

    def push_scope(self) -> None:
        """Push a new lexical scope."""
        ...

    def pop_scope(self) -> None:
        """Pop the current lexical scope."""
        ...

    def find_local_slot(self, name: str) -> ir.AllocaInstr:
        """Find local variable slot by name."""
        ...

    def find_semantic_type(self, name: str) -> Optional[SemanticType]:
        """Find semantic type for a variable."""
        ...

    def create_local(self, name: str, ty: ir.Type, init: ir.Value | None = None,
                    semantic_ty: Optional[SemanticType] = None) -> ir.AllocaInstr:
        """Create local variable with optional initialization."""
        ...

    def entry_alloca(self, ty: ir.Type, name: str) -> ir.AllocaInstr:
        """Create alloca instruction in function entry block."""
        ...


class UtilsProtocol(Protocol):
    """Protocol for LLVM utility operations."""

    def as_i1(self, v: ir.Value) -> ir.Value:
        """Convert value to i1 (boolean)."""
        ...

    def as_i8(self, v: ir.Value) -> ir.Value:
        """Convert value to i8."""
        ...

    def as_i32(self, v: ir.Value) -> ir.Value:
        """Convert value to i32."""
        ...

    def cast_to_int_width(self, v: ir.Value, dst: ir.IntType, is_signed: bool = False) -> ir.Value:
        """Cast value to target integer width using dispatch table."""
        ...

    def cast_for_param(self, v: ir.Value, dst: ir.Type) -> ir.Value:
        """Cast expression value to match function parameter type."""
        ...

    def get_zero_value(self, llvm_type: ir.Type) -> ir.Value:
        """Create a zero/default value for a given LLVM type."""
        ...


class RuntimeProtocol(Protocol):
    """Protocol for runtime operations."""

    def emit_string_comparison(self, op: str, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        """Emit string comparison operation."""
        ...

    def emit_malloc(self, size: ir.Value, name: str = "malloc_result") -> ir.Value:
        """Emit malloc call with runtime error checking."""
        ...


class FunctionManagerProtocol(Protocol):
    """Protocol for function management operations."""

    def get_or_declare_function(self, name: str, ret_ty: ir.Type,
                               arg_types: list[ir.Type]) -> ir.Function:
        """Get or declare a function in the module."""
        ...


class ExpressionsProtocol(Protocol):
    """Protocol for expression emission operations."""

    def emit_expr(self, expr: Any, to_i1: bool = False) -> ir.Value:
        """Emit LLVM IR for an expression."""
        ...


class StatementsProtocol(Protocol):
    """Protocol for statement emission operations."""

    def emit_stmt(self, stmt: Any) -> None:
        """Emit LLVM IR for a statement."""
        ...


class CodegenProtocol(Protocol):
    """
    Main protocol for LLVM code generation operations.

    This protocol defines the interface for the main codegen instance,
    providing access to all necessary components for code generation.

    Attributes:
        builder: The current LLVM IR builder (may be None between functions).
        types: Type system operations.
        memory: Memory management operations.
        utils: Utility operations for casting and conversions.
        runtime: Runtime support operations.
        functions: Function management operations.
        expressions: Expression emission operations.
        statements: Statement emission operations.
        module: The LLVM module being generated.
        func: The current LLVM function being generated (may be None).
        i32, i8, i64, i16, i1: Common LLVM types for convenience.
        f32, f64: Floating-point types.
    """

    # Core builder and module
    builder: ir.IRBuilder | None
    module: ir.Module
    func: ir.Function | None

    # Manager components
    types: TypeSystemProtocol
    memory: MemoryManagerProtocol
    utils: UtilsProtocol
    runtime: RuntimeProtocol
    functions: FunctionManagerProtocol
    expressions: ExpressionsProtocol
    statements: StatementsProtocol

    # Convenience type shortcuts
    i32: ir.IntType
    i8: ir.IntType
    i64: ir.IntType
    i16: ir.IntType
    i1: ir.IntType
    f32: ir.FloatType
    f64: ir.DoubleType

    # Additional attributes commonly accessed
    entry_block: ir.Block | None
    alloca_builder: ir.IRBuilder | None
    variable_types: Dict[str, SemanticType]
    constants: Dict[str, ir.GlobalVariable]
    string_constants: Dict[str, ir.GlobalVariable]
