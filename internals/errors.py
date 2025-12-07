# internals/errors.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from internals.report import Span, Reporter


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class Category(str, Enum):
    GENERAL   = "general"
    NAME      = "name"
    SCOPE     = "scope"
    FUNC      = "function"
    TYPE      = "type"
    RETURN    = "return"
    UNIT      = "unit"
    RUNTIME   = "runtime"
    INTERNAL  = "internal"


@dataclass(frozen=True)
class ErrorMessage:
    code: str
    severity: Severity
    text: str
    category: Category = Category.GENERAL
    doc: str = ""


REGISTRY: Dict[str, ErrorMessage] = {}

class _ErrorCatalog:
    def __init__(self, backing: Dict[str, ErrorMessage]) -> None:
        self._registry = backing

    def __getattr__(self, name: str) -> ErrorMessage:
        try:
            return self._registry[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __getitem__(self, code: str) -> ErrorMessage:
        return self._registry[code]


ERR = _ErrorCatalog(REGISTRY)

def emit(r: Reporter, em: ErrorMessage, span: Optional[Span], **kwargs) -> None:
    text = _fmt(em.code, **kwargs)
    if em.severity == Severity.ERROR:
        r.error(em.code, text, span)
    else:
        r.warn(em.code, text, span)

def raise_internal_error(code: str, **kwargs) -> None:
    """Raise a RuntimeError for internal compiler errors.

    Internal errors (IE codes) indicate compiler bugs, not user code issues.
    These are raised as Python exceptions during code generation.

    Args:
        code: Error code (e.g., "IE0001")
        **kwargs: Format parameters for the error message

    Raises:
        RuntimeError: Always raises with formatted error message
    """
    msg = _get(code)
    text = _fmt(code, **kwargs)
    raise RuntimeError(f"{code}: {text}")


#
# --- Helpers
#

def _add(msg: ErrorMessage) -> None:
    if msg.code in REGISTRY:
        raise ValueError(f"duplicate error code {REGISTRY[msg.code]} in {msg}")
    REGISTRY[msg.code] = msg

def _get(code: str) -> ErrorMessage:
    try:
        return REGISTRY[code]
    except KeyError:
        raise KeyError(f"unknown error code: {code}")

def _fmt(code: str, **kwargs) -> str:
    msg = _get(code)
    try:
        return msg.text.format(**kwargs)
    except KeyError as key_error:
        missing = key_error.args[0]
        raise KeyError(f"missing text key '{missing}' for {code} "
                       f"(needed by: {msg.text!r})") from None

#
# --- Registry population
#

# Internal errors (compiler bugs) - CE0xxx range
_add(ErrorMessage("CE0001", Severity.ERROR,
    "unknown type node '{node}'",
    Category.INTERNAL, "Found an unexpected type (bug or unsupported feature)."))

_add(ErrorMessage("CE0009", Severity.ERROR,
    "builder not initialized",
    Category.INTERNAL, "IR builder is None - function compilation context required."))

_add(ErrorMessage("CE0010", Severity.ERROR,
    "function context not initialized",
    Category.INTERNAL, "Function context is None - cannot emit code outside function context."))

_add(ErrorMessage("CE0011", Severity.ERROR,
    "entry block not initialized",
    Category.INTERNAL, "Entry block is None - function entry block required."))

_add(ErrorMessage("CE0012", Severity.ERROR,
    "alloca builder not initialized",
    Category.INTERNAL, "Alloca builder is None - function entry block required."))

_add(ErrorMessage("CE0013", Severity.ERROR,
    "runtime function '{name}' not declared",
    Category.INTERNAL, "C library function not declared - runtime initialization required."))

_add(ErrorMessage("CE0014", Severity.ERROR,
    "dynamic array manager not initialized",
    Category.INTERNAL, "Dynamic array manager is None - initialization required."))

_add(ErrorMessage("CE0015", Severity.ERROR,
    "AST invariant violated: {message}",
    Category.INTERNAL, "AST structure constraint violated during code generation."))

_add(ErrorMessage("CE0016", Severity.ERROR,
    "scope stack underflow: attempted to pop from empty scope stack",
    Category.INTERNAL, "Push/pop mismatch - each push_scope() must be paired with exactly one pop_scope()."))

# Type Conversion/Casting (CE0017-CE0022)
_add(ErrorMessage("CE0017", Severity.ERROR,
    "cannot convert value of type '{src}' to '{dst}'",
    Category.INTERNAL, "Type conversion failed during code generation - semantic analysis should prevent this."))

_add(ErrorMessage("CE0018", Severity.ERROR,
    "unknown builtin type: '{type}'",
    Category.INTERNAL, "Encountered unrecognized builtin type during codegen."))

_add(ErrorMessage("CE0019", Severity.ERROR,
    "cannot determine language type for LLVM type: {llvm_type}",
    Category.INTERNAL, "Failed to map LLVM type back to semantic type."))

_add(ErrorMessage("CE0020", Severity.ERROR,
    "unresolved type '{type}' - semantic analysis should have caught this",
    Category.INTERNAL, "Type not found in symbol tables - semantic analysis failure."))

_add(ErrorMessage("CE0021", Severity.ERROR,
    "unsupported type for size calculation: {type}",
    Category.INTERNAL, "Cannot calculate byte size for this type."))

_add(ErrorMessage("CE0022", Severity.ERROR,
    "unsupported type for operation: {type}",
    Category.INTERNAL, "Type cannot be used in this context."))

# Method/Function Validation (CE0023-CE0028)
_add(ErrorMessage("CE0023", Severity.ERROR,
    "method '{method}' expects {expected} argument(s), got {got}",
    Category.INTERNAL, "Method called with wrong number of arguments during codegen."))

_add(ErrorMessage("CE0024", Severity.ERROR,
    "unknown method '{method}' for type '{type}'",
    Category.INTERNAL, "Method not found for this type - semantic analysis should prevent this."))

_add(ErrorMessage("CE0025", Severity.ERROR,
    "extension method '{name}' not declared",
    Category.INTERNAL, "Extension method reference not found in symbol table."))

_add(ErrorMessage("CE0026", Severity.ERROR,
    "function expects {expected} arguments, got {got}",
    Category.INTERNAL, "Function parameter count mismatch during codegen."))

_add(ErrorMessage("CE0027", Severity.ERROR,
    "callee must be a Name, got {type}",
    Category.INTERNAL, "Function call expression has invalid callee type - AST invariant violation."))

_add(ErrorMessage("CE0028", Severity.ERROR,
    "stdlib method not implemented: {method}",
    Category.INTERNAL, "Standard library method missing backend implementation."))

# Struct Operations (CE0029-CE0032)
_add(ErrorMessage("CE0029", Severity.ERROR,
    "struct '{struct}' has no field '{field}'",
    Category.INTERNAL, "Struct field lookup failed - semantic analysis should prevent this."))

_add(ErrorMessage("CE0030", Severity.ERROR,
    "cannot get address of struct for member access",
    Category.INTERNAL, "Failed to retrieve struct pointer for field access."))

_add(ErrorMessage("CE0031", Severity.ERROR,
    "member access on non-struct type: {type}",
    Category.INTERNAL, "Attempted field access on non-struct type - semantic analysis failure."))

_add(ErrorMessage("CE0032", Severity.ERROR,
    "expected StructType, got {type}",
    Category.INTERNAL, "Type mismatch: expected struct type but got different type."))

# Enum Operations (CE0033-CE0040)
_add(ErrorMessage("CE0033", Severity.ERROR,
    "unknown enum type: {name}",
    Category.INTERNAL, "Enum type not found in symbol table during codegen."))

_add(ErrorMessage("CE0034", Severity.ERROR,
    "variant '{variant}' not found in enum '{enum}'",
    Category.INTERNAL, "Enum variant lookup failed - semantic analysis should prevent this."))

_add(ErrorMessage("CE0035", Severity.ERROR,
    "enum '{enum}' missing required variant '{variant}'",
    Category.INTERNAL, "Expected enum variant not found in enum definition."))

_add(ErrorMessage("CE0036", Severity.ERROR,
    "enum variant '{variant}' has wrong number of associated types: expected {expected}, got {got}",
    Category.INTERNAL, "Enum variant associated type count mismatch - monomorphization failure."))

_add(ErrorMessage("CE0037", Severity.ERROR,
    "malformed enum '{enum}': {reason}",
    Category.INTERNAL, "Enum structure violates expected invariants."))

_add(ErrorMessage("CE0038", Severity.ERROR,
    "variable '{var}' is not an enum type",
    Category.INTERNAL, "Expected enum type but got different type - semantic analysis failure."))

_add(ErrorMessage("CE0039", Severity.ERROR,
    "try operator requires Result-like or Maybe-like enum, got {type}",
    Category.INTERNAL, "Try operator applied to non-result type - semantic analysis should prevent this."))

_add(ErrorMessage("CE0040", Severity.ERROR,
    "cannot construct {variant} variant for return type: {type}",
    Category.INTERNAL, "Failed to construct enum variant for error propagation."))

# Array Operations (CE0041-CE0044)
_add(ErrorMessage("CE0041", Severity.ERROR,
    "expected ArrayType, got {type}",
    Category.INTERNAL, "Type mismatch: expected fixed-size array but got different type."))

_add(ErrorMessage("CE0042", Severity.ERROR,
    "expected DynamicArrayType, got {type}",
    Category.INTERNAL, "Type mismatch: expected dynamic array but got different type."))

_add(ErrorMessage("CE0043", Severity.ERROR,
    "array.get() returned non-struct type: {type}",
    Category.INTERNAL, "Array element type is not a struct as expected."))

_add(ErrorMessage("CE0044", Severity.ERROR,
    "nested member access on non-struct field type: {type}",
    Category.INTERNAL, "Attempted nested field access on non-struct type."))

# Generic Type Resolution (CE0045-CE0050)
_add(ErrorMessage("CE0045", Severity.ERROR,
    "generic type '{type}' not found in enum or struct table - monomorphization may have failed",
    Category.INTERNAL, "Generic type instantiation not found - monomorphization failure."))

_add(ErrorMessage("CE0046", Severity.ERROR,
    "Result<{type}> not found in enum table - monomorphization failed",
    Category.INTERNAL, "Result type instantiation missing - monomorphization failure."))

_add(ErrorMessage("CE0047", Severity.ERROR,
    "failed to create Maybe<{type}> enum type",
    Category.INTERNAL, "Maybe type instantiation failed during codegen."))

_add(ErrorMessage("CE0048", Severity.ERROR,
    "failed to create Maybe<{type}>. Available: {available}",
    Category.INTERNAL, "Maybe type instantiation failed - debugging info shows available types."))

_add(ErrorMessage("CE0049", Severity.ERROR,
    "invalid {generic} type name: {name}",
    Category.INTERNAL, "Generic type name does not match expected pattern."))

_add(ErrorMessage("CE0050", Severity.ERROR,
    "{generic} should have exactly {expected} type parameters, got {got}",
    Category.INTERNAL, "Generic type parameter count mismatch."))

# Hash Method Validation (CE0051-CE0054)
_add(ErrorMessage("CE0051", Severity.ERROR,
    "no hash method for type: {type}",
    Category.INTERNAL, "Type does not have a hash() method - required for HashMap keys."))

_add(ErrorMessage("CE0052", Severity.ERROR,
    "cannot hash value of type: {type}",
    Category.INTERNAL, "Type is not hashable - semantic analysis should prevent this."))

_add(ErrorMessage("CE0053", Severity.ERROR,
    "type '{type}' does not have a hash() method",
    Category.INTERNAL, "Hash method not found for type - required for HashMap operations."))

_add(ErrorMessage("CE0054", Severity.ERROR,
    "hash() expects 0 arguments, got {got}",
    Category.INTERNAL, "Hash method called with incorrect number of arguments."))

# Variable/Constant Lookup (CE0055-CE0058)
_add(ErrorMessage("CE0055", Severity.ERROR,
    "unknown variable or constant: {name}",
    Category.INTERNAL, "Variable/constant not found in symbol table during codegen."))

_add(ErrorMessage("CE0056", Severity.ERROR,
    "cannot determine type for variable: {name}",
    Category.INTERNAL, "Variable type information missing during codegen."))

_add(ErrorMessage("CE0057", Severity.ERROR,
    "dynamic array '{name}' not declared",
    Category.INTERNAL, "Dynamic array not found in tracking state."))

_add(ErrorMessage("CE0058", Severity.ERROR,
    "dynamic array '{name}' already destroyed",
    Category.INTERNAL, "Attempted to use dynamic array after explicit destruction."))

# Builder/Context State (CE0059-CE0063)
_add(ErrorMessage("CE0059", Severity.ERROR,
    "attempt to emit into a terminated block",
    Category.INTERNAL, "Cannot add instructions to basic block that already has a terminator."))

_add(ErrorMessage("CE0060", Severity.ERROR,
    "attempt to emit statement after a terminator",
    Category.INTERNAL, "Cannot emit code after return/branch - control flow analysis failure."))

_add(ErrorMessage("CE0061", Severity.ERROR,
    "block has no 'statements' list",
    Category.INTERNAL, "AST block node missing expected statements field."))

_add(ErrorMessage("CE0062", Severity.ERROR,
    "invalid cleanup_type: {type}",
    Category.INTERNAL, "Unknown cleanup type in destructor emission."))

_add(ErrorMessage("CE0063", Severity.ERROR,
    "cannot infer return type for method: {method}",
    Category.INTERNAL, "Method return type inference failed during codegen."))

# Main Function Validation (CE0064-CE0066)
_add(ErrorMessage("CE0064", Severity.ERROR,
    "C-style main function not found",
    Category.INTERNAL, "Generated C main wrapper not found in module."))

_add(ErrorMessage("CE0065", Severity.ERROR,
    "args parameter not found in main function",
    Category.INTERNAL, "Main function parameter structure invalid."))

_add(ErrorMessage("CE0066", Severity.ERROR,
    "main function validation failed: {reason}",
    Category.INTERNAL, "Main function does not meet requirements."))

# Type Inference (CE0067-CE0070)
_add(ErrorMessage("CE0067", Severity.ERROR,
    "cannot infer struct type from expression: {expr}",
    Category.INTERNAL, "Struct type inference failed for expression."))

_add(ErrorMessage("CE0068", Severity.ERROR,
    "cannot infer struct type from method call: {method}",
    Category.INTERNAL, "Struct type inference failed for method call."))

_add(ErrorMessage("CE0069", Severity.ERROR,
    "cannot infer struct type from DotCall: {method}",
    Category.INTERNAL, "Struct type inference failed for dot-call expression."))

_add(ErrorMessage("CE0070", Severity.ERROR,
    "cannot determine type for expression: {expr}",
    Category.INTERNAL, "Expression type inference failed during codegen."))

# Iterator Operations (CE0071-CE0072)
_add(ErrorMessage("CE0071", Severity.ERROR,
    "iter() expects 0 arguments, got {got}",
    Category.INTERNAL, "Iterator method called with incorrect number of arguments."))

_add(ErrorMessage("CE0072", Severity.ERROR,
    "unsupported iterator operation: {operation}",
    Category.INTERNAL, "Iterator operation not implemented for this type."))

# Primitive Type Operations (CE0073-CE0076)
_add(ErrorMessage("CE0073", Severity.ERROR,
    "unknown primitive type: {type}",
    Category.INTERNAL, "Primitive type not recognized during codegen."))

_add(ErrorMessage("CE0074", Severity.ERROR,
    "unknown builtin primitive method: {type}.{method}",
    Category.INTERNAL, "Primitive type method not implemented."))

_add(ErrorMessage("CE0075", Severity.ERROR,
    "unknown conversion kind: {kind}",
    Category.INTERNAL, "Type conversion operation not recognized."))

_add(ErrorMessage("CE0076", Severity.ERROR,
    "unsupported primitive type for operation: {type}",
    Category.INTERNAL, "Primitive type cannot be used in this operation."))

# String/Formatting Operations (CE0077-CE0078)
_add(ErrorMessage("CE0077", Severity.ERROR,
    "unknown stdlib string method: {method}",
    Category.INTERNAL, "Standard library string method not implemented."))

_add(ErrorMessage("CE0078", Severity.ERROR,
    "to_str() expects 0 arguments, got {got}",
    Category.INTERNAL, "String conversion method called with incorrect arguments."))

# Memory/Element Size (CE0079)
_add(ErrorMessage("CE0079", Severity.ERROR,
    "unsupported element type for size calculation: {type}",
    Category.INTERNAL, "Cannot calculate element size for this type."))

# Own<T> Operations (CE0080-CE0082)
_add(ErrorMessage("CE0080", Severity.ERROR,
    "unknown Own<T> method: {method}",
    Category.INTERNAL, "Own<T> method not implemented."))

_add(ErrorMessage("CE0081", Severity.ERROR,
    "Own<T> field 'value' has unexpected type: {type}",
    Category.INTERNAL, "Own<T> internal structure does not match expected layout."))

_add(ErrorMessage("CE0082", Severity.ERROR,
    "Own<T> operation failed: {reason}",
    Category.INTERNAL, "Own<T> operation encountered unexpected condition."))

# List<T> Operations (CE0083-CE0084)
_add(ErrorMessage("CE0083", Severity.ERROR,
    "unknown List<T> method: {method}",
    Category.INTERNAL, "List<T> method not implemented."))

_add(ErrorMessage("CE0084", Severity.ERROR,
    "List<T>.{method}() expects {expected} argument(s), got {got}",
    Category.INTERNAL, "List method called with incorrect number of arguments."))

# HashMap<K,V> Operations (CE0085-CE0088)
_add(ErrorMessage("CE0085", Severity.ERROR,
    "unknown HashMap<K, V> method: {method}",
    Category.INTERNAL, "HashMap method not implemented."))

_add(ErrorMessage("CE0086", Severity.ERROR,
    "HashMap operation failed: {reason}",
    Category.INTERNAL, "HashMap operation encountered unexpected condition."))

_add(ErrorMessage("CE0087", Severity.ERROR,
    "expected HashMap<K, V> type, got {type}",
    Category.INTERNAL, "Type mismatch: expected HashMap but got different type."))

_add(ErrorMessage("CE0088", Severity.ERROR,
    "HashMap type parsing failed: {reason}",
    Category.INTERNAL, "Failed to parse HashMap type signature."))

# Result<T> Operations (CE0089-CE0091)
_add(ErrorMessage("CE0089", Severity.ERROR,
    "Result enum missing Ok variant: {enum}",
    Category.INTERNAL, "Result-like enum does not have Ok variant."))

_add(ErrorMessage("CE0090", Severity.ERROR,
    "Result.Ok variant should have 1 associated type, got {got}",
    Category.INTERNAL, "Result.Ok variant has incorrect number of associated types."))

_add(ErrorMessage("CE0091", Severity.ERROR,
    "Result type not found: {type}",
    Category.INTERNAL, "Result enum type not found in symbol table."))

# Maybe<T> Operations (CE0092-CE0095)
_add(ErrorMessage("CE0092", Severity.ERROR,
    "Maybe enum missing Some variant: {enum}",
    Category.INTERNAL, "Maybe-like enum does not have Some variant."))

_add(ErrorMessage("CE0093", Severity.ERROR,
    "Maybe.Some variant should have 1 associated type, got {got}",
    Category.INTERNAL, "Maybe.Some variant has incorrect number of associated types."))

_add(ErrorMessage("CE0094", Severity.ERROR,
    "unknown Maybe<T> method: {method}",
    Category.INTERNAL, "Maybe<T> method not implemented."))

_add(ErrorMessage("CE0095", Severity.ERROR,
    "expect() expects 1 argument, got {got}",
    Category.INTERNAL, "Maybe.expect() method called with incorrect arguments."))

# Intrinsic/Builtin Operations (CE0096-CE0098)
_add(ErrorMessage("CE0096", Severity.ERROR,
    "invalid intrinsic operation: {operation}",
    Category.INTERNAL, "Intrinsic operation not recognized."))

_add(ErrorMessage("CE0097", Severity.ERROR,
    "intrinsic validation failed: {reason}",
    Category.INTERNAL, "Intrinsic operation validation failed."))

_add(ErrorMessage("CE0098", Severity.ERROR,
    "unsupported intrinsic type: {type}",
    Category.INTERNAL, "Type not supported for intrinsic operation."))

# Expression Type Validation (CE0099-CE0100)
_add(ErrorMessage("CE0099", Severity.ERROR,
    "not an operator type: {type}",
    Category.INTERNAL, "Expression node is not a valid operator - AST invariant violation."))

_add(ErrorMessage("CE0100", Severity.ERROR,
    "unsupported expression type: {expr}",
    Category.INTERNAL, "Expression type not supported in this context."))

# Function/header errors
_add(ErrorMessage("CE0101", Severity.ERROR,
    "duplicate function '{name}' (first defined at {prev_loc})",
    Category.FUNC, "Two functions share the same name in a compilation unit."))

_add(ErrorMessage("CE0102", Severity.ERROR,
    "duplicate parameter '{name}'",
    Category.FUNC, "A function declares the same parameter name more than once."))

_add(ErrorMessage("CE0103", Severity.ERROR,
    "missing return type for function '{name}'",
    Category.FUNC, "Function header must specify an explicit return type."))

_add(ErrorMessage("CE0104", Severity.ERROR,
    "missing type annotation for constant '{name}'",
    Category.FUNC, "Constant declarations must specify an explicit type."))

_add(ErrorMessage("CE0105", Severity.ERROR,
    "duplicate constant '{name}' (first defined at {prev_loc})",
    Category.FUNC, "Two constants share the same name in a compilation unit."))

_add(ErrorMessage("CE0106", Severity.ERROR,
    "main() function must return an integer type (i8-i64, u8-u64), got '{type}'",
    Category.FUNC, "The main function must return an integer type to be used as a shell exit code."))

_add(ErrorMessage("CE0107", Severity.ERROR,
    "function '{name}' must return a value on all code paths",
    Category.FUNC, "All functions with a return type must have a return statement."))

# Constant expression evaluation errors
_add(ErrorMessage("CE0108", Severity.ERROR,
    "expression is not a compile-time constant (type: {expr_type})",
    Category.FUNC, "Constant declarations must use compile-time evaluable expressions. Function calls, method calls, and variable references are not allowed."))

_add(ErrorMessage("CE0109", Severity.ERROR,
    "circular constant dependency detected: {chain}",
    Category.FUNC, "Constants cannot depend on themselves directly or indirectly."))

_add(ErrorMessage("CE0110", Severity.ERROR,
    "unsupported operation '{op}' in constant expression",
    Category.FUNC, "This operation cannot be evaluated at compile-time for constants."))

_add(ErrorMessage("CE0111", Severity.ERROR,
    "invalid type cast in constant expression from {from_type} to {to_type}",
    Category.FUNC, "Type cast is not allowed in constant expressions."))

_add(ErrorMessage("CE0112", Severity.ERROR,
    "division by zero in constant expression",
    Category.FUNC, "Constant expressions cannot divide by zero."))

_add(ErrorMessage("CE0113", Severity.ERROR,
    "{message}",
    Category.INTERNAL, "Generic enum constructor requires type annotation from semantic analysis."))

# Struct errors
_add(ErrorMessage("CE0004", Severity.ERROR,
    "duplicate struct '{name}' (first defined at {prev_loc})",
    Category.TYPE, "Two structs share the same name in a compilation unit."))

_add(ErrorMessage("CE0005", Severity.ERROR,
    "duplicate field '{name}' in struct '{struct_name}'",
    Category.TYPE, "A struct declares the same field name more than once."))

_add(ErrorMessage("CE0006", Severity.ERROR,
    "enum '{name}' already defined as struct '{prev_loc}'",
    Category.TYPE, "An enum is declared with the same name as a struct."))

# Scope errors
_add(ErrorMessage("CE1001", Severity.ERROR,
    "use of undeclared identifier '{name}'",
    Category.SCOPE, "The identifier was used before it was declared or is not in scope."
))

_add(ErrorMessage("CE1002", Severity.ERROR,
    "rebind to undeclared variable '{name}'",
    Category.SCOPE, "Use 'let' to declare a variable before using ':=' to rebind it."))

_add(ErrorMessage("CE1003", Severity.ERROR,
    "not allowed here (must be inside a loop).",
    Category.SCOPE, "Emitted when 'break' or 'continue' appear outside any loop."))

_add(ErrorMessage("CE1004", Severity.ERROR,
    "variable {name} shadows the loop condition.",
    Category.SCOPE, "Emitted when declaring let name inside a loop body when name is read in the loop condition."))

# Type Errors
_add(ErrorMessage("CE2001", Severity.ERROR,
    "unknown type '{name}'",
    Category.TYPE, "A declared type is not recognized by the compiler."))

_add(ErrorMessage("CE2002", Severity.ERROR,
    "type mismatch: cannot assign {got} to {expected}",
    Category.TYPE, "The right-hand side expression type does not match the declared or inferred left-hand side type."))

_add(ErrorMessage("CE2003", Severity.ERROR,
    "return type mismatch: got {got}, expected {expected}",
    Category.TYPE, "A function's return expression type does not match its declared return type."))

_add(ErrorMessage("CE2004", Severity.ERROR,
    "invalid operand types for operator '{op}'",
    Category.TYPE, "An operator was applied to operands of incompatible types."))

_add(ErrorMessage("CE2005", Severity.ERROR,
    "condition must be bool",
    Category.TYPE, "If/elif/while conditions require a 'bool' expression; implicit int→bool coercions are not allowed."))

_add(ErrorMessage("CE2006", Severity.ERROR,
    "argument type mismatch at position {index}: expected {expected}, got {got}",
    Category.TYPE, "A function call argument type does not match the corresponding parameter type."))

_add(ErrorMessage("CE2007", Severity.ERROR,
    "missing type annotation for variable '{name}'",
    Category.TYPE, "Variable declaration with 'let' requires an explicit type annotation."))

_add(ErrorMessage("CE2008", Severity.ERROR,
    "undefined function '{name}'",
    Category.TYPE, "Function call references a function that was not declared."))

_add(ErrorMessage("CE2009", Severity.ERROR,
    "function '{name}' expects {expected} arguments, got {got}",
    Category.TYPE, "Function call has wrong number of arguments."))

# Array-specific errors
_add(ErrorMessage("CE2010", Severity.ERROR,
    "array size must be a positive integer literal, got {size}",
    Category.TYPE, "Array type declaration requires a positive integer literal for size."))

_add(ErrorMessage("CE2011", Severity.ERROR,
    "array literal has {got} elements but declared type expects {expected}",
    Category.TYPE, "Array literal element count must match declared array size."))

_add(ErrorMessage("CE2012", Severity.ERROR,
    "array index {index} is out of bounds for array of size {size}",
    Category.TYPE, "Array access with compile-time constant index exceeds array bounds."))

_add(ErrorMessage("CE2013", Severity.ERROR,
    "array element type mismatch: expected {expected}, got {got}",
    Category.TYPE, "Array literal element type does not match declared array element type."))

_add(ErrorMessage("CE2014", Severity.ERROR,
    "invalid cast from '{source}' to '{target}'",
    Category.TYPE, "Type cast is not allowed between the specified types."))

_add(ErrorMessage("CE2015", Severity.ERROR,
    "constant '{name}' cannot use dynamic array type",
    Category.TYPE, "Constants must use compile-time types. Dynamic arrays are not allowed."))

# Dynamic array-specific errors (compile-time only)

_add(ErrorMessage("CE2022", Severity.ERROR,
    "invalid dynamic array element type '{type}'",
    Category.TYPE, "Dynamic arrays can only hold supported element types (int, bool, string)."))

_add(ErrorMessage("CE2023", Severity.ERROR,
    "dynamic array method argument mismatch for '{method}': expected {expected}, got {got}",
    Category.TYPE, "Dynamic array method called with incorrect argument types."))

_add(ErrorMessage("CE2024", Severity.ERROR,
    "use of destroyed dynamic array '{name}'",
    Category.TYPE, "Attempted to use a dynamic array after it was explicitly destroyed."))

_add(ErrorMessage("CE2026", Severity.ERROR,
    "unterminated interpolation in string literal",
    Category.TYPE, "String interpolation braces must be properly closed with '}'."))

# Struct errors
_add(ErrorMessage("CE2027", Severity.ERROR,
    "struct '{name}' expects {expected} field(s), got {got}",
    Category.TYPE, "Struct constructor must provide exact number of fields."))

_add(ErrorMessage("CE2028", Severity.ERROR,
    "field '{field_name}' expects type '{expected}', got '{got}'",
    Category.TYPE, "Struct constructor field type mismatch."))

# Result type errors
_add(ErrorMessage("CE2030", Severity.ERROR,
    "return statement must use Ok() or Err()",
    Category.TYPE, "All return statements must explicitly wrap values in Ok() or use Err()."))

_add(ErrorMessage("CE2031", Severity.ERROR,
    "Ok() value type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The value inside Ok() must match the function's return type."))

_add(ErrorMessage("CE2032", Severity.ERROR,
    "blank type (~) can only be used as function return type",
    Category.TYPE, "Blank type cannot be used for variables, parameters, or constants."))

_add(ErrorMessage("CE2039", Severity.ERROR,
    "Err() error type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The error value inside Err() must match the function's error type."))

_add(ErrorMessage("CE2033", Severity.ERROR,
    "foreach requires an iterator, got '{got}'",
    Category.TYPE, "The expression in foreach must be an iterator (e.g., from calling .iter() on an array)."))

_add(ErrorMessage("CE2034", Severity.ERROR,
    "foreach item type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The declared item type in foreach does not match the iterator's element type."))

_add(ErrorMessage("CE2035", Severity.ERROR,
    "cannot interpolate expression of type '{type}' into string",
    Category.TYPE, "String interpolation only supports: integers, floats, booleans, and strings."))

_add(ErrorMessage("CE2036", Severity.ERROR,
    "Ok() requires a value. For blank return type use Ok(~)",
    Category.TYPE, "Empty Ok() is not allowed. Use Ok(value) for regular returns or Ok(~) for blank type returns."))

_add(ErrorMessage("CE2037", Severity.ERROR,
    "cannot print Result<T> directly (use .realise() to unwrap first)",
    Category.TYPE, "Result<T> must be explicitly handled before printing. Use .realise(default) to extract the value."))

_add(ErrorMessage("CE2038", Severity.ERROR,
    "empty interpolation in string literal",
    Category.TYPE, "String interpolation braces must contain an expression (e.g., \"{value}\" not \"{}\")."))

# Result<T> method errors (CE25xx)
_add(ErrorMessage("CE2502", Severity.ERROR,
    "realise() requires exactly 1 argument, got {got}",
    Category.TYPE, "The realise() method on Result<T> must be called with exactly one default value argument."))

_add(ErrorMessage("CE2503", Severity.ERROR,
    "realise() default type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The default value type passed to realise() must match the T type in Result<T>."))

_add(ErrorMessage("CW2001", Severity.WARNING,
    "unused Result<T> value (use .realise() or if statement to handle the result)",
    Category.TYPE, "Result<T> values should be explicitly handled to avoid losing error information."))

_add(ErrorMessage("CE2505", Severity.ERROR,
    "cannot assign Result<T> to non-Result variable without handling (use .realise() or pattern matching)",
    Category.TYPE, "Result<T> values must be explicitly handled before assigning to non-Result variables."))

_add(ErrorMessage("CE2506", Severity.ERROR,
    "cannot call .realise() on Result<~> (blank type has no value to extract)",
    Category.TYPE, "Blank functions return Result<~> which has no meaningful value. Use if statement to check success/failure instead."))

# Try operator (??) errors (CE25xx continued)
_add(ErrorMessage("CE2507", Severity.ERROR,
    "?? operator requires Result<T>, Maybe<T>, or result-like enum (with Ok/Err or Some/None variants), got '{got}'",
    Category.TYPE, "The ?? operator requires an enum with Ok/Err variants (e.g., Result<T>, FileResult) or Some/None variants (e.g., Maybe<T>)."))

_add(ErrorMessage("CE2508", Severity.ERROR,
    "?? operator can only be used in functions returning a result-like enum (with Ok/Err variants)",
    Category.TYPE, "The ?? operator propagates errors by early return, so it requires the enclosing function to return a result-like enum (e.g., Result<T>, FileResult). Note: Maybe<T> can be used with ??, but it propagates as Result.Err()."))

_add(ErrorMessage("CE2509", Severity.ERROR,
    "operator '+' cannot be used with string types (use string interpolation instead: \"text {{variable}}\")",
    Category.TYPE, "Sushi does not support string concatenation with the + operator. Use string interpolation for combining strings."))

_add(ErrorMessage("CE2510", Severity.ERROR,
    "cannot use operator with mixed numeric types: {left_type} and {right_type} (use 'as' to explicitly cast one operand)",
    Category.TYPE, "Sushi does not allow implicit numeric type conversions in comparisons or arithmetic operations. Use the 'as' keyword to explicitly cast one operand to match the other's type. Example: if (x == 3.14 as f32) { ... }"))

_add(ErrorMessage("CE2511", Severity.ERROR,
    "error type mismatch in propagation: cannot propagate Result<{ok_type}, {inner_err}> to function returning Result<{ok_type}, {outer_err}>",
    Category.TYPE, "The ?? operator requires error types to match exactly. Inner function returns Result<T, {inner_err}> but outer function returns Result<T, {outer_err}>. Error type conversion is not supported yet."))

_add(ErrorMessage("CW2511", Severity.WARNING,
    "?? operator used in main function (consider explicit error handling for clarity)",
    Category.TYPE, "While ?? works in main, explicit error handling with .realise(), if statements, or match expressions makes error behavior clearer at the program entry point."))

# Borrow/reference errors (CE24xx)
_add(ErrorMessage("CE2400", Severity.ERROR,
    "cannot borrow '{name}': variable does not exist",
    Category.TYPE, "Attempted to borrow a variable that was not declared."))

_add(ErrorMessage("CE2401", Severity.ERROR,
    "cannot move/rebind '{name}' while it is borrowed",
    Category.TYPE, "A variable cannot be moved or rebound while a reference to it is active."))

_add(ErrorMessage("CE2402", Severity.ERROR,
    "cannot destroy '{name}' while it is borrowed",
    Category.TYPE, "A variable cannot be explicitly destroyed (.destroy()) while a reference to it is active."))

_add(ErrorMessage("CE2403", Severity.ERROR,
    "'{name}' already has an active &poke borrow (only one exclusive borrow allowed)",
    Category.TYPE, "A variable can only have one active &poke (read-write) borrow at a time to prevent aliasing issues."))

_add(ErrorMessage("CE2404", Severity.ERROR,
    "cannot borrow '{expr}': expression has no stable address",
    Category.TYPE, "The borrow operator (&) can only be applied to variables and struct member access (e.g., &x, &obj.field), not temporary values or function call results."))

_add(ErrorMessage("CE2405", Severity.ERROR,
    "cannot borrow moved variable '{name}'",
    Category.TYPE, "Attempted to borrow a variable whose ownership has been transferred elsewhere."))

_add(ErrorMessage("CE2406", Severity.ERROR,
    "use of destroyed variable '{name}'",
    Category.TYPE, "Variable was explicitly destroyed via .destroy() and is no longer valid."))

_add(ErrorMessage("CE2407", Severity.ERROR,
    "cannot have &peek and &poke borrows of '{name}' simultaneously",
    Category.TYPE, "A variable cannot have both read-only (&peek) and read-write (&poke) borrows at the same time."))

_add(ErrorMessage("CE2408", Severity.ERROR,
    "cannot modify '{name}' through &peek reference (read-only)",
    Category.TYPE, "&peek references are read-only. Use &poke for mutable access."))

_add(ErrorMessage("CW2409", Severity.WARNING,
    "re-borrowing '{name}' as &poke (nested mutable borrow)",
    Category.TYPE, "Creating a &poke borrow of a &poke reference parameter passes through exclusive access. Ensure the original reference is not used until the nested borrow ends."))

# Enum errors
_add(ErrorMessage("CE2040", Severity.ERROR,
    "non-exhaustive match pattern (missing variants: {variants})",
    Category.TYPE, "Match statement must handle all enum variants."))

_add(ErrorMessage("CE2041", Severity.ERROR,
    "duplicate match arm for variant '{variant}'",
    Category.TYPE, "The same enum variant cannot be matched more than once."))

_add(ErrorMessage("CE2042", Severity.ERROR,
    "unreachable match arm",
    Category.TYPE, "This match arm can never be reached because previous arms cover all cases."))

_add(ErrorMessage("CE2043", Severity.ERROR,
    "pattern type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "Pattern binding type does not match the expected type from the enum variant."))

_add(ErrorMessage("CE2044", Severity.ERROR,
    "wrong number of pattern bindings: variant '{variant}' expects {expected}, got {got}",
    Category.TYPE, "Pattern must bind the exact number of variables for the variant's associated data."))

_add(ErrorMessage("CE2045", Severity.ERROR,
    "enum variant '{variant}' not found in enum '{enum}'",
    Category.TYPE, "The specified variant does not exist in the enum type."))

_add(ErrorMessage("CE2046", Severity.ERROR,
    "duplicate enum '{name}' (first defined at {prev_loc})",
    Category.TYPE, "Two enums share the same name in a compilation unit."))

_add(ErrorMessage("CE2047", Severity.ERROR,
    "duplicate variant '{name}' in enum '{enum_name}'",
    Category.TYPE, "An enum declares the same variant name more than once."))

_add(ErrorMessage("CE2048", Severity.ERROR,
    "match scrutinee must be an enum type, got '{got}'",
    Category.TYPE, "Match expressions can only be used with enum types."))

_add(ErrorMessage("CE2049", Severity.ERROR,
    "enum constructor argument type mismatch for variant '{variant}': expected '{expected}', got '{got}'",
    Category.TYPE, "Enum variant constructor argument type does not match the expected associated data type."))

_add(ErrorMessage("CE2050", Severity.ERROR,
    "enum variant '{variant}' expects {expected} argument(s), got {got}",
    Category.TYPE, "Enum variant constructor must provide exact number of arguments for associated data."))

_add(ErrorMessage("CE2051", Severity.ERROR,
    "{message}",
    Category.TYPE, "Struct hashing limitation or error."))

_add(ErrorMessage("CE2052", Severity.ERROR,
    "recursive enum '{name}' requires Own<T> indirection (example: enum IntList: Cons(i32, Own<IntList>))",
    Category.TYPE, "Direct recursion in enums without Own<T> creates infinite size types."))

# List<T> method errors
_add(ErrorMessage("CE2053", Severity.ERROR,
    "List<T>.{method}() expects {expected} argument(s), got {got}",
    Category.TYPE, "List method called with incorrect number of arguments."))

# HashMap<K, V> type errors
_add(ErrorMessage("CE2054", Severity.ERROR,
    "HashMap<K, V> key type '{key_type}' does not support hashing (missing .hash() method)",
    Category.TYPE, "HashMap keys must support hashing. Use types that have .hash() method (primitives, strings, structs with hashable fields, enums, arrays)."))

_add(ErrorMessage("CE2055", Severity.ERROR,
    "HashMap<K, V> key type '{key_type}' does not support equality comparison",
    Category.TYPE, "HashMap keys must support equality comparison (==). This is required for collision resolution."))

_add(ErrorMessage("CE2058", Severity.ERROR,
    "HashMap<K, V> key type '{key_type}' is not comparable (dynamic arrays cannot be HashMap keys)",
    Category.TYPE, "Dynamic arrays are not allowed as HashMap keys due to memory management constraints. Use fixed-size arrays instead (e.g., i32[3] instead of i32[])."))

# Array indexing errors (CE2056-CE2057, CE2059)
_add(ErrorMessage("CE2056", Severity.ERROR,
    "array index {index} is negative (indices must be >= 0)",
    Category.TYPE, "Array indices must be non-negative. Negative indices are not supported."))

_add(ErrorMessage("CE2057", Severity.ERROR,
    "array index {index} out of bounds for array of size {size}",
    Category.TYPE, "Array index exceeds array bounds. This error is caught at compile-time for constant indices."))

_add(ErrorMessage("CE2059", Severity.ERROR,
    "enum variant '{variant}' cannot have dynamic array field '{field_type}'",
    Category.TYPE, "Dynamic arrays in enum variants cause memory management issues. Use fixed-size arrays instead (e.g., i32[3] instead of i32[])."))

# Generic function call errors (CE2060-CE2069)
_add(ErrorMessage("CE2060", Severity.ERROR,
    "cannot infer type arguments for generic function '{name}': {reason}",
    Category.TYPE, "Type inference failed for generic function call. Type parameters could not be determined from argument types."))

_add(ErrorMessage("CE2061", Severity.ERROR,
    "monomorphized function '{mangled}' not found for '{name}' with type arguments {type_args}",
    Category.INTERNAL, "Internal compiler error: monomorphized function missing from function table."))

# Radix literal errors (CE2070-CE2079)
_add(ErrorMessage("CE2070", Severity.ERROR,
    "{radix} literal {literal} overflows {type}",
    Category.TYPE, "The literal value is too large to fit in the target integer type. Use a wider integer type or reduce the value."))

_add(ErrorMessage("CE2071", Severity.ERROR,
    "C-style octal literal '{literal}' is not supported. Use '0o' prefix instead (e.g., 0o{octal})",
    Category.TYPE, "Leading zero octals (like 077) are ambiguous and error-prone. Use explicit 0o prefix instead."))

_add(ErrorMessage("CE2072", Severity.ERROR,
    "range expression requires integer types for start and end bounds. Got {got}, expected {expected}",
    Category.TYPE, "Range expressions (.. and ..=) can only be used with integer types (i8, i16, i32, i64, u8, u16, u32, u64)."))

# Named struct constructor errors (CE2080-CE2089)
_add(ErrorMessage("CE2080", Severity.ERROR,
    "unknown field '{field}' for struct '{struct}'",
    Category.TYPE, "Named struct constructor field name does not exist in struct definition."))

_add(ErrorMessage("CE2081", Severity.ERROR,
    "duplicate field '{field}' in struct constructor",
    Category.TYPE, "Field name appears more than once in named struct constructor."))

_add(ErrorMessage("CE2082", Severity.ERROR,
    "missing required field(s) '{fields}' for struct '{struct}'",
    Category.TYPE, "Named struct constructor must provide all required fields."))

_add(ErrorMessage("CE2083", Severity.ERROR,
    "field '{field}' expects type '{expected}', got '{got}'",
    Category.TYPE, "Named struct constructor field type mismatch."))

_add(ErrorMessage("CE2084", Severity.ERROR,
    "error type must be an enum, not '{type_name}'",
    Category.TYPE, "Custom error types (fn foo() T | E) must be enums. Structs and primitives are not allowed as error types."))

_add(ErrorMessage("CE2085", Severity.ERROR,
    "cannot use '| {err_type}' syntax with explicit Result<T, E> return type",
    Category.TYPE, "When using explicit Result<T, E> syntax, the error type is already specified. Remove the '| ErrorType' syntax or use implicit return type."))

# CE2086-CE2089 reserved for future extensions

# Unit Management Errors (CE3xxx)
_add(ErrorMessage("CE3001", Severity.ERROR,
    "circular dependency detected: {cycle}",
    Category.SCOPE, "Units have circular dependencies that prevent compilation ordering."))

_add(ErrorMessage("CE3002", Severity.ERROR,
    "unit '{name}' not found (expected: {path})",
    Category.SCOPE, "A required unit file could not be found at the expected location."))

_add(ErrorMessage("CE3003", Severity.ERROR,
    "duplicate public symbol '{symbol}' found in units: {units}",
    Category.SCOPE, "Multiple units export the same public symbol name, creating an ambiguity."))

_add(ErrorMessage("CE3004", Severity.ERROR,
    "invalid unit path '{path}': {reason}",
    Category.SCOPE, "Unit path contains invalid characters or structure."))

_add(ErrorMessage("CE3005", Severity.ERROR,
    "cannot call private function '{name}' from unit '{current_unit}' (function is defined in '{func_unit}')",
    Category.SCOPE, "Private functions can only be called from within the same unit. Use 'public fn' to make the function accessible across units."))

# Perk-related errors (CE4xxx)
_add(ErrorMessage("CE4001", Severity.ERROR,
    "duplicate perk definition: {name}",
    Category.TYPE, "A perk with this name has already been defined. Each perk must have a unique name."))

_add(ErrorMessage("CE4002", Severity.ERROR,
    "type {type} already implements perk {perk}",
    Category.TYPE, "A perk can only be implemented once for each type. Remove the duplicate implementation."))

_add(ErrorMessage("CE4003", Severity.ERROR,
    "unknown perk: {perk}",
    Category.TYPE, "The perk being implemented has not been defined. Define the perk with 'perk {perk}:' before implementing it."))

_add(ErrorMessage("CE4004", Severity.ERROR,
    "method {method} signature does not match perk {perk} requirement",
    Category.TYPE, "The implementation method signature must exactly match the signature declared in the perk definition."))

_add(ErrorMessage("CE4005", Severity.ERROR,
    "missing required method {method} for perk {perk}",
    Category.TYPE, "The perk implementation is missing a required method. All methods declared in the perk must be implemented."))

_add(ErrorMessage("CE4006", Severity.ERROR,
    "type {type} does not implement perk {perk} required by constraint",
    Category.TYPE, "A type constraint requires the type to implement a specific perk. Add an implementation with 'extend {type} with {perk}:'."))

_add(ErrorMessage("CE4007", Severity.ERROR,
    "method {method} conflicts with perk method from {perk}",
    Category.TYPE, "A regular extension method has the same name as a perk method. Rename one of the methods to avoid ambiguity."))

_add(ErrorMessage("CE4008", Severity.ERROR,
    "cannot implement perk {perk} for type {type}: perk is generic but no type arguments provided",
    Category.TYPE, "Generic perks require type arguments when implemented. Use 'extend {type} with {perk}<T>:' syntax."))

_add(ErrorMessage("CE4009", Severity.ERROR,
    "perk {perk} requires {expected} type arguments, got {actual}",
    Category.TYPE, "The number of type arguments provided does not match the perk definition."))

# General warnings
_add(ErrorMessage("CW0001", Severity.WARNING,
    "missing trailing newline", Category.GENERAL,
    "Source file should end with a newline character."))

# Rebinding / scope warnings
_add(ErrorMessage("CW1001", Severity.WARNING,
    "unused variable '{name}'", Category.SCOPE,
    "A variable was declared with 'let' but never used."))

_add(ErrorMessage("CW1002", Severity.WARNING,
    "declared variable '{name}' already exists in an outer scope (first declared at {prev_loc})", Category.SCOPE,
    "A variable was declared with 'let' outside of this scope."))

_add(ErrorMessage("CW1003", Severity.WARNING,
    "variable '{name}' is only used through borrows (not directly accessed)", Category.SCOPE,
    "A variable was declared but only accessed through &references. This is valid but may indicate unnecessary indirection."))

# Unit/module warnings
_add(ErrorMessage("CW3001", Severity.WARNING,
    "duplicate use statement for unit '{unit}' (first used at {prev_loc})", Category.UNIT,
    "A unit was already imported earlier in this file. The duplicate use statement has no effect."))

#
# --- Runtime Error Codes (RExxxx) ---
#
# Runtime errors occur during program execution (not during compilation).
# These are emitted as runtime checks in the generated LLVM code.
# Convention: RE prefix indicates Runtime Error
#

# Library System Errors (CE35xx)
_add(ErrorMessage("CE3500", Severity.ERROR,
    "library output path must have .slib extension: '{path}'",
    Category.UNIT, "Library compilation requires output file with .slib extension."))

_add(ErrorMessage("CE3501", Severity.ERROR,
    "main() function not allowed in library mode",
    Category.UNIT, "Libraries cannot have a main() function. Remove it or compile as executable."))

_add(ErrorMessage("CE3502", Severity.ERROR,
    "library not found: '{lib}' (searched: {paths})",
    Category.UNIT, "Library bitcode and manifest files not found in search paths."))

_add(ErrorMessage("CE3503", Severity.ERROR,
    "invalid library manifest '{path}': {reason}",
    Category.UNIT, "Library manifest file is malformed or missing required fields."))

_add(ErrorMessage("CE3504", Severity.ERROR,
    "platform mismatch: library compiled for '{lib_platform}', current platform is '{current_platform}'",
    Category.UNIT, "Libraries must be compiled for the same platform they are used on."))

_add(ErrorMessage("CW3505", Severity.WARNING,
    "platform mismatch: library compiled for '{lib_platform}', current platform is '{current_platform}'",
    Category.UNIT, "Library was compiled for a different platform. This may cause runtime issues."))

_add(ErrorMessage("CE3506", Severity.ERROR,
    "cannot use --lib with --link: libraries cannot link other libraries yet",
    Category.UNIT, "Transitive library dependencies are not yet supported."))

_add(ErrorMessage("CE3507", Severity.ERROR,
    "failed to link library '{lib}': {reason}",
    Category.UNIT, "LLVM bitcode linking failed for the specified library."))

# Binary library format errors (.slib)
_add(ErrorMessage("CE3508", Severity.ERROR,
    "invalid library file '{path}': not a valid .slib file (bad magic)",
    Category.UNIT, "File does not start with SUSHILIB magic bytes."))

_add(ErrorMessage("CE3509", Severity.ERROR,
    "unsupported library format version '{version}' in '{path}' (compiler supports version {supported})",
    Category.UNIT, "Library was created with incompatible format version."))

_add(ErrorMessage("CE3510", Severity.ERROR,
    "corrupted library file '{path}': metadata section truncated (expected {expected} bytes, got {actual})",
    Category.UNIT, "Library file is incomplete or corrupted."))

_add(ErrorMessage("CE3511", Severity.ERROR,
    "corrupted library file '{path}': bitcode section truncated (expected {expected} bytes, got {actual})",
    Category.UNIT, "Library file is incomplete or corrupted."))

_add(ErrorMessage("CE3512", Severity.ERROR,
    "invalid library metadata in '{path}': {reason}",
    Category.UNIT, "MessagePack decoding failed or metadata schema is invalid."))

_add(ErrorMessage("CE3513", Severity.ERROR,
    "library file too large '{path}': {size} bytes exceeds maximum {max_size} bytes",
    Category.UNIT, "Library file exceeds reasonable size limit."))

# Array bounds errors
_add(ErrorMessage("RE2020", Severity.ERROR,
    "array index out of bounds",
    Category.RUNTIME, "Array access with index outside valid range [0, size)."))

# Memory allocation errors
_add(ErrorMessage("RE2021", Severity.ERROR,
    "memory allocation failed",
    Category.RUNTIME, "System could not allocate memory (malloc/realloc returned NULL)."))
