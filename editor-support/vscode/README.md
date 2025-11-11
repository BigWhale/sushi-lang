# Sushi Language Support for VS Code

Provides syntax highlighting for the Sushi programming language (.sushi files).

## Features

- **Syntax Highlighting**: Full syntax highlighting for Sushi language constructs
  - Keywords: `fn`, `extend`, `const`, `struct`, `use`, `public`, `if`, `while`, `return`, etc.
  - Types: `i8`-`i64`, `u8`-`u64`, `f32`, `f64`, `bool`, `string`
  - Operators: arithmetic, comparison, bitwise, logical
  - Result types: `Ok()` and `Err()`
  - String interpolation: `"Hello {name}"`
  - Comments: `# line comments`

- **Editor Features**:
  - Auto-closing brackets and quotes
  - Comment toggling (Ctrl+/)
  - Bracket matching
  - Indentation support

## Installation

### From VSIX File

1. Download the `.vsix` file from the releases page
2. Open VS Code
3. Go to Extensions view (Ctrl+Shift+X)
4. Click the `...` menu at the top
5. Select "Install from VSIX..."
6. Choose the downloaded `.vsix` file

### Manual Installation

1. Copy this directory to your VS Code extensions folder:
   - **Windows**: `%USERPROFILE%\.vscode\extensions\sushi-lang-0.1.0`
   - **macOS/Linux**: `~/.vscode/extensions/sushi-lang-0.1.0`

2. Restart VS Code

### Building from Source

```bash
# Install vsce (VS Code Extension Manager)
npm install -g @vscode/vsce

# Package the extension
cd editor-support/vscode
vsce package

# This creates sushi-lang-0.1.0.vsix
```

## Usage

Once installed, any `.sushi` file will automatically get syntax highlighting.

## Language Features

### Example Code

```sushi
# Define a constant
const i32 MAX_SIZE = 100

# Define a struct
struct Point:
  i32 x
  i32 y

# Define a function
fn add(i32 a, i32 b) i32:
  return Ok(a + b)

# Extend types with methods
extend i32 square() i32:
  return Ok(self * self)

# Main function
fn main() i32:
  let i32 result = add(5, 10)
  print("Result: {result}")
  return Ok(0)
```

## License

MIT

## Contributing

Bug reports and pull requests are welcome at https://github.com/yourusername/sushi
