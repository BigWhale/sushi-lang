# Environment Variables Module

[← Back to Standard Library](../standard-library.md)

System environment variable access and manipulation.

## Import

```sushi
use <sys/env>
```

The import brings `EnvError`, the module's error enum: the bare name needs it, and
`use <sys/env> as env` puts it behind the dot (`env.EnvError.NotFound`).

## Overview

The env module provides functions for reading and modifying environment variables. It uses POSIX `getenv()` and `setenv()` functions for Unix portability across macOS, Linux, and other Unix-like systems.

## Functions

### getenv

Get an environment variable value.

```text
getenv(string key) Maybe@(string)
```

The `Maybe` is the bare return value, not wrapped in a `Result`: a missing variable is a
value, not a failure.

**Parameters:**
- `key` - Environment variable name

**Returns:**
- `Maybe.Some(value)` if variable exists
- `Maybe.None()` if variable does not exist

**Example:**

```sushi
use <sys/env>

fn main() i32:
    match getenv("PATH"):
        Maybe.Some(path) ->
            println("PATH: {path}")
        Maybe.None() ->
            println("PATH not set")

    return 0
```

**Common environment variables:**

```sushi
use <sys/env>

fn main() i32:
    # User information
    let Maybe@(string) home = getenv("HOME")
    let Maybe@(string) user = getenv("USER")

    # System paths
    let Maybe@(string) path = getenv("PATH")
    let Maybe@(string) tmpdir = getenv("TMPDIR")

    # Shell information
    let Maybe@(string) shell = getenv("SHELL")

    # Display variables
    let Maybe@(string) display = getenv("DISPLAY")

    if (home.is_some()):
        println("Home: {home.realise('')}")

    return 0
```

### setenv

Set an environment variable value.

```text
setenv(string key, string value) Result@(i32, EnvError)
```

**Parameters:**
- `key` - Environment variable name
- `value` - New value to set

An existing variable with the same name is always overwritten.

**Returns:**
- `Result.Ok(0)` on success
- `Result.Err(EnvError.InvalidValue)` on failure (e.g., an empty name, a name that contains `=`, insufficient memory)

**Example:**

```sushi
use <sys/env>

fn configure() i32 | EnvError:
    # Set a custom environment variable -- ?? propagates EnvError, so the
    # enclosing function must declare it
    setenv("MY_APP_CONFIG", "/etc/myapp.conf")??

    # Verify it was set
    match getenv("MY_APP_CONFIG"):
        Maybe.Some(config_path) ->
            println("Config path: {config_path}")
        Maybe.None() ->
            println("Failed to set variable")

    return Result.Ok(0)

fn main() i32:
    return configure().realise(1)
```

**Overwriting an existing value:**

```sushi
use <sys/env>

fn set_twice() i32 | EnvError:
    # Set an initial value
    setenv("MY_VAR", "initial")??

    # A second call always overwrites the existing value
    setenv("MY_VAR", "overwritten")??

    let string value = getenv("MY_VAR").realise("")
    println("MY_VAR: {value}")  # MY_VAR: overwritten

    return Result.Ok(0)

fn main() i32:
    return set_twice().realise(1)
```

## Error Handling

Both functions integrate with Sushi's error handling system:

### getenv Error Handling

Since `getenv` returns `Maybe@(string)`, use pattern matching or `.realise()`:

```sushi
use <sys/env>

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

    return 0
```

### setenv Error Handling

Since `setenv` returns `Result@(i32, EnvError)`, use error propagation -- inside a
function that declares `| EnvError` -- or pattern matching:

```sushi
use <sys/env>

fn set_var() i32 | EnvError:
    # With error propagation (??)
    setenv("MY_VAR", "value")??
    return Result.Ok(0)

fn main() i32:
    # With explicit error handling
    let Result@(i32, EnvError) set_result = setenv("MY_VAR", "value")
    match set_result:
        Result.Ok(_) ->
            println("Variable set successfully")
        Result.Err(_) ->
            println("Failed to set variable")

    return set_var().realise(1)
```

## Platform-Specific Behavior

### macOS (darwin)

Platform-specific implementation in `sushi_stdlib/src/_platform/darwin/env.py`:
- Uses standard POSIX `getenv()` and `setenv()`
- Follows BSD semantics

### Linux

Platform-specific implementation in `sushi_stdlib/src/_platform/linux/env.py`:
- Uses standard POSIX `getenv()` and `setenv()`
- Follows GNU/Linux semantics

### Other platforms

The stdlib has platform code for macOS and Linux only. There is no Windows
implementation.

## Security Considerations

### Sensitive Data

Environment variables may contain sensitive information:

```sushi
use <sys/env>

fn main() i32:
    # Be cautious when logging or displaying env vars
    let Maybe@(string) api_key = getenv("API_KEY")

    # Don't print sensitive values
    if (api_key.is_some()):
        println("API key is configured")
        # Bad: println("API key: {api_key.realise("")}")

    return 0
```

### Validation

Always validate environment variable values:

```sushi
use <sys/env>

fn is_valid_port(peek string port) bool:
    # Add validation logic
    return true

fn main() i32:
    match getenv("SERVER_PORT"):
        Maybe.Some(port) ->
            # port is a match binding, a borrow: pass it by peek rather than
            # by value (a by-value call would need port.clone() instead)
            if (is_valid_port(peek port)):
                println("Using port: {port}")
            else:
                println("Invalid port in SERVER_PORT")
        Maybe.None() ->
            println("Using default port: 8080")

    return 0
```

### Name Restrictions

Environment variable names should:
- Contain only uppercase letters, digits, and underscores
- Not start with a digit
- Not contain `=` or null bytes

An empty name, or a name that contains `=`, causes `setenv` to return
`Result.Err(EnvError.InvalidValue)`.

## Example: Configuration from Environment

```sushi
use <sys/env>
use <collections/strings>

struct Config:
    string host
    i32 port
    bool debug

fn load_config() Config:
    let string host = getenv("APP_HOST").realise("localhost")

    let string port_str = getenv("APP_PORT").realise("8080")
    let i32 port = port_str.to_i32().realise(8080)

    let string debug_str = getenv("APP_DEBUG").realise("false")
    let bool debug = debug_str == "true" or debug_str == "1"

    let Config config = Config(host, port, debug)
    return config

fn main() i32:
    let Config config = load_config()

    println("Host: {config.host}")
    println("Port: {config.port}")

    if (config.debug):
        println("Debug mode enabled")

    return 0
```

## Testing with Environment Variables

Test files can use `setenv` to set up test conditions:

```sushi
use <sys/env>

fn test_env_vars() i32 | EnvError:
    # Setup test environment
    setenv("TEST_VAR", "test_value")??

    # Run tests
    let string value = getenv("TEST_VAR").realise("")

    if (value == "test_value"):
        println("Test passed")
    else:
        println("Test failed")

    return Result.Ok(0)

fn main() i32:
    return test_env_vars().realise(1)
```

## See Also

- [Standard Library Reference](../standard-library.md) - Complete stdlib reference
- [Error Handling](../error-handling.md) - Result and Maybe types
- [String Methods](collections/strings.md) - String operations for parsing env values
