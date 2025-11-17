# semantics/ast.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union, Literal
from internals.report import Span
from semantics.typesys import Type

from lark import Token

# === Core node base ===

@dataclass
class Node:
    loc: Optional[Span]

@dataclass
class Stmt(Node):
    pass

# === Program structure ===

@dataclass
class UseStatement(Node):
    path: str                        # Path string like "math/integer" or "core/results"
    is_stdlib: bool = False          # True for <module>, False for "module"

@dataclass
class Program(Node):
    uses: List["UseStatement"]
    constants: List["ConstDef"]
    structs: List["StructDef"]
    enums: List["EnumDef"]
    perks: List["PerkDef"]
    functions: List["FuncDef"]
    extensions: List["ExtendDef"]           # Non-generic extensions only
    generic_extensions: List["ExtendDef"]   # Generic extensions only (e.g., extend Box<T>)
    perk_impls: List["ExtendWithDef"]

@dataclass
class Param:
    name: str
    ty: Optional[Type]
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None
    loc: Optional[Span] = None

@dataclass
class BoundedTypeParam:
    """Type parameter with optional perk constraints (e.g., T: Hashable)."""
    name: str
    constraints: List[str] = None  # Perk names (e.g., ["Hashable", "Eq"])
    loc: Optional[Span] = None

    def __post_init__(self):
        if self.constraints is None:
            self.constraints = []

    def __str__(self) -> str:
        if self.constraints:
            constraints_str = " + ".join(self.constraints)
            return f"{self.name}: {constraints_str}"
        return self.name

@dataclass
class FuncDef(Node):
    name: str
    params: List[Param]
    ret: Optional[Type]
    body: "Block"
    is_public: bool = False          # True if declared with 'public' keyword
    type_params: Optional[List[BoundedTypeParam]] = None  # Generic type parameters with constraints
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None

@dataclass
class ConstDef(Node):
    name: str                    # Constant name
    ty: Optional[Type]           # Constant type (must be specified)
    value: "Expr"                # Constant value expression
    is_public: bool = False      # True if declared with 'public' keyword
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None

@dataclass
class StructField:
    """Single field in a struct definition."""
    ty: Optional[Type]           # Field type
    name: str                    # Field name
    loc: Optional[Span] = None

@dataclass
class StructDef(Node):
    """Struct definition with fields."""
    name: str                             # Struct name
    fields: List[StructField]             # List of fields
    type_params: Optional[List[BoundedTypeParam]] = None  # Type parameters with optional constraints
    name_span: Optional[Span] = None

@dataclass
class EnumVariant:
    """Single variant in an enum definition."""
    name: str                           # Variant name (e.g., "Some", "None")
    associated_types: List[Type]        # Associated data types (empty for unit variants)
    name_span: Optional[Span] = None
    loc: Optional[Span] = None

@dataclass
class EnumDef(Node):
    """Enum definition with variants."""
    name: str                           # Enum name (e.g., "Option", "Result")
    variants: List[EnumVariant]         # List of variants
    type_params: Optional[List[BoundedTypeParam]] = None  # Type parameters with optional constraints
    name_span: Optional[Span] = None

@dataclass
class ExtendDef(Node):
    target_type: Optional[Type]  # Type being extended (int, bool, string)
    name: str                    # Method name (add, multiply, etc.)
    params: List[Param]          # Parameters excluding implicit 'self'
    ret: Optional[Type]          # Return type
    body: "Block"                # Method body
    target_type_span: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None

@dataclass
class PerkMethodSignature:
    """Method signature required by a perk."""
    name: str
    params: List[Param]
    ret: Optional[Type]
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None

@dataclass
class PerkDef(Node):
    """Perk definition (trait/interface)."""
    name: str
    methods: List[PerkMethodSignature]
    type_params: Optional[List[BoundedTypeParam]] = None  # Generic params with optional constraints
    name_span: Optional[Span] = None

@dataclass
class ExtendWithDef(Node):
    """Perk implementation (extend Type with Perk)."""
    target_type: Optional[Type]
    perk_name: str
    methods: List[FuncDef]  # Implementation methods
    target_type_span: Optional[Span] = None
    perk_name_span: Optional[Span] = None

@dataclass
class TypeConstraint:
    """Perk constraint on a type parameter (T: Hashable)."""
    perk_name: str
    loc: Optional[Span] = None

@dataclass
class Block(Node):
    statements: List[Stmt]

# === Statements ===

@dataclass
class Let(Stmt):
    name: str
    ty: Optional[Type]
    value: "Expr"
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None

@dataclass
class Rebind(Stmt):
    target: "Expr"  # Can be Name or MemberAccess (for field rebinding)
    value: "Expr"

@dataclass
class ExprStmt(Stmt):
    expr: "Expr"

@dataclass
class Return(Stmt):
    value: "Expr"

@dataclass
class Print(Stmt):
    value: "Expr"

@dataclass
class PrintLn(Stmt):
    value: "Expr"

@dataclass
class If(Stmt):
    arms: List[Tuple["Expr", Block]]     # [(cond, block), ...]
    else_block: Optional[Block]

@dataclass
class While(Stmt):
    cond: "Expr"
    body: Block

@dataclass
class Foreach(Stmt):
    """Foreach loop statement: foreach(type item in iterable):"""
    item_name: str              # Loop variable name
    item_type: Optional[Type]   # Declared type (may be None for inference)
    iterable: "Expr"            # Expression yielding iterator
    body: Block                 # Loop body
    item_name_span: Optional[Span] = None
    item_type_span: Optional[Span] = None

@dataclass
class Break(Stmt):
    pass

@dataclass
class Continue(Stmt):
    pass

@dataclass
class Pattern(Node):
    """Pattern for match arms: EnumName.VariantName(binding1, binding2, ...)

    Supports nested patterns for matching nested enums:
    - Simple binding: FileResult.Err(err) - binds error to variable 'err'
    - Nested pattern: FileResult.Err(FileError.NotFound()) - matches specific error variant
    - Wildcard: FileResult.Err(_) - matches any error
    - Own pattern: Expr.BinOp(Own(left), Own(right), op) - auto-unwraps Own<T>
    """
    enum_name: str                                      # Enum type name (e.g., "FileResult")
    variant_name: str                                   # Variant name (e.g., "Err")
    bindings: List[Union[str, 'Pattern', 'OwnPattern']] # Variable names, nested patterns, Own patterns, or '_'
    enum_name_span: Optional[Span] = None
    variant_name_span: Optional[Span] = None

@dataclass
class WildcardPattern(Node):
    """Wildcard pattern (_) for match arms - catches all remaining variants"""
    pass

@dataclass
class OwnPattern(Node):
    """Own(inner_pattern) - auto-unwrap Own<T> in pattern matching.

    Syntax: Own(pattern)

    Example:
        match expr:
            Expr.BinOp(Own(left), Own(right), op) ->
                # left and right are auto-unwrapped to Expr

    The compiler generates Own<T>.get() to unwrap the owned value
    before matching the inner pattern.
    """
    inner_pattern: Union[str, 'Pattern']  # Variable name or nested pattern

@dataclass
class MatchArm(Node):
    """Single arm in a match statement/expression"""
    pattern: Union[Pattern, WildcardPattern]  # Pattern to match against
    body: Union["Expr", "Block"]  # Expression or block to execute

@dataclass
class Match(Stmt):
    """Match statement: match expr: pattern -> body"""
    scrutinee: "Expr"           # Expression being matched
    arms: List[MatchArm]        # Match arms


# === Expressions ===

@dataclass
class Name(Node):
    id: str

@dataclass
class IntLit(Node):
    value: int
    radix: int = 10  # 2 (binary), 8 (octal), 10 (decimal), 16 (hexadecimal)

@dataclass
class FloatLit(Node):
    value: float

@dataclass
class BoolLit(Node):
    value: bool

@dataclass
class BlankLit(Node):
    """Blank literal (~) - represents the single value of blank type"""
    pass

@dataclass
class StringLit(Node):
    value: str

@dataclass
class InterpolatedString(Node):
    """Represents a string with interpolated expressions like "Hello, {name}!"

    The parts list alternates between string literals and expressions:
    - String: "Hello, " -> parts[0] = "Hello, "
    - Expression: {name} -> parts[1] = Name("name")
    - String: "!" -> parts[2] = "!"
    """
    parts: List[Union[str, "Expr"]]  # Alternating string literals and expressions

@dataclass
class ArrayLiteral(Node):
    elements: List["Expr"]

@dataclass
class IndexAccess(Node):
    array: "Expr"
    index: "Expr"

UnOp = Literal["neg", "not", "~"]
@dataclass
class UnaryOp(Node):
    op: UnOp
    expr: "Expr"

BinOp = Literal["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "and", "or", "xor", "&", "|", "^", "<<", ">>"]
@dataclass
class BinaryOp(Node):
    op: BinOp
    left: "Expr"
    right: "Expr"

@dataclass
class Call(Node):
    callee: Name
    args: List["Expr"]
    field_names: Optional[List[str]] = None  # For named struct construction

@dataclass
class MethodCall(Node):
    receiver: "Expr"    # The object/expression being called (x in x.add(5))
    method: str         # Method name (add, multiply, etc.)
    args: List["Expr"]  # Arguments to the method
    inferred_return_type: Optional["Type"] = None  # Return type inferred by type checker

@dataclass
class DotCall(Node):
    """Unified node for X.Y(args) - resolved during semantic analysis.

    This node represents all dot-call syntax (receiver.method(args)) before semantic
    analysis determines whether it's an enum constructor or method call:
    - If receiver is an enum type name: EnumConstructor (e.g., Result.Ok(42))
    - If receiver is a variable/expression: MethodCall (e.g., arr.push(5))

    This eliminates the need for AST transformation passes and special cases.
    The type checker resolves the actual meaning based on semantic information.
    """
    receiver: "Expr"    # The receiver expression (variable, type name, etc.)
    method: str         # Method/variant name
    args: List["Expr"]  # Arguments
    inferred_return_type: Optional["Type"] = None  # Return type inferred by type checker
    resolved_enum_type: Optional["Type"] = None  # Resolved concrete enum type (populated by type checker)

@dataclass
class MemberAccess(Node):
    """Member access expression: obj.field"""
    receiver: "Expr"    # The struct expression (p in p.x)
    member: str         # Member name (x, y, etc.)

@dataclass
class StructConstructor(Node):
    """Struct constructor call: Point(10, 20) or Point(x: 10, y: 20)"""
    struct_name: str                     # Name of the struct type
    args: List["Expr"]                   # Constructor arguments (in source order for named, field order after reordering)
    field_names: Optional[List[str]] = None     # Field names for named construction (None for positional)

@dataclass
class EnumConstructor(Node):
    """Enum variant constructor: Option.Some(42) or Color.Red"""
    enum_name: str      # Enum type name (e.g., "Option", "Color")
    variant_name: str   # Variant name (e.g., "Some", "Red")
    args: List["Expr"]  # Arguments for associated data (empty for unit variants)
    enum_name_span: Optional[Span] = None
    variant_name_span: Optional[Span] = None
    resolved_enum_type: Optional["Type"] = None  # Resolved concrete enum type (populated by type checker)

@dataclass
class DynamicArrayNew(Node):
    pass  # Empty constructor new()

@dataclass
class DynamicArrayFrom(Node):
    elements: ArrayLiteral  # from([1, 2, 3]) -> holds the array literal

@dataclass
class CastExpr(Node):
    expr: "Expr"           # The expression being cast
    target_type: Type      # The target type to cast to

@dataclass
class Borrow(Node):
    """Borrow expression: &expr

    Creates a reference (borrow) to a variable without transferring ownership.
    The borrowed variable cannot be moved, rebound, or destroyed while the borrow is active.

    Example: process_array(&my_array)
    """
    expr: "Expr"  # The expression being borrowed (typically a Name)

@dataclass
class TryExpr(Node):
    """Try expression: expr??

    Error propagation operator that unwraps Result<T> or propagates errors.
    If the expression evaluates to Result.Ok(value), returns value.
    If the expression evaluates to Result.Err(), immediately returns Result.Err() from the enclosing function.

    Example: let file f = open("file.txt", FileMode.Read())??
    """
    expr: "Expr"  # The expression being unwrapped (must be Result<T>)

@dataclass
class RangeExpr(Node):
    """Range expression: start..end or start..=end

    Represents an integer range for iteration:
    - start..end: Exclusive upper bound (start <= i < end)
    - start..=end: Inclusive upper bound (start <= i <= end)

    Both start and end are evaluated at runtime, allowing dynamic ranges.
    Direction (ascending/descending) is determined automatically at runtime
    by comparing start and end values.

    Examples:
        0..10       # Yields 0, 1, 2, ..., 9
        0..=10      # Yields 0, 1, 2, ..., 10
        10..0       # Yields 10, 9, 8, ..., 1 (descending)
        5..5        # Empty range (zero iterations)

    Type: Always evaluates to Iterator<i32> for consistency with array iterators.
    Backend: Compiles to optimized for-loop (no iterator struct overhead).
    """
    start: "Expr"           # Start expression (must evaluate to integer)
    end: "Expr"             # End expression (must evaluate to integer)
    inclusive: bool         # True for ..=, False for ..

Expr = Union[Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess, UnaryOp, BinaryOp, Call, MethodCall, DotCall, MemberAccess, StructConstructor, EnumConstructor, DynamicArrayNew, DynamicArrayFrom, CastExpr, Borrow, TryExpr, RangeExpr]

def normalize_bin_op(op_tok_or_str: Token | str) -> BinOp:
    """
    Accepts either a Token (from the parser) or a str (already a lexeme).
    Returns one of: "+","-","*","/","%","==","!=","<","<=",">",">=","and","or","&","|","^","<<",">>".
    Raises if unknown (fail-fast so we don't emit invalid AST).
    """
    # Map both token TYPES and string LEXEMES to canonical op strings
    op_map = {
        # token types (adjust to your lexer names if different)
        "PLUS": "+", "MINUS": "-", "STAR": "*", "SLASH": "/", "MOD": "%",
        "EQEQ": "==", "NEQ": "!=",
        "LT": "<", "LE": "<=", "GT": ">", "GE": ">=",
        "AND": "and", "OR": "or", "XOR": "xor",
        "BIT_AND": "&", "BIT_OR": "|", "BIT_XOR": "^",
        "LSHIFT": "<<", "RSHIFT": ">>",

        # lexemes (when you see raw text in c.value already)
        "+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
        "==": "==", "!=": "!=",
        "<": "<", "<=": "<=", ">": ">", ">=": ">=",
        "and": "and", "or": "or", "xor": "xor",
        "&&": "and", "||": "or", "^^": "xor",
        "&": "&", "|": "|", "^": "^",
        "<<": "<<", ">>": ">>",
    }

    # Figure out a lookup key from the input
    key = getattr(op_tok_or_str, "type", None)
    if key is not None:
        hit = op_map.get(key)
        if hit is not None:
            return hit

    # If it wasn't a token type (or type wasn’t mapped), try its string value
    val = getattr(op_tok_or_str, "value", op_tok_or_str)
    hit = op_map.get(val)
    if hit is not None:
        return hit

    # No match → fail fast
    raise NotImplementedError(f"unknown binary operator: {op_tok_or_str!r}")


__all__ = [
    "Node", "Program", "UseStatement", "FuncDef", "ConstDef", "StructDef", "StructField", "EnumDef", "EnumVariant", "ExtendDef", "Block", "Param",
    "Let", "ExprStmt", "Return", "Print", "PrintLn", "If", "While", "Foreach", "Match", "MatchArm", "Pattern", "WildcardPattern", "Break", "Continue",
    "Name", "IntLit", "FloatLit", "BoolLit", "BlankLit", "StringLit", "InterpolatedString", "ArrayLiteral", "DynamicArrayNew", "DynamicArrayFrom", "IndexAccess", "UnaryOp", "UnOp", "BinaryOp", "BinOp", "Call", "MethodCall", "DotCall", "MemberAccess", "StructConstructor", "EnumConstructor", "CastExpr", "Borrow", "TryExpr", "RangeExpr",
    "Stmt", "Expr", "Rebind", "normalize_bin_op",
]
