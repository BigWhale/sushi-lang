# Sushi Examples

[← Back to Documentation](../README.md)

Learn Sushi by example! Each example demonstrates a specific feature with detailed comments.

All examples use references from a certain guide about hitchhiking through the galaxy, featuring Arthur, Ford, Zaphod,
Trillian, Marvin, towels, and the number 42.

## Running Examples

```bash
# Compile an example
./sushic docs/examples/01-hello.sushi -o hello

# Run it
./hello
```

## Basic Examples

### 01-hello.sushi
Your first Sushi program - the classic "Hello World" (or rather, "Mostly Harmless").

### 02-variables.sushi
Variable declarations, types, and basic operations.

### 03-functions.sushi
Function definitions, parameters, return values, and the `Result<T>` type.

### 04-strings.sushi
String operations, concatenation, and basic manipulation.

### 05-interpolation.sushi
String interpolation with variables and expressions.

### 06-arrays.sushi
Fixed and dynamic arrays, array operations, and iteration.

## Error Handling

### 07-result.sushi
The `Result<T>` type for explicit error handling.

### 08-maybe.sushi
The `Maybe<T>` type for optional values.

### 09-error-propagation.sushi
The `??` operator for ergonomic error propagation.

## Data Structures

### 10-structs.sushi
Defining and using custom struct types with positional parameters.

### 11-enums.sushi
Rust-style enums with associated data.

### 12-pattern-matching.sushi
Exhaustive pattern matching with enums.

### 15-lists.sushi
Generic `List<T>` - dynamic growable arrays.

### 16-hashmaps.sushi
Generic `HashMap<K,V>` - hash tables with key-value pairs.

### 25-named-parameters.sushi
Named parameter syntax for struct construction - order-independent, prevents boolean traps.

## Advanced Features

### 13-references.sushi
Mutable references and compile-time borrow checking.

### 14-generics.sushi
Generic types with compile-time monomorphization.

### 19-extension-methods.sushi
Extension methods for zero-cost method chaining.

### 20-ownership.sushi
Ownership, RAII, and the `Own<T>` type for recursive structures.

### 23-perks-basic.sushi
Perks (traits/interfaces) - defining and implementing shared behavior.

### 24-perks-constraints.sushi
Generic constraints with perks - polymorphic functions and types.

## Control Flow & I/O

### 17-loops.sushi
Loop constructs: `while`, `foreach`, iteration.

### 21-control-flow.sushi
Conditional statements and control flow patterns.

### 18-file-io.sushi
File operations: reading, writing, error handling.

## Complex Examples

### 22-linked-list.sushi
Implementing a linked list with `Own<T>` for recursive structures.

## Libraries

### 26-libraries.sushi
Using precompiled libraries - demonstrates library linking.

### mathlib.sushi
Sample library source - compile with `--lib` to create reusable bitcode.

---

**Tip**: Examples are numbered for suggested reading order, but feel free to explore based on your interests!
