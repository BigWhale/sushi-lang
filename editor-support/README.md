# Sushi Language Editor Support

This directory contains syntax highlighting support for the Sushi programming language for popular code editors.

## Available Editors

### [VS Code](./vscode/)

Full syntax highlighting extension for Visual Studio Code.

**Quick Install:**
```bash
cd vscode
# Install vsce if you don't have it
npm install -g @vscode/vsce
# Package the extension
vsce package
# Install the generated .vsix file in VS Code
```

**Features:**
- Syntax highlighting
- Auto-closing brackets
- Comment toggling
- String interpolation support
- Bracket matching

### [JetBrains IDEs](./jetbrains/) (TextMate Bundle)

TextMate bundle for IntelliJ IDEA, PyCharm, WebStorm, CLion, and other JetBrains IDEs.

**Quick Install:**
```bash
# macOS/Linux
cp -r jetbrains/Sushi.tmbundle ~/Library/Application\ Support/JetBrains/IntelliJIdea2024.1/textmate/

# Windows
# Copy jetbrains/Sushi.tmbundle to %APPDATA%\JetBrains\IntelliJIdea2024.1\textmate\

# Restart your IDE
```

**Features:**
- Syntax highlighting via TextMate grammar
- File type recognition (.sushi)
- Works with all JetBrains IDEs
- Uses IDE's existing color scheme

## Language Features Supported

Both extensions support highlighting for:

- **Keywords**: `fn`, `extend`, `const`, `struct`, `use`, `public`, `if`, `elif`, `else`, `while`, `break`, `continue`, `return`, `let`, `print`, `and`, `or`, `not`, `as`, `new`, `from`, `Ok`, `Err`

- **Types**: `i8`, `i16`, `i32`, `i64`, `u8`, `u16`, `u32`, `u64`, `f32`, `f64`, `bool`, `string`

- **Literals**: `true`, `false`, integers, floats, strings (with escape sequences and interpolation)

- **Operators**:
  - Arithmetic: `+`, `-`, `*`, `/`, `%`
  - Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=`
  - Bitwise: `&`, `|`, `^`, `<<`, `>>`
  - Logical: `and`, `or`, `not`
  - Assignment: `:=`

- **Comments**: Line comments starting with `#`

- **String Interpolation**: `"Hello {name}"`

## Example Sushi Code

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
  let bool flag = true
  let string message = "Hello World"

  if (result > 10):
    print("Result is {result}")

  return Ok(0)
```

## Installation Guides

### VS Code

1. **From VSIX**:
   - Download or build the `.vsix` file
   - Open VS Code
   - Extensions view (Ctrl+Shift+X)
   - Click `...` → Install from VSIX

2. **Manual**:
   - Copy `vscode/` directory to `~/.vscode/extensions/sushi-lang-0.1.0/`
   - Restart VS Code

### JetBrains IDEs (TextMate Bundle)

1. **Copy the bundle**:
   ```bash
   # Find your IDE's TextMate directory (adjust version as needed):
   # macOS: ~/Library/Application Support/JetBrains/{IDE}{Version}/textmate/
   # Linux: ~/.config/JetBrains/{IDE}{Version}/textmate/
   # Windows: %APPDATA%\JetBrains\{IDE}{Version}\textmate\

   # Copy the bundle
   cp -r jetbrains/Sushi.tmbundle <textmate-directory>/
   ```

2. **Restart the IDE**

3. **Verify**: Open a `.sushi` file and check for syntax highlighting

See [JetBrains README](./jetbrains/README.md) for detailed installation instructions and troubleshooting.

## Editor Comparison

| Feature | VS Code | JetBrains (TextMate) |
|---------|---------|---------------------|
| Syntax Highlighting | ✅ | ✅ |
| Auto-closing brackets | ✅ | ✅ (IDE default) |
| Comment toggling | ✅ | ✅ (IDE default) |
| String interpolation | ✅ | ✅ |
| Custom color scheme | ✅ | ✅ (uses IDE scheme) |
| Code completion | ❌ | ❌ |
| Go to definition | ❌ | ❌ |
| Refactoring | ❌ | ❌ |

> **Note**: Both provide syntax highlighting only. Advanced IDE features would require a full language server or native plugin.

## Contributing

Contributions are welcome! Please submit issues and pull requests for:
- Bug fixes
- New features
- Grammar improvements
- Additional editor support

## License

MIT License - See individual extension directories for details.
