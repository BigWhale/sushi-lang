# Sushi Language Style Guide (SSG)

This document defines the official coding conventions for Sushi code.

## Naming Conventions

### Identifiers

| Category            | Convention             | Examples                                              |
|---------------------|------------------------|-------------------------------------------------------|
| Functions           | snake_case             | `parse_expr`, `load_file`, `calculate_hash`           |
| Variables           | snake_case             | `user_count`, `file_path`, `is_valid`                 |
| Parameters          | snake_case             | `input_string`, `max_retries`, `buffer_size`          |
| Extension methods   | snake_case             | `.to_string()`, `.iter()`, `.split_at()`              |
| Structs             | PascalCase             | `HttpServer`, `UserProfile`, `ParseError`             |
| Enums               | PascalCase             | `TokenType`, `ErrorCode`, `FileMode`                  |
| Enum variants       | PascalCase             | `Result.Ok()`, `Maybe.Some()`, `FileError.NotFound`   |
| Type parameters     | Single uppercase       | `T`, `K`, `V`, `E`                                    |
| Constants           | SCREAMING_SNAKE_CASE   | `MAX_SIZE`, `DEFAULT_PORT`, `BUFFER_LENGTH`           |

### Examples

**Functions and variables:**
```sushi
fn calculate_total_cost(i32 item_count, f64 unit_price) f64:
    let f64 subtotal = item_count * unit_price
    let f64 tax_rate = 0.08
    return Result.Ok(subtotal * (1.0 + tax_rate))
```

**Structs and enums:**
```sushi
struct UserProfile:
    string user_name
    i32 age_years
    bool is_active

enum ResponseStatus:
    Success(i32)
    NotFound()
    ServerError(string)
```

**Constants:**
```sushi
const i32 MAX_BUFFER_SIZE = 4096
const string DEFAULT_CONFIG_PATH = "/etc/sushi.conf"
const f64 PI = 3.14159265359
```

**Extension methods:**
```sushi
extend string to_upper_case() string:
    # Implementation
    return Result.Ok(result)

extend i32[] calculate_sum() i32:
    let i32 total = 0
    foreach(n in self.iter()):
        total := total + n
    return Result.Ok(total)
```

## Special Cases

### Boolean Variables

Prefix boolean variables with verbs for clarity:

```sushi
# Good
let bool is_valid = true
let bool has_errors = false
let bool can_retry = true
let bool should_continue = false

# Avoid
let bool valid = true
let bool errors = false
```

### Acronyms and Initialisms

Treat acronyms as regular words (lowercase in snake_case, only first letter capitalized in PascalCase):

```sushi
# Good - Functions/variables
fn parse_http_request() ~
fn decode_utf8_string() ~
fn base64_encode() ~
let string json_data = "{}"
let i32 html_length = 100

# Good - Types
struct HttpServer
struct JsonParser
struct Utf8Decoder

# Avoid
fn parse_HTTP_request() ~
fn decode_UTF8_string() ~
struct HTTPServer
struct JSONParser
```

### Numbers in Identifiers

Keep numbers lowercase (part of the word):

```sushi
# Good
fn base64_encode() ~
fn utf8_decode() ~
struct Vec3d
const i32 MAX_IPV4_LENGTH = 15

# Avoid
fn base64Encode() ~
fn utf8Decode() ~
struct Vec3D
```

### Underscores for Readability

Use underscores liberally to improve readability:

```sushi
# Good - Clear word boundaries
fn calculate_hash_value() ~
let string config_file_path = "/etc/config"
struct UserAccountSettings

# Less clear
fn calculatehashvalue() ~
let string configfilepath = "/etc/config"
struct Useraccountsettings
```

## File Naming

### Source Files

Use snake_case with `.sushi` extension:

```
good_examples.sushi
test_parser_functions.sushi
http_server_main.sushi
utf8_string_utils.sushi
```

### Test Files

Prefix test files with `test_`:

```
test_basic_arithmetic.sushi
test_err_division_by_zero.sushi
test_warn_unused_variable.sushi
test_enum_pattern_matching.sushi
```

**Test prefixes:**
- `test_` - Must compile successfully (exit code 0)
- `test_warn_` - Compile with warnings (exit code 1)
- `test_err_` - Must fail compilation (exit code 2)

## Code Organization

### Struct Field Ordering

Order fields logically, typically:
1. Simple types before complex types
2. Group related fields together
3. Consider alignment/padding (larger types first can reduce padding)

```sushi
struct ServerConfig:
    # Simple fields first
    i32 port
    i32 max_connections
    bool enable_logging

    # Complex fields after
    string host_name
    string[] allowed_origins
```

### Function Parameter Ordering

1. Primary data parameters first
2. Configuration/options after
3. References last (if any)

```sushi
# Good ordering
fn process_data(string input, i32 max_length, bool verbose) string:
    return Result.Ok(input)

# Less clear
fn process_data(bool verbose, i32 max_length, string input) string:
    return Result.Ok(input)
```

## Formatting

### Indentation

Use **4 spaces** per indentation level (not tabs):

```sushi
fn example() i32:
    let i32 x = 10
    if (x > 5):
        println("Greater than 5")
        return Result.Ok(x)
    return Result.Ok(0)
```

### Line Length

**Code:** Maximum 120 characters per line. Break longer lines logically:

```sushi
# Good - fits within 120 characters
fn create_user_profile(string user_name, string email_address, i32 age, bool is_admin) UserProfile:
    return Result.Ok(UserProfile.new())

# Also good - broken at logical points when too long
fn create_detailed_user_profile(
    string user_name,
    string email_address,
    i32 age,
    bool is_admin,
    bool email_verified
) UserProfile:
    return Result.Ok(UserProfile.new())
```

**Comments and documentation:** Maximum 78 characters per line for better readability:

```sushi
# Good - comment fits within 78 characters
# This function validates the user input and returns true if valid.
fn validate_input(string input) bool:
    return Result.Ok(true)

# Good - multi-line comment for longer descriptions
# This function performs complex validation on the user input by checking
# multiple conditions including length, character set, and format. It
# returns true if all validations pass, false otherwise.
fn complex_validation(string input) bool:
    return Result.Ok(true)
```

### Blank Lines

- One blank line between function definitions
- One blank line between struct/enum definitions
- No blank line at start/end of functions or blocks

```sushi
struct Point:
    i32 x
    i32 y

struct Rectangle:
    Point top_left
    Point bottom_right

fn calculate_area(Rectangle rect) i32:
    let i32 width = rect.bottom_right.x - rect.top_left.x
    let i32 height = rect.bottom_right.y - rect.top_left.y
    return Result.Ok(width * height)

fn calculate_perimeter(Rectangle rect) i32:
    let i32 width = rect.bottom_right.x - rect.top_left.x
    let i32 height = rect.bottom_right.y - rect.top_left.y
    return Result.Ok(2 * (width + height))
```

### Trailing Newlines

Always end files with a single trailing newline (prevents compilation warnings).

## Comments

### Single-line Comments

Use `#` for single-line comments:

```sushi
# Calculate the factorial of n
fn factorial(i32 n) i32:
    if (n <= 1):
        return Result.Ok(1)
    let i32 prev = factorial(n - 1)??
    return Result.Ok(n * prev)  # Recursive case
```

### Documentation Comments

Use `#` comments above functions, structs, and enums to document their purpose:

```sushi
# Represents a 2D point in Cartesian coordinates.
# Fields are in pixels from the top-left origin.
struct Point:
    i32 x
    i32 y

# Calculates the Euclidean distance between two points.
# Returns the distance as a floating-point value.
fn distance(Point p1, Point p2) f64:
    let i32 dx = p2.x - p1.x
    let i32 dy = p2.y - p1.y
    return Result.Ok(sqrt(dx * dx + dy * dy))
```

## Language-Specific Conventions

### Struct Construction

Sushi supports both positional and named parameter syntax for struct constructors. Choose the appropriate style based on the situation:

**Use positional construction when:**
- The struct has 1-3 fields
- Field types make the meaning obvious
- The struct is used frequently in tight loops

```sushi
struct Point:
    i32 x
    i32 y

# Good - simple struct, types are clear
let Point p = Point(10, 20)
```

**Use named construction when:**
- The struct has 4+ fields
- Multiple fields have the same type (especially booleans)
- Field names provide important context
- Code clarity is more important than brevity

```sushi
struct ServerConfig:
    string host
    i32 port
    bool use_ssl
    bool enable_cache
    i32 timeout_ms

# Good - named parameters prevent confusion
let ServerConfig config = ServerConfig(
    host: "localhost",
    port: 8080,
    use_ssl: false,
    enable_cache: true,
    timeout_ms: 5000
)

# Avoid - unclear which boolean is which
let ServerConfig config = ServerConfig("localhost", 8080, false, true, 5000)
```

**Boolean trap prevention:**

Named parameters are especially valuable when a struct has multiple boolean fields:

```sushi
struct Flags:
    bool verbose
    bool debug
    bool strict
    bool quiet

# Good - crystal clear
let Flags flags = Flags(
    verbose: true,
    debug: false,
    strict: true,
    quiet: false
)

# Bad - impossible to tell which flag is which without checking the definition
let Flags flags = Flags(true, false, true, false)
```

**Formatting multi-field construction:**

For structs with many fields, break across lines:

```sushi
# Good - one field per line for complex structs
let DatabaseConfig db = DatabaseConfig(
    host: "db.example.com",
    port: 5432,
    database: "production",
    username: "admin",
    password: get_password()??,
    pool_size: 10,
    timeout_ms: 30000,
    use_ssl: true
)

# Also acceptable - compact for simple cases
let Point p = Point(x: 10, y: 20)
```

### Result Handling

Always handle `Result` types explicitly:

```sushi
# Good - explicit handling
let i32 value = risky_operation()??
let i32 value2 = risky_operation().realise(0)

# Good - explicit match
match risky_operation():
    Result.Ok(val) -> println("Success: {val}")
    Result.Err() -> println("Failed")
```

### Maybe/Option Handling

Check for `None` before accessing values:

```sushi
# Good - safe access
match optional_value:
    Maybe.Some(val) -> println("Got: {val}")
    Maybe.None() -> println("No value")

# Good - with realise
let string value = optional_value.realise("default")
```

### Pattern Matching

Always handle all cases (compiler enforces exhaustiveness).

Sushi supports single-line and multi-line match arms:

```sushi
enum Status:
    Active()
    Inactive()
    Pending()

fn handle_status(Status status) ~:
    # Single-line match arms (concise for simple cases)
    match status:
        Status.Active() -> println("Active")
        Status.Inactive() -> println("Inactive")
        Status.Pending() -> println("Pending")

    # Multi-line match arms (for complex logic)
    match status:
        Status.Active() ->
            println("Status: Active")
            println("Processing...")
        Status.Inactive() ->
            println("Status: Inactive")
        Status.Pending() ->
            println("Status: Pending")
            println("Waiting for approval")

    return Result.Ok(~)
```

**Single-line arms support:**
- Statements: `return`, `print`, `println`, function calls, `break`, `continue`
- Expressions: any valid expression

**Use single-line when:** The arm body is a single simple statement or expression

**Use multi-line when:** The arm body has multiple statements or complex logic

## Comparison with Other Languages

| Language  | Functions/Variables | Types          | Constants                |
|-----------|---------------------|----------------|--------------------------|
| **Sushi** | **snake_case**      | **PascalCase** | **SCREAMING_SNAKE_CASE** |
| Rust      | snake_case          | PascalCase     | SCREAMING_SNAKE_CASE     |
| Python    | snake_case          | PascalCase     | SCREAMING_SNAKE_CASE     |
| C         | snake_case          | varies         | SCREAMING_SNAKE_CASE     |
| Go        | camelCase           | PascalCase     | MixedCase                |
| Java      | camelCase           | PascalCase     | SCREAMING_SNAKE_CASE     |
| C++       | varies              | PascalCase     | SCREAMING_SNAKE_CASE     |


## Rationale

### Why snake_case?

1. **Readability** - Word boundaries are clearer: `parse_token_stream` vs `parseTokenStream`
2. **Consistency** - Matches Rust, Python, and traditional C conventions
3. **Compatibility** - Works naturally with file naming conventions
4. **Accessibility** - Easier to read for non-native English speakers
5. **Already in use** - Dominant convention in existing Sushi codebase

### Why PascalCase for types?

1. **Visual distinction** - Types stand out from values: `UserProfile user_profile`
2. **Universal convention** - Nearly all languages use this for type names
3. **Clarity** - Clear separation between type space and value space

### Why SCREAMING_SNAKE_CASE for constants?

1. **High visibility** - Constants are immediately recognizable
2. **Traditional** - C convention dating back decades
3. **Compiler hints** - Visual cue that value never changes

## Enforcement

The Sushi compiler may add linting warnings in the future for style violations. For now:

- Code reviews should enforce these conventions
- All standard library code follows these rules
- Test code follows the same conventions as production code
- Examples and documentation use consistent style

## Examples

### Good Style

```sushi
const i32 MAX_RETRIES = 3
const f64 TIMEOUT_SECONDS = 30.0

struct ConnectionConfig:
    string host_name
    i32 port_number
    i32 max_retries
    bool use_encryption

enum ConnectionError:
    Timeout()
    Refused()
    InvalidHost(string)

fn connect_to_server(ConnectionConfig config) bool:
    let i32 retry_count = 0

    while (retry_count < config.max_retries):
        match attempt_connection(config):
            Result.Ok(_) -> return Result.Ok(true)
            Result.Err() -> retry_count := retry_count + 1

    return Result.Ok(false)

fn attempt_connection(ConnectionConfig config) ~:
    println("Connecting to {config.host_name}:{config.port_number}")
    return Result.Ok(~)
```

### Style to Avoid

```sushi
# BAD: Mixed naming conventions
const i32 maxRetries = 3  # Should be MAX_RETRIES
const f64 TIMEOUT_SECONDS = 30.0  # OK

struct connectionConfig:  # Should be ConnectionConfig
    string hostName  # Should be host_name
    i32 PortNumber  # Should be port_number

enum connection_error:  # Should be ConnectionError
    timeout()  # Should be Timeout()
    Refused()  # OK

fn ConnectToServer(connectionConfig cfg) bool:  # Bad function name
    let i32 retryCount = 0  # Should be retry_count
    return Result.Ok(false)
```

## Summary

Follow these simple rules:

1. ✅ **snake_case** for functions, variables, parameters, methods
2. ✅ **PascalCase** for structs, enums, variants, type parameters
3. ✅ **SCREAMING_SNAKE_CASE** for constants
4. ✅ 4 spaces for indentation
5. ✅ ~100 character line limit
6. ✅ Always add trailing newline to files
7. ✅ Use descriptive names (prefer `user_count` over `n` or `uc`)

When in doubt, look at the standard library or existing test files for examples.
