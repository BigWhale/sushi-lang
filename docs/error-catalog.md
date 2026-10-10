# Error Catalog

This page lists all the diagnostic codes of the Sushi toolchain. There are four families:

- **CE**: compiler errors. An error makes the compiler exit 2.
- **CW**: compiler warnings. A warning makes the compiler exit 1, if there is no error.
- **RE**: runtime errors. A compiled program gives a runtime error when it stops on a trap. The program exits 1.
- **NE**: errors of the package manager, `nori`. `nori` exits 1 for an NExxxx error and 2 for an internal error ([NE0000](#ne0000)).

Each entry gives the severity, the category, the message, the help (if there is one) and the description.

A name in braces in a message or in a help, for example `{name}`, is a placeholder. The tool replaces it with a value when it shows the diagnostic.

The help is not part of the message. The compiler adds a help line in some situations only. If a code has more than one help, the list shows all of them. The compiler shows the help that applies to the situation.

An internal error (for example [CE0000](#ce0000)) is a defect in the compiler, not in your program. Please report it.


## CE0xxx: Internal errors and function errors {#ce0xxx}

An internal error (CE00xx and some CE01xx codes) is a defect in the compiler, not in your program. The function errors (CE01xx) are about functions, methods, parameters and variadic arguments.

### CE0000 {#ce0000}

**Error** · internal

**Message:** `internal compiler error: {detail}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The compiler crashed. This is a compiler bug, not a problem with the program being compiled.

### CE0001 {#ce0001}

**Error** · general

**Message:** `the program nests too deeply for the compiler`

**Help:** `` split the nest into `let` steps: bind an inner part to a name, then use the name in the outer part ``

Each walk of the compiler goes down one or more levels for each level of a nest in the source: a call in the argument of a call, a `??` on a call, an operand of an operator, a block in a block. The compiler runs with a deep stack, so a nest of some thousands of levels compiles. A nest that goes past that depth stops the compile with this error. The program is not wrong, but the compiler cannot read it in one piece. The error has no location. Split the nest into `let` steps.

### CE0002 {#ce0002}

**Error** · internal

**Message:** `internal error: malformed parse tree at '{node}': {detail}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The grammar produced a node shape the compiler cannot build. No accepted source can reach this: it is a compiler bug.

### CE0003 {#ce0003}

**Error** · internal

**Message:** `internal error: unhandled parse-tree node '{node}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The grammar produces this node but the AST builder does not dispatch on it -- grammar/builder drift. This is a compiler bug.

### CE0004 {#ce0004}

**Error** · type

**Message:** `duplicate struct '{name}'`

Two structs share the same name in a compilation unit.

### CE0005 {#ce0005}

**Error** · type

**Message:** `duplicate field '{name}' in struct '{struct_name}'`

A struct declares the same field name more than once.

### CE0006 {#ce0006}

**Error** · type

**Message:** `{kind} '{name}' already defined as {other}`

A struct and an enum share one type name for the whole program. The compiler refuses the second declaration of a name, in SOURCE order and of either kind, where it is written, and the note points at the first. The first declaration keeps the name, so its uses give no more errors. In one unit the position in the file decides; across units the order is the unit order, and a dependency comes first. The header says `generic` when a declaration is generic. Rename one of the two types.

### CE0007 {#ce0007}

**Error** · internal

**Message:** `standard library build failed: {detail}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

A generator under sushi\_stdlib/src failed to produce the standard library bitcode.

### CE0008 {#ce0008}

**Error** · internal

**Message:** `internal error: the grammar itself is malformed: {detail}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

grammar.lark failed to load. This is a compiler bug.

### CE0009 {#ce0009}

**Error** · internal

**Message:** `builder not initialized`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

IR builder is None - function compilation context required.

### CE0010 {#ce0010}

**Error** · internal

**Message:** `function context not initialized`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Function context is None - cannot emit code outside function context.

### CE0013 {#ce0013}

**Error** · internal

**Message:** `runtime function '{name}' not declared`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

C library function not declared - runtime initialization required.

### CE0014 {#ce0014}

**Error** · internal

**Message:** `dynamic array manager not initialized`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Dynamic array manager is None - initialization required.

### CE0015 {#ce0015}

**Error** · internal

**Message:** `AST invariant violated: {message}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

An AST invariant is violated: a semantic pass or the code generator found a structure that the compiler does not make. This is a fault in the compiler, not in the program.

### CE0016 {#ce0016}

**Error** · internal

**Message:** `scope stack underflow: attempted to pop from empty scope stack`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Push/pop mismatch - each push\_scope() must be paired with exactly one pop\_scope().

### CE0017 {#ce0017}

**Error** · internal

**Message:** `cannot convert value of type '{src}' to '{dst}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type conversion failed during code generation - semantic analysis should prevent this.

### CE0018 {#ce0018}

**Error** · internal

**Message:** `unknown builtin type: '{type}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Encountered unrecognized builtin type during codegen.

### CE0019 {#ce0019}

**Error** · internal

**Message:** `cannot determine language type for LLVM type: {llvm_type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Failed to map LLVM type back to semantic type.

### CE0020 {#ce0020}

**Error** · internal

**Message:** `unresolved type '{type}' - semantic analysis should have caught this`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type not found in symbol tables - semantic analysis failure.

### CE0021 {#ce0021}

**Error** · internal

**Message:** `unsupported type for size calculation: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Cannot calculate byte size for this type.

### CE0022 {#ce0022}

**Error** · internal

**Message:** `unsupported type for operation: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type cannot be used in this context.

### CE0023 {#ce0023}

**Error** · internal

**Message:** `method '{method}' expects {expected} argument(s), got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Method called with wrong number of arguments during codegen.

### CE0024 {#ce0024}

**Error** · internal

**Message:** `unknown method '{method}' for type '{type}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Method not found for this type - semantic analysis should prevent this.

### CE0025 {#ce0025}

**Error** · internal

**Message:** `extension method '{name}' not declared`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Extension method reference not found in symbol table.

### CE0026 {#ce0026}

**Error** · internal

**Message:** `function expects {expected} arguments, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Function parameter count mismatch during codegen.

### CE0027 {#ce0027}

**Error** · internal

**Message:** `callee must be a Name, got {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Function call expression has invalid callee type - AST invariant violation.

### CE0028 {#ce0028}

**Error** · internal

**Message:** `stdlib method not implemented: {method}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Standard library method missing backend implementation.

### CE0029 {#ce0029}

**Error** · internal

**Message:** `struct '{struct}' has no field '{field}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Struct field lookup failed - semantic analysis should prevent this.

### CE0030 {#ce0030}

**Error** · internal

**Message:** `cannot get address of struct for member access`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Failed to retrieve struct pointer for field access.

### CE0031 {#ce0031}

**Error** · internal

**Message:** `member access on non-struct type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Attempted field access on non-struct type - semantic analysis failure.

### CE0032 {#ce0032}

**Error** · internal

**Message:** `expected StructType, got {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type mismatch: expected struct type but got different type.

### CE0033 {#ce0033}

**Error** · internal

**Message:** `unknown enum type: {name}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Enum type not found in symbol table during codegen.

### CE0034 {#ce0034}

**Error** · internal

**Message:** `variant '{variant}' not found in enum '{enum}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Enum variant lookup failed - semantic analysis should prevent this.

### CE0035 {#ce0035}

**Error** · internal

**Message:** `enum '{enum}' missing required variant '{variant}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Expected enum variant not found in enum definition.

### CE0036 {#ce0036}

**Error** · internal

**Message:** `enum variant '{variant}' has wrong number of associated types: expected {expected}, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Enum variant associated type count mismatch - monomorphization failure.

### CE0040 {#ce0040}

**Error** · internal

**Message:** `cannot construct {variant} variant for return type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Failed to construct enum variant for error propagation.

### CE0041 {#ce0041}

**Error** · internal

**Message:** `expected ArrayType, got {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type mismatch: expected fixed-size array but got different type.

### CE0042 {#ce0042}

**Error** · internal

**Message:** `expected DynamicArrayType, got {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type mismatch: expected dynamic array but got different type.

### CE0043 {#ce0043}

**Error** · internal

**Message:** `array.get() returned non-struct type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Array element type is not a struct as expected.

### CE0044 {#ce0044}

**Error** · internal

**Message:** `nested member access on non-struct field type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Attempted nested field access on non-struct type.

### CE0045 {#ce0045}

**Error** · internal

**Message:** `generic type '{type}' not found in enum or struct table - monomorphization may have failed`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Generic type instantiation not found - monomorphization failure.

### CE0047 {#ce0047}

**Error** · internal

**Message:** `failed to create Maybe@({type}) enum type`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Maybe type instantiation failed during codegen.

### CE0049 {#ce0049}

**Error** · internal

**Message:** `invalid {generic} type name: {name}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Generic type name does not match expected pattern.

### CE0050 {#ce0050}

**Error** · internal

**Message:** `{generic} should have exactly {expected} type parameters, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Generic type parameter count mismatch.

### CE0051 {#ce0051}

**Error** · internal

**Message:** `no hash method for type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type does not have a hash() method - required for HashMap keys.

### CE0052 {#ce0052}

**Error** · internal

**Message:** `cannot hash value of type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type is not hashable - semantic analysis should prevent this.

### CE0054 {#ce0054}

**Error** · internal

**Message:** `hash() expects 0 arguments, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Hash method called with incorrect number of arguments.

### CE0055 {#ce0055}

**Error** · internal

**Message:** `unknown variable or constant: {name}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Variable/constant not found in symbol table during codegen.

### CE0056 {#ce0056}

**Error** · internal

**Message:** `cannot determine type for variable: {name}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Variable type information missing during codegen.

### CE0057 {#ce0057}

**Error** · internal

**Message:** `dynamic array '{name}' not declared`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Dynamic array not found in tracking state.

### CE0058 {#ce0058}

**Error** · internal

**Message:** `dynamic array '{name}' already destroyed`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Attempted to use dynamic array after explicit destruction.

### CE0059 {#ce0059}

**Error** · internal

**Message:** `attempt to emit into a terminated block`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Cannot add instructions to basic block that already has a terminator.

### CE0060 {#ce0060}

**Error** · internal

**Message:** `attempt to emit statement after a terminator`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Cannot emit code after return/branch - control flow analysis failure.

### CE0061 {#ce0061}

**Error** · internal

**Message:** `block has no 'statements' list`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

AST block node missing expected statements field.

### CE0064 {#ce0064}

**Error** · internal

**Message:** `C-style main function not found`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Generated C main wrapper not found in module.

### CE0067 {#ce0067}

**Error** · internal

**Message:** `cannot infer struct type from expression: {expr}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Struct type inference failed for expression.

### CE0068 {#ce0068}

**Error** · internal

**Message:** `cannot infer struct type from method call: {method}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Struct type inference failed for method call.

### CE0069 {#ce0069}

**Error** · internal

**Message:** `cannot infer struct type from DotCall: {method}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Struct type inference failed for dot-call expression.

### CE0071 {#ce0071}

**Error** · internal

**Message:** `iter() expects 0 arguments, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Iterator method called with incorrect number of arguments.

### CE0073 {#ce0073}

**Error** · internal

**Message:** `unknown primitive type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Primitive type not recognized during codegen.

### CE0075 {#ce0075}

**Error** · internal

**Message:** `unknown conversion kind: {kind}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type conversion operation not recognized.

### CE0076 {#ce0076}

**Error** · internal

**Message:** `unsupported primitive type for operation: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Primitive type cannot be used in this operation.

### CE0077 {#ce0077}

**Error** · internal

**Message:** `unknown stdlib string method: {method}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Standard library string method not implemented.

### CE0078 {#ce0078}

**Error** · internal

**Message:** `to_str() expects 0 arguments, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

String conversion method called with incorrect arguments.

### CE0079 {#ce0079}

**Error** · internal

**Message:** `unsupported element type for size calculation: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Cannot calculate element size for this type.

### CE0080 {#ce0080}

**Error** · internal

**Message:** `unknown Own@(T) method: {method}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Own@(T) method not implemented.

### CE0081 {#ce0081}

**Error** · internal

**Message:** `Own@(T) field 'value' has unexpected type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Own@(T) internal structure does not match expected layout.

### CE0083 {#ce0083}

**Error** · internal

**Message:** `unknown List@(T) method: {method}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

List@(T) method not implemented.

### CE0085 {#ce0085}

**Error** · internal

**Message:** `unknown HashMap@(K, V) method: {method}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

HashMap method not implemented.

### CE0087 {#ce0087}

**Error** · internal

**Message:** `expected HashMap@(K, V) type, got {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Type mismatch: expected HashMap but got different type.

### CE0089 {#ce0089}

**Error** · internal

**Message:** `Result enum missing Ok variant: {enum}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

A Result@(T, E) enum does not have an Ok variant.

### CE0090 {#ce0090}

**Error** · internal

**Message:** `Result.Ok variant should have 1 associated type, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Result.Ok variant has incorrect number of associated types.

### CE0091 {#ce0091}

**Error** · internal

**Message:** `Result type not found: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Result enum type not found in symbol table.

### CE0092 {#ce0092}

**Error** · internal

**Message:** `Maybe enum missing Some variant: {enum}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Maybe-like enum does not have Some variant.

### CE0093 {#ce0093}

**Error** · internal

**Message:** `Maybe.Some variant should have 1 associated type, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Maybe.Some variant has incorrect number of associated types.

### CE0094 {#ce0094}

**Error** · internal

**Message:** `unknown Maybe@(T) method: {method}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Maybe@(T) method not implemented.

### CE0095 {#ce0095}

**Error** · internal

**Message:** `expect() expects 1 argument, got {got}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Maybe.expect() method called with incorrect arguments.

### CE0096 {#ce0096}

**Error** · internal

**Message:** `invalid intrinsic operation: {operation}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Intrinsic operation not recognized.

### CE0099 {#ce0099}

**Error** · internal

**Message:** `not an operator type: {type}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Expression node is not a valid operator - AST invariant violation.

### CE0100 {#ce0100}

**Error** · internal

**Message:** `unsupported expression type: {expr}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Expression type not supported in this context.

### CE0101 {#ce0101}

**Error** · function

**Message:** `duplicate function '{name}'`

**Help:**

- `a method is found on the receiver's type, so no alias can choose between the two; rename one of them`
- `a method is found on the receiver's type, so no import can choose between the two; rename one of them`
- `a method is found on the receiver's type, so no alias can choose between the two; rename one of them, or put the method behind a perk`
- `Sushi has no specialization: make both targets fully concrete, or implement a perk on the concrete target -- a perk implementation outranks an extension method by design.`

Two functions share the same name in a compilation unit.

### CE0102 {#ce0102}

**Error** · function

**Message:** `duplicate parameter '{name}'`

A function, a method or a lambda declares the same parameter name more than once.

### CE0103 {#ce0103}

**Error** · function

**Message:** `missing return type for function '{name}'`

Function header must specify an explicit return type.

### CE0104 {#ce0104}

**Error** · function

**Message:** `missing type annotation for constant '{name}'`

Constant declarations must specify an explicit type.

### CE0105 {#ce0105}

**Error** · function

**Message:** `duplicate constant '{name}'`

Two constants share the same name in a compilation unit.

### CE0106 {#ce0106}

**Error** · function

**Message:** `main() function must return a bare integer type (i8-i64, u8-u64), got '{type}'`

The main function answers the exit code of the program, so it returns a bare integer type and has no error channel: a '| E' on it, or a 'Result@(T, E)' return, is refused. Handle a failure in main in its body, with match or .realise(default), and answer it as a code. See [the error-channel design](design/error-channel.md).

### CE0107 {#ce0107}

**Error** · function

**Message:** `{callable} must return a value on all code paths`

A body that answers a value or a Result must end in a return on every code path. The rule is one for a function, a lambda and an extension or perk-implementation method alike. A body with a channel ('| E', or an explicit Result@(T, E) return) answers a Result, so a `~` one ends with `return Result.Ok(~)`. A BARE `~` body answers nothing, so it may reach its end. See [the error-channel design](design/error-channel.md). For a lambda, the diagnostic names it `lambda` and points at its own location. An `expand` may run zero times, as a loop may, so a `return` inside it does not end the path. The rule is judged once, on the template.

### CE0108 {#ce0108}

**Error** · function

**Message:** `{what} is not a compile-time constant`

Constant declarations must use compile-time evaluable expressions: literals, other constants, operators, casts, interpolation, and a struct or enum variant built from constants. Function calls, method calls, and variable references are not allowed. `{what}` names the expression as the source writes it ("a function call", "a `from(...)` call").

### CE0109 {#ce0109}

**Error** · function

**Message:** `circular constant dependency detected: {chain}`

Constants cannot depend on themselves directly or indirectly.

### CE0110 {#ce0110}

**Error** · function

**Message:** `unsupported operation '{op}' in constant expression`

This operation cannot be evaluated at compile-time for constants.

### CE0111 {#ce0111}

**Error** · function

**Message:** `invalid type cast in constant expression from {from_type} to {to_type}`

Type cast is not allowed in constant expressions.

### CE0112 {#ce0112}

**Error** · function

**Message:** `division by zero`

The compiler can read this divisor, and it is zero. The same compile-time arithmetic applies to a constant and to a body, so '10 / 0' is refused in both positions. Only a divisor the compiler can read is an error: a computed one -- a variable, a loop index, a call -- is ordinary code and no check is emitted around it.

### CE0113 {#ce0113}

**Error** · internal

**Message:** `{message}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Generic enum constructor requires type annotation from semantic analysis.

### CE0114 {#ce0114}

**Error** · function

**Message:** `{message}`

A variadic '...T' parameter must be the last parameter, a function may declare at most one, and its element type must not be a reference (a dynamic-array element '...T\[\]' is allowed and moved per element).

### CE0115 {#ce0115}

**Error** · function

**Message:** `variadic {what} not allowed in {context}`

Variadic parameters are only permitted in plain function definitions, not in perk methods or extension methods. A type pack `...Ts` is refused in the type-parameter list of a struct, an enum, an error type, an extension method and a static method: a pack has two halves, and only a free function has the parameter list for its value pack `...Ts name`. The caret is on the `...Ts`. When a method has a type pack and a value pack, the type pack is the one fault. The analysis stops after the refusal, so a written instance of the type or a call of the method adds no second diagnostic.

### CE0116 {#ce0116}

**Error** · function

**Message:** `public function '{name}' is variadic and cannot appear in a library public API`

Native variadic functions cannot be exported through a .slib public API (the library format does not record the variadic flag).

### CE0117 {#ce0117}

**Error** · function

**Message:** `{message}`

A type-pack parameter '...Ts' must be the last parameter, and a function may declare at most one type-pack.

### CE0118 {#ce0118}

**Error** · function

**Message:** `{message}`

A function cannot mix a type-pack parameter '...Ts' with a native variadic '...T'.

### CE0119 {#ce0119}

**Error** · function

**Message:** `malformed expand(...): {message}`

An `expand(a in args):` walks the value pack `...Ts args` of its own function, and it can stand in no other position. It is refused in a lambda body (a lambda is a callable of its own and has no type pack), in a body with no type pack (a concrete function, a generic function with no pack, an extension method, a perk method, a conversion), when the iterable is not a name, and when the name is not the value pack of the function. The rule is judged once, on the written body, so an uncalled template is refused too. The analysis stops after it, as it does after [CE0147](#ce0147), because no copy of the body can be cut.

### CE0120 {#ce0120}

**Error** · function

**Message:** `{message}`

A bloom argument 'arr...' requires a variadic '...T' parameter and must be the sole, last trailing argument at the call site.

### CE0121 {#ce0121}

**Error** · internal

**Message:** `could not resolve the concrete enum type for match pattern '{pattern}' - pattern bindings cannot be extracted`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The backend could not determine the scrutinee's concrete enum type for a match arm with bindings. The type checker should have annotated Match.resolved\_scrutinee\_type; a miss here would otherwise silently drop the arm's binding locals.

### CE0122 {#ce0122}

**Error** · type

**Message:** `generic type '{name}' is infinitely recursive - monomorphization exceeded the maximum depth`

A generic type instantiation nests without bound (e.g. a type parameter that grows on each self-reference), so monomorphization cannot terminate. A finite self-reference through an opaque pointer (Own@(T)) is fine; an ever-growing type argument is not.

### CE0123 {#ce0123}

**Error** · internal

**Message:** `no hash emitter registered for kind '{kind}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The derive pass registered a hash() method whose LLVM emitter the backend never supplied. The backend types modules register their emitter factories at import; one of them failed to load.

### CE0124 {#ce0124}

**Error** · internal

**Message:** `'??' expression reached codegen without a type annotation from semantic analysis`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The typecheck pass annotates every TryExpr it validates (inner type, unwrapped type, success tag, error type). Reaching the backend without one means the expression's type was never inferred. This is a gap in the typecheck pass, not a user error.

### CE0125 {#ce0125}

**Error** · internal

**Message:** `internal error: borrow checker has no arm for expression node '{node}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The Expr union grew a member the borrow checker does not dispatch on. Without the dispatch, the borrow checker does not check that node. This is a compiler bug, not a user error.

### CE0126 {#ce0126}

**Error** · internal

**Message:** `poisoned intern of '{name}': already interned as {existing}, rebuilt as {rebuilt}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Two spellings of one generic enum (Result@(T, E), Maybe@(T)) mangled to the same name but carry different payload types: one was interned before its payloads were resolved. The table entry then describes the wrong payload, and this guard catches it. Intern only through ensure\_result\_type\_in\_table / ensure\_maybe\_type\_in\_table, which resolve their payloads before mangling the name.

### CE0127 {#ce0127}

**Error** · internal

**Message:** `no clone emitter registered for kind '{kind}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The derive pass registered a clone() method whose LLVM emitter the backend never supplied. The backend types modules register their emitter factories at import; one of them failed to load.

### CE0129 {#ce0129}

**Error** · internal

**Message:** `no ownership decision for the {use} consuming use of a {node}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Every position that takes ownership routes through backend/ownership.py::consume, which reads the Provenance semantics stamped on the source expression. A missing stamp means the borrow pass did not classify this position -- a gap in the borrow checker's coverage of the ConsumingUse set, not a user error. [CE0124](#ce0124) is the same check for a missing try-expression annotation.

### CE0130 {#ce0130}

**Error** · internal

**Message:** `internal error: scope checker has no arm for node '{node}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The AST grew a statement or expression node the scope checker does not dispatch on. Without the dispatch, the scope checker does not analyze that node. [CE0125](#ce0125) is the same check in the borrow checker. This is a compiler bug, not a user error.

### CE0131 {#ce0131}

**Error** · function

**Message:** `'??' operator not allowed in {context}: it returns a bare value and has no error channel`

**Help:**

- `write '| E' in the function type the lambda takes, or handle the value in the body (match, .realise(default))`
- `handle the value in the body (match, .realise(default)), or write '| E' in the signature`
- `a conversion is bare, so handle the Result in the body (match, .realise(default))`

A callable has an error channel only when its signature writes '| E' or returns an explicit Result@(T, E). See [the error-channel design](design/error-channel.md). A BARE function, method or lambda returns the value itself ([CE2091](#ce2091)), so a '??' has no Result return to propagate into. Handle the Result in the body with match or .realise(default), or write '| E' in the signature. For a written function or method body the collect pass emits it, so it fires once per declaration and covers templates nobody instantiates. A lambda takes its channel from its type, so the typecheck pass emits it there; a ?? inside a lambda whose type writes '| E' is legal in any body. 'main' is bare, so a ?? in main is this error too.

### CE0132 {#ce0132}

**Error** · internal

**Message:** `no write address for a fixed-array receiver of kind {node}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

A mutating built-in on a fixed array -- fill, reverse -- routes its receiver through backend/types/arrays/fixed\_addressing.py::as\_fixed\_array\_address with writable=True, which resolves an address from the AST. A receiver that names no storage has no write address, and every such receiver already has a diagnostic: [CE2096](#ce2096) for a constant, and [CE2408](#ce2408), [CE2414](#ce2414), [CE2421](#ce2421), [CE2422](#ce2422), [CE2426](#ce2426) or [CE2429](#ce2429) for the read-only receivers. Reaching this means one of them did not fire, so it is a gap in that coverage and not a user error. [CE0129](#ce0129) is the same check for a consuming use with no ownership decision.

### CE0133 {#ce0133}

**Error** · function

**Message:** `method '{name}' declares {found}, but perk '{perk}' requires {expected}`

The '| E' channel is part of a perk method's signature, so a contract that declares one and an implementation that omits it are the same mismatch read from opposite ends, as are two channels over different error types. The primary location is at the implementation, and a note points at the contract method. Write the same channel on the contract method and on the implementation.

### CE0134 {#ce0134}

**Error** · function

**Message:** `static method '{name}' has no receiver`

**Help:**

- `` a static is called on the type name; drop the receiver, or drop the `static` marker to get an instance method ``
- `` a static has no receiver: take what it needs as a parameter, or drop the `static` marker ``

A `static` extension method is called on the TYPE name (`Vec.at(3, 4)`), so nothing was called ON: there is no receiver to declare a mode for and none to read in the body. ONE code for TWO positions, because it is one fault -- a receiver mode in the signature (`extend Vec static at(poke self)`) and a mention of `self` in the body -- and the caret sits on whichever one was written. Drop the `static` marker to get an instance method, whose `self` is implicit, or take the value as an ordinary parameter.

### CE0135 {#ce0135}

**Error** · internal

**Message:** `internal error: type substitution has no arm for {kind} node '{node}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The monomorphizer substitutes a type argument for every type parameter an instantiated body names. This error means that an expression or a statement in an instantiated body was not substituted, so an internal type parameter name (T, U) would reach the user. This is a compiler bug, not a user error. [CE0125](#ce0125) is the same check in the borrow checker, and [CE0130](#ce0130) is the same check in the scope checker.

### CE0136 {#ce0136}

**Error** · internal

**Message:** `internal error: the AST walk has no arm for node '{node}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

semantics/visitors.py dispatches on a method name it builds from the class name of the node. This error means that a node kind has no arm in a visitor (StatementValidator, ExpressionValidator or TypeInferenceVisitor), so the visitor would skip that node with no diagnostic. A node kind that a parent arm reads inside itself -- an ArrayElement, a MatchArm, a Pattern -- is named in visitors.WALKED\_IN\_PARENT and never arrives here. This is a compiler bug, not a user error. [CE0125](#ce0125) is the same check in the borrow checker, and [CE0130](#ce0130) is the same check in the scope checker.

### CE0137 {#ce0137}

**Error** · internal

**Message:** `lifted lambda name '{name}' is already registered`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The lambda lifter claims its index before it builds anything: it steps past every \_\_lambda\_&lt;n> already in the function table and every \_\_closure\_env\_&lt;n> already in the struct table, so the name it then registers is free in both. A registration that fails anyway means the name entered a table between that search and the registration. The closure would then use the body and the environment layout of another closure, so the compiler stops. This is a compiler bug, not a user error.

### CE0138 {#ce0138}

**Error** · function

**Message:** `` main() takes one parameter, `string[] args`, or no parameter ``

The entry point receives the command line as `string[] args` or receives nothing. The type and the name are both part of the rule, so `fn main(string[] argv)`, `fn main(i32 x)` and `fn main(string[] args, i32 x)` are refused at the parameter that breaks it. The parameter takes no mode: argv is a borrowed view that the runtime owns, so `nom string[] args` (main would free argv a second time at exit), `peek string[] args` and `poke string[] args` are refused too.

### CE0139 {#ce0139}

**Error** · internal

**Message:** `operator '{op}' received integer operands of two widths: {left} and {right}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The typecheck pass refuses a mixed width for `== != < <= > >=`, `+ - * / %` and `& | ^` ([CE2510](#ce2510)), and a shift brings its count to the value's width itself. So the backend comparison and bitwise emitters always receive two integer operands of one width. Reaching this means a mixed pair passed the typecheck pass, which is a gap in [CE2510](#ce2510) and not a user error.

### CE0140 {#ce0140}

**Error** · function

**Message:** `unreachable statement: the path ended before it`

**Help:** `remove the statement, or move it before the one that ends the path`

A statement that follows a statement which always ends the path can never run: a `return`, an `if` with an `else` whose every arm returns, an exhaustive `match` whose every arm returns, and a `break` or a `continue` in the same block. It is an error, not a warning: dead code is a statement the author thinks runs. The rule uses the same reach analysis as [CE0107](#ce0107). A `match` that is not exhaustive ends no path: its exhaustiveness error ([CE2040](#ce2040), [CE2074](#ce2074)) is the one diagnostic, and the next statement is not dead. It is reported ONCE per block, at the first dead statement, with a note at the statement that ends the path. Remove the statement, or move it before the one that ends the path.

### CE0141 {#ce0141}

**Error** · internal

**Message:** `unknown lifecycle kind: '{kind}'`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The lifecycle table in backend/lifecycle.py holds one clone/destroy pair per composite kind: dynamic\_array, fixed\_array, struct and enum. A backend module registered a handler under a kind that is not one of them, so no value of any type can reach that handler. It is a compiler fault, and no user program causes it.

### CE0142 {#ce0142}

**Error** · internal

**Message:** `unit '{unit}' has no source text for its cache key`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The cache key of a unit hashes the text that the parser read, which every unit carries as its source. A unit with no source text means a loader built it without the text, which is a compiler fault and not a user error.

### CE0143 {#ce0143}

**Error** · internal

**Message:** `internal error: the {kind} call '{name}' behind a namespace has no parameter modes`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

The typecheck pass stamps the declared parameter modes on every function call written behind a namespace (`l.gen(nom n)`), through the one mode seam, semantics/param\_modes.py. The borrow pass reads that stamp to check each `nom` marker and to record each consuming use. A call with no stamp is a compiler fault and not a user error. Without the stamp, the borrow pass cannot do the [CE2427](#ce2427) marker check for that call.

### CE0144 {#ce0144}

**Error** · function

**Message:** `'{name}' is a type pack, not a value`

**Help:** `` walk a pack with `expand(a in {name}):`; pack forwarding and pack indexing are not supported ``

A type-pack parameter `...Ts args` is used only through `expand(a in args):`, which gives one value per element. The pack name is not a value in any other position: pack forwarding (`g(args...)`, `g(args)`) and pack indexing (`args[0]`) are not supported. The diagnostic is the one fault of that use, so the call that holds it gives no second diagnostic. Walk the pack with `expand(a in args):`. A template is checked where it is written, so an uncalled template is refused too.

### CE0145 {#ce0145}

**Error** · internal

**Message:** `a stdlib row names the type '{type}', which has no LLVM value type`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

A registry row (`Signature`) and a string method row (`MethodSpec`) give their parameters and their answer in Sushi types, and `llvm_value_type` (backend/expressions/calls/stdlib/signatures.py) turns each into the LLVM type it crosses as. This row names a type that function cannot map, so the call site cannot declare the generated function. The method or the function is KNOWN: the fault is the type in its row. Add the type to `llvm_value_type`, built through the helper in sushi\_stdlib/src/type\_definitions.py that the generator also uses.

### CE0146 {#ce0146}

**Error** · internal

**Message:** `internal error: the typecheck pass found no type for this foreach iterable`

The typecheck pass gives the iterable of every foreach its type, and the loop reads its item type from it. When inference answers nothing and no other error was reported for the iterable, the fault is in the compiler, not in the program. The pass reports it here, at the iterable, so the backend never reads a loop with no item type. Bind the iterable to a 'let' with a written type first, and please report the program.

### CE0147 {#ce0147}

**Error** · function

**Message:** `` '{name}' is a type pack: it names a type only in its `...{name}` parameter ``

A type pack `...Ts` stands for any number of types, so its name is a type in ONE position: the trailing parameter `...Ts args`, which takes one argument per element. Everywhere else the name would have to be ONE type, and it is not: a parameter written `Ts x` with no `...`, a return type, an error type, a `let` type, an array element (`Ts[]`), a type argument (`List@(Ts)`), a cast and a lambda parameter are all refused at the declaration. Walk the values with `expand(a in args):`, or declare an ordinary type parameter `T` for a single value.

### CE0148 {#ce0148}

**Error** · internal

**Message:** `an opaque type parameter reached the program table: '{name}'`

The check of a generic template builds its instances over an opaque type parameter in a scratch layer over the program tables. A program table must never hold one: the backend has no layout for a type parameter. One scan at the end of the analysis reads every struct and enum name. This is a fault in the compiler, not in the program; please report the program.

### CE0149 {#ce0149}

**Error** · internal

**Message:** `the check of the template '{template}' did not report a fault that a copy of it reports: [{original}] {message}`

A generic template is checked one time, where it is written. Its copies then report only what each instance decides: an error type for a type argument in an error position, and a lambda parameter that owns for this type argument. Every other rule ran on the template. A copy that reports another error has found a fault that the template check did not find. This is a fault in the compiler. The text holds the code and the message that the copy reported, so you can still see the fault. Please report the program.

### CE0150 {#ce0150}

**Error** · internal

**Message:** `the borrow pass has no move location for the moved variable '{name}'`

A use-after-move error ([CE2405](#ce2405), [CE2435](#ce2435)) is relational: it shows the use, and a note shows where the value moved. The borrow pass records the location with the moved flag, and every branch join and loop join keeps the two together. A moved variable with no location means that a path set the flag and lost the location. This is a fault in the compiler, not in the program; please report the program.

### CE0151 {#ce0151}

**Error** · function

**Message:** `the copies of the {kind} '{name}' grow without end: this call names the copy '{instance}', which has a larger type argument`

A generic function, or an extension method that the compiler copies at the call site (an `extend T[]` method, or a method with a method-level type parameter), gets one copy for each list of type arguments. When the body of a copy calls the same template, directly or through other templates, with a type argument that holds the earlier one (`grow(nom Box(x), n)` in `grow@(U)`), each copy names a new and larger copy, and the chain has no end. The value of a run-time argument has no effect, because the copies are made at compile time. The diagnostic is at the call in the template body that names the larger copy, and a note shows the first call that started the chain. A chain of different templates, and a template that calls itself with the same type argument, make no new copy and are not refused. [CE0122](#ce0122) is the same fault for a generic type. Call the template with a type argument that does not grow.

### CE0152 {#ce0152}

**Error** · function

**Message:** `{subject} is marked dont_panic, and this build does not allow it`

**Help:**

- `pass --dont-panic to allow the unchecked indexes of this build, or remove the marker`
- `the library '{library}' asks for unchecked indexes; pass --dont-panic to consent`

The `dont_panic because "<reason>"` marker removes the bounds check of each `[]` in the body that carries it, so an index out of range there is undefined behaviour and not the runtime error [RE2020](#re2020). Only three kinds of unit may write it. A bundled stdlib unit needs no flag. A unit of a build that passes `--dont-panic` may write it. A source library is parsed again in each build that uses it, so its marker meets this gate in the consumer's build, and the consumer must pass `--dont-panic` too: the consumer compiles the unchecked code, so the consumer consents. The message then names the library. A CONCRETE marked body of a binary or hybrid library is compiled into the shipped bitcode already, so its consumer needs no consent. A marked generic TEMPLATE of any library kind ships as source, so the consumer compiles it and must pass `--dont-panic`. Pass `--dont-panic`, or remove the marker. See [the dont_panic design](design/dont-panic.md).

## CE1xxx: Scope and variable errors {#ce1xxx}

These errors are about names, scopes and variables.

### CE1001 {#ce1001}

**Error** · scope

**Message:** `use of undeclared identifier '{name}'`

**Help:**

- `` '{unit}' declares it; add `use "{unit}"` above to name it here ``
- `` '{module}' declares it; add `use <{module}>` above to name it here ``
- `` library '{library}' declares it; add `use <lib/{library}>` above to name it here ``
- `` unit '{unit}' binds the namespace '{name}'; an `unsafe external` block binds its namespace in the unit that writes it ``

The identifier was used before it was declared or is not in scope.

### CE1002 {#ce1002}

**Error** · scope

**Message:** `assignment to undeclared variable '{name}'`

Use 'let' to declare a variable before reassigning it with ':='.

### CE1003 {#ce1003}

**Error** · scope

**Message:** `not allowed here (must be inside a loop).`

Emitted when 'break' or 'continue' appear outside any loop.

### CE1005 {#ce1005}

**Error** · scope

**Message:** `{kind} '{name}' already declared in this unit as {other}`

In one unit, one name has one declaration, whatever its kind: `fn`, `const`, `var`, `struct`, `enum` and `perk` share one set of names. The SECOND declaration in source order is the error, and the note points at the first. The first keeps the name, and the refused declaration enters no table, so the uses of the first give no more errors. A use of the name that finds nothing under it -- a call of a refused function, a type position or a construction of a refused struct -- is a use of the refused declaration, and it gives no second diagnostic either. Two declarations of ONE kind keep that kind's code: [CE0004](#ce0004) (struct), [CE2046](#ce2046) (enum), [CE4001](#ce4001) (perk), [CE0101](#ce0101) (fn) and [CE0105](#ce0105) (const and var), and a struct beside an enum is [CE0006](#ce0006). Across units a name may be used again: the unit's own declaration wins over a name a flat `use` brings, and two imported public names of one spelling are reached with `use ... as` ([CE3012](#ce3012) at a bare use). Two TYPES of one name in two units stay refused, because a type is one per program. Rename one of the two declarations.

### CE1006 {#ce1006}

**Error** · scope

**Message:** `'{name}' is already declared in this scope`

One scope declares a local name one time. A `let`, a destructure binder, a pattern binding, a `foreach` item, an `expand` binder and a parameter share the scope of the body they start, so a `let` in a `foreach` body cannot repeat the item name, and a `let` in a function body cannot repeat a parameter name. The SECOND declaration is the error, and the note points at the first. The first keeps the name. To change the value, write `:=`. To use the name again for a different value, open a nested block: a declaration there shadows the outer one ([CW1002](#cw1002)). A repeated parameter name of a function or a lambda is [CE0102](#ce0102).

## CE20xx and CE21xx: Type, array and struct errors {#ce20xx}

These errors are about types, arrays, structs, enums and generics.

### CE2001 {#ce2001}

**Error** · type

**Message:** `unknown type '{name}'`

**Help:**

- `write its type arguments, as in '{name}@(i32)'`
- `'{name}' is a perk, and a target argument names a type; to constrain a type parameter, write '{base}@(T: {name})'`
- `` '{unit}' declares it; add `use "{unit}"` above to name it here ``
- `` '{module}' declares it; add `use <{module}>` above to name it here ``
- `` library '{library}' declares it; add `use <lib/{library}>` above to name it here ``
- `` no namespace '{namespace}' is bound in this unit; bind one with `use "..." as {namespace}` ``
- `did you mean '{namespace}.{closest}'?`
- `'{unit}' declares no '{name}'`

A declared type is not recognized by the compiler.

### CE2002 {#ce2002}

**Error** · type

**Message:** `type mismatch: cannot assign {got} to {expected}`

The right-hand side expression type does not match the declared or inferred left-hand side type.

### CE2003 {#ce2003}

**Error** · type

**Message:** `return type mismatch: got {got}, expected {expected}`

A function's return expression type does not match its declared return type. A generic body is checked where it is written: a value of a type parameter is not the declared return type `i32`, whatever a call gives for the parameter. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2004 {#ce2004}

**Error** · type

**Message:** `invalid operand types for operator '{op}'`

This is the operand rule of a bitwise operator: & | ^ ~ &lt;&lt; >> combine or move BITS, so every operand must be an integer. A string has none, and a float keeps its own behind f64.to\_bits()/f32.to\_bits(). To work on the bits of a float, convert it first, operate on the integer, and go back through from\_bits().

### CE2005 {#ce2005}

**Error** · type

**Message:** `condition must be bool`

**Help:** `use '== 0' or '!= 0' for integer conditions`

A condition takes a 'bool' and nothing else. Sushi converts no type to a truth value: an integer is not true when it is not zero, and a string is not true when it holds bytes. Write the question instead -- 'n != 0', 's.len() > 0'. This covers every condition position: an if, an elif, a while, an assert, and the operands of the logical operators and, or, xor and not. A Result@(T, E) or a Maybe@(T) in one of these positions is [CE2516](#ce2516) instead, which names the predicate that answers for it.

### CE2006 {#ce2006}

**Error** · type

**Message:** `argument type mismatch at position {index}: expected {expected}, got {got}`

**Help:**

- `convert it with 'as i32'`
- `` borrow it at the call site: `{mode} {place}` ``

A function call argument type does not match the corresponding parameter type. A generic body is checked where it is written: an argument of a type parameter is compared with the parameter of the callee as written, so `T` is not `i32` whatever a call gives for it. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2007 {#ce2007}

**Error** · type

**Message:** `missing type annotation for variable '{name}'`

Variable declaration with 'let' requires an explicit type annotation.

### CE2008 {#ce2008}

**Error** · type

**Message:** `undefined function '{name}'`

**Help:**

- `` '{unit}' declares it; add `use "{unit}"` above to name it here ``
- `` '{module}' declares it; add `use <{module}>` above to name it here ``
- `` library '{library}' declares it; add `use <lib/{library}>` above to name it here ``
- `an iterator has no methods: walk it with 'foreach', or call the method on the collection it comes from`
- `did you mean '{namespace}.{closest}'?`

Function call references a function that was not declared. This is for a name that no unit and no linked library declares: a name a library declares and keeps is [CE3005](#ce3005), on either library kind. It is also the answer for a method call that the receiver type does not have, for every receiver kind. An `Iterator@(T)` has no method at all: `foreach` walks it, and `next()` is the protocol of a user type, not a method of an iterator. A range has one method, `.rev()`; any other method on a range is [CE2122](#ce2122). `0..n.rev()` is the range `0..(n.rev())`, so its method is on the i32 `n`, and that is this error. In a generic body, a method on a type parameter is one that a constraint of the parameter promises, and nothing more. The help names the perks that declare the method; add one of them to the constraints of the parameter: `@(T: Hashable)`. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2009 {#ce2009}

**Error** · type

**Message:** `wrong number of arguments: '{name}' expects {expected}, got {got}`

A call has the wrong number of arguments: a function, a method, a static or a built-in. The text names the callee as written and states the counts. The same code covers every callee kind, the bulk-copy array methods (`extend`, `extend_range`, `s`, `ss`), and the built-in methods and statics of `List@(T)`, `HashMap@(K, V)`, `Own@(T)`, `Maybe@(T)` and `Result@(T, E)`. A call in a generic body is counted where the template is written, also when no call instantiates the template.

### CE2010 {#ce2010}

**Error** · type

**Message:** `array size must be a positive integer literal, got {size}`

Array type declaration requires a positive integer literal for size.

### CE2011 {#ce2011}

**Error** · type

**Message:** `array literal has {got} elements but declared type expects {expected}`

Array literal element count must match declared array size.

### CE2012 {#ce2012}

**Error** · type

**Message:** `array index {index} is out of bounds for array of size {size}`

An index the compiler can read is past the end of a fixed array, in `a[i]`, in `a[i] := v` and in `a.get(i)`. The index is read through the constant evaluator: a literal, a named constant (`a[K]`) and an expression of them (`a[K + 1]`). A local of the same name as a constant shadows it, and an index that names a local is not read. An index that the compiler cannot read is checked at run time ([RE2020](#re2020)). A negative index is [CE2056](#ce2056).

### CE2013 {#ce2013}

**Error** · type

**Message:** `array element type mismatch: expected {expected}, got {got}`

Array literal element type does not match declared array element type.

### CE2014 {#ce2014}

**Error** · type

**Message:** `invalid cast from '{source}' to '{target}'`

**Help:**

- `` a conversion cannot take the generic error type '{generic}'; call a function that takes the error `nom` and answers '{target}' ``
- `` no unit may declare a conversion into '{target}', because it has no home module; call a function that takes the error `nom` and answers '{target}', or use an error type of your own as the target, and declare `extend {source} as <YourError>:` in the unit that declares it ``
- `` only <{home}> may declare `extend {source} as {target}:`; answer an error type of your own, and convert both errors into it ``
- `` declare `extend {source} as {target}:` in the unit that declares '{target}', and return the converted value ``

Type cast is not allowed between the specified types. In a generic body, a cast of a value of a type parameter is refused where the template is written: a type parameter is no numeric type. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2015 {#ce2015}

**Error** · type

**Message:** `constant '{name}' cannot use dynamic array type`

Constants must use compile-time types. A dynamic array is not allowed at any depth of an array type: `i32[]`, `i32[][2]` (a fixed array of dynamic rows) and `i32[2][]` are all refused. A fixed array of fixed rows (`i32[2][3]`) is a constant. A `var` can hold a dynamic array.

### CE2017 {#ce2017}

**Error** · type

**Message:** `invalid repeat count in an array literal: {reason}`

A repeated element is 'value; count', and the count is a count of elements: a positive integer the compiler can read. That is a literal in any base, the name of an integer constant, or an expression of them -- the same reader a fixed array size uses. One code covers every way it can go wrong, as [CE2099](#ce2099) does for an array size. A count of zero spells nothing and Sushi has no zero-length array, so the lower bound is one. The count is read at the typecheck pass, so unlike an array size it may name a constant of ANOTHER unit.

### CE2019 {#ce2019}

**Error** · type

**Message:** `invalid range in an array literal: {reason}`

A range element fills the slots it spans: '0..5' is five elements and '0..=5' is six. A range always goes up, as in `foreach`, and '(0..5).rev()' fills the same five values, last first. A range whose readable bounds go down is [CE2125](#ce2125). One code carries every way a range cannot fill slots, the way [CE2017](#ce2017) carries every bad repeat count, because they share one rule and one fix. Two ways: a bound the compiler cannot read in a position that needs a readable LENGTH -- a fixed array, whose length is part of its type, and a constant, whose evaluator needs the values -- and a readable range that yields nothing, because Sushi has no zero-length array and '3..3' spells nothing. The escape for the first is `from()`, which carries its length in the descriptor and accepts any i32 expression as a bound. A range yields i32 ([CE2121](#ce2121) for a bound of any other type), and it cannot carry a repeat count ([CE2020](#ce2020)).

### CE2020 {#ce2020}

**Error** · type

**Message:** `a range element cannot carry a repeat count`

'value; count' repeats ONE value, and a range is not one value: it is already a sequence. '\[0..2; 3\]' has no reading a person would agree on -- three copies of the span, or a span of three -- so it is refused rather than given one. Write the repeat out as a plain element ('\[0, 1, 0, 1\]'), or use a range alone. [CE2017](#ce2017) is the code for a count that is wrong; this one is for a count that has nothing to repeat.

### CE2023 {#ce2023}

**Error** · type

**Message:** `dynamic array method {part} mismatch for '{method}': expected {expected}, got {got}`

A built-in array method was called on a receiver it does not take, or with a copy source of the wrong type. `{part}` names which one is at fault: the RECEIVER when the method takes a dynamic array (`push`, `pop`, `insert`, `truncate`) or a `u8[]` alone and the value is another kind -- a fixed array cannot change its length -- and the ARGUMENT when the source of a bulk copy (`extend`, `extend_range`) is not an array of the receiver's element type, or the argument of `extend_str` is not a string.

### CE2026 {#ce2026}

**Error** · type

**Message:** `unterminated interpolation in string literal`

String interpolation braces must be properly closed with '\}'.

### CE2027 {#ce2027}

**Error** · type

**Message:** `struct '{name}' expects {expected} field(s), got {got}`

Struct constructor must provide exact number of fields.

### CE2028 {#ce2028}

**Error** · type

**Message:** `field '{field_name}' expects type '{expected}', got '{got}'`

Struct constructor field type mismatch.

### CE2030 {#ce2030}

**Error** · type

**Message:** `return statement must use Ok() or Err()`

**Help:** `wrap return value: return Result.Ok(value)`

Every return in a body that has an error channel spells its constructor: 'return Result.Ok(value)' or 'return Result.Err(e)'. The rule holds for a function, a lambda block body, and an extension or perk-impl method with a '| E' channel alike: nothing wraps a bare value, and a '~' success is 'return Result.Ok(~)'. A BARE body (no '| E') is the other way round ([CE2091](#ce2091)).

### CE2031 {#ce2031}

**Error** · type

**Message:** `Ok() value type mismatch: expected '{expected}', got '{got}'`

The value inside Ok() must match the function's return type.

### CE2032 {#ce2032}

**Error** · type

**Message:** `blank type (~) can only be used as function return type`

Blank type cannot be used for variables, parameters, or constants.

### CE2033 {#ce2033}

**Error** · type

**Message:** `foreach needs something to walk, and '{got}' is neither an iterator nor a type with next()`

Two things are walkable. An ITERATOR, which is what .iter() on an array or a List answers, what .keys() / .values() / .entries() answer on a HashMap, and what a range is. Or any type carrying a method 'next()' that answers Maybe@(T): the loop calls it until it answers None, and that is the whole protocol -- there is no type to implement and no perk to name. So the fix is one of three: call .iter() on the container, give this type a next(), or check the spelling of the next() it has. These spellings are refused, each because the loop must be able to call the method repeatedly and read a stop out of its answer: a next() answering a bare T rather than a Maybe@(T) cannot say when to stop; one declaring '| E' answers a Result and not a Maybe; one taking arguments has nothing to be handed; and a 'nom self' receiver answers once and spends the iterator. A fallible iterator puts the failure IN the item instead: Maybe@(Result@(T, E)).

### CE2034 {#ce2034}

**Error** · type

**Message:** `foreach item type mismatch: expected '{expected}', got '{got}'`

The declared item type in foreach does not match the iterator's element type.

### CE2035 {#ce2035}

**Error** · type

**Message:** `cannot interpolate expression of type '{type}' into string`

An interpolation hole takes a value with a string form: an integer, a float, a bool, a string, or a struct, an enum, an array, a `List@(T)` or an `Own@(T)` through the predefined perk `Display`. The compiler derives `Display` from what a type holds -- `Point(x: 1, y: 2)`, `Shape.Circle(5)`, `[1, 2, 3]` -- and `extend T with Display: fn to_str() string` overrides it. A type that holds something with no string form (a function value, a `ptr`, a `HashMap`) has none, and a note names the field or the element. A `HashMap` itself is refused, because its iteration order is not specified. A `Maybe` and a `Result` are not printed: handle the missing value or the error first. In a generic body, a hole of a type parameter needs the constraint `Display` on that parameter: `@(T: Display)`. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2036 {#ce2036}

**Error** · type

**Message:** `Ok() requires a value. For blank return type use Ok(~)`

Empty Ok() is not allowed. Use Ok(value) for regular returns or Ok(~) for blank type returns.

### CE2037 {#ce2037}

**Error** · type

**Message:** `cannot print Result@(T, E) directly (use .realise() to unwrap first)`

Result@(T, E) must be explicitly handled before printing. Use .realise(default) to extract the value.

### CE2038 {#ce2038}

**Error** · type

**Message:** `empty interpolation in string literal`

String interpolation braces must contain an expression (e.g., "\{value\}" not "\{\}").

### CE2039 {#ce2039}

**Error** · type

**Message:** `Err() error type mismatch: expected '{expected}', got '{got}'`

The error value inside Err() must match the function's error type.

### CE2040 {#ce2040}

**Error** · type

**Message:** `non-exhaustive match pattern (missing variants: {variants})`

A match must have an arm for every value of its scrutinee. One checker reads every match: an enum match, a nested enum match and a tuple match (see [the tuple design](design/tuples.md)). It is the usefulness algorithm over a pattern matrix: an enum position splits into its variants, a tuple position into its elements, and an integer position into intervals of its type, cut at the bounds of the literals and the ranges of the arms. The arms cover an integer position when they cover each interval, so `Maybe.Some(0..=127) -> ...` and `Maybe.Some(-128..=-1) -> ...` cover a `Maybe@(i8)` payload; else a `_` or a binding covers it. A string position has no end of values, so only a `_` or a binding covers it. The `{variants}` slot lists what is missing. For a plain enum match, where no arm tests inside a payload, it lists the names of the missing variants (`Blue, Green`). When an arm tests inside a payload or a tuple, it lists the missing PATTERNS in source syntax: `Maybe.Some(Color.Green)`, `(Color.Red, _)`, `(10, _)`, at most 16 of them. An integer position in a missing pattern is a value or a range of values that no arm matches (`Maybe.Some(128..=255)`). Add an arm for each, or a `_` arm last. An integer or a string scrutinee that the arms do not cover is [CE2074](#ce2074).

### CE2041 {#ce2041}

**Error** · type

**Message:** `duplicate match arm for variant '{variant}'`

The same enum variant cannot be matched more than once. An alternative is an arm for this rule: `Color.Red | Color.Red`, and an alternative that names a variant an arm above already matches, are this error at that alternative.

### CE2044 {#ce2044}

**Error** · type

**Message:** `wrong number of pattern bindings: variant '{variant}' expects {expected}, got {got}`

Pattern must bind the exact number of variables for the variant's associated data.

### CE2045 {#ce2045}

**Error** · type

**Message:** `enum variant '{variant}' not found in enum '{enum}'`

**Help:** `a name behind an enum's dot is a variant or a static method: add the variant, or declare 'extend {enum} static {variant}(...)'`

The specified variant does not exist in the enum type. An enum's dot holds TWO kinds of member -- a variant, and a static method -- so the help names both escapes: add the variant, or declare the name as a static. It is still one namespace: a variant and a static of one name on one enum is [CE2103](#ce2103), because the variant would always win.

### CE2046 {#ce2046}

**Error** · type

**Message:** `duplicate {word} '{name}'`

Two enums share the same name in a compilation unit. An `error` declaration is an enum with a flag, and the header calls it an error type.

### CE2047 {#ce2047}

**Error** · type

**Message:** `duplicate variant '{name}' in enum '{enum_name}'`

An enum declares the same variant name more than once.

### CE2048 {#ce2048}

**Error** · type

**Message:** `match scrutinee must be an enum, integer or string type, got '{got}'`

A match dispatches on an enum's variants, on an integer's value with literal arms, or on a string's value with string literal arms. A tuple scrutinee is legal too. Other types have no match semantics. This is the SCRUTINEE's rule and nothing else. An arm or a nested pattern that names another enum is [CE2107](#ce2107), a nested pattern over a payload that is not an enum is [CE2108](#ce2108), and an `Own(...)` pattern over a value that is not an `Own@(T)` is [CE2109](#ce2109).

### CE2049 {#ce2049}

**Error** · type

**Message:** `enum constructor argument type mismatch for variant '{variant}': expected '{expected}', got '{got}'`

Enum variant constructor argument type does not match the expected associated data type.

### CE2050 {#ce2050}

**Error** · type

**Message:** `enum variant '{variant}' expects {expected} argument(s), got {got}`

Enum variant constructor must provide exact number of arguments for associated data.

### CE2054 {#ce2054}

**Error** · type

**Message:** `HashMap@(K, V) key type '{key_type}' does not support hashing (missing .hash() method)`

HashMap keys must support hashing. Use types that have .hash() method (primitives, strings, structs with hashable fields, enums, arrays). In a generic body, a key of a type parameter needs the constraint `Hashable` on that parameter.

### CE2055 {#ce2055}

**Error** · type

**Message:** `HashMap@(K, V) key type '{key_type}' does not support equality comparison`

HashMap keys must support equality comparison (==). This is required for collision resolution. The probe compares two keys through the `Eq` contract: an `extend K with Eq` implementation, else the equality the compiler derives from what the key holds. A function value, a `ptr` and a `HashMap` have no equality, and neither does a type that holds one. A `Hashable` override gives a hash only; it does not make a type comparable, so a key needs both halves: implement `Eq` beside it. In a generic body, a key of a type parameter needs the constraint `Eq` on that parameter.

### CE2056 {#ce2056}

**Error** · type

**Message:** `array index {index} is negative (indices must be >= 0)`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Array indices must be non-negative. Negative indices are not supported. The index is read as [CE2012](#ce2012) reads it: a literal (`a[-1]`), a named constant and an expression of them.

### CE2057 {#ce2057}

**Error** · type

**Message:** `array index {index} out of bounds for array of size {size}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

Array index exceeds array bounds. This error is caught at compile-time for constant indices.

### CE2058 {#ce2058}

**Error** · type

**Message:** `HashMap@(K, V) key type '{key_type}' is not comparable (dynamic arrays cannot be HashMap keys)`

Dynamic arrays are not allowed as HashMap keys due to memory management constraints, at any depth of an array type: `i32[]`, `i32[2][]` and `i32[][2]` (a fixed array of dynamic rows) are all refused. Use fixed-size arrays instead (e.g., i32\[3\] instead of i32\[\]); a fixed array of fixed rows (`i32[2][2]`) is a key.

### CE2060 {#ce2060}

**Error** · type

**Message:** `cannot infer type arguments for generic function '{name}': {reason}`

**Help:**

- `bind the result to a declared type ('let {base}@(...) x = {base}.{method}(...)'), or name {params} in a parameter so the argument solves it`
- `a static whose return does not name '{base}' has no declared type to read from -- name {params} in a parameter, or make it a generic free function ('fn {method}@(T)(...)'), which takes explicit type arguments`

Type inference failed for a generic call. A generic FREE function solves its type parameters from its arguments, and a parameter named only in the return has no source -- spell the type arguments (`f@(i32)()`). A generic STATIC solves them in two steps, as one resolution: from the arguments, for every target type parameter a parameter names, then from the declared type at the binding site for the rest. A parameter neither step reaches is this error, and the text names both sources and the parameter. When the unifier knows why an argument does not fit, the reason names it: a fixed-size parameter takes an array of that size alone, so `T[3]` against an `i32[4]` says 'the parameter is 'T\[3\]', the argument is 'i32\[4\]''.

### CE2061 {#ce2061}

**Error** · internal

**Message:** `monomorphized function '{mangled}' not found for '{name}' with type arguments {type_args}`

Internal compiler error: monomorphized function missing from function table.

### CE2062 {#ce2062}

**Error** · type

**Message:** `generic '{name}' expects {expected} type argument(s), got {got}`

A `@(...)` type-argument list does not give the generic the count it declares. One rule for every position: an explicit call-site list (`id@(i32, i32)(1)`; explicit type arguments are all-or-nothing), a written type (`let Box@(i32, i32) b`, a parameter, a field, a payload), and an extension or perk-implementation target (`extend Box@(T, U)`).

### CE2063 {#ce2063}

**Error** · type

**Message:** `cannot infer method type parameter{plural} {names} for '{method}' from this call`

**Help:**

- `annotate the lambda's parameter types ('|i32 x| ...'), or pass a named function -- a bare-param lambda has no type of its own to infer from`
- `no argument of this call has a type that holds {it_or_them}; a method call has no '@(...)' slot, so write the type parameter in a parameter type`

A method-level type parameter (`extend List@(T) mapv@(U)(...)`) is inference-only: there is no call-site `@(...)` slot on a method call, so every parameter must be solvable from the arguments. Two shapes cannot be solved, and the help names the one that applies. The bare-param lambda: `xs.mapv(|x| x * 2)` gives the lambda no type of its own, so nothing unifies against `fn(T) -> U`; the escape is to annotate the lambda's parameter -- `xs.mapv(|i32 x| x * 2)` -- or to pass a named function. And a type parameter that no parameter type holds: no argument can solve it, so the help says to write it in a parameter type. A written parameter type is resolved before the solve, so `|P p|` over a struct or an enum `P` solves as `|i32 x|` does.

### CE2064 {#ce2064}

**Error** · type

**Message:** `method type parameter '{name}' shadows a type parameter of the extension target`

The receiver target's bare names (`extend Box@(T)`, `extend T[]`) and the method's own `@(...)` list share one namespace inside the body, so a repeated name would make `T` mean two types in one signature. Rename the method-level parameter. The rule mirrors [CE2097](#ce2097)'s spirit: a declaration that could only ever mislead is refused where it is written.

### CE2065 {#ce2065}

**Error** · type

**Message:** `cannot infer the type of '{constructor}': {reason}`

**Help:** `declare the type first, e.g. 'let {example} v = {constructor}', then use 'v'`

A generic struct constructor in a position with no declared type solves its type parameters from its arguments, as a generic function call does: `Box(5)` is `Box@(i32)`. Each argument is unified with the field it fills, through the one leading solver. Two arguments that give one type parameter two types cannot be solved: `Both(1, "x")` over `T left` and `T right` gives `T` the types `i32` and `string`, and the reason names the field and the argument that disagree. A literal takes no type from its sibling here, the rule of a generic call (`same(big, 1)` is [CE2060](#ce2060)). Declare the type first, `let Both@(i64) b = Both(big, 1)`: a declared type gives the type arguments and each argument is then checked against its field. A constructor whose arguments give a type parameter nothing is [CE2112](#ce2112).

### CE2070 {#ce2070}

**Error** · type

**Message:** `{radix} literal {literal} overflows {type}`

The literal value is too large to fit in the target integer type. Use a wider integer type or reduce the value.

### CE2071 {#ce2071}

**Error** · type

**Message:** `C-style octal literal '{literal}' is not supported. Use '0o' prefix instead (e.g., 0o{octal})`

Leading zero octals (like 077) are ambiguous and error-prone. Use explicit 0o prefix instead.

### CE2072 {#ce2072}

**Error** · type

**Message:** `range expression requires integer types for start and end bounds. Got {got}, expected {expected}`

A range bound is an i32 position, as an index is. This code is for a bound that is not a number at all (a string, a bool). A number of another type -- an i8, an i64, an f64 -- is [CE2002](#ce2002) with the help 'as i32'; a bare literal takes i32 from the position. A range in a match PATTERN (`0x80..=0x8f ->`) takes an integer literal as each bound, and a string bound there (`"a".."z" ->`) is this error at that bound: a string has no order that a pattern can read.

### CE2073 {#ce2073}

**Error** · type

**Message:** `literal {literal} out of range for {type}`

The literal does not fit the target type's range. Use a wider type, or an explicit 'as' cast if you intend the bit pattern. A literal arm of a match and each bound of a range arm (`0..=255 ->`) take the type of the value they read, by the same rule: on a u8, `-1..=5` and `0..256` are this error at the bound that does not fit.

### CE2074 {#ce2074}

**Error** · type

**Message:** `non-exhaustive {kind} match: no arm matches {missing} (add a trailing '_' arm)`

A match must have an arm for every value of its scrutinee. The arms of an integer match cover the type when their literals and ranges together hold every value of it, from the lowest to the highest: `0x00..=0x7f` and `0x80..=0xff` cover a u8, and that match needs no `_` arm. Else the match ends with a `_` arm. The `{missing}` slot names the FIRST value that no arm matches, in decimal and in the base of the first integer arm (`the value 128 (0x80)`), so the gap is visible. The values of a string cannot be listed, so a string match always ends with a `_` arm, and the slot reads `every other string`. The `{kind}` slot names the scrutinee kind: integer or string.

### CE2075 {#ce2075}

**Error** · type

**Message:** `duplicate literal match arm: value {value} is already matched by arm '{first}'`

Two literal arms match the same VALUE. For an integer, the radix does not change the value: 0x2a and 42 are the same arm. For a string, the quotes do not change the value, and the value is read after escape processing: "a" and 'a' are the same arm, and "\t" and a literal tab are the same arm. The `{value}` slot prints a string value with its quotes. The second arm is unreachable. An alternative is an arm for this rule: `"a" | 'a'` in one arm, and `2 | 0x1` after an arm `1`, are this error at the second alternative, in a nested list of alternatives too (`Maybe.Some(1 | 1)`). A non-decimal literal is a bit pattern of the scrutinee type, so on an i8 `0xff` and `-1` are one value and one arm. A RANGE arm (`0x80..=0x8f`) matches each value from its start to its end, and a range or a literal that shares a value with an arm above is this error: the `{value}` slot names the FIRST value in both, in decimal and in the written base (`143 (0x8f)`), and `{first}` names the arm above. One fault gives one diagnostic, so the code depends on what the pattern adds. A literal whose value an arm above matches is this error, because it is a repeated value. A range that shares some values with the arms above and has values of its own is this error. A range whose every value the arms above match adds nothing: it is a dead arm, [CE2118](#ce2118), and not this error.

### CE2076 {#ce2076}

**Error** · type

**Message:** `match arm does not fit the scrutinee: {arm_kind} arm on a '{scrutinee_type}' scrutinee`

An integer literal arm needs an integer scrutinee, a string literal arm needs a string scrutinee, and an enum pattern arm needs an enum scrutinee. One match cannot mix these arm kinds: a string arm on an integer match, an integer arm on a string match, and a literal arm on an enum match are all this error. A literal NESTED in a pattern that reads a value of the wrong kind is [CE2119](#ce2119).

### CE2077 {#ce2077}

**Error** · type

**Message:** `operator '{op}' gives {value}, which is out of range for {type}`

**Help:** `use a wider type, or compute in one and cast the result with 'as'`

An expression whose value the compiler reads is computed at the declared width, and a result that leaves the type is reported. For example, a u8 constant of '200 + 100' is this error. The overflow-checked operators are + - \* / % and unary minus; & | ^ ~ &lt;&lt; >> are width-defined and never report, because the bits that leave the width are lost by design. The escape is a wider type, or an explicit 'as' cast when the bit pattern is what you want. Run time does not change: two locals still wrap.

### CE2080 {#ce2080}

**Error** · type

**Message:** `unknown field '{field}' for struct '{struct}'`

Named struct constructor field name does not exist in struct definition.

### CE2081 {#ce2081}

**Error** · type

**Message:** `duplicate field '{field}' in struct constructor`

Field name appears more than once in named struct constructor.

### CE2082 {#ce2082}

**Error** · type

**Message:** `missing required field(s) '{fields}' for struct '{struct}'`

Named struct constructor must provide all required fields.

### CE2083 {#ce2083}

**Error** · type

**Message:** `field '{field}' expects type '{expected}', got '{got}'`

Named struct constructor field type mismatch.

### CE2084 {#ce2084}

**Error** · type

**Message:** `'{type_name}' is {kind}, not an error type`

**Help:** `declare '{name}' with 'error' in place of 'enum'`

The `E` of every `Result@(T, E)` is an error type: an enum declared with `error`, or one of the seven predefined error types (see [the error-conversion design](design/error-conversion.md)). A `Result` is the value of an error channel, and only an error fits into an error channel. The rule covers both spellings, because `T | E` is sugar for `Result@(T, E)`. It covers every position: the channel of a function, a method, a perk contract, a perk implementation, a lambda and a function type, and also a `let`, a field, a payload, a parameter and a generic argument, at any depth. The diagnostic points at the written type that holds the `E`. A type parameter in the `E` position is judged at each instance: the diagnostic is at the instance and names the type argument, and a note points at the template. A `Result` that the compiler infers is not judged, because the position that it comes from was judged. A plain enum, a struct, a primitive, an array, a function type, `Maybe` and `Result` are refused, and the message says which one the type is. For a plain enum, the help says to declare it with `error`. A name that spells nothing stops at [CE2001](#ce2001).

### CE2085 {#ce2085}

**Error** · type

**Message:** `cannot use '| {err_type}' syntax with explicit Result@(T, E) return type`

When using explicit Result@(T, E) syntax, the error type is already specified. Remove the '| ErrorType' syntax or use implicit return type.

### CE2090 {#ce2090}

**Error** · type

**Message:** `type-pack element {index} of type '{ty}' does not satisfy constraint '{perk}'`

Each element type bound to a perk-constrained type-pack '...Ts: Perk' must implement the required perk. In a generic body, a type parameter or an element of a pack passes the constraint of a pack on only when its own constraints promise it, and the help names the constraint to add.

### CE2091 {#ce2091}

**Error** · type

**Message:** `{callable} must use a bare 'return <value>' ('return ~' in a '~' body), not 'return Result.Ok(...)' or 'return Result.Err(...)'`

A BARE function, lambda, or extension or perk-impl method (no '| E' and no Result@(T, E) return) has an unwrapped ABI: it answers the value itself and no Result, so both Result constructors are refused. See [the error-channel design](design/error-channel.md). A callable with a '| E' channel spells 'return Result.Ok(x)' and 'return Result.Err(e)', and a bare 'return x' there is [CE2030](#ce2030). A bare function that can fail writes '| E'.

### CE2092 {#ce2092}

**Error** · type

**Message:** `function value type mismatch: expected '{expected}', got '{actual}'`

**Help:** `` a borrow is created where it is USED, so the argument is written `{mode} {name}`; a reference-typed name mentioned bare is its referent ``

A first-class function value must match the expected function type exactly: same arity, parameter types, return type, and error type (function types are invariant).

### CE2093 {#ce2093}

**Error** · type

**Message:** `cannot take a function value of '{name}': {reason}`

**Help:**

- `state a function type whose parameter types solve each type parameter of '{name}'`
- `state the function type at the position, for example 'let fn(i32) -> i32 g = {name}'`

A top-level function is a function value. A generic function is a function value only where the position states a function type that solves its type arguments: a typed `let` (`let fn(i32) -> i32 g = identity`), a parameter of a function type, or a typed field. A position that states no function type (a `print` argument, a parameter of an unsolved generic callee), or a function type that does not solve the type arguments, is this error. An extension method, a perk method and an FFI external are not function values.

### CE2094 {#ce2094}

**Error** · type

**Message:** `illegal closure capture: {reason}`

A closure captures by value (copy) or by move (owned types). It cannot capture a borrow (peek/poke). A function-value parameter type that owns a resource or is variadic is also refused.

### CE2095 {#ce2095}

**Error** · type

**Message:** `recursive type '{name}' has infinite size: {chain}`

A type that contains itself by value has no finite size. Every hop in the reported chain stores its target inline -- a struct field, a fixed-size array element, or an enum payload. Break the cycle with indirection: Own@(T) for a single value, or a dynamic array / List@(T) for many. Compare Rust's E0072 and Go's "invalid recursive type".

### CE2096 {#ce2096}

**Error** · type

**Message:** `cannot {what} constant '{name}': constants are immutable`

A constant is emitted as a read-only global (.rodata), so a write that would reach it -- an in-place method, or an indexed assignment -- cannot target one; the store would be undefined behaviour rather than a diagnostic. Copy the constant into a local first and mutate that.

### CE2097 {#ce2097}

**Error** · type

**Message:** `extension method '{name}()' conflicts with the built-in '{type}.{name}()'`

**Help:** `a built-in method is always chosen before an extension method, so this one could never be called -- rename it, or provide '{method}()' through a perk implementation ('extend {type} with <Perk>'), which does take precedence`

Method resolution always considers built-in methods before extension methods -- during type validation, during type inference, and again during code generation -- so an extension method whose name collides with one is compiled and then never called. The built-in families are: the hash() and clone() the compiler derives for every struct and enum; the primitive and string methods (to\_str, hash, to\_bits, len, trim, ...); the array methods; and the methods of the built-in containers Result, Maybe, Own, List and HashMap. A perk implementation is the supported way to replace a built-in: it takes precedence at every layer, by design.

### CE2098 {#ce2098}

**Error** · type

**Message:** `{kind} target '{target}' mixes concrete type arguments with type parameters`

**Help:** `name every type parameter, or make every argument concrete -- there is no partial specialization`

An extension target names either every type parameter -- `extend Box@(T)`, which applies to every instantiation -- or a concrete type for every argument -- `extend Box@(i32)`, which applies to that instantiation alone. A partial form such as `extend Pair@(i32, U)` is partial specialization, and Sushi has none. Name every parameter, make every argument concrete, or implement a perk on the concrete target. A perk implementation's target reads the same rule and the same code.

### CE2099 {#ce2099}

**Error** · type

**Message:** `invalid size '{size}' for a fixed array: {reason}`

**Help:**

- `declare an integer constant in this unit and name it bare`
- `write a positive integer in any base (256, 0x100, 0b1_0000_0000) or the name of an integer constant declared in this unit`

A fixed array's size is a count of elements, so it must be a positive integer the compiler can read: a literal in any base (256, 0x100, 0b1\_0000\_0000, 0o400) or the name of an integer constant. One code carries every way it can go wrong, because they share one rule and one fix. The size is read while the unit's AST is built, so the constant must be declared in the SAME unit -- a constant next door is reachable as a value but not as a size. A constant of this unit that is not an integer (a `f64`, a `bool`) cannot count elements, and the reason names its type.

### CE2100 {#ce2100}

**Error** · type

**Message:** `'{method}' needs an element type with equality: {reason}`

**Help:** `put the row in a struct ('struct Row: {type} cells'), which takes a derived 'Eq', and search a 'Row[]'`

contains() and index\_of() compare the needle against each element with '==', and starts\_with() and eq\_range() compare two arrays element by element with '=='. The element type must be one that '==' accepts ([CE2514](#ce2514) is the operator half of the same rule): a numeric type, bool, string, or a struct or an enum with a derived or implemented `Eq`. A closure element, or a struct that holds one, has no '==', so a search over it has no meaning the compiler could supply. Implement `Eq` for the element (`extend T with Eq: fn eq(T other) bool`), or search an array of the identifying part instead. An array element (fixed or dynamic, at any depth) is refused too, because an array has no '==' at the top level ([CE2514](#ce2514)). An array cannot implement `Eq`, so the escape is a struct that holds the row (`struct Row: i32[2] cells`): the struct takes a derived `Eq` that compares the row element by element, and a `Row[]` can be searched. In a generic body, a search in an array or a List of a type parameter needs the constraint `Eq` on that parameter.

### CE2101 {#ce2101}

**Error** · type

**Message:** `invalid element '{element}' in an array extension target`

**Help:** `write a bare type-parameter name ('extend T[]') or a plain declared type ('extend i32[]'); 'extend T[]' also applies to a nested array, with T the inner array type`

An array extension target's element position takes exactly two spellings: a bare undeclared name, which binds a type parameter (`extend T[]` applies to every element type), and the name of a plain declared type (`extend i32[]`, `extend Crate[]`), which applies to that array type alone. A generic instantiation (`extend Maybe@(T)[]`) has no meaning here -- the parameter would bind through two layers. A nested array element (`extend T[][]`, `extend i32[][]`, `extend i32[3][]`) is refused for the same reason, and it is not necessary: `extend T[]` binds `T = i32[]` and applies to an `i32[][]` receiver, and to an `i32[3][]` one with `T = i32[3]`.

### CE2102 {#ce2102}

**Error** · type

**Message:** `'{type}' has no static method '{method}'`

**Help:**

- `'{method}' is an instance method here: call it on a value of '{type}', not on the type name`
- `declare it as 'extend {type} static {method}(...)'`

A name behind a type's dot is a MEMBER of that type: a variant, or a static method. This type declares neither of that name. The fault is the POSITION and not the name. Declare the method as `extend {type} static {method}(...)`, or call an instance method on a value of the type.

### CE2103 {#ce2103}

**Error** · type

**Message:** `static method '{method}' collides with a variant of enum '{enum}'`

**Help:** `a name behind a type's dot is a variant or a static, never both -- rename the static`

One namespace sits behind a type's dot: a name there is a variant or a static method, never both. A variant always wins at the call site, so a static of that name is compiled and then never called -- the hazard [CE2097](#ce2097) refuses for a built-in. Relational: the primary sits at the static declaration and a note at the variant. Rename the static.

### CE2104 {#ce2104}

**Error** · type

**Message:** `a static method cannot be declared on an array target`

**Help:** `write a free function, or a static on a struct that holds the array`

A static is called on the TYPE name, and an array type has no spelling in an expression position: `i32[].two()` is a parse error, and there is no form that would reach `extend i32[] static two()` or `extend T[] static two()`. The declaration would compile and never be callable, which is the hazard [CE2097](#ce2097) refuses for a colliding built-in. Write a free function, or a static on a struct that holds the array.

### CE2105 {#ce2105}

**Error** · type

**Message:** `'{name}' is a type, not a value`

**Help:**

- `` a value of an enum is one of its variants: `{name}.{variant}` ``
- `a value of an enum is one of its variants`
- `` a value of a struct is a construction or a static: `{name}(...)` ``

A type name is written in a TYPE position -- a declaration, an annotation, a constraint -- and behind its own dot, where it names a member. It is not a value, so a value position cannot hold it. The fault is the POSITION and not the name. Write a value of the type: a variant for an enum, a construction or a static for a struct.

### CE2106 {#ce2106}

**Error** · type

**Message:** `'{type}' has no field '{field}'`

**Help:**

- `call it: write the parentheses`
- `take the value first with '??', '.realise(default)' or match`
- `take the value first with '.realise(default)', match, or '.or_err(nom e)??'`
- `read a payload with 'match'`
- `did you mean '{field}'?`
- `'{type}' declares {fields}`

A name behind a VALUE's dot is a field of that value's type, and this type declares no such field -- [CE2102](#ce2102) is the same rule one position over, behind a TYPE's dot. A METHOD is not a field: `v.name` with no parentheses reads a field, and Sushi has no bound-method value, so write the call. The rule covers every receiver: a struct, and a receiver that carries NO field -- an array, a primitive, a string, a closure, a `ptr`. A compiler-defined method, such as `s.len`, gets the same note as a method of a struct. An ENUM receiver is this error too: an enum carries variants, and a variant is reached by a pattern and not by a dot. So the help says to take the value first: `??`, `.realise(default)` or `match` for a Result, `.realise(default)`, `match` or `.or_err(nom e)??` for a Maybe (`??` takes a Result only), `match` for a user enum. A `Maybe@(T)` gets no implicit unwrap, for the reason a condition is a bool and nothing else, and because the `None` arm has no answer. A type parameter has no field: a constraint promises methods and nothing else, so a generic body cannot read a field of a value of a type parameter. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2107 {#ce2107}

**Error** · type

**Message:** `pattern matches enum '{got}', but the value has type '{expected}'`

A pattern names the enum it destructures, and the value it reads is of another type. This is the outer arm's rule and the nested pattern's rule alike: `Other.Alpha ->` against a `Shape` scrutinee, and `Outer.Wrap(Other.Alpha)` against a payload the variant declares as `Inner`. It is relational, so the note points at the value -- the scrutinee for an outer arm, the variant that declares the payload for a nested one.

### CE2108 {#ce2108}

**Error** · type

**Message:** `nested pattern needs an enum value, got '{got}'`

A nested pattern destructures a variant's payload, so the payload must be an enum. `Box.Held(Other.Alpha)` over a `Held(i32)` reads this, and so does the same shape one level down inside an `Own(...)` pattern -- one rule, two positions. An element of a tuple pattern is a third position: `(Color.Red, n)` over a `(bool, i32)` reads this too.

### CE2109 {#ce2109}

**Error** · type

**Message:** `Own(...) pattern needs an Own@(T) value, got '{got}'`

An `Own(...)` pattern reads through an owning pointer, so the value it reads must be an `Own@(T)`. A plain payload is not one, and neither is a malformed `Own@(i32, i32)`, whose payload cannot be read -- that one arrives behind a [CE2001](#ce2001) for the type itself.

### CE2110 {#ce2110}

**Error** · type

**Message:** `a {kind} cannot be an extension or perk-implementation target: '{target}'`

An extension names a type that the extension table can key on: a primitive, an array, a struct or an enum. A function type and a tuple type are structural, and an extension on one is refused. For a tuple, write a struct with named fields and extend that, or a free function that takes the tuple (see [the tuple design](design/tuples.md)). The refusal at the target is the one diagnostic: the body is not checked and a call of the method adds nothing. A perk implementation follows the same rule: `extend fn(i32) -> i32 with P` is refused too. A method a function value carries is built in (`.clone()`). Write a free function that takes the function value as a parameter.

### CE2111 {#ce2111}

**Error** · type

**Message:** `cannot infer the element type of an empty {form}`

An empty `from([])` or a `new()` spells no element, so it takes the element type of its POSITION: a `let`, a field, a payload, a parameter, a `.realise()` default, a return. A position with no type -- a method receiver, an index base, a `println` argument -- gives it nothing, and no element can be read. Declare the array first: `let i32[] xs = from([])`, then use `xs`.

### CE2112 {#ce2112}

**Error** · type

**Message:** `cannot infer the type of '{constructor}': nothing gives {params}`

**Help:** `declare the type first, e.g. 'let {example} v = {constructor}', then use 'v'`

A generic enum constructor takes its instance from the position that holds it: a `let`, a `return`, a parameter, a field, a payload. A position with no declared type -- a match scrutinee, a method receiver, a `??` operand, a foreach iterable, an interpolation hole, an expression statement, a generic argument -- gives it nothing, so the arguments give the type: `Maybe.Some(1)` is `Maybe@(i32)` and `Slot.Full(7)` is `Slot@(i32)`. A type parameter that no argument gives has no source: the error type of `Result.Ok(1)`, the Ok type of `Result.Err(e)`, the type of `Maybe.None()`. There is no default, and a missing error type is not `StdError` (the rule of Rust E0282 and of Swift). Declare the type first, `let Maybe@(i32) m = Maybe.None()`, then use `m`. A generic STRUCT constructor follows the same rule from its fields: `Box(5)` is `Box@(i32)`, and a type parameter that no field argument gives is this error (`struct Marker@(T)` with only an `i32` field). Two arguments that disagree are [CE2065](#ce2065).

### CE2113 {#ce2113}

**Error** · type

**Message:** `` a string is immutable: `s[i]` reads a byte and cannot be written ``

A string gives its bytes in place for a read: `s[i]` answers the `u8` at byte offset `i`, bounds-checked like an array index. A write through it is refused. A string bound from a literal lives in `.rodata`, so a store there is undefined behaviour, and a string is immutable by design in every other position too. Build a new string instead: copy the bytes with `s.to_bytes()`, change the `u8[]`, and hand it over with `string.from_bytes(nom b)`, which takes the buffer with no second copy.

### CE2114 {#ce2114}

**Error** · type

**Message:** `` a value of type {type} is not indexable: only an array or a string takes `[i]` ``

An index reads an element of an array (`T[N]`, `T[]`) or a byte of a string, and nothing else. A `List@(T)` answers `.get(i)` (a `Maybe@(T)`), a `HashMap@(K, V)` answers `.get(key)`.

### CE2115 {#ce2115}

**Error** · type

**Message:** `cannot print a value of type '{type}'`

`print` and `println` take what an interpolation hole takes ([CE2035](#ce2035)): an integer, a float, a bool, a string, or a struct or an enum through the predefined perk `Display`. An array, a `List@(T)` and an `Own@(T)` print as a type that holds them prints them (`[1, 2, 3]`, the payload of an `Own@(T)`), and a `HashMap` is refused, because its iteration order is not specified. A `Maybe` must be handled first -- `match` it, or take the value with `.realise(default)`. A type that holds something with no string form has none, and a note names the field or the element. In a generic body, a print of a type parameter needs the constraint `Display` on that parameter: `@(T: Display)`. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2116 {#ce2116}

**Error** · type

**Message:** `the message of an assert must be a string, got {got}`

`assert(cond, message)` prints its message when the condition is false, so the message is a `string`: a literal, an interpolation, or a call that answers one. An assert does not convert a value to a string for you, as `print` does, because the message is a sentence about the fault and not a value to show. Write the value in an interpolation hole: `assert(n > 0, "n was {n}")`. The program builds the message only when the condition is false. See [the assert design](design/assert.md).

### CE2117 {#ce2117}

**Error** · type

**Message:** `cannot destructure '{type}': it is not a tuple`

Only a tuple destructures: `let (a, b) = t` splits a tuple value into its elements, and each binder owns its element (from an owned value) or borrows it (from a borrow). A struct has named fields and reads them by name (`s.field`); a struct field that owns a resource comes out with a marked field take (`nom s.field`). A nested destructure element follows the same rule: the element it splits must be a tuple. The item of a `foreach` destructure and the right side of a destructuring rebind (`(a, b) := v`) must be a tuple too. A tuple pattern in a `match` arm reads a tuple, at the top of an arm, in an enum payload and in another tuple pattern, so it is refused over a value of any other type. See [the tuple design](design/tuples.md).

### CE2118 {#ce2118}

**Error** · type

**Message:** `unreachable match arm '{pattern}': the arms above it match every value it matches`

A match tries its arms in order, and an arm runs only for a value that no arm above it matches. When the arms above match every value that this arm matches, the arm can never run: `(_, _) -> ...` before `(Color.Red, _) -> ...`, or `Maybe.Some(c) -> ...` before `Maybe.Some(Color.Red) -> ...`. Such an arm is dead code, and dead code is an error in Sushi, as a statement after a `return` is. The note at each covering arm names the arms that match those values first; the arms together can cover it, as `(Color.Red, _)` and `(Color.Green, _)` cover `(_, Color.Red)`. Remove the arm, or move it above the arms that cover it. One checker reads every match: an enum match, a nested enum match, an integer match and a tuple match (see [the tuple design](design/tuples.md)). The arms of an integer match can cover the type ([CE2074](#ce2074)), so a `_` arm after arms that hold every value of a u8 is this error. A range arm whose every value the arms above match is this error, and so is a range that matches no value (`5..5`). Where another code names the fault, that code is the one diagnostic for the arm: a second arm for the same enum variant is [CE2041](#ce2041), a `_` arm that is not the last arm is [CE2041](#ce2041) (and the arms after it get no second error), and a literal arm for a value that an arm above matches is [CE2075](#ce2075), as is a range that shares some values with the arms above and has values of its own. An alternative of an arm (`Maybe.Some(1) | Maybe.None -> ...`) takes the same rule, with the error at that alternative: it can never match when the arms above and the earlier alternatives of the same list match every value it matches, as `2` after `_` in `1 | _ | 2`. When every alternative of an arm is dead, the error is once for the whole arm.

### CE2119 {#ce2119}

**Error** · type

**Message:** `{kind} literal pattern needs {kind} value, got '{got}'`

An integer literal and a string literal are legal in every pattern position: a literal arm of a match, an element of a tuple pattern (`(0, n) ->`, `("get", v) ->`) and a payload of an enum pattern (`Maybe.Some(0) ->`, `Maybe.Some("--help") ->`). See [the tuple design](design/tuples.md). Inside a pattern, the value at the position must then be of the literal's kind: an integer for an integer literal, a `string` for a string literal. The `{kind}` slot names the literal kind with its article (`an integer`, `a string`), so that the text reads correctly for both kinds. A `bool`, a float and a struct have no literal pattern: such a position takes a binding or `_`, and the arm body tests the value (`(b, n) -> if (b): ...`). A literal arm at the top of a match whose scrutinee is of another kind is [CE2076](#ce2076).

### CE2120 {#ce2120}

**Error** · type

**Message:** `the destructure names {count} elements, but '{type}' has {arity}`

A `let` destructure names one element for each element of the tuple, in order: `let (q, r) = divmod(7, 2)` splits a two-element tuple. A `_` is an element too, so it keeps the count. A nested destructure follows the same rule for the nested tuple. There is no rest element: write `_` for each element the destructure does not keep. A `foreach` destructure (`foreach((k, v) in pairs.iter()):`) and a destructuring rebind (`(a, b) := f()`) are destructures too, and they take the same rule. A tuple pattern in a `match` arm (`(a, b, c) ->`) names one item for each element by the same rule. See [the tuple design](design/tuples.md).

### CE2121 {#ce2121}

**Error** · type

**Message:** `{position} is i32, got {got}`

**Help:** `convert it with 'as i32'`

An index, a count and a range bound are i32 positions: an array index `a[i]`, a repeat count `[v; n]`, and each bound of a range `a..b`, in a `foreach` and in an array literal (`[a..b]`) alike. A bare literal takes i32 there. A value of any other type is refused, and nothing widens it. Convert the value with `as i32`. The text names the position and states the rule, because nothing is assigned. A method argument in an i32 position (`get`, `insert`, `truncate`, `s`) is [CE2006](#ce2006), which names the argument; an assignment of the wrong type is [CE2002](#ce2002).

### CE2122 {#ce2122}

**Error** · type

**Message:** `` a range is not a value: it is walked by `foreach` or spelled into an array ``

**Help:** `` walk it with `foreach`, or spell it into an array: `from([a..b])` ``

A range (`a..b`, `a..=b`) has two positions, and only two: the iterable of a `foreach` (`foreach(i in 0..3):`) and an element of an array literal (`[0..3]`, `from([0..n])`). Sushi has no range type and no range object, as Rust (`Range`) and Python (`range`) have, so a range cannot be a function or a method argument, a constructor argument, a `return` value, a `let` initializer or an operand. Walk it with `foreach`, or spell it into an array with `from([a..b])` and use the array. `.rev()` is the one method a range takes, and the result is a range again, with the same two positions: `foreach(i in (0..3).rev()):` and `[(0..3).rev()]`. Any other method on a range is this error.

### CE2123 {#ce2123}

**Error** · type

**Message:** `a string pattern cannot hold an interpolation hole`

**Help:** `write '{text}' with single quotes to match the braces as text`

A match arm compares the value with a FIXED value, and the compiler must know that value. A hole (`"{x}" ->`) is a run-time value, so a double-quoted pattern with a hole is refused. The compiler does not read the hole as a value to compare with, and it does not fold it. A single-quoted literal does not interpolate: write `'{x}' ->` to match the braces as text. To compare with a run-time value, use a `_` arm and test the value in the arm body (`_ -> if (s == x): ...`), or bind it in a nested position.

### CE2124 {#ce2124}

**Error** · type

**Message:** `the target '{target}' puts a bound on '{name}', which is a type: a bound constrains a type parameter`

**Help:** `rename the type parameter so that it names no type, or remove the bound; a concrete target argument already names one instance`

An extension target and a perk-implementation target can put a bound on a type parameter: `extend List@(T: Clone) filter(...)`, `extend (T: Clone)[] filter(...)`, `extend Box@(T: Eq) with Show:`. A bare name that a unit declares is a type, and a target argument that names a type is a constraint on one instance (`extend Box@(Point)`). A bound on a type has no meaning: the type satisfies the perk or it does not. The declaration is refused, and a call of its method gives no second error.

### CE2125 {#ce2125}

**Error** · type

**Message:** `range '{range}' goes down: a range always goes up`

**Help:** `` to count down from {high} to {low}, write `({low}..={high}).rev()` ``

A range always goes up: `a..b` yields a, a+1, ..., b-1 and is empty when a >= b; `a..=b` yields a to b and is empty when a > b. A countdown is written `(a..b).rev()`, which yields the same values, last first: `(1..=10).rev()` is 10 down to 1, and `(0..n).rev()` is n-1 down to 0 and empty when n is 0. This error is for a range whose two bounds the compiler can read and whose start is above its end, in a `foreach` iterable and in an array literal element (a constant and a fixed array included), with or without `.rev()`. The help gives the countdown that yields the values the bounds name: `10..0` is `(1..=10).rev()`, `10..=0` is `(0..=10).rev()`. A range with a computed bound gets no diagnostic: it is empty when it does not go up. Before this rule the values gave the direction, so `10..0` counted down. That made the empty case run backward when a bound was computed: the countdown `(n - 1)..=0` visited -1 and 0 when n was 0, where it must visit nothing. An inclusive range could never be empty. The countdown is `(0..n).rev()` now, and it is empty when n is 0. Rust, Kotlin and Python write the direction in the code, never in the data, and Sushi does the same. `3..3` in a `foreach` is legal and empty; in an array literal it is [CE2019](#ce2019), because it spells no slot. A range in a match PATTERN (`0x80..=0x8f ->`) goes up by the same rule, and `0x8f..=0x80` or `5..3` there is this error. Its help gives the written order to use (`0x80..=0x8f`), because a pattern has no direction to keep. The bounds are read as values of the scrutinee type, so on an i8 `0x80..=0xff` is -128 to -1 and goes up. `5..5` in a pattern goes nowhere: it matches no value, which is the dead-arm error [CE2118](#ce2118).

### CE2126 {#ce2126}

**Error** · type

**Message:** `alternative '{alternative}' binds differently from the first alternative: {detail}`

The alternatives of a pattern (`Shape.Circle(r) | Shape.Ring(r) -> ...`) bind the same names, with the same types and the same modes (bare, `peek`, `poke`, `nom`), as in Rust and Python. The arm body reads one binding, whatever alternative matched, so a name that only one alternative binds has no value on the other path, a name with two types has no one type, and a name with two modes is a copy on one path and a reference or a take on the other. The `{detail}` slot names the first difference: a name that is not bound, a name the first alternative does not bind, a different type, or a different mode. The note is at the first alternative, which every other alternative is held to. A nested list of alternatives (`Maybe.Some(Shape.Circle(r) | Shape.Ring(r))`) takes the same rule. Write two arms when the alternatives must bind different things.

### CE2127 {#ce2127}

**Error** · type

**Message:** `the iterator of HashMap '.{method}()' must be the iterable of a foreach`

**Help:** `` walk the map where the call is written, `foreach(x in map.{method}())`, or pass the map and call '.{method}()' in the function that walks it ``

A HashMap iterator (`.keys()`, `.values()`, `.entries()`, `.pairs()`) walks the buckets of the map, and the loop must know the key type and the value type to step over them. The loop knows them only when the call is the iterable itself: `foreach(k in m.keys())`. In every other position the value leaves the call with the type `Iterator@(K)` alone -- an argument of a generic function, an element of a tuple, the payload of a `Maybe` -- and a later `foreach` over it cannot find the buckets. Before this error that loop walked zero entries and gave no diagnostic. An array or a List iterator (`.iter()`) has no such limit. The fix is to walk the map where the call is written, or to pass the map and call the method in the function that walks it.

### CE2128 {#ce2128}

**Error** · type

**Message:** `'{method}' needs a numeric element type, and '{element}' is not numeric`

The array reductions `.min()`, `.max()` and `.add_up()` read each element as a number: `.min()` and `.max()` compare with the `<` of a number, and `.add_up()` adds with the `+` of a number. The element type must be an integer type or a float type. A `bool`, a `string`, a struct, an enum, an array and a function value are refused, on a fixed and on a dynamic array alike. For an order over other element types, sort the array with `.sort()` from `<collections/sort>`, or reduce it with `.fold()` from `<collections/iter>`. The reduction that adds is named `add_up` and not `sum`, so a user extension named `sum` or `total` on an array does not collide with a built-in ([CE2097](#ce2097)).

## CE24xx: Borrow and reference errors {#ce24xx}

The borrow checker gives these errors. They are about ownership, moves and borrows.

### CE2400 {#ce2400}

**Error** · borrow

**Message:** `` cannot borrow '{name}' with `{mode}` ``

**Help:**

- `` a constant is read-only storage: `peek` reads it, and `poke` writes it; declare a `var` for storage that you can write ``
- `only storage can be borrowed, and this name has none; read the value, or copy it into a local first`

This is a WRITE rule. A constant is read-only STORAGE: it is one object in `.rodata`, so a pointer into it exists and `peek` reads through it. `poke` writes through the pointer, and a write to read-only memory is undefined behaviour. Declare a `var` for storage that you can write. A TAKE is not this code's: `nom` is a consuming use, so [CE2436](#ce2436) answers it for a constant and a unit variable alike. The code has a second half: a name that reaches storage of NO kind -- a top-level function, a registry stdlib constant, an FFI namespace -- refuses every mode, `peek` too, because there is nothing to point at. A name that is declared NOWHERE is [CE1001](#ce1001) and a TYPE name in a value position is [CE2105](#ce2105), so neither is reported together with this one for one token. The rule is the same in every borrow position: a `peek` or `poke` argument, a `let peek` or `let poke`, a `peek self` or `poke self` call, a pattern binding and a foreach item.

### CE2401 {#ce2401}

**Error** · borrow

**Message:** `cannot move '{name}' while it is borrowed`

**Help:**

- `the new owner frees this value while the borrow still points at it; borrow it twice: '{name}' owns a resource and cannot be cloned`
- `` the new owner frees this value while the borrow still points at it; borrow it twice, or clone what the owning position needs: `{name}.clone()` ``

One statement borrowed a value and also handed it to a position that takes ownership -- `both(peek s, s)`. The new owner frees the buffer while the borrow still points at it, so `both(poke a, a)` is a double free plus a read of released memory, whichever order the arguments are written in. Borrow it twice (`peek` is shareable), or clone the value the owning position needs: `both(peek s, s.clone())`. A borrow lasts only for the statement that creates it, so the same two lines written as two statements are unaffected.

### CE2403 {#ce2403}

**Error** · borrow

**Message:** `'{name}' already has an active poke borrow (only one exclusive borrow allowed)`

A variable can only have one active poke (read-write) borrow at a time to prevent aliasing issues.

### CE2404 {#ce2404}

**Error** · borrow

**Message:** `cannot borrow '{expr}': expression has no stable address`

**Help:** `` a `let {mode}` binds a PLACE: a local, a field, an element, or an Own's payload (`o.get()`); bind a call result by value, with `let T {name} = ...` ``

A borrow is a pointer, so it needs storage that the frame keeps. A `peek x` / `poke x` argument takes a name or a field chain off one (`poke obj.field`). A `let peek T x = <place>` / `let poke T x = <place>` takes a place: a name, a member or index chain off one, or an `Own@(T).get()` on one. A `peek` / `poke` pattern binding points into the scrutinee, so the scrutinee must be a place (the same places a reference `let` takes) or a temporary that the match owns. A call result, a `??`, a literal and a construction are temporaries and have no address. Bind the value first (`let T x = make()`), and then borrow the name.

### CE2405 {#ce2405}

**Error** · borrow

**Message:** `cannot borrow moved variable '{name}'`

Attempted to borrow a variable whose ownership has been transferred elsewhere: `f(nom x)`, `match nom x:`, a marked field take `nom x.field`, or `x??` over a `Result` local that owns something in either arm -- the unwrap moves the payload out, so the wrapper is spent (see [the borrow model](design/borrow-model.md)).

### CE2406 {#ce2406}

**Error** · borrow

**Message:** `use of destroyed variable '{name}'`

Variable was explicitly destroyed via .destroy() and is no longer valid.

### CE2407 {#ce2407}

**Error** · borrow

**Message:** `cannot have peek and poke borrows of '{name}' simultaneously`

A variable cannot have both read-only (peek) and read-write (poke) borrows at the same time.

### CE2408 {#ce2408}

**Error** · borrow

**Message:** `cannot modify '{name}' through peek reference (read-only)`

**Help:**

- `` the write ({what}) would change the borrowed value through a read-only reference; declare it `poke` if the write must reach the owner{copy} ``
- `` a `poke` element binding would write the caller's container through a read-only borrow; declare the parameter `poke` if the elements must be written, or drop the marker and bind the element by value ``

peek references are read-only. Use poke for mutable access.

### CE2410 {#ce2410}

**Error** · borrow

**Message:** `cannot move '{name}': it is a borrowed view of the process arguments (main's string[] args); borrow it instead with 'peek string[]'`

main's `string[] args` aliases the process argv, which the runtime owns and frees. Moving it by value (passing it to a by-value parameter, rebinding, or storing it) would make the callee free argv and double-free. Take it by reference with `peek string[]`.

### CE2411 {#ce2411}

**Error** · borrow

**Message:** `cannot consume '{name}': another owner keeps this value`

**Help:**

- `` clone it to take an independent value: `{name}.clone()` ``
- `` clone it, and {use_of_copy}: `{name}.clone(){tail}` ``
- `` a descriptor cannot be deep-copied, so there is no `{name}.clone()`; take a second owner with `{name}.share()`, or restructure so only one owner is needed ``
- `` a descriptor cannot be deep-copied, so there is no `{name}.clone()`, and its type has no `share()` that gives a second owner; hand the value over with a `nom` parameter, or restructure so only one owner is needed ``

A borrow names storage something else owns and still frees, so a position that takes ownership cannot have it. Three shapes borrow: a `match` payload binding, a `foreach` loop binding over a container, and every read THROUGH a live owner -- a field read (`h.inner`), an index (`rows[i]`), a container get-out (`c.get(0)`, `own.get()`, also under `.or_err(nom e)??`) and `r??` over a BORROWED `Result` (over one the function owns, the `??` spends it instead). The `??` itself is a consuming use when the ERROR type of a borrowed `Result` owns heap, because the Err path puts the error in the returned Err while the owner still frees it: write `r.clone()??`. A plain error type is copied and stays legal. Reading through a borrow is free; clone it to take an independent value: `{name}.clone()`. A `nom self` method call consumes its receiver and an `as` conversion consumes its operand, so the help names the use of the copy: `r.clone().map_err(f)`, `e.clone() as AppError` (see [the error-conversion design](design/error-conversion.md)). Where the OWNER is a local this function holds, `nom {name}` is the third way -- a marked field TAKE, one step off a bare name, which hands the field over and spends the whole receiver (see [the borrow model](design/borrow-model.md)). Only a value whose type transitively owns heap (a dynamic array, List, Own, HashMap, a string or a capturing closure) is affected -- a primitive borrow is unrestricted, and so is a string bound directly from a literal, which points into read-only memory and owns nothing.

### CE2412 {#ce2412}

**Error** · borrow

**Message:** `cannot mutate '{owner}' while '{name}' borrows from it`

**Help:**

- `'{name}' owns a resource and cannot be cloned, so no call can read it while it changes '{owner}'`
- `` pass an independent value: `{name}.clone()` ``
- `{change} after the last use of '{name}': '{name}' owns a resource and cannot be cloned`
- `` {change} after the last use of '{name}', or bind an independent value with `.clone()` ``
- `{change} after the loop: '{receiver}' owns a resource and cannot be cloned`
- `` {change} after the loop, or walk an independent value: `{receiver}.clone().{method}()` ``

A `let` bound from a read THROUGH an owner -- `let v = h.items`, `let v = c.get(0).or_err(nom e)??` -- BORROWS: it names storage the owner keeps and still frees. Mutating, freeing, rebinding or moving that owner while the binding is live would leave the binding pointing at storage the owner no longer holds. A bare `match` payload binding of an owning payload views the owner's storage the same way, for the arm. A `foreach` over a container iterator (`.iter()`, `.keys()`, `.values()`, `.entries()`) views the container's storage from the loop entry to the loop exit, so a change that can move or free that storage -- a `push`, `insert`, `remove`, `pop`, `clear`, `truncate`, `extend` or `reserve`, a rebind, a `nom` or a `poke` of the container -- is refused in the body; an in-place write (`fill`, `reverse`, an indexed assignment) keeps the storage and stays legal. A borrowed argument is read DURING its call, so a binding passed to a call that changes its owner (`a.fill(first)`) is a use while the owner changes. The borrow lasts to the end of the block that declares it, so move the mutation after that block, or take an independent value with `.clone()`. This is Rust's E0502.

### CE2414 {#ce2414}

**Error** · borrow

**Message:** `cannot write to binding '{name}': a match/foreach binding is a read-only view`

**Help:** `` '{name}' is a view of the owner's value and not storage of its own, so the write ({what}) cannot stand; bind the payload `poke` to write through to the owner, or `nom` to take it where the match owns its scrutinee -- otherwise {escape} ``

A BARE `match` payload binding and a BARE `foreach` loop binding borrow a value the scrutinee or the container owns. The compiled binding is a private copy, so a write through it -- a mutating method, a field assignment, or a `poke` borrow -- never reaches the owner and is lost. The binding carries a MODE, and the mode is the escape: `poke` binds a pointer into the owner's storage, so the write reaches it, and `nom` takes the value outright where the match owns its scrutinee. A copy is the third way -- `.clone()`, mutate, store back -- and a type that owns a resource has none, so for one of those the message names `.share()` instead, exactly as [CE2411](#ce2411) does. A rebind of the binding ITSELF is refused with the same code, and the escapes are the same three: the compiled copy is SHALLOW, so the store would free a payload the scrutinee still owns. A parameter and a by-value receiver own their slot and stay rebindable.

### CE2415 {#ce2415}

**Error** · borrow

**Message:** `a struct field cannot have a reference type ('{ty}')`

A `peek` / `poke` struct field has no checked semantics: nothing relates the field's borrow to the value it points at, so the struct may outlive it. Store an owned value, or an index into a container the struct does not own.

### CE2416 {#ce2416}

**Error** · borrow

**Message:** `an enum variant payload cannot have a reference type ('{ty}')`

A `peek` / `poke` enum payload has no tracking of any kind, so the enum may outlive the value it borrows. It is also how a returned borrow would escape: a `Result@(peek T, E)` is an enum payload. Carry an owned value in the variant. Same lifetime problem as a reference struct field ([CE2415](#ce2415)).

### CE2417 {#ce2417}

**Error** · borrow

**Message:** `a function cannot return a reference type ('{ty}')`

Returning a `peek` / `poke` would let a function hand out a borrow of its own local, and the caller would read it after the frame is gone -- a dangling read. A borrow ends when the function returns. Return an owned value, or `.clone()` what you borrowed.

### CE2418 {#ce2418}

**Error** · borrow

**Message:** `a reference to a reference is not supported ('{outer} {inner} ...')`

**Help:** `write the single borrow: a borrow of a borrow is the same borrow`

Both grammar rules for a borrow are recursive, so `peek peek i32` parses -- in a type position and in an expression position. There is no double borrow in the language: a borrow of a borrow is the same borrow, and the extra level has no meaning at any layer. Write the single borrow.

### CE2419 {#ce2419}

**Error** · borrow

**Message:** `a reference type cannot be a generic type argument ('{ty}')`

A container of borrows -- `List@(peek T)`, `HashMap@(peek K, V)`, `Maybe@(peek T)` -- has no defined semantics: nothing relates the stored borrows to the values they point at, and the backend cannot lay one out. Store owned values, or indices into a container that outlives the uses. There is NO `Maybe` / `Result` exemption: those two are how a returned borrow would escape ([CE2417](#ce2417)). Foreign `ptr` carries the same restriction, as [CE5012](#ce5012).

### CE2420 {#ce2420}

**Error** · borrow

**Message:** `an extension cannot target a reference type ('{ty}')`

`extend peek T` would be permanently uncallable: a reference target falls through method resolution, so every call would report 'no such method'. Extend the referent instead -- the methods on a borrow of `T` ARE the methods on `T`, so `extend T` is already callable through a `peek T` / `poke T` receiver. This is the [CE2097](#ce2097) shape: an extension that can never be reached is a diagnostic, not silence.

### CE2421 {#ce2421}

**Error** · borrow

**Message:** `cannot write through 'self': a method receiver is a read-only borrow`

**Help:** `` the write ({what}) would land on the method's private copy of the receiver; declare the receiver mutable -- `(poke self, ...)` -- and the write reaches the caller, or return the new value and let the caller store it ``

An extension or perk method receives `self` as a BORROW: the caller keeps the value (see [the ownership conventions](design/ownership-conventions.md)). The compiled receiver is a private copy, so a write through it -- a mutating method, a field assignment, or a `poke` borrow of it -- never reaches the caller. This is [CE2414](#ce2414)'s rule for the one receiver [CE2414](#ce2414) does not cover. The mutating receiver is spelled `poke self`: declare the method `extend T name(poke self, ...)` and the write reaches the caller. Alternatively return the new value and let the caller store it.

### CE2422 {#ce2422}

**Error** · borrow

**Message:** `cannot write through '{name}': a by-value {callable} parameter is a read-only borrow`

**Help:** `` the write ({what}) would land on the {callable}'s private copy of the argument; declare the parameter `poke` if the {callable} must write through it{copy} ``

Every parameter of every callable is a BORROW of the caller's value unless it says `nom`: a free function, a lambda, and an extension or perk method, `self` and the explicit parameters alike. A by-value one is compiled as a private copy, so a write through it -- a mutating method, a field assignment, or a `poke` borrow of it -- would never reach the caller. [CE2421](#ce2421) is the same rule for the receiver. `{callable}` is "method" or "function", as the declaration is. The escape: declare the parameter `poke T` and the write reaches the caller, or `nom T` and the callee owns the value.

### CE2423 {#ce2423}

**Error** · borrow

**Message:** `a reference binding needs addressable elements; this iterable yields values`

A `peek`/`poke` foreach binding is a pointer into the container's element storage, so the iterable must HAVE element storage. A range (`0..10`) synthesizes its values, and `HashMap.entries()` synthesizes each `Entry` pair on the fly -- there is no address to bind. Iterate a container (`arr.iter()`, `list.iter()`, `map.keys()`, `map.values()`) or drop the marker and take the value.

### CE2424 {#ce2424}

**Error** · borrow

**Message:** `a reference binding in a NESTED match pattern is not supported`

**Help:** `bind the payload by value in the nested pattern, or restructure to match the inner enum at the top level`

A top-level `Variant(poke x)` binds a pointer into the scrutinee's payload storage and is supported. A NESTED pattern is different: extraction walks through temporary copies of the inner enums, so a pointer into one writes to storage nobody reads, and the write is lost. Bind the payload by value in the nested pattern, or restructure to match the inner enum at the top level. A tuple pattern follows the same line: an element of the arm's own tuple pattern, also in a tuple pattern inside it, takes `poke` (`(poke n, _) ->`), and a tuple pattern in an enum payload (`Maybe.Some((poke a, b))`) and an enum pattern inside a tuple pattern are nested.

### CE2425 {#ce2425}

**Error** · borrow

**Message:** `a 'peek self'/'poke self' receiver parameter is not valid here`

**Help:**

- `` the receiver comes first: `(poke self, <params>)` ``
- `` a reference parameter is written `poke T name`; the bare form is only the receiver, spelled `poke self` ``

The receiver parameter is the FIRST parameter of an EXTENSION or PERK method: `extend Counter bump(poke self) ~:`. It is not valid in a plain top-level function (a plain function has no receiver -- take `poke T name`), not valid after the first position, and a bare `poke name` that is not `self` is a reference parameter missing its type.

### CE2426 {#ce2426}

**Error** · borrow

**Message:** `cannot write to '{name}': it borrows storage another value still owns`

**Help:** `the write ({what}) reaches storage another value owns and still frees, so it is lost from the owner's view, a reallocating write frees the owner's buffer, and a rebind frees a value the owner still holds; write to the owner directly -- otherwise {escape}`

A `let` bound from a read THROUGH an owner -- `let v = h.items`, `let v = c.get(0).or_err(nom e)??` -- BORROWS: it names storage the owner keeps and still frees. A write through it is not merely lost, which is what [CE2414](#ce2414) says for a match/foreach binding: the binding holds its own copy of the descriptor while the DATA is shared, so a mutating method updates a length nobody reads, a field assignment lands on the private copy, and a `.push()` that reallocates frees the OWNER's buffer -- a double free plus a read of released memory. Write to the owner directly (`h.items.push(9)`), or take an independent value with `.clone()`, mutate it, and store it back. A rebind of the binding itself reads the same code: the slot holds a descriptor over data the owner keeps, so the store frees a value the owner still holds. [CE2412](#ce2412) is the complementary question -- may the OWNER be changed while the binding lives -- not an alternative to this one.

### CE2427 {#ce2427}

**Error** · borrow

**Message:** `argument mode does not match the declared mode of parameter '{name}'`

**Help:**

- `` the callee takes ownership here; write `nom` at the call site too, or `nom <arg>.clone()` to keep your own value ``
- `` the callee only borrows this argument, so it stays yours after the call; drop the `nom` ``

A `nom` parameter takes OWNERSHIP of its argument, and that must be visible where the value is handed over: without the marker, `f(s)` would not show whether `s` survives the call, and the reader would have to open the callee to find out (see [the borrow model](design/borrow-model.md)). So the marker is written at both ends, or at neither. Add `nom` at the call site to hand the value over, or drop it if the callee only borrows. `.clone()` is the escape when the caller needs to keep its own value: `f(nom s.clone())`. A built-in method follows the same rule: its parameters borrow unless its family's table says otherwise, so `m.realise(nom 3)` is this error; a container slot (`push`, `insert`, `Own.alloc`) takes ownership by position, with or without the marker.

### CE2428 {#ce2428}

**Error** · borrow

**Message:** `` `nom` has no meaning on the foreign parameter '{name}' ``

FFI is outside the mode system. A C callee never receives a Sushi value: the compiler marshals the argument into a fresh C representation that the CALLER owns and frees at scope exit, so there is nothing for a foreign parameter to take ownership of. Declare the parameter without the marker. The four modes describe how a value crosses a SUSHI call boundary (see [the borrow model](design/borrow-model.md)).

### CE2429 {#ce2429}

**Error** · borrow

**Message:** `cannot write through an unbound chained borrow`

**Help:** `` the write ({what}) would land on the copy and be lost; bind a clone, mutate it, and rebuild the owner -- or mutate in place through a nested `Own(poke ...)` reference binding where the `Own` sits in an enum payload ``

The value past a call boundary is a temporary copy, not the owner's storage: `o.get()` is a get-out, so `o.get().items.push(9)` would land on the copy and be lost, while the `Own` keeps and frees the real buffer. A FRESH temporary is rejected by the same rule, because the statement discards the value and the write is dead either way. Bind a clone, mutate it, and rebuild the owner -- `let Holder h = o.get().clone()`, `h.items.push(9)`, `o := Own.alloc(h)` -- or, where the `Own` sits in an enum payload, mutate in place through a nested `Own(poke inner)` reference binding.

### CE2430 {#ce2430}

**Error** · borrow

**Message:** `'{name}' cannot be the source of a bulk write into '{target}'`

A bulk write borrows its source and writes its destination, and the source may not be storage the write changes. A growth -- `.extend()`, `.extend_range()` -- may REALLOCATE the destination's buffer, so `a.extend(a)` would leave the source pointer dangling in the middle of the copy. A refill -- `.fill()` -- frees each slot before it stores a copy of the argument, so `a.fill(a[0])` frees slot 0 and then copies freed memory into every later slot. The refill is refused only when the element type owns a resource: a plain element such as an `i32` is read by value, nothing aliases, and `b.fill(b[0])` stays legal. A fixed array follows the same rule, and an index is read as any slot, so `rows[i].cells.fill(rows[j].cells[0])` is refused too. The escape is `.clone()` (`a.fill(a[0].clone())`), or `.ss(start, count)` for a range, either of which gives an independent source. [CE2412](#ce2412) is the neighbouring question -- may the OWNER be changed while a `let`-borrow of it lives -- and not this one, because here the borrow is a method argument. A copy that must read what it is writing, such as a DEFLATE back-reference, is not this operation: write it as a per-element loop.

### CE2431 {#ce2431}

**Error** · borrow

**Message:** `cannot clone '{type}': it owns a resource, and a copy of it would be a second handle`

**Help:** `a second owner of a handle is '.share()'; for a value that holds handles, build a new one from a '.share()' of each handle`

`.clone()` is the one deep copy, and a derived clone copies a value FIELD BY FIELD. A type that implements the `Drop` perk owns something no field walk can see -- a file or a socket holds one i32 descriptor -- so a derived clone would copy that number and leave two values holding one descriptor, both of which drop. That is a double close. Use `.share()`, which is dup(2) and says what it does -- an independent descriptor over a SHARED open file description, so the offset is shared too. For concurrent reads of one file the answer is `read_at`/`write_at`, where the offset is an argument and no state is shared. The refusal reaches a struct that HOLDS a resource type, an array of them and a container of them, because cloning any of those copies the descriptor one level down; it is reported at the INSTANTIATION for a generic body that clones, because one monomorphized body serves every type argument and the argument is what makes it illegal.

### CE2432 {#ce2432}

**Error** · borrow

**Message:** `cannot take '{name}': this match only borrows its scrutinee`

**Help:** `` hand the value to the match -- `match nom {scrutinee}:` -- and it may be taken here; drop the marker to read through the borrow instead ``

A `nom` payload binding takes the value out of the scrutinee, so the match has to OWN the scrutinee to give it away. A TEMPORARY -- a call result, a constructor, a `??` -- is owned by construction and needs no marker. A place expression is not: `match r:` leaves `r` the owner, and `r` still frees the payload at the end of its scope, so a second owner here would be a double free. Write `match nom r:` to hand the value to the match; `r` is then consumed exactly as `f(nom r)` consumes it, and a later mention of it is [CE2405](#ce2405). Marked at both ends or neither is the same rule [CE2427](#ce2427) states for a call argument. To keep `r`, drop the marker and read through the borrow, or bind `poke` to write through it.

### CE2433 {#ce2433}

**Error** · borrow

**Message:** `'{name}' is bound by value while this arm takes another payload of the same variant`

**Help:** `` mark '{name}' `nom` as well, or drop the `nom` from '{taken}' and read both through the borrow ``

This is the all-or-nothing rule. What suppresses the match's free is the WHOLE scrutinee and not one payload slot, so an arm that takes any payload leaves every other owning payload of that variant with no owner at all -- taking one and borrowing its neighbour is a leak of the neighbour, not a dangling read. Mark every owning binding in the arm `nom`, or none of them. A payload that owns no heap is unaffected: an i32 beside a taken array stays a plain binding. Discarding the neighbour with `_` does NOT help -- it is the same slot with no name. The variant moves whole.

### CE2434 {#ce2434}

**Error** · borrow

**Message:** `` a `nom` binding is not valid inside an `Own(...)` pattern ``

**Help:** `` bind the pointee by value, or `Own(poke x)` to write through it; to take the value out, move the whole `Own@(T)` with a `nom` binding on the payload that holds it ``

`Own@(T)` is a heap cell that owns its pointee. Taking the pointee out with `nom` would leave the cell itself with nothing to free it, because the only thing that can suppress the match's free is the whole scrutinee -- so the malloc'd box would leak while the value inside it moved on. `Own(poke x)` and `Own(peek x)` stay legal: both bind the heap pointer and take nothing. To move the value out, take the `Own@(T)` itself -- a `nom` binding on the payload that holds it -- and read through it at the new owner.

### CE2435 {#ce2435}

**Error** · borrow

**Message:** `cannot use '{name}': '{method}' consumed it`

A `nom self` method takes ownership of what it was called on, so the binding is spent by the call. This is not [CE2405](#ce2405): nothing was transferred to another owner that the reader can point at, and a receiver's mode is DECLARATION-only, so there is no `nom` marker anywhere on the page. So the diagnostic names the method. One code covers every consuming receiver, and the method name is what tells the two shapes apart: `close()` releases a descriptor and answers `~`, so nothing went anywhere, while `lines()` and `into_inner()` hand the value onward. A `nom` ARGUMENT keeps [CE2405](#ce2405), because `eat(nom s)` is a real move and the marker is visible. To keep using the value, do not call the consuming method -- an owned handle closes itself when its owner leaves scope, so an explicit `close()` is only for the caller who has to SEE the failure.

### CE2436 {#ce2436}

**Error** · borrow

**Message:** `cannot move '{name}': it is {kind}, storage the program keeps for its whole run; borrow it instead, or take an independent value`

This is the rule for UNIT-LEVEL STORAGE, and both kinds read it. A `var` declaration is storage in the data segment and a `const` is one object in `.rodata`: one per program, initialized before `main`, never destroyed at exit (see [the unit-storage design](design/unit-storage.md)). Neither has an owner that can hand the value away. Moving it out -- a `nom` argument, a `let` bound straight from it, a `return` of it, a `nom self` method such as `close()` -- would give a callee or a binding the right to free storage nothing re-initializes, so the same rule that fences `main`'s argv view ([CE2410](#ce2410)) fences both. A plain value copies out freely; only a type that owns a resource is refused, so `nom` of a `const i32` is a harmless copy. Pass it as a borrow (`f(v)`, `peek v`, `poke v`), or take an independent value: `.clone()` for a plain owner, `.share()` for a handle. A REBIND is the one way to change what a `var` holds, and it frees the old value. [CE2400](#ce2400) is the WRITE rule. A name behind an alias (`c.GREETING`) is the same declaration as the flat name and reads the same rule.

## CE25xx: Result, Maybe and conversion errors {#ce25xx}

These errors are about `Result@(T, E)`, `Maybe@(T)`, the `??` operator and the conversions between error types.

### CE2503 {#ce2503}

**Error** · type

**Message:** `realise() default type mismatch: expected '{expected}', got '{got}'`

The default value type passed to realise() must match the T type in Result@(T, E).

### CE2505 {#ce2505}

**Error** · type

**Message:** `cannot assign Result@(T, E) to non-Result variable without handling (use .realise() or pattern matching)`

Result@(T, E) values must be explicitly handled before assigning to non-Result variables.

### CE2506 {#ce2506}

**Error** · type

**Message:** `cannot call .realise() on '{ty}' (a '~' success has no value to extract)`

A `~ | E` function answers Result@(~, E): its success carries no value, so a default for it means nothing. Test the outcome with `match` or `.is_ok()`, or propagate it with `??` in a body with a channel. A bare `~` function answers no Result at all (see [the error-channel design](design/error-channel.md)). The same rule holds for a Maybe@(~).

### CE2507 {#ce2507}

**Error** · type

**Message:** `` `??` takes a `Result@(T, E)`, got '{got}' ``

**Help:**

- `` a `Maybe` holds no error value, so write one with `or_err`: `m.or_err(nom <error value>)??` ``
- `` answer a `Result@(T, E)` here (the callee writes `| E`), or use the value without `??` ``

The ?? operator takes a Result@(T, E) and nothing else. It unwraps the Ok, or it returns the Err from the enclosing body, so the Err must hold an error value that the program made. A Maybe@(T) holds no error value. Write the error value at the site with or\_err: m.or\_err(nom AppError.Empty)??. The ?? operator reads the TYPE of its operand and not its variant names, because type identity is nominal. So a user enum with Ok/Err or Some/None variants is not a Result: its Err payload is out of reach of the rule that an E is an error type. Answer a Result@(T, E) (a callee writes | E), or use the value without ??. See [the error-conversion design](design/error-conversion.md).

### CE2508 {#ce2508}

**Error** · type

**Message:** `` `??` is legal only in a body with an error channel ('| E' or a Result@(T, E) return) ``

The ?? operator propagates an error by an early return, so it needs an enclosing body that returns a Result@(T, E): a function, a method or a lambda that writes '| E', or a function that returns an explicit Result@(T, E). This code is the backstop for a `??` that stands outside every body. A `??` in a BARE body is [CE0131](#ce0131), and a `??` on an operand that is not a Result@(T, E) is [CE2507](#ce2507) (see [the error-conversion design](design/error-conversion.md)).

### CE2509 {#ce2509}

**Error** · type

**Message:** `operator '+' cannot be used with string types (use string interpolation instead: "text {{variable}}")`

**Help:** `use string interpolation: "{a}{b}"`

Sushi does not support string concatenation with the + operator. Use string interpolation for combining strings.

### CE2510 {#ce2510}

**Error** · type

**Message:** `cannot use operator with mixed numeric types: {left_type} and {right_type} (use 'as' to explicitly cast one operand)`

Sushi converts no numeric type on its own, so two numeric operands of one operator must have the same type. This covers arithmetic (+ - \* / %), the comparisons (== != &lt; &lt;= > >=) and the bitwise & | ^. Use 'as' to cast one operand to the other's type: (low as u32) | wide. A shift is the exception: its right operand is a count, not a second value, so its type is free and the result keeps the type of the left operand.

### CE2511 {#ce2511}

**Error** · type

**Message:** `error type mismatch in propagation: cannot propagate Result@({ok_type}, {inner_err}) to function returning Result@({ok_type}, {outer_err})`

**Help:**

- `` a conversion cannot take the generic error type '{generic}'; convert the error at this site with `.map_err(f)??`, where `f` takes '{source}' `nom` and answers '{target}' ``
- `` no unit may declare a conversion into '{target}', because it has no home module; convert the error at this site with `.map_err(f)??`, where `f` takes '{source}' `nom` and answers '{target}', or use an error type of your own as the target, and declare `extend {source} as <YourError>:` in the unit that declares it ``
- `` only <{home}> may declare `extend {source} as {target}:`; answer an error type of your own, and convert both errors into it ``
- `` declare `extend {source} as {target}:` in the unit that declares '{target}', and return the converted value ``

The ?? operator propagates the error of the inner Result@(T, \{inner\_err\}) into the channel of the enclosing body, Result@(T, \{outer\_err\}). When the two error types are the same type, the error propagates unchanged. A `??` converts an error only through a declared conversion (see [the error-conversion design](design/error-conversion.md)): when the two error types differ, it calls `extend {inner_err} as {outer_err}:` if the program declares it, and the help names that declaration. When no declaration of the pair is legal (a target with no home module, such as `StdError`, or a generic error type on either side), the help names `.map_err(f)??` at the site. A conversion is one step, so `A as B` and `B as C` do not give `A` to `C`: the lookup is an exact match on the pair, and a program that wants `A` to `C` declares it. Only the unit that declares the target type may declare the conversion ([CE2519](#ce2519)). For one site, `r.map_err(f)??` converts with no declaration. In a generic body, a `??` on a Result whose error type is a type parameter propagates that same error type and no other, because the template cannot name a conversion for it.

### CE2512 {#ce2512}

**Error** · type

**Message:** `shift count {count} is out of range for {value_type}: a count must be from 0 to {max_count}`

A shift moves the bits of its left operand, so the width of that operand is what limits the count. A count at or above the width moves every bit out of the type, and a negative count is no shift at all. Neither has a defined answer: LLVM makes the result poison and the hardware promises nothing. Cast the value to a wider type when the shift is meant to reach further: (high as u32) &lt;&lt; 8. Only a count the compiler can read is an error. A computed count is defined instead of checked: a shift that empties the type answers 0, and an arithmetic right shift leaves the sign behind, which is the rule of Go.

### CE2513 {#ce2513}

**Error** · type

**Message:** `cannot compare '{left_type}' with '{right_type}' using operator '{op}'`

A comparison asks one question of two values, so both operands must be of one type. Sushi converts nothing on its own, and there is no order between a string and a number to fall back on. Cast one operand with 'as' when both are numeric, or compare like with like. Two numeric operands that disagree are [CE2510](#ce2510) instead, which says which widths met. In a generic body, each `expand` binds its own element type: in a copy, the elements of two `expand`s of one pack can have two different types, so a comparison of the two is a mixed pair, and the notes point at both binders.

### CE2514 {#ce2514}

**Error** · type

**Message:** `operator '{op}' cannot compare two values of type '{type_name}'`

**Help:**

- `use match to ask which variant the value holds`
- `use != to ask whether two bools differ`
- `compare the elements, or the lengths`
- `compare the fields one at a time`

Equality (== !=) reads the predefined perk `Eq`, and an order (&lt; > &lt;= >=) reads `Ord`. A primitive keeps a closed set: equality for the numeric types, bool and string, an order for the numeric types and string, where it reads the bytes. A struct or an enum, `Maybe` and `Result` included, has the equality and the order the compiler DERIVES from what it holds -- every field, or the variant and then its payload -- unless an `extend T with Eq` (or `Ord`) implementation overrides it. A type that holds something with no such contract -- a function value, a `ptr`, a `HashMap` -- has none, and a note names the field that stops it. An array, a `List` and an `Own` compare only where they are held inside a type. A bool is deliberately excluded from the order at the top level: false &lt; true is almost always a typo for != or a missing 'and'. Inside a derived order a bool field orders false before true. A generic body is checked where it is written: `==` and `!=` on a type parameter need the constraint `Eq`, and `<`, `<=`, `>` and `>=` need `Ord`. Add the constraint to the type parameter. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2515 {#ce2515}

**Error** · type

**Message:** `'{method}' is not a method of '{wrapper}' -- the call before it returns a channel that is still unhandled`

**Help:**

- `handle the Maybe first: match on it, '.realise(default)', or give it an error value and propagate with '.or_err(nom e)??' -- e.g. '{called}().or_err(nom <error value>)??.{method}()'`
- `handle the channel first: match on it, '.realise(default)', or propagate with '??' -- e.g. '{called}()??.{method}()'`

A method that declares '| E' returns Result@(T, E), and a Maybe@(T) is likewise more than the bare T, so the chain stops until the wrapper is handled. This is a RESOLUTION FALLBACK, not a receiver-kind ban: resolution runs first, a method found on the Result/Maybe enum itself (.realise, .hash) is legal, and this code fires only when the method is missing there but present on the payload type -- which is what tells a typo from an unhandled channel. Append '??' to the call that returns a Result to propagate its Err. A Maybe holds no error value and `??` takes a Result only ([CE2507](#ce2507)), so the help for a Maybe writes one first: '.or\_err(nom e)??'. Or handle the wrapper in place with match or .realise(default). The receiver is a Result or a Maybe by type identity, never by the names of its variants.

### CE2516 {#ce2516}

**Error** · type

**Message:** `a condition must be a bool, and '{ty}' is an unhandled {wrapper}`

**Help:**

- `use '.is_ok()' to test it, or take the value with '??', '.realise(default)' or match`
- `use '.is_some()' to test it, or take the value with '.realise(default)', match, or '.or_err(nom e)??'`

A condition asks a yes-or-no question, so it takes a bool and nothing else. Sushi gives no other type a truth value: an integer and a string are refused with [CE2005](#ce2005), and a Result@(T, E) or a Maybe@(T) is refused with this code. A wrapper in a condition has no clear meaning: a Result@(bool, E) has two meanings, because both the wrapper and the payload are a legal condition on their own. Rust, Swift, Go, Kotlin and Haskell also refuse the wrapper and make you name the question. Name the question: '.is\_ok()' or '.is\_some()' asks whether the call succeeded, '??' propagates the error of a Result ('.or\_err(nom e)??' gives a Maybe an error value first, because '??' takes a Result only), '.realise(default)' takes the value with a fallback, and match reads both arms. This code covers every condition position: an if, a while, and the operands of and, or, xor and not.

### CE2517 {#ce2517}

**Error** · type

**Message:** `the '??' binder needs an item that is a Result, and this loop's item is '{ty}'`

A foreach walks anything whose next() answers Maybe@(T), and the '??' on the BINDER is the short form for the case where T is a Result: it unwraps the Ok and leaves the function on the first Err, exactly as '??' does in every other position. An item that is not a Result has nothing to unwrap, so the marker would mean nothing -- drop it and bind the item itself. This is not [CE2515](#ce2515), which is a resolution fallback for a CHAINED call whose channel is still unhandled, and not [CE2516](#ce2516), which is a wrapper standing where a bool belongs; here the item is the right shape for the loop and the wrong shape for the marker. A fallible iterator is written by setting T to a Result: next() answers Maybe@(Result@(T, E)), where the outer Maybe says whether there is more and the inner Result says whether reading it worked. The protocol itself carries no error channel, so a next() that declares '| E' is not a fallible iterator -- it is a method whose own answer is wrapped, and the loop will not accept it.

### CE2518 {#ce2518}

**Error** · type

**Message:** `operator '{op}' takes a numeric operand, and '{type_name}' is not one`

**Help:**

- `take the value with '??', '.realise(default)' or match`
- `take the value with '.realise(default)', match, or '.or_err(nom e)??'`

Arithmetic combines numbers, so every operand of + - \* / % and of the unary minus must be an integer or a float. Nothing else carries the operation. A bool is not 0 and 1 here, an enum is a tag and a payload, and a struct or an array is a value with parts -- add the parts one at a time, and use match to read an enum. An unhandled Result@(T, E) or Maybe@(T) is the common way to land here: for a Result the fault is a missing '??', and the diagnostic names that escape beside '.realise(default)' and match. A Maybe holds no error value and '??' takes a Result only, so for a Maybe the help names '.or\_err(nom e)??' in place of '??'. The one carve-out is '+' with a string operand, which is [CE2509](#ce2509), because Sushi has no concatenation operator and [CE2509](#ce2509) names the interpolation that replaces it. Two numeric operands that disagree in width are [CE2510](#ce2510) instead, which says which widths met. In a constant expression, the same operands are [CE0110](#ce0110). A type parameter has no arithmetic: no constraint promises one, so a generic body refuses the arithmetic operators and the unary minus on a value of a type parameter. Write the function for a numeric type. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE2519 {#ce2519}

**Error** · type

**Message:** `cannot declare a conversion into '{target}' here: {reason}`

**Help:**

- `declare an error type of your own, and convert '{name}' into it`
- `move the conversion into the unit that declares '{name}'`

A conversion `extend <Source> as <Target>:` may be declared only in the unit that declares the TARGET type, the rule that `Drop` has already (see [the error-conversion design](design/error-conversion.md)). So each pair of types has at most one declaration in the program, because the target's unit is unique: two libraries cannot both declare `FileError as AppError`, and no clash must be found across units and libraries. An application lists the errors that its own error type absorbs beside the declaration of that type. For a predefined error type the declaring unit is its HOME module: `FileError` and `IoError` have the home `<io/error>`, so a conversion into `IoError` lives there and nowhere else. A library cannot convert its own error into `IoError`; it declares its own error type and converts `IoError` into it. `StdError` has no home module, so no unit may declare a conversion into it: a program that wants a target for its conversions declares an error type of its own. A note points at the declaration of the target when the target has one.

### CE2520 {#ce2520}

**Error** · type

**Message:** `the {side} of a conversion must be a non-generic error type, and '{type_name}' is {kind}`

**Help:** `declare '{name}' with 'error' in place of 'enum'`

A conversion turns one error value into another, for `??` and for `as` (see [the error-conversion design](design/error-conversion.md)). The `E` of every `Result` is an error type ([CE2084](#ce2084)), so a source or a target that is not an error type -- a plain enum, a struct, a primitive -- could never meet a `??`. Declare an enum that is an error vocabulary with `error`. Both sides are also NON-GENERIC: a generic error type and an instance of one (`DecodeError@(i32)`) are refused, so the lookup is a pair of names and the library manifest a pair of strings. A generic function cannot convert an OPAQUE error type: its body is checked once, where it is written, and a `??` from a type parameter `E` into another error type is [CE2511](#ce2511) (#1070). A generic function whose error types are concrete uses a conversion as a concrete function does. To convert a generic error, declare a non-generic error type that holds it, or convert at the one site with `map_err`.

### CE2521 {#ce2521}

**Error** · type

**Message:** `a conversion from '{type_name}' into itself is refused`

**Help:** `` `??` already propagates an error of the same type unchanged; delete the declaration ``

An identity conversion does nothing that the language does not already do: `??` propagates an error of the same type unchanged, and `e as T` on a value of type `T` gives the value unchanged to its new owner (see [the error-conversion design](design/error-conversion.md)). A declaration of one would be a second, silent path for the same propagation, so it is refused. Delete the declaration.

### CE2522 {#ce2522}

**Error** · type

**Message:** `'or_err' reads a borrowed '{maybe}' through, so its Result must be the operand of '??'`

**Help:** `` put the call under '??': `{receiver}.{method}(...)??`, or take an owned copy first: `{receiver}.clone().{method}(...)` ``

A Maybe that is a borrow -- a get-out such as 'xs.get(0)', a parameter, a pattern binding -- keeps its payload with its owner. 'or\_err' reads such a Maybe through and does not take it (see [the error-conversion design](design/error-conversion.md)), so its Result holds a borrowed Ok and an owned Err. Only '??' takes the two apart: the Err moves out to the caller, and the Ok binds a borrow, as 'xs.get(0)??' does. Any other position -- a 'let', a method call such as '.is\_ok()', a 'match', an argument, a 'return' -- would hold the Result whole, and no rule frees one arm and keeps the other. So the call stands under '??', or the program takes an owned copy first with '.clone().or\_err(...)'. The rule applies when the payload owns a resource. An owned Maybe, and a borrowed Maybe whose payload owns nothing, give a Result that owns both arms, and that Result is legal in every position.

### CE2523 {#ce2523}

**Error** · type

**Message:** `the conversion '{source} as {target}' calls itself`

**Help:** `build the target value directly, for example with one of its variants`

Inside the body of `extend <Source> as <Target>:`, a cast `x as <Target>` of a value of the source calls that same conversion. `self` is always a value of the source, so `self as <Target>` never ends, and the cast of another value of the source only moves the work to one more call that the body can write directly. So the cast of the conversion's own pair is refused in its own body; a cast of another pair (`self as <OtherError>`) is legal. A recursion through another function is not detected, as for every function. See [the error-conversion design](design/error-conversion.md).

## CE30xx: Unit errors {#ce30xx}

These errors are about units and imports.

### CE3001 {#ce3001}

**Error** · unit

**Message:** `circular dependency detected: {cycle}`

Units have circular dependencies that prevent compilation ordering.

### CE3002 {#ce3002}

**Error** · unit

**Message:** `unit '{name}' not found (expected: {path})`

A required unit file could not be found at the expected location.

### CE3005 {#ce3005}

**Error** · unit

**Message:** `cannot {verb} private {kind} '{name}' from unit '{current_unit}' ({kind} is defined in '{owner}')`

A private declaration can only be named from within the unit that declares it. Mark it `public` to let another unit name it. The `{kind}` and `{verb}` fields carry which kind of declaration it was, so one code answers for a function, a constant, a struct and an enum. A generic is no exception: a source library's units are ordinary units at the consumer, so a private generic of one resolves like any other symbol, and this is where it is refused. A binary library answers here too: the manifest names what the library declares and does not export, so a name that reaches the consumer's tables not at all is still private and not undefined. `{owner}` is then the library rather than a unit.

### CE3006 {#ce3006}

**Error** · unit

**Message:** `unknown stdlib module <{module}>`

The imported standard-library module does not exist. Check the spelling against the available modules.

### CE3007 {#ce3007}

**Error** · unit

**Message:** `no main() function: an executable needs an entry point`

**Help:** `` add `fn main() i32:` to the program, or compile it as a library with `--lib` ``

A program compiled without --lib is linked into an executable, and the linker needs a main(). Add one, or compile the unit as a library with --lib.

### CE3008 {#ce3008}

**Error** · unit

**Message:** `linking failed: '{cc}' exited with status {status}`

The C compiler used as the linker rejected the object file. Its own output is attached as a note. This is an environment condition, not a compiler bug.

### CE3009 {#ce3009}

**Error** · unit

**Message:** `public {kind} '{name}' names private type '{type}'`

Privacy on a type is worth nothing if a public signature hands the type out anyway: a consumer would receive a value of a type it cannot name, declare or construct. Rust answers the same condition with E0446. The rule reads what the signature SPELLS -- the return, the error arm, every parameter, a constant's type, a public struct's field and a public enum's variant payload -- and it follows a type argument, an array element, a borrow and a function type into what they carry. What a named type holds is that type's own declaration's business and is fenced there, so a public struct holding a private field hears this once, at the field. Mark the type `public`, or make the declaration that names it private.

### CE3010 {#ce3010}

**Error** · unit

**Message:** `public {kind} '{name}' constrains a type parameter with private perk '{perk}'`

A constraint is part of a signature: a consumer that calls a generic has to satisfy the constraint, which means naming the perk. Rust answers this with E0445. It is not [CE4011](#ce4011), and the difference is the direction: [CE4011](#ce4011) is a USE-site rule -- the perk is not nameable in that unit at all -- while this is a LEAK rule, where the perk is nameable right there in its own unit and the signature would hand it to a unit where it is not. Mark the perk `public`, or make the generic private.

### CE3011 {#ce3011}

**Error** · unit

**Message:** `cannot declare {kind} '{name}': '{owner}' declares it too`

A TYPE is one name for the whole program. Identity is nominal, so one name is one shape, and a source library's units and a bundled stdlib module are ordinary compilation units at the consumer: a struct or an enum that either of them declares is a name the consumer cannot declare again, even where the library keeps it private and the consumer cannot see it. This code refuses the declaration and names the unit that holds the name. A BINARY library holds the same rule: a concrete type it exports and a type it keeps PRIVATE are both refused with this code, because the library's bodies were compiled against its own layout, and the analysis stops after the refusal so no body is checked against the consumer's type. A function is not affected: a function carries the unit that declared it and each unit reads its own, so a consumer may declare a function beside a library's private one. Rename your type. See [the type-identity design](design/type-identity.md).

### CE3012 {#ce3012}

**Error** · unit

**Message:** `'{name}' is offered by more than one import`

**Help:** `` say which one: `use "{unit}" as u` above, then `u.{name}` ``

More than one unit in scope declares this name, and nothing written here says which one is meant. A note points at each candidate. The unit's OWN declaration always wins, so this can only happen where the name comes from somewhere else entirely; a private declaration next door is not a candidate, because it is not nameable. Bind one of the units to an alias and write the name behind it -- `use "math" as m` makes `m.sine` the answer -- or rename one of the declarations.

### CE3013 {#ce3013}

**Error** · unit

**Message:** `'{alias}' is already bound in this unit`

**Help:**

- `` `_` is the discard name; it cannot name a namespace ``
- `` an `unsafe external` block already binds this namespace ``
- `this unit already declares the name`

An alias binds a name in the unit that wrote it, so it collides with anything else that unit binds: another alias, an `unsafe external` namespace, or one of its own declarations. Two aliases for one import are legal and both work; one name holding two namespaces is not, because a qualified name would have two answers. The note points at what bound the name first. `_` is refused for the same reason: the language binds it as the discard name, so it cannot name a namespace. Rename the alias.

### CE3014 {#ce3014}

**Error** · unit

**Message:** `` a `use` must come before every declaration ``

Every import stands at the top of the unit, after the unit's own doc block if it has one, and a namespace is bound for the whole unit rather than from its `use` downwards. So a reader sees the dependencies of a unit in one block, as in Go and Java. Move the `use` above the first declaration.

### CE3016 {#ce3016}

**Error** · unit

**Message:** `` a `public use` takes no `as` ``

`public use X` re-exports what X brings: the importing unit takes X's public names as its own and hands them to its importers, flat behind a flat `use` of it and behind the dot of an aliased one (see [the unit-namespaces design](design/unit-namespaces.md)). A re-export is of NAMES and never of a namespace, so there is nothing an alias could bind: an alias is local to the unit that wrote it and is not exported, and a re-export that carried one would make the importer's spelling depend on a name it never wrote. The alias still binds here, as a plain `use ... as` would, so the one fault gets one diagnostic. Drop the `public` to keep the alias for this unit alone, or drop the `as` to re-export.

### CE3017 {#ce3017}

**Error** · unit

**Message:** `cannot read '{path}': {reason}`

The compiler could not read a source file: the main source or a unit it imports. The reason is the operating system's (the path is a directory, the file cannot be opened) or the text is not valid UTF-8, and then the reason names the first byte that is not and its line. Sushi source is UTF-8. This is an input condition and not a compiler bug. Save the file as UTF-8, or fix the path.

### CE3018 {#ce3018}

**Error** · unit

**Message:** `no source file to compile`

`sushic` needs a `.sushi` file to compile, unless the run only rebuilds the standard library (`--build-stdlib`) or only removes the cache (`--clean-cache`). Name the source file: `sushic app.sushi`.

### CE3019 {#ce3019}

**Error** · unit

**Message:** `cannot write '{path}': '{directory}' is not a directory`

The output path named with `-o` is in a directory that does not exist, or its parent is not a directory. The binary, the object file, the `.ll` file and the `.slib` are all written beside that path, so the build is refused after the analysis and before any code is generated. The compiler does not create the directory: a mistyped path would otherwise create a directory nobody asked for. This is an input condition and not a compiler bug. Create the directory, or fix the path.

### CE3020 {#ce3020}

**Error** · unit

**Message:** `cannot write '{path}': {reason}`

The compiler could not write a file that the command line asked for: the output named with `-o` (the binary, the `.slib`, the object file beside it) or a file in the `--cache-dir` cache. The reason is the operating system's. An `-o` path that is an existing directory, or that is in a directory the user cannot write, is refused after the analysis and before any code is generated; a write that fails later reads the same code. This is an input or environment condition and not a compiler bug. Fix the path, remove the directory, or choose a directory that you can write.

### CE3021 {#ce3021}

**Error** · unit

**Message:** `` `use <{module}>` has no file for this host '{host}' (the hosts are: {hosts}) ``

A per-platform standard-library module is one bundled source file per platform and architecture, and the compiler selects the file of the host it compiles on. Sushi has no conditional compilation, so a host with no file cannot compile the module at all: every value in it (an `open` flag, a struct offset, an errno number) is a fact about one platform. The supported hosts are the ones the message lists. A new host needs its own file, made with `tests/platform_probe/probe.c` on that host.

### CE3022 {#ce3022}

**Error** · unit

**Message:** `method '{name}' comes from unit '{owner}', and this unit does not import it`

A public extension on a type that its unit does not declare (a built-in type, an array type, the type of another unit) is visible in its own unit and in each unit that imports that unit: with `use "u"`, with `use "u" as x`, or through a `public use` chain. It is not visible in another unit, even when a third unit of the program imports it. A public extension in the unit that declares its target type travels with the type and needs no import. Add the import that the help names.

### CE3023 {#ce3023}

**Error** · unit

**Message:** `method '{name}' comes from more than one imported unit`

This unit imports two or more units that each declare a public extension of this name on this type, and this unit declares none itself. A note points at each declaration. Sushi has no qualified method call, so the call cannot say which unit it means. The error stands at the call: an import that brings two such methods and no call of them is no fault. Move the code that needs each method into a unit of its own, or rename one of the methods. An extension that this unit declares itself always wins, and that is the warning [CW3007](#cw3007) instead.

## CE35xx: Library errors {#ce35xx}

These errors are about libraries and the `.slib` format.

### CE3500 {#ce3500}

**Error** · library

**Message:** `library output path must have .slib extension: '{path}'`

Library compilation requires output file with .slib extension.

### CE3501 {#ce3501}

**Error** · library

**Message:** `main() function not allowed in library mode`

Libraries cannot have a main() function. Remove it or compile as executable.

### CE3502 {#ce3502}

**Error** · library

**Message:** `library not found: '{lib}' (searched: {paths})`

Library bitcode and manifest files not found in search paths.

### CE3503 {#ce3503}

**Error** · library

**Message:** `library '{lib}' accepts compiler {requires}, this is {current}`

A source library is compiled by the CONSUMER's compiler, not the author's, so a library that built cleanly under one compiler can fail under a later one. Every .slib states `requires_compiler`; the default a build stamps is `~<major>.<minor>` of the building compiler, because pre-1.0 semver makes the minor the breaking unit. The escape, for an author testing a library forward against a new compiler, is --ignore-compiler-version. The check is skipped, never failed, when either version cannot be parsed.

### CE3504 {#ce3504}

**Error** · library

**Message:** `platform mismatch: library compiled for '{lib_platform}', current platform is '{current_platform}'`

Libraries must be compiled for the same platform they are used on.

### CE3505 {#ce3505}

**Error** · library

**Message:** `cannot determine the version of library '{lib}': {reason}`

A .slib records `library_version`. The value comes from `[package] version` in the nori.toml in the current directory when one exists, otherwise from an explicit --lib-version. A nori.toml in a parent directory or beside the sources is not read. Neither present is this error, and so is a --lib-version that CONTRADICTS the nori.toml -- the compiler does not choose one, because the package must not ship under a version it does not claim.

### CE3506 {#ce3506}

**Error** · library

**Message:** `corrupted library file '{path}': source section truncated (expected {expected} bytes, got {actual})`

The container's sibling of [CE3510](#ce3510)/[CE3511](#ce3511) for the source section between the metadata and the bitcode. Each of the three codes names the section that is short, so it tells a reader where the file was cut.

### CE3507 {#ce3507}

**Error** · library

**Message:** `failed to link library '{lib}': {reason}`

LLVM bitcode linking failed for the specified library.

### CE3508 {#ce3508}

**Error** · library

**Message:** `invalid library file '{path}': not a valid .slib file (bad magic)`

File does not start with SUSHILIB magic bytes.

### CE3509 {#ce3509}

**Error** · library

**Message:** `unsupported library format version '{version}' in '{path}' (compiler supports version {supported})`

Library was created with incompatible format version.

### CE3510 {#ce3510}

**Error** · library

**Message:** `corrupted library file '{path}': metadata section truncated (expected {expected} bytes, got {actual})`

Library file is incomplete or corrupted.

### CE3511 {#ce3511}

**Error** · library

**Message:** `corrupted library file '{path}': bitcode section truncated (expected {expected} bytes, got {actual})`

Library file is incomplete or corrupted.

### CE3512 {#ce3512}

**Error** · library

**Message:** `invalid library metadata in '{path}': {reason}`

MessagePack decoding failed or metadata schema is invalid.

### CE3513 {#ce3513}

**Error** · library

**Message:** `library file too large '{path}': {size} bytes exceeds maximum {max_size} bytes`

Library file exceeds reasonable size limit.

### CE3515 {#ce3515}

**Error** · library

**Message:** `cannot read library file '{path}': {reason}`

The operating system refused to open or to read the file: a directory, a file with no read permission, or an I/O failure. The reason names the cause in the words the slib-info tool uses for the same errno, so both halves of --lib-info say the same thing.

### CE3516 {#ce3516}

**Error** · library

**Message:** `'{path}' is not a library file: the name of a .slib file ends in .slib`

`--lib-info` reads a library, and a library file is named `<name>.slib`. Both halves of the command refuse another name before they open the file, with this code, so a file that happens to hold a library under another name is refused as well.

### CE3517 {#ce3517}

**Error** · library

**Message:** `cannot build library '{lib}': {reason} [{nori_code}]`

A --lib build reads the nori.toml in the current directory for the library version. A nori.toml that exists must be valid: every fault that nori's manifest reader refuses stops the build here, a file that is not TOML or UTF-8, a field of the wrong TOML type, and also a field with a bad value (a missing field, a bad package name, a bad version, a bad dependency), although the compiler reads the version alone. The user wrote a manifest and it is wrong, and a build that used --lib-version would hide the fault and the version conflict of [CE3505](#ce3505). The reason is the text of the nori code in brackets -- the one manifest reader makes the check for the compiler and for nori, so both name the file and the field in the same words. A missing nori.toml is not this error, and a nori.toml that cannot be read is [CE3518](#ce3518).

### CE3518 {#ce3518}

**Error** · library

**Message:** `cannot build library '{lib}': cannot read '{path}': {reason}`

A --lib build reads the nori.toml in the current directory for the library version. A nori.toml that exists but cannot be opened or read (no read permission, a directory, a broken link) stops the build here, and the reason is the one that the operating system gives. It is not a missing manifest: a build that used --lib-version would hide a manifest that the user wrote. A nori.toml that can be read and is not valid is [CE3517](#ce3517).

### CE3519 {#ce3519}

**Error** · library

**Message:** `library '{lib}' is in the build at two versions, {first} and {second}`

A library records every `use <lib/...>` that its units write, with the version of the library that its build found, and the consumer's build loads the whole graph. A library is identified by the `library_name` stamped into its `.slib`, so the graph holds ONE copy of each library: a body compiled against one version and linked against another is a wrong program with no diagnostic. When two paths of the graph reach two versions of one library -- a library was built against 0.1.0 and the consumer's search finds 0.2.0, or two imports find two files -- the build stops here. Each note names a version and the path that reached it: the consumer's `use`, or the library that recorded it. The escape is to rebuild the library that recorded the old version against the one that the search finds, or to put the version that it was built against first on SUSHI\_LIB\_PATH.

## CE4xxx: Perk errors {#ce4xxx}

These errors are about perks, perk implementations and perk constraints.

### CE4001 {#ce4001}

**Error** · perk

**Message:** `duplicate perk definition: {name}`

A perk with this name has already been defined. Each perk must have a unique name.

### CE4002 {#ce4002}

**Error** · perk

**Message:** `type {type} already implements perk {perk}`

**Help:** `Sushi has no specialization: implement the perk once, on the template or on each concrete target`

A perk can only be implemented once for each type. Remove the duplicate implementation.

### CE4003 {#ce4003}

**Error** · perk

**Message:** `unknown perk: {perk}`

**Help:**

- `` '{unit}' declares it; add `use "{unit}"` above to name it here ``
- `` '{module}' declares it; add `use <{module}>` above to name it here ``
- `` library '{library}' declares it; add `use <lib/{library}>` above to name it here ``

The perk is not in the scope of this unit, in an implementation, a constraint or a pack constraint. When no unit declares the perk, define it with 'perk \{perk\}:'. When another unit or a library declares it, the help names the import that brings it: scope is per unit and not transitive, so a plain `use` in another unit does not bring it here. The predefined perks are in every scope.

### CE4004 {#ce4004}

**Error** · perk

**Message:** `method {method} signature does not match perk {perk} requirement`

**Help:** `'{perk}' requires {contract}`

The implementation method signature must exactly match the signature declared in the perk definition.

### CE4005 {#ce4005}

**Error** · perk

**Message:** `missing required method {method} for perk {perk}`

The perk implementation is missing a required method. All methods declared in the perk must be implemented.

### CE4006 {#ce4006}

**Error** · perk

**Message:** `type {type} does not implement perk {perk} required by constraint`

A type constraint requires the type to implement a specific perk. Add an implementation with 'extend \{type\} with \{perk\}:'. A call, a written type or a constructor in a generic body passes a type parameter on to another generic. The parameter satisfies a constraint only when a constraint of its own promises it: add the constraint to the type parameter of the caller. An extension or a perk implementation can add a bound in its target (`extend List@(T: Clone)`); a call on a receiver whose type argument does not satisfy it is refused at the call. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE4007 {#ce4007}

**Error** · perk

**Message:** `method {method} conflicts with perk method from {perk}`

A regular extension method has the same name as a perk method. Rename one of the methods to avoid ambiguity.

### CE4010 {#ce4010}

**Error** · perk

**Message:** `perk {name} cannot have type parameters`

**Help:** `a perk contract declares no type parameters, so neither does its implementation; a generic method is a plain extension method ('extend T name@(U)(...)')`

Perks cannot be generic. Remove the @(...) type parameter list; constrain generic functions with '@(T: \{name\})' instead.

### CE4011 {#ce4011}

**Error** · perk

**Message:** `cannot {action} private perk '{name}' from unit '{current_unit}' (perk is defined in '{owner}')`

A perk carries `public` and is private by default, and what a private perk hides is the CONTRACT (see [the visibility design](design/visibility.md)). Another unit may not implement it and may not constrain a type parameter with it, because both of those are promises about the perk itself. Calling a method it provides is untouched: method resolution is blind to the caller, so a unit that can name the type can call what the type implements. This is a different rule from [CE3005](#ce3005), which is about naming a declaration; a perk contract has its own code because it has its own answer -- the method stays reachable while the contract does not. Mark the perk `public`, or ask its unit for a function that does the work.

### CE4012 {#ce4012}

**Error** · perk

**Message:** `'Drop' cannot be implemented for '{type}' here: only unit '{owner}' declares that type`

This is the orphan rule, narrowed to one perk: only the unit that declares a type may implement `Drop` for it. A consumer may replace a library's implementation of an ordinary perk with its own `extend X with P`. For `Drop` that would let a consumer stop a handle from closing with no diagnostic, so the type that owns a resource is the only one allowed to say what releasing it means. Add the implementation to the unit that declares the type, or ask that unit for a function that does the work.

### CE4014 {#ce4014}

**Error** · perk

**Message:** `perk '{perk}' cannot hold the static method '{method}'`

**Help:** `declare it as a plain extension method on the type ('extend T static name(...)'); a perk contracts instance methods only`

A perk has no `Self`, so a contract cannot say 'returns one of me' and a constructor has no signature to declare there. The compiler refuses a `static` in a perk implementation and points at the marker. Declare the static as a plain extension method on the type (`extend Vec static at(...)`) -- it is as visible as the type either way -- and leave the perk to the instance methods it can contract.

### CE4015 {#ce4015}

**Error** · perk

**Message:** `perk '{perk}' gives '{method}' a second home: perk '{other}' already provides it`

A name has exactly one home on a type (see [the method-resolution design](design/method-resolution.md)). Two perks that each provide a method of one name on one type leave a call of that name naming neither implementation, and the two bodies would take one symbol. The note points at the first one. Rename the method of one perk, or implement only one of them. A derived method is not a home: a type that derives `compare` from `Ord` may still implement a user perk that provides `compare`, and an explicit call then reads the implementation. The rule holds for a type parameter too: two constraints of one parameter that both declare a method of one name give it two homes, and the body cannot call the method. Remove one constraint, or rename the method of one perk. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE4016 {#ce4016}

**Error** · perk

**Message:** `'Drop' cannot be implemented for '{type}': no unit declares that type`

The orphan rule of [CE4012](#ce4012), at a type no unit declares: a primitive, a `string`, a fixed or a dynamic array (an array template `extend T[]` included), `List`, `HashMap`, `Own`, `Maybe`, `Result` and the predefined error enums. Only the unit that declares a type may say what releasing it means, and no unit declares one of these. The compiler gives these types their own release. A resource needs a type of its own: declare a struct that holds the value and implement `Drop` on that struct.

### CE4017 {#ce4017}

**Error** · perk

**Message:** `'Clone' cannot be implemented for '{type}': the compiler decides it`

`Clone` is a structural perk. A type satisfies it when it holds no resource, that is when it is not a `Drop` type and holds none in a field, an element or a payload. The built-in `.clone()` is the contract, so an implementation would have no method to override. A handle has no `Clone` on purpose: a copy would close one descriptor twice. `.share()` is the way to get a second owner of a handle. Remove the implementation.

### CE4018 {#ce4018}

**Error** · perk

**Message:** `cannot clone '{type}': the type parameter '{param}' has no constraint 'Clone'`

A generic body is checked one time, where it is written. A type parameter is opaque there: the body knows only what its constraints promise. `.clone()` makes a deep copy, and a deep copy of a value that holds a resource is a second handle. So a body can clone a value of a type parameter, or of a type that holds one (a `List@(T)`, a `Box@(T)`), only when the parameter declares the constraint `Clone`. Every type that holds no resource satisfies `Clone`. A handle does not; `.share()` is the way to get a second owner of a handle. Add `Clone` to the constraints: `@(T: Clone)`. The note points at the type parameter. [CE2431](#ce2431) is the refusal of a concrete type that holds a resource. An element of a pack, which an `expand` binds, is a type parameter for this rule: it has what the constraints of the pack promise, and nothing more.

### CE4019 {#ce4019}

**Error** · perk

**Message:** `'Drop' for '{target}' cannot add the bound '{param}: {perk}'`

**Help:** `put the bound on the type ('struct {base}@({param}: {perk})'), or remove it`

A `Drop` implementation says how each value of a type releases what it holds. A bound in its target gives `drop()` to some instances of the type and not to others, and an instance that the bound excludes releases nothing, with no diagnostic. So a `Drop` implementation inherits the bounds that its type declares and adds none. An array target is [CE4016](#ce4016).

### CE4020 {#ce4020}

**Error** · perk

**Message:** `cannot call 'drop()' of the perk 'Drop' on '{type}': the compiler runs it at the scope exit`

**Help:** `to end the value early, let it leave its scope, give it to a function with 'nom', or declare a 'nom self' method on the type (as 'close()' on a stdlib handle)`

The compiler runs the `drop()` method of the predefined `Drop` perk at the scope exit of each value, one time. The contract takes `poke self`, so a written call does not end the life of its receiver: the value stays live, and the scope exit runs `drop()` again. Thus a written call of the `drop` method of `Drop` is an error in every position: on a value, through a `Drop` constraint, and on a field inside a destructor (the compiler destroys the owning fields after `drop()`). The rule reads the perk, not the name: a method `drop` of a user perk is an ordinary method. To end a value early, let it leave its scope, give it to a function with `nom`, or declare a `nom self` method on the type (the `close()` pattern of the stdlib handles). Rust refuses the same call (E0040, explicit use of a destructor method).

## CE5xxx: Foreign function interface errors {#ce5xxx}

These errors are about `unsafe external` blocks and the `ptr` type.

### CE5001 {#ce5001}

**Error** · ffi

**Message:** `external link-name '{symbol}' is declared with another signature`

One C symbol has one signature in one program. Two `unsafe external` declarations of one link name agree only when each position has the SAME C type: each parameter, the return and `var_arg`. `i32` against `u32` does not agree, because `int` and `unsigned int` are two C types, and a C compiler refuses the pair. A fixed signature against a `var_arg` signature does not agree, also when the fixed parameters are equal. Two spellings of one C type agree: `string` and `Maybe@(string)` are both `char*`, `ptr` and `Maybe@(ptr)` are both `void*`, and a link name written as a constant is the name it folds to. Two external variables of one link name must have the same C type, and a function and a variable cannot share one link name. The rule reads every declaration in the program, in each unit and across units, the standard library units written in Sushi included. The three libc symbols that are the compiler's own, `malloc`, `free` and `exit`, are the first declaration of their names, so a user declaration of one of them must have the compiler's C types. Every other libc symbol that the compiler declares is independent of a user declaration: each call goes through the type of its own declaration. The note points at the other declaration. Make the two signatures the same, or remove one declaration.

### CE5002 {#ce5002}

**Error** · ffi

**Message:** `` public function '{name}' exposes a foreign `ptr` and cannot appear in a library (.slib) public API ``

FFI is a private implementation detail of a unit. Externals and any public function whose signature exposes a foreign `ptr` cannot propagate through Nori packages.

### CE5003 {#ce5003}

**Error** · ffi

**Message:** `external signature uses non-C-ABI type '{type}'`

External (FFI) signatures are limited to C-representable types: i8..i64, u8..u64, f32, f64, bool, string (auto-marshalled), ptr, and ~ (void), plus `Maybe@(string)` and `Maybe@(ptr)` at the top level of a parameter or a return, which say that the pointer may be NULL: a NULL return answers `Maybe.None`, and a `Maybe.None` argument crosses as NULL. A PARAMETER may also be a byte buffer, `u8[]`, `peek u8[]` or `poke u8[]`, which crosses as the pointer to its first byte; a `u8[]` return is refused, because C cannot answer a Sushi array. Every other Maybe (`Maybe@(i32)`, a nested one), a Result, a struct, any other array (`i32[]`, a fixed `u8[N]`), a reference and a user type cannot cross the C ABI boundary.

### CE5004 {#ce5004}

**Error** · ffi

**Message:** `variadic external '{name}' requires at least one fixed parameter`

A variadic `unsafe external` declaration (trailing `...`) must declare at least one fixed parameter. The C ABI's va\_start needs a named argument to anchor the variadic argument list.

### CE5005 {#ce5005}

**Error** · ffi

**Message:** `non-C-ABI type '{type}' passed as variadic argument to external '{name}'`

Each trailing variadic argument to an external call must be C-representable: i8..i64, u8..u64, f32, f64, bool, string (auto-marshalled), or ptr. Result/Maybe, structs, arrays, references, and user types cannot cross the C ABI boundary.

### CE5006 {#ce5006}

**Error** · ffi

**Message:** `public generic '{name}' cannot be exported: it references un-shippable library symbol '{symbol}'`

A public generic is shipped in a library (.slib) and monomorphized at the consumer. Library-private helpers it references ship automatically as part of the export closure (as templates if generic, as linkable signatures if concrete, with values for constants). Two classes of reference cannot cross the boundary: a symbol whose signature exposes a foreign 'ptr' (FFI is a private unit detail, see [CE5002](#ce5002)), and an 'unsafe external' namespace (foreign bindings cannot be re-declared at the consumer). Wrap the foreign detail behind a private helper with a C-ABI-free signature, or restructure the generic to avoid it.

### CE5007 {#ce5007}

**Error** · ffi

**Message:** `library '{lib}' ships private symbol '{name}' which conflicts with a local definition`

An imported library's exported generics depend on this private helper, which ships in the .slib export closure and must be registered at the consumer under its original name. A local symbol with the same name would silently change what the library's monomorphized bodies call. Rename the local symbol.

### CE5008 {#ce5008}

**Error** · ffi

**Message:** `` public {kind} '{name}' exposes a foreign `ptr` in its signature and cannot cross a unit boundary ``

FFI is a private implementation detail of a unit. A public declaration whose parameters, return type or error arm contain `ptr` (including inside Result or Maybe) cannot be part of a unit's public API. `{kind}` says which declaration it was: a public generic, an extension method and a perk method are all reached. Keep the function private, or wrap the pointer in a struct (struct fields may carry `ptr` across units).

### CE5009 {#ce5009}

**Error** · ffi

**Message:** `` foreign `ptr` used in a unit with no `unsafe external` block ``

The `ptr` type may only be named in a unit that declares an `unsafe external` block - no danger zone, no ptr. This keeps every file that can traffic in raw foreign handles greppable by its `unsafe external` marker. Other units hold handles through wrapper structs declared in the FFI unit. The rule reads two walks, because a body spells the name exactly as a signature does: a local, a `foreach` item, a cast target, a lambda parameter and a call-site type argument are all naming positions.

### CE5010 {#ce5010}

**Error** · ffi

**Message:** `` foreign `ptr` cannot be used with operator '{op}' ``

A `ptr` is an opaque handle: it has no comparable identity, no arithmetic, and no truthiness. There is nothing to test for null either: a null is never a Sushi value. A C function that may answer NULL is declared `Maybe@(ptr)` and its NULL arrives as `Maybe.None`, while a plain `ptr` return asserts non-null and a NULL there is [RE2025](#re2025) at the call.

### CE5011 {#ce5011}

**Error** · ffi

**Message:** `` foreign `ptr` has no method '.{method}()' ``

A `ptr` has one closed set of methods, the foreign-memory methods: `load_<width>(off)` and `store_<width>(off, v)` for each integer and float width, `load_ptr(off)` (a `Maybe@(ptr)`), `store_ptr(off, q)`, `offset(n)` and `to_string(off)`, each at a byte offset. It has nothing else: no hash, no string form, no extension method. Pass it back to an external function, or wrap it in a struct and attach extension methods to the struct.

### CE5012 {#ce5012}

**Error** · ffi

**Message:** `` foreign `ptr` cannot be a type argument of '{base}' ``

Only Result@(ptr, E) and Maybe@(ptr) support carrying a foreign `ptr`. Other generic containers (HashMap, List, user-defined generics) cannot store an opaque handle. Wrap the pointer in a concrete struct and store that instead.

### CE5013 {#ce5013}

**Error** · ffi

**Message:** `external link-name '{symbol}' names a symbol this program defines`

An `unsafe external` reaches OUT of the program: it may name a foreign symbol, never one this build defines. A program's units share one LLVM module and a linked library's module is merged into it, so a declaration and a definition of one name would UNIFY -- the declaration would then enter the program's own body with no ABI check, and code that may not call a library-PRIVATE body could run it. Rename the link-name, or call the Sushi function directly. The rule reads every symbol this build defines: the function and constant tables, the linked libraries, and the symbols the standard library GENERATES -- those last are in no semantic table, so the compiler reads the manifest the stdlib build writes beside its bitcode, plus a small reserved set for the ones the backend emits inline. A generated name is refused whether this program links the unit or not. A function or a constant is compared by the SYMBOL it emits, not by its Sushi name: `<unit>$<name>` (`main` alone has no prefix), or the `link_symbol` that a binary library's manifest records. A wrapper can have the name of the C function it calls, and a link name that spells an emitted symbol is refused. [CE5001](#ce5001) is the neighbouring rule for a built-in extern DECLARATION, which LLVM deduplicates when the signatures match.

### CE5014 {#ce5014}

**Error** · ffi

**Message:** `` `errno()` is read in a unit with no `unsafe external` block ``

`errno()` answers the calling thread's `errno`, the cause a failed C call leaves behind. Only a C call can leave one, and only a unit that declares an `unsafe external` block can make a C call, so the built-in has the same confinement as the `ptr` type ([CE5009](#ce5009)): no danger zone, no errno. A unit's own `fn errno` is an ordinary declaration and wins over the built-in anywhere. Read `errno()` directly after the failed call and before any `close`, `free` or other C call, because those can overwrite it.

### CE5015 {#ce5015}

**Error** · ffi

**Message:** `the link name of external '{name}' is a constant of type {type}, not a string`

The link name after `=` in an `unsafe external` declaration is a string literal or a string constant: `= "stat"`, `= STAT_SYMBOL`, or `= platform.STAT_SYMBOL`. A constant is the form for a symbol that differs per platform, such as `stat$INODE64` on macOS x86\_64, and `<sys/platform>` holds those names. The constant is folded in the unit that declares the block, and [CE5013](#ce5013) and [CE5001](#ce5001) read the folded name. A constant of another type names no symbol.

### CE5016 {#ce5016}

**Error** · ffi

**Message:** `external variable '{name}' is read-only`

A `var` in an `unsafe external` block declares a C global variable, and Sushi reads it: each read loads the global at that moment. A write to a C global (`libc.optind := 1`) is refused, because nothing says who else reads the global or when. Call a C function that sets the global, or keep the value in a Sushi variable.

## CE6xxx: Syntax errors {#ce6xxx}

The parser gives these errors.

### CE6001 {#ce6001}

**Error** · syntax

**Message:** `unexpected token '{token}'`

**Help:**

- `use 'if (condition):' instead of 'if condition:'`
- `use 'elif (condition):' instead of 'elif condition:'`
- `use 'while (condition):' instead of 'while condition:'`
- `a perk method declares no type parameters; a generic method is a plain extension method ('extend T name@(U)(...)')`
- `a double-quoted string cannot stand inside an interpolation hole; use single quotes inside the hole, or bind the expression to a local first`

The parser reached a token that cannot appear here.

### CE6002 {#ce6002}

**Error** · syntax

**Message:** `unexpected character '{char}'`

**Help:** `a double-quoted string cannot stand inside an interpolation hole; use single quotes inside the hole, or bind the expression to a local first`

The character is not part of any Sushi token.

### CE6003 {#ce6003}

**Error** · syntax

**Message:** `unexpected end of file`

The source ended in the middle of a construct.

### CE6004 {#ce6004}

**Error** · syntax

**Message:** `inconsistent indentation: dedent to column {got}, expected column {expected}`

A dedent must return to a column that an enclosing block opened.

### CE6005 {#ce6005}

**Error** · syntax

**Message:** `could not tokenize input`

The lexer failed on this input.

### CE6006 {#ce6006}

**Error** · syntax

**Message:** `malformed numeric literal '{literal}': {reason}`

**Help:** `write '{suggestion}'`

An underscore groups the digits of a numeric literal. One underscore, between two digits -- so not after the base prefix, not doubled, and not next to a point or an exponent marker.

### CE6010 {#ce6010}

**Error** · syntax

**Message:** `could not parse the interpolated expression '{expr}'`

The text between \{braces\} in a string literal must be a valid expression.

### CE6011 {#ce6011}

**Error** · syntax

**Message:** `a documentation block is opened here and never closed`

A `##:` opens a documentation block, and a `:##` closes it. The closer is on the opening line, or it is line-initial. The compiler does not read an unmatched opener as a comment, because that would remove a whole documented API from a build with no diagnostic.

### CE6012 {#ce6012}

**Error** · syntax

**Message:** `a documentation block is closed here, but never opened`

A `:##` closes a documentation block that a `##:` opened. The two delimiters are different, so the compiler can say which of the two mistakes was made.

### CE6013 {#ce6013}

**Error** · syntax

**Message:** `a documentation block is opened inside a documentation block`

Documentation blocks do not nest: a block ends at the first closer that qualifies. A line-initial `##:` in the interior means the enclosing block swallowed the blocks between the two openers. This is the signal GCC gives for a `/*` inside a block comment.

### CE6014 {#ce6014}

**Error** · syntax

**Message:** `malformed byte literal '{literal}': {reason}`

A byte literal `a'x'` holds exactly one character from 0 to 127, or exactly one escape: `\n`, `\t`, `\r`, `\0`, `\\`, `\'`, `\"` or `\xNN` with two hex digits. The reason names the fault: empty, more than one character, unknown escape, or `\x` without two hex digits.

### CE6015 {#ce6015}

**Error** · syntax

**Message:** `the character '{char}' is not one byte`

**Help:** `'{char}' is {count} bytes in UTF-8 ({bytes}); write a'\xNN' for the byte {value}, or compare with the string "{char}"` (the escape is left out when the character is above 255)

A Sushi string is UTF-8, so a character above 127 is two to four bytes, and one byte literal cannot hold it. The help gives the UTF-8 bytes and the escape for a byte from 128 to 255. No character set is assumed.

### CE6101 {#ce6101}

**Error** · syntax

**Message:** `nested function definitions are not supported`

**Help:** `move the function to the top level, or use a lambda`

A function may only be defined at the top level. Use a lambda for a local callable.

### CE6102 {#ce6102}

**Error** · syntax

**Message:** `explicit type arguments are only supported on direct function calls`

**Help:** `call the generic function directly, e.g. foo@(i32)(x)`

The `@(...)` type-argument list may appear only on a call to a named free function, not on a method call or an indirect call.

### CE6103 {#ce6103}

**Error** · syntax

**Message:** `` a perk implementation method cannot be marked `public` ``

**Help:** `an implementation is as visible as its target type; mark the type instead`

An implementation carries no marker, because it is exactly as visible as the type it is attached to (see [the visibility design](design/visibility.md)). A private type cannot be named, constructed or received in another unit, so its methods are already unreachable there, and a public type's methods are reachable wherever the type is. Remove the marker, and mark the target type instead.

### CE6104 {#ce6104}

**Error** · syntax

**Message:** `the named argument '{name}:' is only supported in a struct construction`

**Help:** `pass the arguments in declaration order`

A named argument names a FIELD, and a struct declaration is the only declaration that gives a name to each position at the call site. A function parameter, a method parameter and an enum payload have no such name, so a label there has no meaning: `p.moved(dy: 5)` against `(i32 dx, i32 dy)` would fill the wrong slot. The grammar takes `name: value` in every argument list, because the parser cannot know what the callee is; the typecheck pass refuses the label where the callee is known, as it does for [CE6102](#ce6102). Write the arguments in declaration order.

### CE6105 {#ce6105}

**Error** · syntax

**Message:** `invalid tuple element: {reason}`

**Help:**

- `` write a type in each position, as in `(i32, string)` ``
- `` a record with names is a struct; write the types alone, as in `(i32, i32)` ``
- `` name the element, as in `i32 x`, or discard it with `_` ``

A tuple TYPE and a `let` destructure share one list rule, `(elem, elem, ...)`, and the token after the `)` tells them apart: `=` is a destructure, a NAME is a typed binding. The AST builder judges each element by its position. In a TYPE position every element is a type and nothing else: a name (`(i32 quot, i32 rem)`) and a `_` are refused, because a tuple has no named elements -- a record with names is a struct. In a DESTRUCTURE position an element is a bare name (`q`, the type is the element type), a typed name (`i32 q`), a `_`, or a nested destructure (`(a, b)`); a type with no name (`i32` alone) binds nothing and is refused. See [the tuple design](design/tuples.md).

### CE6106 {#ce6106}

**Error** · syntax

**Message:** `'.{index}' is not a tuple element index: {reason}`

**Help:** `` write the element number in decimal, as in `t.0` or `t.0.1` ``

A tuple element is read with `.N`, where N is a plain decimal number that starts at 0: `t.0`, `t.1`, and `t.0.1` for an element of a nested tuple. The element type depends on the index, so the index is a literal and never a value (`t[i]` is not a tuple access). The lexer reads `t.0.1` as `t` `.` and the number `0.1`, and the builder splits it in two steps; a number with an underscore (`t.0_1`), an exponent (`t.1e3`) or a leading zero (`t.01`) names no element and is refused here. See [the tuple design](design/tuples.md).

### CE6107 {#ce6107}

**Error** · syntax

**Message:** `a tuple element takes no mode: '{mode}'`

**Help:**

- `a tuple holds values; write the element type alone`
- `a binder owns its element from an owned value and borrows it from a borrow; remove the mode`

A destructure element is a name, a typed name, a `_` or a nested destructure, and it carries no `peek`, `poke` or `nom`: a binder OWNS its element when the destructure takes an owned value (a temporary, or an owned local, which the destructure spends), and BORROWS it when the value is a borrow (a parameter, a field, a binding). So `let (peek i32 a, b) = t` is refused. A tuple TYPE takes no mode on an element either: a tuple holds values, and a reference is a parameter or a `let` mode. See [the tuple design](design/tuples.md).

### CE6109 {#ce6109}

**Error** · syntax

**Message:** `'{place}' is written twice in one destructuring rebind`

**Help:** `write each place once; a second assignment would replace the first`

A destructuring rebind `(a, b) := v` evaluates all of `v` first and then assigns each target from left to right. A place written twice, as in `(a, a) := (1, 2)` or `(p.x, p.x) := f()`, gets two values, and the second assignment silently replaces the first. So the same place twice in the target, nested targets included, is refused, with a note at its first position. The check compares the written places: a name, a field chain, a tuple element, and an index that is a literal or a name. Two different places of one value are legal: `(p.x, p.y) := (p.y, p.x)` is a swap, and `(i, xs[i]) := (1, 9)` assigns `i` first, so `xs[i]` reads the new `i`. See [the tuple design](design/tuples.md).

### CE6110 {#ce6110}

**Error** · syntax

**Message:** `the bound '{param}: {perks}' can stand only in the target of an 'extend'`

**Help:** `write the bound in the type-parameter list of the declaration: '@({param}: {perks})'`

The target of an `extend` can put a bound on a type parameter: `extend List@(T: Clone) filter(...)`, `extend (T: Clone)[] filter(...)`. The grammar accepts `NAME: Perk` in every type-argument list, because the parser cannot know the position. The AST builder refuses it in every other position: a `let` type, a parameter type, a return type, a field, an explicit type argument of a call, a conversion source, and a position inside a target argument (`extend Box@(List@(T: Clone))`). A function, a struct and an enum write the bound in their own type-parameter list.

### CE6111 {#ce6111}

**Error** · syntax

**Message:** `'dont_panic' cannot stand on {position}: {reason}`

**Help:**

- `write the marker on a named function that holds the loop`
- `write the marker on the implementation method`
- `remove the marker; a C function has no index for Sushi to uncheck`

The `dont_panic because "<reason>"` marker removes the bounds check of each `[]` in the body of the declaration that carries it. So it stands only on a named declaration with a body: a free function, a static, an extension method, a perk implementation method and a conversion. A perk contract method and an extern have no body, and the marker on one would remove nothing. A lambda is refused because the lift pass makes it a function of its own, which can run after the marked function returned, outside the guards that the `because` text states. The marker also does not reach a lambda in a marked body: the indexes there keep their check. See [the dont_panic design](design/dont-panic.md).

## CE7xxx: Documentation block errors {#ce7xxx}

These errors are about what a documentation block (`##: ... :##`) tells about its declaration.

### CE7001 {#ce7001}

**Error** · docs

**Message:** `the '- Parameter {name}:' tag names no parameter of '{callable}'`

The tag names the thing the declaration DECLARES, so the compiler can tell that no such parameter exists. A renamed parameter and a copied tag both land here.

### CE7002 {#ce7002}

**Error** · docs

**Message:** `the parameter '{name}' is documented twice`

A `- Parameter` tag is keyed by the name it carries: many are legal, one for each parameter. Two for one name is almost always a tag that was copied and not renamed.

### CE7003 {#ce7003}

**Error** · docs

**Message:** `the '- {tag}:' tag may appear only once`

`- Returns:` and `- Errors:` are singletons: a declaration has one success value and one error arm. This is a different mistake from [CE7002](#ce7002) -- that one is a tag that was not renamed, this one is a tag written twice -- and the two are fixed differently.

### CE7004 {#ce7004}

**Error** · docs

**Message:** `'{word}' is not a documentation tag`

**Help:** `` did you mean `- {suggestion}:`? ``

A list item shaped `- <Word>:` whose word is close to a tag keyword is a typo, not prose. If the compiler read it as text, the misspelled tag would disappear with no diagnostic. A word further from every keyword, such as `- Note:`, stays prose.

### CE7005 {#ce7005}

**Error** · docs

**Message:** `a documentation block in a body must be the first item there`

**Help:** `a block in a body documents the function around it, so it goes first; move it above the declaration to document something else`

A block that is first in a body documents the function that encloses it. A block between two statements has no declaration it could plausibly have meant, so there is nothing to guess at.

### CE7006 {#ce7006}

**Error** · docs

**Message:** `'{name}' is documented twice: from above, and from inside its body`

The two positions document the same declaration, so a declaration that uses both says which one is the documentation twice over. Keep one of them.

### CE7007 {#ce7007}

**Error** · docs

**Message:** `the '- Example:' tag introduces no fenced block`

**Help:** ```` the tag introduces a fenced block; open one with ```sushi on the next line, or delete the tag ````

The whole job of the tag is to introduce a fenced block of Sushi, so a tag with nothing to introduce contradicts itself the way a `- Parameter q:` that names no parameter does. This check is always on. An ABSENT example is reported only with `--warn-missing-docs`.

### CE7008 {#ce7008}

**Error** · docs

**Message:** `this documentation fence is never closed`

**Help:** `` close it with a run of the same character that is at least as long, before the block's own `:##` ``

A doc block ends at its own `:##`, so a fence that runs past it is truncated, and a truncated example is not one. Close the fence with a run of the SAME character that is at least as long.

## CWxxxx: Warnings {#cwxxxx}

A warning makes the compiler exit 1, if there is no error. Some warnings are off by default: `--warn-missing-docs` and `--warn-unused` turn them on.

### CW0001 {#cw0001}

**Warning** · general

**Message:** `missing trailing newline`

Source file should end with a newline character.

### CW0002 {#cw0002}

**Warning** · general

**Message:** `cannot write LLVM IR to '{path}': {reason}`

`--write-ll` asked for the IR beside the output, and the file could not be written, for example because the path is a directory or a directory on it is a regular file. The build itself succeeded and the binary or the library is written; only the IR is missing. Fix the path, or choose another output with `-o`.

### CW0003 {#cw0003}

**Warning** · general

**Message:** `'{flag}' has no effect {reason}`

The command line names a flag that the build it asks for does not read, so the flag is ignored. `--docs` is read only by `--lib-info`; `--lib-kind` and `--lib-version` only by a `--lib` build; `--keep-object` only by a build that writes one object file, which a `--lib` build and the incremental build of a program of more than one unit do not (add `--no-incremental` to keep the object); `--write-ll` is not written by the incremental build either; `--dont-panic` is read only by a build that compiles a function marked `dont_panic` outside the bundled stdlib. The build goes on without the flag.

### CW0004 {#cw0004}

**Warning** · general

**Message:** `{subject} is marked dont_panic, but its body has no index to uncheck`

**Help:** `remove the marker; an index in a lambda keeps its check`

The `dont_panic` marker removes the bounds check of each `[]` in the body that carries it. A marked body with no `[]` removes nothing, so the author believes that a check went and none did. An index in a lambda in the body does not count, because a lambda keeps its checks. The warning is always on, and it is for the author: a user unit and a bundled stdlib unit get it, and a source library's unit in a consumer's build does not. Remove the marker. See [the dont_panic design](design/dont-panic.md).

### CW1001 {#cw1001}

**Warning** · scope

**Message:** `unused {kind} '{name}'`

A name was declared and nothing reads it. The message says which kind of name it is: a `variable` is declared with `let` or bound by a `foreach` item or a pattern, and a `parameter` is a parameter of a function, a method or a lambda. The binder of an `expand` is reported as a `foreach` item is, and `expand(_ in args)` discards the element. A template body is checked once, where it is written, also when no call instantiates it, and a copy of the template does not report the name again. Read the name, remove it, or write `_` where the position takes a discard.

### CW1002 {#cw1002}

**Warning** · scope

**Message:** `declared variable '{name}' already exists in an outer scope`

A variable was declared with 'let' outside of this scope. A `foreach` item, an `expand` binder and a pattern binding are declarations too.

### CW1004 {#cw1004}

**Warning** · scope

**Message:** `private {kind} '{name}' is never used in this unit`

Behind `--warn-unused`, off by default. A private declaration is visible only in its own unit, so the unit is the whole question: the declaration is dead when nothing reachable from a ROOT of the unit names it. The roots are every `public` declaration, every `extend` block (an extension method and a perk implementation alike, because a call reaches it through a receiver and not through a name), the `unsafe external` blocks, and `fn main()`. A private declaration named only by another dead one is dead too, so both are reported. A bundled stdlib unit is checked only when the test runner sets SUSHI\_STDLIB\_DEAD\_GATE; a library unit never is. Delete the declaration, or make it `public` when it is API.

### CW2001 {#cw2001}

**Warning** · type

**Message:** `unused '{ty}' value (handle it with match, .realise(default), or '??' in a body with a channel)`

A call to a function with a '| E' channel answers a Result@(T, E), and a statement that drops it loses the error. Handle it with `match`, take the value with `.realise(default)`, or propagate it with `??` in a body that has a channel itself. A `~ | E` result is exempt: there is no value to lose, and the error is still the caller's to ignore.

### CW3001 {#cw3001}

**Warning** · unit

**Message:** `duplicate use statement for unit '{unit}'`

A unit was already imported earlier in this file. The duplicate use statement has no effect.

### CW3002 {#cw3002}

**Warning** · unit

**Message:** `'{name}' shadows the {kind} '{owner}' exports`

A program's own declaration takes priority over a name that a library of any kind (source, binary or hybrid) or a bundled stdlib module exports, and that is legal: a private function is emitted with internal linkage, so the two are separate symbols. The consumer's call binds to the consumer's declaration, and the library's own body keeps calling its own. Both declarations may be public: an unqualified name with two candidates is [CE3012](#ce3012) at the use. It warns because shadowing an export is rarely intended, and because the reader of the call site cannot see which of the two answers it. Rename your declaration, or keep it and accept that the library's body is unaffected. A name the library declares privately is shadowed the same way and says nothing, because each declaration carries the unit that declared it; a library TYPE is still one name for the program ([CE3011](#ce3011)). Write `use <lib/name> as alias` to put the export behind a dot and take the shadow away. The warning is the same for each library kind: a source library's export is found in its unit, a binary or hybrid library's export in its manifest, which lists every public function and template.

### CW3003 {#cw3003}

**Warning** · unit

**Message:** `this library extends '{type}', a type it does not declare`

The warning fires at `--lib` build time and nowhere else: shipping is when the claim becomes other people's problem, and it is the moment the author is present. A method is found on the receiver's type, so an extension puts its method name on the type for every consumer of the library, and a second library that claims the same name on the same type makes the two unusable together -- [CE0101](#ce0101), at a consumer who can edit neither. A builtin target is not exempt: `i32` is the most collidable target of all, because every unit of every program can reach it. A perk implementation does not warn, because the consumer's own implementation is the sanctioned override, so that claim has an escape. A conversion (`extend IoError as LibError:`) does not warn either: it puts no method name on its source, and only the unit that declares the target may declare it. An extension inside an ordinary program stays silent, `extend i32 squared()` is idiomatic Sushi there. To ship the method without the claim, declare your own wrapper type and extend that; to accept the claim, publish it -- `--lib-info` lists it under 'Foreign Extensions'.

### CW3004 {#cw3004}

**Warning** · unit

**Message:** `'{alias}' binds an empty namespace`

The import brought no name that a qualified form could reach, so the `as` clause does nothing. The import still did its work: for example, a unit that is nothing but `extend` blocks exports methods rather than names. Drop the `as`.

### CW3005 {#cw3005}

**Warning** · unit

**Message:** `` `public use` of '{origin}' re-exports nothing ``

The import brought no public name to hand on, so the `public` marker does nothing: the unit's importers get exactly what they would get without it. A method interface such as the directory import `<collections>` brings no name (a `public use` of one still opens its methods to the importers), and a unit of nothing but `extend` blocks exports methods rather than names. The import itself still did its work for this unit. Drop the `public`, or make the imported unit export something.

### CW3006 {#cw3006}

**Warning** · unit

**Message:** `'{import_}' brings nothing this unit names`

Behind `--warn-unused`, off by default. The unit writes no name the import brings: no function, constant, type or perk it declares or re-exports, no member behind its alias, no extension or perk method it declares, and for `<collections/strings>` no string method the module enables (the per-unit rule [CE3015](#ce3015) reads). A `public use` re-exports to the unit's own importers and is never reported, and neither is an import of a library. Delete the line.

### CW3007 {#cw3007}

**Warning** · unit

**Message:** `'{name}' hides the public extension method that '{owner}' declares`

This unit declares an extension method, and it imports a unit that declares a public extension method of the same name on the same type. That is legal. A call in this unit calls the method of this unit, and the other unit keeps calling its own. It is a warning because a reader of a call cannot see which of the two methods answers it. Rename the method of this unit to call the imported one, or keep it.

### CW3506 {#cw3506}

**Warning** · library

**Message:** `library perk implementation for '{type}' could not be loaded and was skipped`

A perk implementation shipped by a library failed to deserialize. Methods it provides will be unavailable unless the consumer supplies its own.

### CW5001 {#cw5001}

**Warning** · type

**Message:** `` unsafe external block suspends four Sushi guarantees (add `because "..."` to acknowledge) ``

**Help:** `` acknowledge with `because "<reason>"` and use a safe wrapper ``

An `unsafe external` block disables borrow checking, RAII, Result/Maybe error handling, and bounds/null safety for the foreign declarations it contains. Provide a `because "<reason>"` clause to acknowledge the contract and silence this warning.

### CW7001 {#cw7001}

**Warning** · docs

**Message:** `this documentation block documents nothing`

**Help:** `a block documents the declaration on the next line; a blank line or a comment between the two breaks the attachment`

A block attaches to the declaration on the NEXT line. A blank line breaks the attachment, and so does an ordinary `#` comment: both are absorbed into the newline token, so the compiler cannot tell one from the other. The escape is to move the comment, or to move the block. A block that is the first item in its file documents the unit and never warns.

### CW7002 {#cw7002}

**Warning** · docs

**Message:** `this {kind} has no documentation block: '{name}'`

Every declaration is asked the same question, public and private. The `public` marker is not the test: an internal API is documented surface as much as an exported one, and a reader of the code is a reader. Two declarations are exempt. `fn main()` is nobody's API, and a library cannot declare one at all. An `unsafe external` block and the declarations in it carry `because "..."`, which acknowledges the contract that matters at that seam.

### CW7003 {#cw7003}

**Warning** · docs

**Message:** `the parameter '{name}' of '{callable}' is not documented`

A block that documents some of the parameters and not the rest is the shape a reader trusts least: it looks complete. `self` is never asked for, because the builders lift the receiver onto the declaration and it is not a parameter by the time the pass reads one. A declaration with NO block is [CW7002](#cw7002) instead.

### CW7004 {#cw7004}

**Warning** · docs

**Message:** `'{name}' returns a value, and no '- Returns:' tag says what it is`

The tag describes T, not the Result that wraps it (see [the documentation design](design/documentation.md)). A callable that returns `~` returns nothing to describe and is never asked. A declaration with NO block is [CW7002](#cw7002) instead.

### CW7005 {#cw7005}

**Warning** · docs

**Message:** `'{name}' declares an error arm, and no '- Errors:' tag says when it fails`

A function written `fn f() T | E` names its own error type, so the author chose to have more than one way to fail and the reader needs to know which. A function that writes no '| E' is not asked. A declaration with NO block is [CW7002](#cw7002) instead.

### CW7006 {#cw7006}

**Warning** · docs

**Message:** `this unit has no documentation block`

A unit block is the first block in a file, and it travels in a `.slib` as `unit_docs`, which `--lib-info` prints under the unit name. A library whose units say nothing is the first hole a reader meets. This is the one lint about something that is not there, so it carries no caret.

## RExxxx: Runtime errors {#rexxxx}

A compiled program gives a runtime error when it stops on a trap. The program writes `Runtime Error RExxxx: <message>` to the standard error stream and exits 1. In the message, `%d` is a number that the program fills in.

### RE2020 {#re2020}

**Error** · runtime

**Message:** `array index %d out of bounds for array of size %d`

Array access with index outside valid range \[0, size).

### RE2021 {#re2021}

**Error** · runtime

**Message:** `memory allocation failed`

System could not allocate memory (malloc/realloc returned NULL).

### RE2022 {#re2022}

**Error** · runtime

**Message:** `insert into an unusable HashMap: no free bucket`

HashMap.insert() probed every bucket without finding a slot. A live map always resizes below a 0.75 load factor, so this means the map has no buckets at all -- it was destroyed. Using a destroyed map is [CE2406](#ce2406), also through a `poke` parameter. The compiler cannot see every destroy -- a generic callee, an extension method destroying its implicit `self`, a library callee, or an argument that is not a bare name -- so this trap catches the rest at run time.

### RE2023 {#re2023}

**Error** · runtime

**Message:** `no match arm matched the value (expected {pattern})`

A match tested its last candidate arm, and the value did not match it. `{pattern}` is that arm. Exhaustiveness checking makes this unreachable: one checker reads every match, nested patterns and tuple patterns included, and a match that does not cover a value is the compile error [CE2040](#ce2040). The check stays in the program as a backstop.

### RE2025 {#re2025}

**Error** · runtime

**Message:** `a foreign call returned a null pointer where its declaration says non-null`

An `unsafe external` function declared to return a plain `string` or `ptr` answered NULL. Null is never a Sushi value, so a plain pointer type asserts that C cannot answer one, and the call stops the program instead of passing the null on to `strlen` or to the next C call. Declare the return `Maybe@(string)` or `Maybe@(ptr)` when C can answer NULL: the call then answers `Maybe.None`.

### RE2026 {#re2026}

**Error** · runtime

**Message:** `assertion failed at {where}`

An `assert(cond)` or `assert(cond, message)` found its condition false. `{where}` is the file, the line and the column of the `assert`, with the file named as a compile-time diagnostic names it; a message, when there is one, follows after `: `. An assert states an invariant, so a failure is a defect and not data: the program stops with exit code 1, and no error channel catches it. A failure that a caller can handle belongs in the channel, `| E`. See [the assert design](design/assert.md).

## NExxxx: Nori package manager errors {#nexxxx}

The package manager, `nori`, gives these errors. The ranges are: NE00xx internal, NE10xx the manifest, NE20xx the archive, NE30xx the installed packages, NE40xx the operating system, NE50xx the package repository.

### NE0000 {#ne0000}

**Error** · internal

**Message:** `internal error in nori: {detail}`

**Help:** `re-run with --traceback for the full Python traceback, then please report it`

A fault in nori itself, not in the package or in the environment. It exits 2, as the compiler's [CE0000](#ce0000) does, and `--traceback` appends the Python traceback.

### NE1001 {#ne1001}

**Error** · packager

**Message:** `no {manifest} found in '{directory}'`

A command that works on a package reads its manifest from the current directory.

### NE1002 {#ne1002}

**Error** · packager

**Message:** `'{path}' is not valid TOML: {detail}`

A TOML file that nori reads could not be parsed: the project manifest, the manifest of an installed package, or the credentials file. The text names the line and the column.

### NE1003 {#ne1003}

**Error** · packager

**Message:** `'{path}': [{table}] must be a table, not {kind}`

Each manifest section is a TOML table.

### NE1004 {#ne1004}

**Error** · packager

**Message:** `'{path}': missing required field: [package] {field}`

A manifest must state the package name and its version.

### NE1005 {#ne1005}

**Error** · packager

**Message:** `'{path}': invalid package name '{name}': use 1 to 64 lowercase letters, digits and hyphens, and start with a letter`

A package name is also a directory name and a part of a repository URL.

### NE1006 {#ne1006}

**Error** · packager

**Message:** `'{path}': invalid version '{version}': use the form major.minor.patch (for example 1.0.0)`

A package version has three numbers.

### NE1007 {#ne1007}

**Error** · packager

**Message:** `'{path}': the version of dependency '{name}' must be a string`

A dependency is written `name = "1.0.0"`.

### NE1008 {#ne1008}

**Error** · packager

**Message:** `'{path}': invalid version '{version}' for dependency '{name}': use the form major.minor.patch (for example 1.0.0)`

A dependency names one exact version.

### NE1009 {#ne1009}

**Error** · packager

**Message:** `'{path}' already exists`

`nori init` does not write over a manifest.

### NE1010 {#ne1010}

**Error** · packager

**Message:** `'{path}' is not UTF-8 text`

A manifest is TOML, and TOML is UTF-8.

### NE1011 {#ne1011}

**Error** · packager

**Message:** `'{path}': {field} must be {expected}, not {found}`

Each manifest field has one TOML type: a string, or a list of strings. The reader checks every field before it uses one, so a string is never read as a list.

### NE2001 {#ne2001}

**Error** · packager

**Message:** `'{path}' is not a readable .nori archive: {reason}`

A .nori archive is a gzip-compressed tar file.

### NE2002 {#ne2002}

**Error** · packager

**Message:** `'{path}' holds no package directory '{directory}/'`

An archive holds one directory, `<name>-<version>/`, named by its manifest.

### NE2003 {#ne2003}

**Error** · packager

**Message:** `'{path}': cannot read '{member}'`

The archive member that holds the manifest is not a regular file.

### NE2004 {#ne2004}

**Error** · packager

**Message:** `'{path}' holds no {manifest}`

Every archive carries its manifest.

### NE2005 {#ne2005}

**Error** · packager

**Message:** `listed path not found: '{path}'`

The \[files\] section of the manifest names a path that does not exist.

### NE2006 {#ne2006}

**Error** · packager

**Message:** `listed path is not a file: '{path}'`

A library or an executable in the \[files\] section must be a regular file.

### NE2007 {#ne2007}

**Error** · packager

**Message:** `archive not found: '{path}'`

**Help:** `run 'nori build' first to create the package archive`

The .nori archive to install or to publish does not exist.

### NE2008 {#ne2008}

**Error** · packager

**Message:** `listed path leaves the package directory: '{path}'`

Every path in the \[files\] section is relative to the directory of the nori.toml and stays at or below it: an absolute path or a path with a `..` step is refused, so an archive never packs a file from outside the package.

### NE3001 {#ne3001}

**Error** · packager

**Message:** `package {name} v{version} is not in the store`

A project links a package version that is in the global store.

### NE3002 {#ne3002}

**Error** · packager

**Message:** `dependencies missing from the store: {packages}`

**Help:** `install each one from its source: nori install <package> from <path>`

`nori install` in a project links each dependency from the store; it does not fetch one.

### NE3003 {#ne3003}

**Error** · packager

**Message:** `package '{name}' is not installed`

The package is not in the global package directory.

### NE3004 {#ne3004}

**Error** · packager

**Message:** `package '{name}' is not a project dependency`

The package is not linked in the project's .sushi\_bento directory.

### NE3005 {#ne3005}

**Error** · packager

**Message:** `path not found: '{path}'`

The source of an install does not exist.

### NE3006 {#ne3006}

**Error** · packager

**Message:** `cannot install from '{source}': it is not a .nori archive or a directory`

An install source is a .nori archive, or a directory that holds one or a manifest.

### NE3007 {#ne3007}

**Error** · packager

**Message:** `no .nori archive for '{package}' in '{directory}'`

The directory holds no `<package>-*.nori` archive and no manifest.

### NE3008 {#ne3008}

**Error** · packager

**Message:** `not in a Sushi project (no {manifest} found)`

**Help:** `usage: nori install <package>`

A bare `nori install` restores the dependencies of the project it runs in.

### NE3009 {#ne3009}

**Error** · packager

**Message:** `remote install from {repository} is not implemented yet`

**Help:** `install from a local source: nori install <package> from <path>`

Install from a local source until the repository install exists.

### NE3010 {#ne3010}

**Error** · packager

**Message:** `package '{name}' has a corrupted manifest`

The installed package is present, but nori cannot read its manifest.

### NE3011 {#ne3011}

**Error** · packager

**Message:** `expected 'from' before the source, found '{found}'`

**Help:** `usage: nori install <package> from <source>`

The form is `nori install <package> from <source>`.

### NE4001 {#ne4001}

**Error** · packager

**Message:** `cannot use '{path}': {reason}`

The operating system refused an operation on a path: no permission, a full disk, a directory where a file must be. It is the environment's fault, not nori's, so it exits 1 and names the path and the system reason.

### NE4002 {#ne4002}

**Error** · packager

**Message:** `a system operation failed: {reason}`

The operating system refused an operation that names no path.

### NE5001 {#ne5001}

**Error** · packager

**Message:** `cannot connect to {repository}: {reason}`

The repository did not answer: no network, a wrong host name, or a timeout.

### NE5002 {#ne5002}

**Error** · packager

**Message:** `the repository answered HTTP {status}: {detail}`

The repository refused the request with a status that has no code of its own.

### NE5003 {#ne5003}

**Error** · packager

**Message:** `not logged in to {repository}`

**Help:** `run 'nori login' to authenticate first`

`nori publish` needs the API token that `nori login` stores.

### NE5004 {#ne5004}

**Error** · packager

**Message:** `{repository} refused the API key: it is invalid, expired or revoked`

**Help:** `run 'nori login' to authenticate again`

The repository answered HTTP 401.

### NE5005 {#ne5005}

**Error** · packager

**Message:** `invalid API key format: a key starts with '{prefix}'`

nori checks the form of a key before it sends the key to the repository.

### NE5006 {#ne5006}

**Error** · packager

**Message:** `permission denied: you are not the owner of '{name}'`

The repository answered HTTP 403: only the owner publishes a new version.

### NE5007 {#ne5007}

**Error** · packager

**Message:** `version {version} of '{name}' is already published`

The repository answered HTTP 409: a published version does not change.

### NE5008 {#ne5008}

**Error** · packager

**Message:** `the archive is larger than the size limit of the repository (50 MB)`

The repository answered HTTP 413.

### NE5009 {#ne5009}

**Error** · packager

**Message:** `the repository refused the package: {detail}`

The repository answered HTTP 422: the manifest or the archive failed its checks.
