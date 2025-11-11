# AST Builder Module

Modular AST construction system for the Sushi language compiler. Transforms Lark parse trees into typed AST nodes following SOLID/DRY principles.

## Architecture

```
semantics/ast_builder/
├── __init__.py              # Public API exports
├── builder.py               # Main ASTBuilder orchestrator (277 lines)
├── exceptions.py            # Custom parsing exceptions
│
├── utils/                   # Shared utilities
│   ├── tree_navigation.py      # Tree query functions
│   ├── expression_discovery.py # Expression finding utilities
│   └── string_processing.py    # String/interpolation handling
│
├── types/                   # Type system parsing
│   ├── parser.py               # TypeParser coordinator
│   ├── user_defined.py         # Struct/enum types
│   ├── generics.py             # Generic type handling
│   ├── arrays.py               # Array types
│   └── references.py           # Reference types
│
├── expressions/             # Expression parsing
│   ├── parser.py               # ExpressionParser coordinator
│   ├── literals.py             # Literal expressions
│   ├── operators.py            # Unary/binary operators
│   ├── calls.py                # Function calls
│   ├── members.py              # Member access
│   ├── arrays.py               # Array operations
│   └── chains.py               # Call chains
│
├── statements/              # Statement parsing
│   ├── parser.py               # StatementParser coordinator
│   ├── io.py                   # Print/println
│   ├── returns.py              # Return statements
│   ├── variables.py            # Let/rebind
│   ├── control_flow.py         # If/while
│   ├── loops.py                # Foreach
│   ├── flow.py                 # Break/continue
│   ├── calls.py                # Call statements
│   ├── matching.py             # Match statements
│   └── blocks.py               # Block parsing
│
└── declarations/            # Top-level declarations
    ├── imports.py              # Use statements
    ├── functions.py            # Function definitions
    ├── constants.py            # Constant definitions
    ├── structs.py              # Struct definitions
    ├── enums.py                # Enum definitions
    ├── perks.py                # Perk definitions
    └── extensions.py           # Extension methods
```

## Usage

```python
from semantics.ast_builder import ASTBuilder
from lark import Lark

# Create parser and builder
parser = Lark.open("grammar.lark")
builder = ASTBuilder()

# Parse source code
tree = parser.parse(source_code)
program = builder.build(tree)  # Returns Program AST
```

## Design Patterns

### Strategy Pattern
- **TypeParser**: Coordinates type parsing across specialized modules
- **ExpressionParser**: Coordinates expression parsing
- **StatementParser**: Coordinates statement parsing

### Direct Delegation
- **Declaration parsing**: No coordinator class, direct function calls from `build()`

### Lazy Initialization
- Parsers loaded on first use via `@property` decorators
- Zero overhead for unused subsystems

## Key Features

- **Modular**: 39 focused modules vs 1 monolithic file
- **Maintainable**: 50-200 lines per module vs 2,323 lines
- **Testable**: Isolated components for unit testing
- **Extensible**: Add new constructs without modifying existing code
- **SOLID Compliant**: Single responsibility per module
- **DRY Compliant**: Shared utilities eliminate duplication

## Metrics

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Main file size | 2,323 lines | 277 lines | 88% reduction |
| Avg module size | N/A | ~50-200 lines | High cohesion |
| Methods per class | 59 | 3 | 95% reduction |
| Total modules | 1 | 39 | Modular design |
| Code reuse | Low | High | DRY compliance |

## Module Responsibilities

### builder.py (277 lines)
Main orchestrator with 3 delegation methods:
- `build()`: Parse program and dispatch to declaration parsers
- `_parse_type()`: Delegate to TypeParser
- `_block()`: Delegate to block parser
- `_expr()`: Delegate to ExpressionParser

### utils/ (3 modules, 496 lines)
Shared utilities used across all parsers:
- Tree navigation and query functions
- Expression discovery and disambiguation
- String escape processing and interpolation

### types/ (6 modules, 254 lines)
Type system parsing with TypeParser coordinator:
- Primitive types, user-defined types
- Generic type references and constraints
- Array and reference types

### expressions/ (8 modules, 492 lines)
Expression parsing with ExpressionParser coordinator:
- Literals, operators, casts, borrows
- Function/method calls
- Member access and array indexing
- Call chains and atoms

### statements/ (11 modules, 510 lines)
Statement parsing with StatementParser coordinator:
- Control flow (if/while/foreach)
- Variable operations (let/rebind)
- I/O operations (print/println)
- Pattern matching and flow control

### declarations/ (8 modules, 680 lines)
Top-level declaration parsing (no coordinator):
- Imports, functions, constants
- Structs, enums, perks
- Extension methods and perk implementations

## Backward Compatibility

The `__init__.py` exports maintain the original public API:
```python
from semantics.ast_builder import ASTBuilder  # Still works
```

All existing code using `ASTBuilder` continues to work without modifications.

## Contributing

When adding new language features:

1. **Expressions**: Add to `expressions/` with handler in `ExpressionParser`
2. **Statements**: Add to `statements/` with handler in `StatementParser`
3. **Types**: Add to `types/` with handler in `TypeParser`
4. **Declarations**: Add to `declarations/` and call from `build()`

Follow the existing patterns:
- Module-level functions take `ast_builder` parameter
- Use utilities from `utils/` for common operations
- Add comprehensive docstrings
- Keep modules under 250 lines
