# Sushi Language TextMate Bundle for JetBrains IDEs

TextMate bundle for syntax highlighting of Sushi programming language in JetBrains IDEs (IntelliJ IDEA, PyCharm, WebStorm, CLion, etc.).

## Features

- **Syntax Highlighting**: Full syntax highlighting using TextMate grammar
  - Keywords: `fn`, `extend`, `const`, `struct`, `use`, `public`, `if`, `while`, `return`, etc.
  - Types: `i8`-`i64`, `u8`-`u64`, `f32`, `f64`, `bool`, `string`
  - Operators: arithmetic, comparison, bitwise, logical
  - Result types: `Ok()` and `Err()`
  - String interpolation: `"Hello {name}"`
  - Comments: `# line comments`

- **Editor Features**:
  - File type recognition (.sushi files)
  - Comment toggling
  - Bracket matching (via TextMate bundles support)

## Installation

### Prerequisites

Your JetBrains IDE must have TextMate bundles support enabled (available in most modern versions).

### Steps

1. **Copy the bundle** to your JetBrains IDE's TextMate bundles directory:

   **macOS/Linux:**
   ```bash
   # For IntelliJ IDEA
   mkdir -p ~/Library/Application\ Support/JetBrains/IntelliJIdea2024.1/textmate/
   cp -r Sushi.tmbundle ~/Library/Application\ Support/JetBrains/IntelliJIdea2024.1/textmate/

   # For PyCharm
   mkdir -p ~/Library/Application\ Support/JetBrains/PyCharm2024.1/textmate/
   cp -r Sushi.tmbundle ~/Library/Application\ Support/JetBrains/PyCharm2024.1/textmate/

   # For other JetBrains IDEs, adjust the path accordingly
   ```

   **Windows:**
   ```
   Copy Sushi.tmbundle to:
   %APPDATA%\JetBrains\IntelliJIdea2024.1\textmate\
   ```

   > **Note**: Adjust the version number (2024.1) to match your installed IDE version.

2. **Restart the IDE** for the changes to take effect.

3. **Verify** by opening a `.sushi` file - it should now have syntax highlighting.

### Alternative: Manual Configuration

If automatic detection doesn't work:

1. Go to **Settings/Preferences** → **Editor** → **TextMate Bundles**
2. Click the **+** button
3. Navigate to and select the `Sushi.tmbundle` directory
4. Apply changes and restart the IDE

## Usage

Once installed, any `.sushi` file will automatically get syntax highlighting based on the TextMate grammar.

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

## Supported IDEs

This TextMate bundle works with all JetBrains IDEs that support TextMate bundles:

- ✅ IntelliJ IDEA (Community & Ultimate)
- ✅ PyCharm (Community & Professional)
- ✅ WebStorm
- ✅ PhpStorm
- ✅ CLion
- ✅ RubyMine
- ✅ GoLand
- ✅ Rider
- ✅ AppCode

## Finding Your IDE's TextMate Directory

The TextMate bundles directory varies by IDE and version. Common locations:

**macOS:**
- `~/Library/Application Support/JetBrains/{IDE}{Version}/textmate/`
- `~/Library/Preferences/{IDE}{Version}/textmate/`

**Linux:**
- `~/.config/JetBrains/{IDE}{Version}/textmate/`
- `~/.{IDE}{Version}/config/textmate/`

**Windows:**
- `%APPDATA%\JetBrains\{IDE}{Version}\textmate\`
- `%USERPROFILE%\.{IDE}{Version}\config\textmate\`

Replace `{IDE}` with your IDE name (e.g., `IntelliJIdea`, `PyCharm`, `WebStorm`) and `{Version}` with the version number (e.g., `2024.1`).

## Troubleshooting

### Bundle not loading

1. Verify the bundle is in the correct directory
2. Check that TextMate bundles support is enabled:
   - Settings → Editor → TextMate Bundles
3. Restart the IDE completely
4. Check IDE logs for any errors related to TextMate bundles

### No syntax highlighting

1. Verify the file has `.sushi` extension
2. Right-click the file → **Associate with File Type** → Select "Sushi"
3. Check Settings → Editor → File Types to ensure `.sushi` is associated

### Colors don't match expectations

TextMate bundles use your IDE's color scheme. To customize:
- Settings → Editor → Color Scheme → TextMate
- Adjust colors for different scopes (keyword, string, comment, etc.)

## Limitations

TextMate bundles provide **syntax highlighting only**. Advanced IDE features are not available:

- ❌ Code completion
- ❌ Go to definition
- ❌ Find usages
- ❌ Refactoring
- ❌ Error highlighting
- ❌ Debugging

For full IDE features, a native IntelliJ Platform plugin would be required.

## Bundle Structure

```
Sushi.tmbundle/
├── info.plist                    # Bundle metadata
└── Syntaxes/
    └── Sushi.tmLanguage          # Grammar definition
```

## Customization

To modify the syntax highlighting:

1. Edit `Syntaxes/Sushi.tmLanguage`
2. Modify patterns, scopes, or add new rules
3. Restart your IDE to see changes

The file uses TextMate grammar format (XML plist). See [TextMate documentation](https://macromates.com/manual/en/language_grammars) for syntax details.

## License

MIT

## Contributing

Bug reports and improvements welcome! Please submit issues or pull requests to the main Sushi repository.
