# 11. Perks & Extension Methods

In the last chapter we made types *generic*. This chapter is about giving types
**behaviour**. We'll do it two ways: extension methods, which bolt a new method onto a type
you don't own, and perks, which are Sushi's take on interfaces (Java) or traits (Rust) —
contracts that say "any type with these methods qualifies".

Both lower to plain function calls at compile time, so once again there's no runtime price
to pay.

## Extension methods

An **extension method** adds a method to an existing type without touching that type's
definition. You could already write `squared(6)` as a free function; an extension lets you
write `6.squared()` instead. Inside the method, the receiver is called `self`.

```
--8<-- "docs/tutorial/examples/11-perks-and-extensions/extensions.sushi"
```

A few things to notice:

- You can extend **primitives** (`i32`, `string`, `bool`, ...) as well as your own structs
  and enums. Adding a method to `i32` would be unthinkable in Java; here it's one line.
- Unlike ordinary functions, extension methods return a **bare value**, not a `Result`. So
  `squared` ends with `return self * self`, not `return Result.Ok(...)`. That's why the call
  site is plain `six.squared()` with no `??` or `.realise(...)` in sight.
- Calling `something.method()` inside a string interpolation works fine — see
  `{six.squared()}`.

Output:

```
6 squared is 36
42 is the answer
*** Don't panic ***
```

!!! note "UFCS: `x.method(args)` is just `method(x, args)`"
    Sushi uses *Uniform Function Call Syntax*. At compile time `six.squared()` is rewritten
    to `squared(six)`, and `panic.banner()` becomes `banner(panic)`. The dot notation is
    pure sugar — there's no vtable lookup, no boxing, nothing dynamic. It reads like a method
    call and runs like a function call.

## Perks: defining a contract

A **perk** is a named set of method signatures. A type that provides those methods can
declare that it satisfies the perk. If you know Java interfaces or Rust traits, this is the
same idea.

You implement a perk for a type with `extend TypeName with PerkName:` and then supply the
method bodies.

```
--8<-- "docs/tutorial/examples/11-perks-and-extensions/perk-basics.sushi"
```

`perk Describable` declares one method: `fn describe() string`. Note there's no body and no
`Result` — perk method signatures, like the extension methods above, deal in bare types.
Both `Robot` and `Ship` then say `extend ... with Describable:` and fill in their own
`describe`. Two unrelated structs, one shared vocabulary.

Output:

```
Marvin (battery: 42%)
Heart of Gold (crew: 5)
```

## Perks as generic constraints

On its own, the perk above just lets each type have a `describe` method — handy, but we
could have done that with plain extension methods. The real power shows up when you combine
perks with the generics from the previous chapter.

A generic function can **constrain** its type parameter with `<T: PerkName>`, meaning "T can
be any type, as long as it implements `Describable`". Inside the function you may then call
the perk's methods on the value.

```
--8<-- "docs/tutorial/examples/11-perks-and-extensions/perk-constraint.sushi"
```

`announce<T: Describable>(T item)` accepts a `Robot` or a `Ship` — or anything else that
implements `Describable` — and calls `item.describe()` on it. The compiler verifies the
constraint at the call site (and refuses to compile if you pass a type that doesn't qualify)
and then monomorphizes a specialised `announce` for each type, exactly as in Chapter 10. The
constraint is checked once, at compile time; nothing about it survives into the running
program.

Output:

```
Robot Marvin (battery: 42%)
Ship Heart of Gold (crew: 5)
```

## Primitives satisfy perks for free

Some perks describe behaviour the compiler already derives for the built-in types. The prime
example is hashing: every type in Sushi gets an auto-derived `.hash() -> u64`. That means
primitives **automatically satisfy** a `Hashable` perk without you writing any
`extend ... with Hashable` — they pick up a *synthetic* implementation.

```
--8<-- "docs/tutorial/examples/11-perks-and-extensions/synthetic-hash.sushi"
```

`fingerprint<T: Hashable>` needs its argument to be hashable. For `Point` we provide an
explicit `extend Point with Hashable`. But for `42` (an `i32`) and `true` (a `bool`) we
write nothing — the compiler supplies the `Hashable` implementation from the auto-derived
hash. The same generic function therefore works on our struct and on raw primitives alike.

Output:

```
Point fingerprint: 30
i32 and bool hashed via synthetic Hashable: 6807129317463932018, 1
```

(The large number is the real FxHash of the integer `42`; we are not making it up. Yours
will match, because the hash is deterministic.)

## What perks can't do (yet)

Perks are deliberately simple, and it's worth knowing the edges so you don't fight the
compiler:

- **No type parameters.** You can't write `perk Iterator<Item>:`. Perks themselves are not
  generic.
- **No inheritance.** A perk can't require another perk (no `perk Ord: Eq`). If you need
  several capabilities, list them with `+` at the *use* site, as in
  `fn f<T: Hashable + Displayable>(T x)`.
- **No default implementations.** Every method a perk declares must be implemented in full by
  each type; a perk can't provide a fallback body.

These keep the model purely static — every perk method call resolves to a known function at
compile time, which is what makes the whole thing zero-cost.

## What you learned

- **Extension methods** (`extend Type method() Ret:`) add methods to any type, including
  primitives, using `self` for the receiver, and return **bare** values (no `Result`).
- **UFCS** means `x.method(args)` is compiled to `method(x, args)` — sugar with no runtime
  cost.
- A **perk** is a contract of method signatures; types opt in with `extend Type with Perk:`.
- Perks shine as **generic constraints** (`<T: Perk>`), checked at compile time and
  monomorphized away.
- Primitives get **synthetic** perk implementations (e.g. `Hashable`) from their auto-derived
  methods, so generic code works on them without extra boilerplate.
- Perks have no type parameters, no inheritance, and no default methods.

Next we'll look at how Sushi manages memory — ownership, RAII, and borrowing — without a
garbage collector. On to [Memory Management](12-memory-management.md).
