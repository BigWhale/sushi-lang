# 11. Perks & Extension Methods

In the last chapter we made types *generic*. This chapter gives types **behaviour**, in two
ways. An extension method adds a new method to a type, also to a type that you do not own. A
perk is a contract: it is Sushi's version of an interface (Java) or a trait (Rust). A type
that has the methods of the perk can say that it satisfies the perk.

The compiler changes both into plain function calls, so they have no cost at run time.

## Extension methods

An **extension method** adds a method to an existing type. It does not change the
definition of the type. You can write `squared(6)` as a free function; an extension lets you
write `6.squared()`. In the method body, the name of the receiver is `self`.

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/extensions.sushi"
```

Some things to know:

- You can extend **primitives** (`i32`, `string`, `bool`, ...) and also your own structs and
  enums.
- An extension method with no error channel returns a **bare value**, not a `Result`. So
  `squared` ends with `return self * self`. The call site is plain `six.squared()`, with no
  `??` and no `.realise(...)`.
- In a bare method, `return Result.Ok(...)` is an error ([`CE2091`](../error-catalog.md#ce2091)), and so is `??`
  ([`CE0131`](../error-catalog.md#ce0131)): the method has no error channel. The section
  [An error channel](#an-error-channel) shows how to add one.
- You can call `something.method()` in a string interpolation: see `{six.squared()}`.

The rule is the same for a free function (chapter 4): a callable has an error channel only
when its signature writes `| E`. A bare method is the exception. Use it only when the method
is total over its inputs and will stay so, like `squared`. Write a channel for a method that
can fail, now or later. [The error channel](../design/error-channel.md) gives the rule.

Output:

```
6 squared is 36
42 is the answer
*** Don't panic ***
```

!!! note "UFCS: `x.method(args)` is `method(x, args)`"
    Sushi uses *Uniform Function Call Syntax*. The compiler changes `six.squared()` to
    `squared(six)`, and `panic.banner()` to `banner(panic)`. The dot notation is only a
    different spelling: there is no vtable, no boxing and no dynamic lookup.

## The receiver: `self`, `poke self` and `nom self`

The receiver is a parameter, and it has a mode like every other parameter (chapter 12
explains the modes in full):

| Receiver | What the method can do |
|---|---|
| `self` (no mode written) | Read the value. This is a read-only borrow. |
| `poke self` | Read and change the value. The caller keeps it. |
| `nom self` | Take the value. The caller cannot use it after the call. |

Write `poke self` or `nom self` in the parameter list of the method. The plain `self` is
not written: a method with no receiver mode reads `self`.

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/receiver-modes.sushi"
```

Output:

```
towels: 42
the label was towels
```

`bump` changes its receiver, so it declares `poke self`. If you remove `poke self`, the
assignment `self.n := ...` is an error: `CE2421: cannot write through 'self': a method
receiver is a read-only borrow`. The call site does not change: it is `c.bump()` in both
cases.

`into_label` takes the whole counter and gives back its label. `nom self.label` takes the
field out of the receiver. After the call, `c` is spent, and a use of `c` is an error
([`CE2435`](../error-catalog.md#ce2435)). The standard library uses this form where a value must end: `File.close()` and
`BufWriter.finish()` both take `nom self`.

## An error channel

An extension method can declare an **error channel**: write `| E` after the return type,
where `E` is an enum. The method is then fallible, as a free function is:

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/channel.sushi"
```

Output:

```
half of 84 is 42
half of 7 is -1
stopped at the odd number 21
```

With `| E`, the rules change:

- The call gives a `Result@(T, E)`. Handle it with `??`, `.realise(default)` or `match`,
  as in chapter 6.
- The body spells **both** constructors: `return Result.Ok(value)` and
  `return Result.Err(error)`. A bare `return value` is an error ([`CE2030`](../error-catalog.md#ce2030)). A method that
  returns `~` ends with `return Result.Ok(~)`.
- `??` is legal in the body. `quarter` uses it to pass on the error of `half`.
- A chain stops at an unhandled channel. `8.half().half()` is an error ([`CE2515`](../error-catalog.md#ce2515)), because
  the first call gives a `Result`. In a function or method with a channel, write
  `n.half()??.half()`; otherwise handle the first result.

## Array targets and generic targets

The target of an extension can also be an array type or a generic type:

- `extend i32[] total()` adds a method to one array type, `i32[]`.
- `extend T[] count()` adds a method to every dynamic array. `T` is the element type.
- `extend Box@(T: Clone) get()` adds a method to every `Box` whose `T` has `Clone`: it
  returns a clone of the value, because a field read is a borrow. A concrete argument, as in
  `extend Box@(i32) ...`, adds the method to `Box@(i32)` only.

A method can also have its **own** type parameters, after its name: `paired@(U)`. The
compiler finds `U` from the arguments. A method call has no place for explicit type
arguments.

The compiler checks the body of a generic method one time, where it is written, and a
type parameter has only what a perk promises. `paired` puts `self.value` and `other` in
holes, so the target asks for `Display` on both: `extend Box@(T: Display)
paired@(U: Display)`. A bound in the target (`T: Display`) is legal only there; a struct,
an enum and a function write their bounds in their own `@(...)`.

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/targets.sushi"
```

Output:

```
42
42
2
42 and a towel
```

Chapter 19 uses these forms: the standard library declares `extend T[] map@(U)(...)` and
`extend List@(T: Clone) filter(...)`.

## Static methods: a constructor on the type

The extension methods above take a receiver. A **static** method takes no receiver: write
`static` before the name. You call a static method on the *type*, not on a value.

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/statics.sushi"
```

Output:

```
from (0, 0) the distance squared is 25
```

`Vec.at(3, 4)` has the same form as `List.new()` and `HashMap.new()`, which you use in
chapter 13. Those are static methods too, on types that the compiler declares.

Some rules:

- A static has **no `self`**. A receiver in the signature, or `self` in the body, is an
  error ([`CE0134`](../error-catalog.md#ce0134)). Give the method what it needs as an ordinary parameter.
- A name behind a type's dot is **one** thing: a variant or a static method. On an enum, a
  static cannot have the name of a variant ([`CE2103`](../error-catalog.md#ce2103)). On all types, a static cannot have
  the name of an instance method ([`CE0101`](../error-catalog.md#ce0101)).
- An array target cannot have a static ([`CE2104`](../error-catalog.md#ce2104)): `i32[].empty()` has no spelling.
- A perk implementation cannot hold a static ([`CE4014`](../error-catalog.md#ce4014)).

`new` is a good name for a static: `extend Box static new(i32 n) Box:`. A *free* function
cannot have the name `new`.

## Perks: defining a contract

A **perk** is a named set of method signatures. A type that has those methods can declare
that it satisfies the perk.

To implement a perk for a type, write `extend TypeName with PerkName:`, then give the
method bodies.

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/perk-basics.sushi"
```

`perk Describable` declares one method: `fn describe() string`. The signature has no body.
Like an extension method, a perk method returns a bare value when it declares no channel.
`Robot` and `Ship` each say `extend ... with Describable:` and give their own `describe`.
Two different structs have one shared vocabulary.

Output:

```
Marvin (battery: 42%)
Heart of Gold (crew: 5)
```

A perk method can also declare `| E`, for example `fn halve() i32 | HalfError`. Then
every implementation declares the same channel ([`CE0133`](../error-catalog.md#ce0133) if they do not agree), and the
body spells `Result.Ok` and `Result.Err`, as in the section above. The `Reader` and `Writer`
perks of `<io/contracts>` (chapter 14) use this form.

## Perks as generic constraints

The perk above only gives each type a `describe` method. Plain extension methods can do
that too. The real value of perks comes with the generics of the previous chapter.

A generic function can **constrain** its type parameter with `@(T: PerkName)`. This means:
"T can be any type that implements `PerkName`". In the function body, you can then call
the methods of the perk on the value.

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/perk-constraint.sushi"
```

`announce@(T: Describable)(T item)` accepts a `Robot`, a `Ship`, or any other type that
implements `Describable`, and calls `item.describe()`. The compiler checks the constraint
at the call site, and refuses a type that does not satisfy it ([`CE4006`](../error-catalog.md#ce4006)). Then it makes a
specialised `announce` for each type, as in chapter 10. The check occurs at compile time
only.

Output:

```
Robot Marvin (battery: 42%)
Ship Heart of Gold (crew: 5)
```

## A perk on every array

`extend T[] with PerkName:` implements a perk for every dynamic array. `T` is the element
type, as in `extend T[] count()` above. The compiler makes a copy for each array type
that the program uses with the perk.

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/array-perk.sushi"
```

One implementation covers `i32[]`, `string[]` and `bool[]`. The body names `T` to hold the
first element and puts it in a hole, so the target asks for `Display` on the element:
`extend (T: Display)[] with Describable`. `announce` accepts each array, because each
array type implements `Describable`. An array whose element has no `Display` (an array of
function values) does not.

Output:

```
3 items, the first is 42
2 items, the first is Arthur
nothing at all
```

There is no specialization. If `extend T[] with Describable` exists, a second
`extend i32[] with Describable` is a duplicate ([`CE4002`](../error-catalog.md#ce4002)). Implement the perk on the
template, or on each concrete array type.

## `Hashable` is predefined

One perk describes behaviour that the compiler already derives: hashing. Almost every type
in Sushi gets an auto-derived `.hash() -> u64`. The perk `Hashable` (`fn hash() u64`) comes
with the compiler, as does `Drop` (chapter 12). Do not declare it: a `perk Hashable:` of
your own is a duplicate ([`CE4001`](../error-catalog.md#ce4001)). Every type with a derived hash **satisfies it
automatically**: the primitives, `string`, and a plain struct or enum.

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/synthetic-hash.sushi"
```

`fingerprint@(T: Hashable)` needs a hashable argument. `Point` satisfies the constraint
through its derived hash. The explicit `extend Point with Hashable` is an **override**: it
makes the fingerprint `30` and not the derived value. For `42` (an `i32`) and `true` (a
`bool`) we write nothing. A type that the compiler cannot hash (for example, a struct that
holds a `HashMap`) does not satisfy `Hashable` unless it implements the perk. The constraint
refuses it with [`CE4006`](../error-catalog.md#ce4006).

Output:

```
Point fingerprint: 30
i32 and bool hashed through the predefined Hashable: 6807129317463932018, 1
```

(The large number is the FxHash of the integer `42`. The hash is deterministic, so your
number is the same.)

## `Display`, `Eq` and `Ord` are predefined too

[Chapter 8](08-structs-and-enums.md) showed that every struct and enum prints and compares
with no code of your own. Three predefined perks give that behaviour. Each has one method:

| Perk | Contract | Read by |
|------|----------|---------|
| `Display` | `fn to_str() string` | an interpolation hole, `print`, `println` |
| `Eq` | `fn eq(Self other) bool` | `==`, `!=`, `contains`, `index_of` |
| `Ord` | `fn compare(Self other) i32` | `<`, `<=`, `>`, `>=` |

As with `Hashable`, an implementation is an **override** of the derived behaviour:

```sushi
--8<-- "docs/tutorial/examples/11-perks-and-extensions/display-override.sushi"
```

Output:

```
Ford's towel, 2 knots
Look: Ford's towel, 2 knots
Look: 42
Shelf(top: Ford's towel, 2 knots, count: 1)
```

The override applies in every position: `println`, a hole, the generic `announce`, and a
`Towel` that a `Shelf` holds. `Shelf` has no override, so it keeps its derived text and
uses the `Towel` text for its field. `@(T: Display)` takes `42` too, because every
primitive has a `Display`.

One more perk comes with the compiler: **`Clone`**, `fn clone() Self`. A generic body that
calls `.clone()` on a `T` asks for it: `@(T: Clone)`. Every type that holds no resource
satisfies it, so you never implement it (an implementation is [`CE4017`](../error-catalog.md#ce4017)). A `File` does
not satisfy it, because a copy of a file handle would close one descriptor two times;
`.share()` gives a second owner of a handle.

Where the contract says `Self`, an implementation writes its own type: `fn eq(Towel other)
bool`. `Self` is legal in these predefined contracts only. A perk that you write cannot use
it.

## What perks cannot do

Perks are simple on purpose. Know these limits:

- **No type parameters.** `perk Iterator@(Item):` is an error ([`CE4010`](../error-catalog.md#ce4010)).
- **No inheritance.** A perk cannot require another perk (no `perk Ord: Eq`). To ask for
  more than one capability, use `+` at the *use* site:
  `fn f@(T: Hashable + Describe)(T x)`.
- **No default implementations.** Each type implements every method of the perk. A perk
  cannot give a fallback body.
- **No static methods** ([`CE4014`](../error-catalog.md#ce4014)). A perk has no `Self` type, so a contract cannot hold a
  constructor.
- **One home for a name.** A perk method and an extension method with the same name on the
  same type is an error ([`CE4007`](../error-catalog.md#ce4007)). A name is a contract method or a convenience method,
  never both.

With these limits, every perk method call goes to a known function at compile time.

## What you learned

- **Extension methods** (`extend Type method() Ret:`) add methods to any type, primitives
  included. The receiver is `self`. With no channel, the method returns a **bare** value.
- The receiver has a mode: `self` reads, `poke self` changes, `nom self` takes.
- `| E` gives a method an **error channel**: the call gives `Result@(T, E)`, and the body
  spells `Result.Ok` and `Result.Err`.
- A target can be an array (`extend T[]`, `extend i32[]`) or a generic type
  (`extend Box@(T)`), and a method can have its own type parameters (`paired@(U)`).
- **Static methods** (`extend Type static name() Ret:`) have no receiver. You call them on
  the type: `Vec.at(3, 4)`. `List.new()` is a static method too.
- **UFCS**: the compiler changes `x.method(args)` to `method(x, args)`, with no cost at run
  time.
- A **perk** is a contract of method signatures. A type opts in with
  `extend Type with Perk:`.
- Perks are **generic constraints** (`@(T: Perk)`), checked at compile time.
- `Hashable` is **predefined**, as `Drop`, `Eq`, `Ord`, `Display` and `Clone` are. Every type with a
  derived `hash()` satisfies it, and `extend T with Hashable` replaces the derived hash.
  The compiler also derives `==` and the order operators (`Eq`, `Ord`) and the text of
  a struct or enum (`Display`); an `extend T with Display` (or `Eq`, `Ord`)
  implementation overrides them, in every position.
- Perks have no type parameters, no inheritance, no default methods and no statics.

The next chapter is about how Sushi manages memory without a garbage collector: ownership,
RAII and borrowing. Go to [Memory Management](12-memory-management.md).
