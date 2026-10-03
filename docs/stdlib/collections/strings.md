# String Methods

[← Back to Standard Library](../../standard-library.md)

Comprehensive string manipulation methods. Sushi strings are UTF-8 encoded fat pointers
with three fields: `{i8* data, i32 size, i8 owned}`.

## String Literals

Sushi supports two string literal syntaxes:

- **Double quotes (`"..."`)**: Support interpolation with `{expr}` syntax
- **Single quotes (`'...'`)**: Plain string literals, no interpolation

Both support the same escape sequences (`\n`, `\t`, `\\`, `\'`, `\"`, `\xNN`, `\uNNNN`). Single-quote strings are particularly useful as arguments inside interpolation expressions.

```sushi
let string s1 = "double quotes"       # With interpolation
let string s2 = 'single quotes'       # No interpolation
println("{s1.pad_left(20, '*')}")    # Single quotes for args
```

## Import

```sushi
use <collections/strings>
```

The import is checked PER UNIT. The unit that holds a string method call must import
`<collections/strings>` itself, or get it through its own `public use` chain. An import
in another unit of the program does not count. A missing import is **CE3015**. The
examples on this page leave out the `use` line; a complete program needs it.

Some string methods need no import:

- `is_empty()` and `clone()`
- `hash() -> u64` and `to_str() -> string`, which every primitive type has
- The comparisons `==`, `!=`, `<`, `<=`, `>`, `>=`. A string order compares the BYTES
  (`memcmp` over the common prefix, then the length), so a prefix is less than the
  longer string. It is not a collation.

## Overview

String methods do not change their receiver; each one returns a new value. The module
provides these methods (with `is_empty` and `clone`, which need no import):
- **Inspection**: len, size, is_empty, contains, starts_with, ends_with, find, find_last, count
- **Slicing**: s, ss, sleft, sright, char_at
- **Transformation**: upper, lower, cap, reverse, repeat, replace, trim, tleft, tright
- **Padding**: pad_left, pad_right
- **Stripping**: strip_prefix, strip_suffix
- **Splitting/Joining**: split, join
- **Conversion**: to_bytes, to_i32, to_i64, to_f64
- **Concatenation**: concat

## Inspection Methods

### `.len() -> i32`

Character count (UTF-8 aware).

```sushi
let string s = "Hello 🌍"
println(s.len())  # 7 characters
```

### `.size() -> i32`

Byte count.

```sushi
println(s.size())  # 10 bytes
```

### `.is_empty() -> bool`

Check if string is empty.

```sushi
if (s.is_empty()):
    println("Empty string")
```

### `.contains(string needle) -> bool`

Check if string contains substring.

```sushi
let string text = "hello world"
if (text.contains('world')):
    println("Found!")
```

### `.starts_with(string prefix) -> bool`

Check if string starts with prefix.

```sushi
let string path = "/home/user"
if (path.starts_with("/home")):
    println("Home directory")
```

### `.ends_with(string suffix) -> bool`

Check if string ends with suffix.

```sushi
let string filename = "document.txt"
if (filename.ends_with(".txt")):
    println("Text file")
```

### `.find(string needle) -> Maybe@(i32)`

Find first occurrence position (UTF-8 character index).

```sushi
match text.find('world'):
    Maybe.Some(pos) ->
        println("Found at {pos}")
    Maybe.None() ->
        println("Not found")
```

### `.find_last(string needle) -> Maybe@(i32)`

Find last occurrence position (UTF-8 character index).

```sushi
let string text = "hello world hello"
match text.find_last('hello'):
    Maybe.Some(pos) ->
        println("Last at {pos}")  # 12
    Maybe.None() ->
        println("Not found")
```

### `.count(string needle) -> i32`

Count non-overlapping occurrences.

```sushi
let string text = "hello world"
println(text.count("l"))  # 3
println(text.count("oo"))  # 0
```

## Slicing Methods

### `.sleft(i32 n) -> string`

Get first n UTF-8 characters.

```sushi
let string text = "hello"
println(text.sleft(3))  # "hel"

let string utf8 = "café"
println(utf8.sleft(3))  # "caf"
```

### `.sright(i32 n) -> string`

Get last n UTF-8 characters.

```sushi
let string text = "hello"
println(text.sright(3))  # "llo"
```

### `.char_at(i32 index) -> string`

Get UTF-8 character at index.

```sushi
let string text = "hello"
println(text.char_at(0))  # "h"
println(text.char_at(4))  # "o"
```

### `.s(i32 start, i32 end) -> string`

Slice by UTF-8 character indices.

```sushi
let string text = "hello world"
println(text.s(0, 5))  # "hello"
println(text.s(6, 11))  # "world"
```

### `.ss(i32 start, i32 length) -> string`

Substring by character offset and character count (UTF-8 aware, like `.s()`).
`"héllo".ss(1, 3)` is `"éll"`.

```sushi
let string text = "hello"
println(text.ss(0, 3))  # "hel"
println(text.ss(2, 3))  # "llo"
```

## Case Conversion

### `.upper() -> string`

Convert to uppercase (ASCII only).

```sushi
let string loud = "hello".upper()  # "HELLO"
```

### `.lower() -> string`

Convert to lowercase (ASCII only).

```sushi
let string quiet = "HELLO".lower()  # "hello"
```

### `.cap() -> string`

Capitalize first character.

```sushi
let string name = "alice"
println(name.cap())  # "Alice"
```

## Transformation Methods

### `.reverse() -> string`

Reverse string preserving UTF-8 characters.

```sushi
let string s = "hello"
println(s.reverse())  # "olleh"

let string utf8 = "café"
println(utf8.reverse())  # "éfac"
```

`.reverse()` does not change the string, but today the compiler checks it like the
in-place array `.reverse()`. So the receiver must be a name that you could write: a local
works, but a parameter is refused (CE2422) and a temporary is refused (CE2429). Bind the
value to a local first (`let string local = p.clone()`, then `local.reverse()`).

### `.repeat(i32 n) -> string`

Repeat string n times.

```sushi
let string s = "abc"
println(s.repeat(3))  # "abcabcabc"

println("*".repeat(10))  # "**********"
```

### `.replace(string old, string new) -> string`

Replace all occurrences.

```sushi
let string text = "hello world"
println(text.replace('world', 'there'))  # "hello there"

let string censored = "damn damn".replace('damn', '****')
println(censored)  # "**** ****"

# Works beautifully in interpolation:
println("{text.replace('world', 'there')}")
```

### `.concat(string other) -> string`

Concatenate strings.

```sushi
let string greeting = "Hello".concat(" World")
println(greeting)  # "Hello World"
```

`concat` makes a new string and does not change the receiver. When you rebind a string
to its own concatenation, `s := s.concat(x)`, the compiler appends to the buffer of `s`
in place, so a loop of appends takes linear time:

```sushi
use <collections/strings>

fn main() i32:
    let string csv = ""
    foreach(i in 0..5):
        csv := csv.concat("{i},")
    println(csv)  # "0,1,2,3,4,"
    return 0
```

Only this exact form appends in place. In each of these forms, every step copies the
whole string, so a loop of them takes quadratic time:

- `s := s.concat(a).concat(b)` (the second `concat` copies the first result)
- `s := "{s}{x}"`
- `t := s.concat(x)` (a different target)

To build a string from many pieces in those forms, push the pieces to a `string[]` and
join them one time:

```sushi
use <collections/strings>

fn main() i32:
    let string[] parts = from([])
    foreach(i in 0..5):
        parts.push("{i}")
    println(",".join(parts))  # "0,1,2,3,4"
    return 0
```

## Whitespace Trimming

### `.trim() -> string`

Remove leading/trailing whitespace.

```sushi
let string clean = "  hello  ".trim()  # "hello"
```

### `.tleft() -> string`

Remove leading whitespace.

```sushi
let string clean = "  hello".tleft()  # "hello"
```

### `.tright() -> string`

Remove trailing whitespace.

```sushi
let string clean = "hello  ".tright()  # "hello"
```

## Padding Methods

### `.pad_left(i32 width, string char) -> string`

Pad to width by prepending character.

```sushi
let string s = "42"
println(s.pad_left(5, '0'))  # "00042"

let string name = "Alice"
println(name.pad_left(10, ' '))  # "     Alice"

# Great in interpolation:
println("{s.pad_left(5, '0')}")
```

### `.pad_right(i32 width, string char) -> string`

Pad to width by appending character.

```sushi
let string s = "42"
println(s.pad_right(5, '0'))  # "42000"
```

## Stripping Methods

### `.strip_prefix(string prefix) -> string`

Remove prefix if present.

```sushi
let string path = "/home/user/file.txt"
println(path.strip_prefix("/home/user/"))  # "file.txt"

let string text = "hello"
println(text.strip_prefix("bye"))  # "hello" (unchanged)
```

### `.strip_suffix(string suffix) -> string`

Remove suffix if present.

```sushi
let string filename = "document.txt"
println(filename.strip_suffix(".txt"))  # "document"

let string text = "hello"
println(text.strip_suffix("bye"))  # "hello" (unchanged)
```

## Splitting and Joining

### `.split(string delimiter) -> string[]`

Split into array.

```sushi
let string[] parts = "a,b,c".split(',')
# parts = ["a", "b", "c"]

```

A `string[]` cannot go into an interpolation hole (CE2035). Join it first:
`println("Parts: {','.join(parts)}")`.

### `.split_once(string sep) -> Maybe@((string, string))`

### `.rsplit_once(string sep) -> Maybe@((string, string))`

Split at the FIRST (`split_once`) or the LAST (`rsplit_once`) occurrence of `sep`, into the
part before it and the part after it. The separator is in neither part, and both parts are
new owned strings. No occurrence gives `Maybe.None`. An empty separator matches at the
start for `split_once`, `Some(("", s))`, and at the end for `rsplit_once`, `Some((s, ""))`.
The search is by bytes, so a multi-byte UTF-8 separator works.

```sushi
use <collections/strings>

fn main() i32:
    let (key, value) = "name=Arthur=Dent".split_once("=").realise(("", ""))
    println("{key} {value}")                             # name Arthur=Dent
    match "a/b/c".rsplit_once("/"):
        Maybe.Some((dir, file)) -> println("{dir} {file}")   # a/b c
        Maybe.None -> println("no separator")
    return 0
```

### `.join(string[] parts) -> string`

Join array with separator.

```sushi
let string[] words = from(["a", "b", "c"])
println(','.join(words))  # "a,b,c"

println(''.join(words))  # "abc"

# Single quotes shine in interpolation:
println("{','.join(words)}")
```

## Conversion Methods

### `.to_bytes() -> u8[]`

Convert to byte array.

```sushi
let string text = "Hi"
let u8[] bytes = text.to_bytes()
# bytes = [72, 105]
```

The inverse conversions are `u8[]` methods (core, no import):
`bytes.to_string()` (a copy, no UTF-8 check) and
`bytes.to_string_checked() -> Result@(string, StdError)` (validates UTF-8, `Result.Err` on
malformed input).

## Byte access in place (core, no import)

Two primitives of the string itself need no import and copy nothing (#1091). With them a
string method can be an ordinary `extend string` method written in Sushi.

- **`s[i] -> u8`**: the byte at offset `i`, bounds-checked (`RE2020` past `size`). A read
  only: `s[i] := v` is `CE2113`.
- **`string.from_bytes(nom u8[] b) -> string`**: the string takes the array's buffer.
  No byte is copied and none is checked for UTF-8.

```sushi
use <collections/strings>

extend string upper_ascii() string:
    let u8[] out = from([0; self.size()])
    foreach(i in 0..self.size()):
        let u8 b = self[i]
        if (b >= 97 and b <= 122):
            out[i] := b - 32
        else:
            out[i] := b
    return string.from_bytes(nom out)

fn main() i32:
    println("Mostly Harmless".upper_ascii())   # MOSTLY HARMLESS
    return 0
```

`upper_ascii` allocates once and copies once, as the built-in `upper()` does; written
over `to_bytes()` and `to_string()` it would copy twice.

### `.to_i32() -> Maybe@(i32)`

Parse to i32.

```sushi
match "42".to_i32():
    Maybe.Some(n) ->
        println("Parsed: {n}")
    Maybe.None() ->
        println("Invalid number")
```

### `.to_i64() -> Maybe@(i64)`

Parse to i64.

```sushi
let Maybe@(i64) result = "9223372036854775807".to_i64()
```

### `.to_f64() -> Maybe@(f64)`

Parse to f64.

```sushi
match "3.14".to_f64():
    Maybe.Some(pi) ->
        println("Pi: {pi}")
    Maybe.None() ->
        println("Invalid float")
```

## Best Practices

- The methods do not change their receiver (they return new strings)
- Use `.len()` for character count, `.size()` for byte count
- UTF-8 aware methods: len, sleft, sright, char_at, s, ss, find, find_last
- Byte-based methods: size, contains, starts_with, ends_with
- Case conversion is ASCII-only (upper, lower, cap)
- Use `.realise()` or pattern matching to handle Maybe results from find/parse
