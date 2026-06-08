# 5. Strings

Text is everywhere, so a language lives or dies by how pleasant its strings are to work
with. Sushi strings are UTF-8 by default (a full Unicode galaxy, not just ASCII), they are
immutable (every operation returns a *new* string), and they come with two literal styles
and a tidy interpolation syntax. If you know Python's f-strings or Java's
`String.format`, you'll feel at home quickly — and you'll find Sushi a little stricter
about which quotes do what.

## Two kinds of quotes

Sushi has two string literal syntaxes, and the difference is meaningful:

- **Double quotes** `"..."` — support **interpolation** (embedding `{expr}`).
- **Single quotes** `'...'` — plain literals, **no** interpolation.

Both support the same escape sequences: `\n` (newline), `\t` (tab), `\\` (backslash),
`\'` (single quote), `\"` (double quote), and the numeric forms `\xNN` / `\uNNNN`.

```
--8<-- "docs/tutorial/examples/05-strings/literals.sushi"
```

Output:

```
Mostly Harmless
no {interpolation} happens here
tab	here
and a new line
a backslash: \
a quote inside singles: can't
a quote inside doubles: "quoted"
```

Notice that the single-quoted string printed its braces verbatim — `{interpolation}` is
just text there. Single quotes are perfect when you want a literal that happens to contain
braces, or simply to signal "this is a plain constant, nothing clever happening."

!!! note "There is no `+` for strings"
    Unlike Python or Java, Sushi does not overload `+` to glue strings together. The one
    blessed way to build strings is interpolation, which we meet next. This keeps the
    language honest: string building always looks the same.

## Interpolation

Inside a double-quoted string, anything in `{...}` is evaluated and spliced in. The
expression can be a variable, arithmetic, or even a method call.

```
--8<-- "docs/tutorial/examples/05-strings/interpolation.sushi"
```

Output:

```
Hello, Arthur!
The answer is 42, and twice that is 84
Shouted: HELLO WORLD
Padded: 00042
Ford Prefect
```

This is also how you concatenate: `"{first} {last}"` produces a brand-new string. Note the
neat trick of using **single quotes inside the braces** — `'42'.pad_left(5, '0')` — so the
literal argument never collides with the surrounding double quotes.

## Inspecting strings

The methods that ask questions *about* a string — its length, whether it contains
something, where a substring lives — live in the `<collections/strings>` standard library
unit. Import it once at the top of your file with `use <collections/strings>`.

```
--8<-- "docs/tutorial/examples/05-strings/inspect.sushi"
```

Output:

```
len:  11
size: 11
contains Panic: true
starts with Don: true
ends with ic:   true
count of n: 2
Found 'Panic' at 6
```

A few things worth calling out:

- `.len()` counts **characters** (UTF-8 aware) while `.size()` counts **bytes**. For pure
  ASCII like `"Don't Panic"` they match; for `"café"` they won't (`len` 4, `size` 5).
- `.contains()`, `.starts_with()`, and `.ends_with()` return plain `bool`s, so they slot
  straight into an `if`.
- `.count(needle)` counts non-overlapping occurrences.
- `.find(needle)` returns a `Maybe<i32>` — `Maybe.Some(index)` when found, `Maybe.None()`
  when not. That's Sushi refusing to hand you a magic `-1`; you have to acknowledge the
  "not found" case. We'll dig into `Maybe<T>` properly in
  [Chapter 6](06-error-handling.md).

## Transforming strings

The other big family of methods returns a *reshaped* string: trimming whitespace, changing
case, padding, and splitting/joining. Because strings are immutable, the original is never
touched — you always get a fresh value back.

```
--8<-- "docs/tutorial/examples/05-strings/transform.sushi"
```

Output:

```
trimmed: 'vogon poetry'
upper:   VOGON POETRY
lower:   loud noises
ticket:  0007
label:   name....
planets:
  - Earth
  - Betelgeuse
  - Magrathea
rejoined: Earth | Betelgeuse | Magrathea
```

Highlights:

- `.trim()` strips leading and trailing whitespace; `.upper()` / `.lower()` change ASCII
  case. They chain freely: `padded.trim().upper()`.
- `.pad_left(width, char)` and `.pad_right(width, char)` pad to a width using the given
  single-character string — handy for lining up columns or zero-padding numbers.
- `.split(delimiter)` returns a `string[]` (a dynamic array), which you iterate with
  `.iter()` in a `foreach`. Arrays get their own chapter soon.
- `.join(parts)` is the inverse: it's called on the **separator**, so `' | '.join(planets)`
  reads almost like English.

!!! note "What's in `<collections/strings>`?"
    The bare essentials — interpolation, escapes, the literal syntaxes — are built into the
    language and need no import. The richer methods (`contains`, `find`, `count`, `upper`,
    `lower`, `trim`, `pad_left`, `pad_right`, `split`, `join`, `replace`, `reverse`,
    `repeat`, slicing helpers, and more) come from `use <collections/strings>`. If the
    compiler complains that a method needs a stdlib unit, that import is almost always the
    fix.

## What you learned

- Sushi strings are immutable and UTF-8; double quotes interpolate, single quotes don't.
- Interpolation `"{expr}"` is the one way to build and concatenate strings — there's no
  string `+`.
- Escape sequences (`\n`, `\t`, `\\`, `\'`, `\"`) work in both quote styles.
- `use <collections/strings>` unlocks the method library: `len`/`size`, `contains`,
  `starts_with`/`ends_with`, `find` (returns `Maybe<i32>`), `count`, `trim`, `upper`/`lower`,
  `pad_left`/`pad_right`, `split`, and `join`.

We kept bumping into `Maybe` and `Result`. It's time to meet them head-on. On to
[Error Handling](06-error-handling.md).
