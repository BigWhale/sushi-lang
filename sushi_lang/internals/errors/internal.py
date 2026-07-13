"""Internal errors (CE0xxx) -- compiler bugs, not user errors.

This module owns its numeric range: a code may only be added in the file that
owns it, which is what makes the grouping structural rather than conventional.
"""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


# Internal errors (compiler bugs) - CE0xxx range
_add(ErrorMessage("CE0000", Severity.ERROR,
    "internal compiler error: {detail}",
    Category.INTERNAL, "The compiler crashed. This is a compiler bug, not a problem "
                       "with the program being compiled."))

_add(ErrorMessage("CE0001", Severity.ERROR,
    "unknown type node '{node}'",
    Category.INTERNAL, "Found an unexpected type (bug or unsupported feature)."))

_add(ErrorMessage("CE0002", Severity.ERROR,
    "internal error: malformed parse tree at '{node}': {detail}",
    Category.INTERNAL, "The grammar produced a node shape the compiler cannot build. "
                       "No accepted source can reach this: it is a compiler bug."))

_add(ErrorMessage("CE0003", Severity.ERROR,
    "internal error: unhandled parse-tree node '{node}'",
    Category.INTERNAL, "The grammar produces this node but the AST builder does not "
                       "dispatch on it -- grammar/builder drift. This is a compiler bug."))

_add(ErrorMessage("CE0007", Severity.ERROR,
    "standard library build failed: {detail}",
    Category.INTERNAL, "A generator under sushi_stdlib/src failed to produce the "
                       "standard library bitcode."))

_add(ErrorMessage("CE0008", Severity.ERROR,
    "internal error: the grammar itself is malformed: {detail}",
    Category.INTERNAL, "grammar.lark failed to load. This is a compiler bug."))

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

_add(ErrorMessage("CE0126", Severity.ERROR,
    "poisoned intern of '{name}': already interned as {existing}, rebuilt as {rebuilt}",
    Category.INTERNAL,
    "Two spellings of one Result<T, E> mangled to the same enum name but carry different "
    "payload types -- one of them was interned before its UnknownType payloads were resolved. "
    "EnumType hashes on the name alone but compares on the variants, so a poisoned entry "
    "hash-matches and compares unequal: a silent cache miss, not a crash. Every Result must "
    "be interned through ensure_result_type_in_table, which resolves its payloads first."))

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

# Struct errors
_add(ErrorMessage("CE0004", Severity.ERROR,
    "duplicate struct '{name}'",
    Category.TYPE, "Two structs share the same name in a compilation unit."))

_add(ErrorMessage("CE0005", Severity.ERROR,
    "duplicate field '{name}' in struct '{struct_name}'",
    Category.TYPE, "A struct declares the same field name more than once."))

_add(ErrorMessage("CE0006", Severity.ERROR,
    "enum '{name}' already defined as struct",
    Category.TYPE, "An enum is declared with the same name as a struct."))
