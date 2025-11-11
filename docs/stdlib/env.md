# Environment Variables Module

[← Back to Standard Library](../standard-library.md)

System environment variable access and manipulation.

## Import

```sushi
use <env>
```

## Overview

The env module provides functions for reading and modifying environment variables. It uses POSIX `getenv()` and `setenv()` functions for Unix portability across macOS, Linux, and other Unix-like systems.

## Functions

### getenv

Get an environment variable value.

```sushi
fn getenv(string key) -> Maybe<string>
```

**Parameters:**
- `key` - Environment variable name

**Returns:**
- `Maybe.Some(value)` if variable exists
- `Maybe.None()` if variable does not exist

**Example:**

```sushi
use <env>

fn main() i32:
    match getenv("PATH"):
        Maybe.Some(path) ->
            println("PATH: {path}")
        Maybe.None() ->
            println("PATH not set")

    return Result.Ok(0)
```

**Common environment variables:**

```sushi
use <env>

fn main() i32:
    # User information
    let Maybe<string> home = getenv("HOME")
    let Maybe<string> user = getenv("USER")

    # System paths
    let Maybe<string> path = getenv("PATH")
    let Maybe<string> tmpdir = getenv("TMPDIR")

    # Shell information
    let Maybe<string> shell = getenv("SHELL")

    # Display variables
    let Maybe<string> display = getenv("DISPLAY")

    if (home.is_some()):
        println("Home: {home.realise("")}")

    return Result.Ok(0)
```

### setenv

Set an environment variable value.

```sushi
fn setenv(string key, string value, i32 overwrite) -> Result<i32>
```

**Parameters:**
- `key` - Environment variable name
- `value` - New value to set
- `overwrite` - If non-zero, replace existing value; if zero, only set if not already defined

**Returns:**
- `Result.Ok(0)` on success
- `Result.Err()` on failure (e.g., insufficient memory, invalid name)

**Example:**

```sushi
use <env>

fn main() i32:
    # Set a custom environment variable
    setenv("MY_APP_CONFIG", "/etc/myapp.conf", 1)??

    # Verify it was set
    match getenv("MY_APP_CONFIG"):
        Maybe.Some(config_path) ->
            println("Config path: {config_path}")
        Maybe.None() ->
            println("Failed to set variable")

    return Result.Ok(0)
```

**Using the overwrite parameter:**

```sushi
use <env>

fn main() i32:
    # Set only if not already defined
    setenv("MY_VAR", "initial", 0)??

    # This won't overwrite since MY_VAR already exists
    setenv("MY_VAR", "updated", 0)??

    # This WILL overwrite the existing value
    setenv("MY_VAR", "overwritten", 1)??

    let string value = getenv("MY_VAR").realise("")
    println("MY_VAR: {value}")  # MY_VAR: overwritten

    return Result.Ok(0)
```

## Error Handling

Both functions integrate with Sushi's error handling system:

### getenv Error Handling

Since `getenv` returns `Maybe<string>`, use pattern matching or `.realise()`:

```sushi
use <env>

fn main() i32:
    # With pattern matching
    match getenv("CONFIG_FILE"):
        Maybe.Some(path) ->
            println("Using config: {path}")
        Maybe.None() ->
            println("Using default config")

    # With .realise() for default value
    let string config = getenv("CONFIG_FILE").realise("/etc/default.conf")
    println("Config: {config}")

    # With .expect() for required variables
    let string required = getenv("REQUIRED_VAR").expect("REQUIRED_VAR must be set")

    return Result.Ok(0)
```

### setenv Error Handling

Since `setenv` returns `Result<i32>`, use error propagation or pattern matching:

```sushi
use <env>

fn main() i32:
    # With error propagation (??)
    setenv("MY_VAR", "value", 1)??

    # With explicit error handling
    match setenv("MY_VAR", "value", 1):
        Result.Ok(_) ->
            println("Variable set successfully")
        Result.Err() ->
            println("Failed to set variable")

    return Result.Ok(0)
```

## Platform-Specific Behavior

### macOS (darwin)

Platform-specific implementation in `stdlib/src/_platform/darwin/env.py`:
- Uses standard POSIX `getenv()` and `setenv()`
- Follows BSD semantics

### Linux

Platform-specific implementation in `stdlib/src/_platform/linux/env.py`:
- Uses standard POSIX `getenv()` and `setenv()`
- Follows GNU/Linux semantics

### Windows (partial support)

Windows support is planned but not yet fully implemented.

## Security Considerations

### Sensitive Data

Environment variables may contain sensitive information:

```sushi
use <env>

fn main() i32:
    # Be cautious when logging or displaying env vars
    let Maybe<string> api_key = getenv("API_KEY")

    # Don't print sensitive values
    if (api_key.is_some()):
        println("API key is configured")
        # Bad: println("API key: {api_key.realise("")}")

    return Result.Ok(0)
```

### Validation

Always validate environment variable values:

```sushi
use <env>

fn is_valid_port(string port) bool:
    # Add validation logic
    return Result.Ok(true)

fn main() i32:
    match getenv("SERVER_PORT"):
        Maybe.Some(port) ->
            if (is_valid_port(port).realise(false)):
                println("Using port: {port}")
            else:
                println("Invalid port in SERVER_PORT")
        Maybe.None() ->
            println("Using default port: 8080")

    return Result.Ok(0)
```

### Name Restrictions

Environment variable names should:
- Contain only uppercase letters, digits, and underscores
- Not start with a digit
- Not contain `=` or null bytes

Invalid names will cause `setenv` to return `Result.Err()`.

## Example: Configuration from Environment

```sushi
use <env>

struct Config:
    string host
    i32 port
    bool debug

fn load_config() Config:
    let string host = getenv("APP_HOST").realise("localhost")

    let string port_str = getenv("APP_PORT").realise("8080")
    let i32 port = port_str.parse_i32().realise(8080)

    let string debug_str = getenv("APP_DEBUG").realise("false")
    let bool debug = debug_str == "true" or debug_str == "1"

    let Config config = Config(host, port, debug)
    return Result.Ok(config)

fn main() i32:
    let Config config = load_config().realise(Config("localhost", 8080, false))

    println("Host: {config.host}")
    println("Port: {config.port}")

    if (config.debug):
        println("Debug mode enabled")

    return Result.Ok(0)
```

## Testing with Environment Variables

Test files can use `setenv` to set up test conditions:

```sushi
use <env>

fn test_env_vars() i32:
    # Setup test environment
    setenv("TEST_VAR", "test_value", 1)??

    # Run tests
    let string value = getenv("TEST_VAR").realise("")

    if (value == "test_value"):
        println("Test passed")
    else:
        println("Test failed")

    return Result.Ok(0)

fn main() i32:
    return test_env_vars()
```

## See Also

- [Standard Library Reference](../standard-library.md) - Complete stdlib reference
- [Error Handling](../error-handling.md) - Result and Maybe types
- [String Methods](../standard-library.md#string-methods) - String operations for parsing env values
