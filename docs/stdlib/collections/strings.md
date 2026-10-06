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
in another unit of the program does not count. A missing import is **[CE3015](../../error-catalog.md#ce3015)**. The
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

These are written in Sushi (see [Methods written in Sushi](#methods-written-in-sushi)):
- **Strict parses**: parse_u8, parse_u16, parse_u32, parse_u64, parse_i32, parse_i64
- **Trims of a given text**: trim_start_matches, trim_end_matches
- **Lines, tokens and characters**: lines, split_whitespace, chars
- **Number formatting** (on the integer and float types): to_hex, to_hex_width, to_bin,
  to_bin_width, to_fixed
- **Building**: the `StringBuilder` type

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
works, but a parameter is refused ([CE2422](../../error-catalog.md#ce2422)) and a temporary is refused ([CE2429](../../error-catalog.md#ce2429)). Bind the
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

A `string[]` in an interpolation hole prints each element in quotes: `["a", "b", "c"]`.
To write the parts as one string, join them: `println("Parts: {','.join(parts)}")`.

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

### `.join(List@(string) parts) -> string`

Join the parts with the separator between two parts. An empty `parts` gives an empty
string. The argument is a `string[]` or a `List@(string)`, and the two give the same
result for the same elements. The call borrows `parts`, so you can use it again after
the call.

```sushi
let string[] words = from(["a", "b", "c"])
println(','.join(words))  # "a,b,c"

println(''.join(words))  # "abc"

# Single quotes shine in interpolation:
println("{','.join(words)}")

let List@(string) crew = List.new()
crew.push("Arthur")
crew.push("Ford")
println(' & '.join(crew))  # "Arthur & Ford"
println(crew.len())        # 2
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

- **`s[i] -> u8`**: the byte at offset `i`, bounds-checked ([`RE2020`](../../error-catalog.md#re2020) past `size`). A read
  only: `s[i] := v` is [`CE2113`](../../error-catalog.md#ce2113).
- **`string.from_bytes(nom u8[] b) -> string`**: the string takes the array's buffer.
  No byte is copied and none is checked for UTF-8.

```sushi
use <collections/strings>

extend string upper_ascii() string:
    let u8[] out = from([0; self.size()])
    foreach(i in 0..self.size()):
        let u8 b = self[i]
        if (b >= a'a' and b <= a'z'):
            out[i] := b - 32
        else:
            out[i] := b
    return string.from_bytes(nom out)

fn main() i32:
    println("Mostly Harmless".upper_ascii())   # MOSTLY HARMLESS
    return 0
```

`a'a'` and `a'z'` are byte literals: the bytes 97 and 122, typed `u8` here (see
[Numeric Literals](../../language-reference.md#numeric-literals)).

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

## Methods written in Sushi

The module has two halves. The methods above are built into the stdlib bitcode. The
methods below are written in Sushi (`src_sushi/collections/strings.sushi`), and the same
`use <collections/strings>` loads them. Each one is bare and total: a call gives the value
itself, with no `??`.

### Strict parses: `.parse_u8(i32 base)` to `.parse_i64(i32 base)`

```sushi
extend string parse_u8(i32 base) Maybe@(u8)
extend string parse_u16(i32 base) Maybe@(u16)
extend string parse_u32(i32 base) Maybe@(u32)
extend string parse_u64(i32 base) Maybe@(u64)
extend string parse_i32(i32 base) Maybe@(i32)
extend string parse_i64(i32 base) Maybe@(i64)
```

Each method parses the text as a number of its type, in the base `base`. The rule is
strict:

- The text holds the digits of `base` and nothing else. A space, a `+`, a `0x` or `0b`
  prefix and a `_` are refused. An empty text is refused.
- A digit is `0`-`9`, then `a`-`z` or `A`-`Z` for 10 to 35. Each digit must be less than
  `base`.
- A leading zero is legal: `"007"` is 7.
- `parse_i32` and `parse_i64` take ONE leading `-` and no other sign. `"-0"` is 0. `"-"`,
  `"--5"` and `"-+5"` are refused. The negative side reaches the minimum of the type:
  `"-2147483648".parse_i32(10)` is `-2147483648`. The unsigned methods refuse every sign.
- A value out of the range of the type is refused, and so is a base outside 2 to 36.

A refused text gives `Maybe.None()`; a correct text gives `Maybe.Some(value)`.

```sushi
use <collections/strings>

fn main() i32:
    println("ff".parse_u8(16).realise(0))
    println("-80000000".parse_i32(16).realise(0))
    println("007".parse_u32(10).realise(0))
    println("0x1f".parse_u32(16).is_some())
    println(" 42".parse_u32(10).is_some())
    println(" 42".trim().parse_u32(10).realise(0))
    println("256".parse_u8(10).is_some())
    return 0
```

Output:

```
255
-2147483648
7
false
false
42
false
```

`to_i32()` is the lenient decimal parse: it takes a leading space and a `+`. To get the
lenient form of a strict parse, trim the text first: `s.trim().parse_u32(10)`.

### `.trim_start_matches(string t) -> string`

Removes every repeat of `t` at the start of the text, and gives a new string. The repeats
must touch: `"ab ab".trim_start_matches("ab")` gives `" ab"`. An empty `t` gives the text
unchanged. A `t` that is longer than the text removes nothing, and a text of repeats alone
gives `""`. The compare is by bytes, so a multi-byte `t` works.

```sushi
use <collections/strings>

fn main() i32:
    println("0042".trim_start_matches("0"))      # 42
    println("ababx".trim_start_matches("ab"))    # x
    println("ééx".trim_start_matches("é"))       # x
    println("abc".trim_start_matches(""))        # abc
    return 0
```

### `.trim_end_matches(string t) -> string`

Removes every repeat of `t` at the end of the text, and gives a new string. The rules are
the rules of `trim_start_matches`. The repeats are counted from the end, so a part of a
repeat stays: `"aaa".trim_end_matches("aa")` gives `"a"`.

```sushi
use <collections/strings>

fn main() i32:
    println("a/b///".trim_end_matches("/"))      # a/b
    println("babab".trim_end_matches("ab"))      # b
    println("aaa".trim_end_matches("aa"))        # a
    return 0
```

### `.lines() -> string[]`

Splits the text at each `\n`, and gives each line as a new string. A `\r` immediately
before the `\n` is removed, and a `\r` anywhere else stays. A final `\n` does not start an
empty last line.

| Text | Lines |
|---|---|
| `""` | `[]` |
| `"a"` | `["a"]` |
| `"a\n"` | `["a"]` |
| `"a\r\nb"` | `["a", "b"]` |
| `"\n"` | `[""]` |
| `"a\n\nb"` | `["a", "", "b"]` |
| `"a\rb"` | `["a\rb"]` |

```sushi
use <collections/strings>

fn main() i32:
    let string text = "Mostly\r\nHarmless\n"
    let string[] lines = text.lines()
    println(lines.len())                         # 2
    foreach(line in lines.iter()):
        println("[{line}]")                      # [Mostly], then [Harmless]
    return 0
```

### `.split_whitespace() -> string[]`

Splits the text at each run of white space, and gives each token as a new string. No
token is empty, so `""` and `"   "` give `[]`. White space is the set of `trim()`: the
bytes 9, 10, 11, 12, 13 and 32. Every other byte is part of a token.

```sushi
use <collections/strings>

fn main() i32:
    let string[] words = " Mostly \t Harmless\n".split_whitespace()
    println(words.len())                         # 2
    println(words[0])                            # Mostly
    println(words[1])                            # Harmless
    return 0
```

### `.chars() -> string[]`

Splits the text into its characters, and gives each character as a new string that holds
the bytes of that character. A character starts at each byte that is not a UTF-8
continuation byte (`b & 0xC0 != 0x80`). That is the rule of `len()`, so
`s.chars().len()` equals `s.len()` for every string.

The rule applies to text that is not valid UTF-8 too (for example from
`string.from_bytes`). A continuation byte joins the character before it, and continuation
bytes at the start join the first character. A text of continuation bytes alone has no
character, so it gives `[]`, because its `len()` is 0.

```sushi
use <collections/strings>

fn main() i32:
    let string word = "café"
    let string[] cs = word.chars()
    println("{cs.len()} {word.len()}")           # 4 4
    println(cs[3])                               # é
    println(cs[3].size())                        # 2
    return 0
```

## Number formatting

### `.to_hex()`, `.to_hex_width(i32 w)`, `.to_bin()`, `.to_bin_width(i32 w)`

These four methods are on each integer type: `u8`, `u16`, `u32`, `u64`, `i8`, `i16`,
`i32` and `i64`. Each one gives a new string.

- `to_hex()` writes the value in base 16, in lower case, with no leading zero. The value
  0 gives `"0"`.
- `to_bin()` writes the value in base 2, with no leading zero.
- `to_hex_width(w)` and `to_bin_width(w)` add leading zeros until the text has `w`
  digits. A value that has more digits than `w` is never cut. A `w` of 0 or less adds no
  zero.
- A signed value gives the two's complement bits of its own width, with no minus sign:
  `(-1 as i8).to_hex()` is `"ff"`, and `(-1).to_hex()` on an `i32` is `"ffffffff"`.

The methods write digits only, with no `0x` or `0b` prefix. For a width with spaces, or
for an alignment, use `pad_left` or `pad_right` on the result.

```sushi
use <collections/strings>

fn main() i32:
    let u8 b = 10
    let i8 m = -1
    let i32 port = 8080
    println(b.to_hex())
    println(b.to_hex_width(2))
    println(m.to_hex())
    println(port.to_hex_width(8))
    println(b.to_bin())
    println(b.to_bin_width(8))
    println(m.to_bin())
    return 0
```

Output:

```text
a
0a
ff
00001f90
1010
00001010
11111111
```

### `.to_fixed(i32 p) -> string`

This method is on `f64` and `f32`. It writes the value with exactly `p` digits after the
point, and gives a new string.

- The value is rounded as C `printf("%.*f")` rounds it. The rounding uses the exact
  binary value, so 2.5 with `p = 0` gives `"2"`, and 0.125 with `p = 2` gives `"0.12"`.
- A `p` of 0 writes no point. A `p` below 0 is the same as 0.
- An `f32` value is first changed to `f64`, with no change to its value. Thus the digits
  show the precision of the `f32`: `0.1` as an `f32` with `p = 10` is `"0.1000000015"`.
- There is no limit on the length: a very large value gives all its integer digits.

For a width with spaces, or for an alignment, use `pad_left` or `pad_right` on the
result.

```sushi
use <collections/strings>

fn main() i32:
    let f64 pi = 3.14159
    let f64 half = 2.5
    let f32 tenth = 0.1
    println(pi.to_fixed(2))
    println(pi.to_fixed(0))
    println(half.to_fixed(0))
    println(tenth.to_fixed(10))
    println("[{pi.to_fixed(3).pad_left(8, ' ')}]")
    return 0
```

Output:

```text
3.14
3
2
0.1000000015
[   3.142]
```

## StringBuilder

TODO(worker)

## Best Practices

- The methods do not change their receiver (they return new strings)
- Use `.len()` for character count, `.size()` for byte count
- UTF-8 aware methods: len, sleft, sright, char_at, s, ss, find, find_last
- Byte-based methods: size, contains, starts_with, ends_with
- Case conversion is ASCII-only (upper, lower, cap)
- Use `.realise()` or pattern matching to handle Maybe results from find/parse
