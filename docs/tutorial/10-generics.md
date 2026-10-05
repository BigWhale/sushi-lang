# 10. Generics

Until now, each type in this tutorial was concrete: a `Point` holds two `i32` values, and a
`string` holds text. But much code does not change with the type of its data. A container
that holds one value is the same code for an integer and for a starship. If you write that
code two times, you must also fix each bug two times.

A **generic** lets you write the code one time, with a **type parameter** in the place of a
concrete type. The compiler then makes one concrete copy for each type that you use. If you
know Java's `List<T>` or Python's `list[int]`, the idea is familiar. Sushi generics are
closer to Rust generics or C++ templates: they have **no run-time cost**. The last sections
of this chapter tell you why.

In Sushi source, a type parameter list is always `@(...)`: `Box@(T)`, `Pair@(T, U)`,
`fn identity@(T)(...)`.

## Generic structs

A generic struct writes its type parameters in `@(...)` after its name. The fields then use
those names where a concrete type usually goes.

```sushi
--8<-- "docs/tutorial/examples/10-generics/box-and-pair.sushi"
```

Output:

```
Box of i32 holds 42
Box of string holds Mostly Harmless
Arthur is 42
Arthur is still 42
```

`Box@(T)` has one type parameter, `T`. The declaration `let Box@(i32) answer` sets `T` to
`i32`. A `Box@(string)` is a different type, and the compiler checks it separately. You
cannot give a `Box@(string)` to code that expects a `Box@(i32)`.

`Pair@(T, U)` has two type parameters. A generic struct accepts **named construction**, as
an ordinary struct does, and the names can come in any sequence: `Pair(first: "Arthur",
second: 42)` and `Pair(second: 42, first: "Arthur")` make the same value.

## Generic enums

An enum can also be generic. Its variants carry payloads whose types use the type
parameters of the enum. This tree node is a `Leaf` that holds a `T`, or it is `Empty`.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-enum.sushi"
```

Output:

```
A leaf holding 42
An empty tree
```

A variant of a generic enum writes the **bare type** of its payload: `Leaf(T)`, not
`Leaf(T value)`. The pattern `Tree.Leaf(v)` gives the payload its name.

The built-in `Maybe@(T)` and `Result@(T, E)` are generic enums of the same kind. They have
no special form.

### When a constructor needs a declared type

A generic enum constructor must get its type from somewhere. If the payload tells the
type, the constructor needs nothing more. If the variant has no payload, a position must
declare the type.

```sushi
--8<-- "docs/tutorial/examples/10-generics/untyped-constructor.sushi"
```

Output:

```
Maybe.Some(42) holds an i32: 42
Tree.Empty() needs its declared type
```

`Maybe.Some(42)` is a `Maybe@(i32)`, because its payload is an `i32`. `Tree.Empty()` has no
payload, so a `let`, a parameter, a field or a `return` must give its type. When no
position gives it, for example in `match Tree.Empty():`, the compiler stops with [CE2112](../error-catalog.md#ce2112)
("nothing gives 'T'").

A generic struct works the same way: `Pair(1, "one")` is a `Pair@(i32, string)`, because
its fields tell the types.

## Generic types that hold things

A type parameter can go inside other types. A field can be a `List@(T)`, a `Maybe@(T)`, a
`HashMap@(K, V)` or another generic type of your own.

```sushi
--8<-- "docs/tutorial/examples/10-generics/container-field.sushi"
```

Output:

```
Guides: 2 items
  The Hitchhiker's Guide to the Galaxy
  The Restaurant at the End of the Universe
Answers: 1 item
```

`Shelf@(string)` and `Shelf@(i32)` each own a list. When a shelf goes out of scope, the
compiler frees its list. You write no clean-up code (see
[Chapter 12](12-memory-management.md)).

A generic enum can refer to itself, but only through `Own@(...)`. An enum that holds itself
directly has no finite size. `Own@(T)` puts the value on the heap, so the size of the enum
is known.

```sushi
--8<-- "docs/tutorial/examples/10-generics/recursive-tree.sushi"
```

Output:

```
The tree has 3 leaves
```

`Own.alloc(a)` moves the leaf to the heap, and `left.get()` reads it back. The function
`count_leaves@(T)` is generic too: it counts the leaves of a tree of any type. The next
section tells you more about generic functions.

## Generic functions and type inference

A function can be generic. It writes its type parameters after its name, and then uses
them in the parameter list and in the return type.

```sushi
--8<-- "docs/tutorial/examples/10-generics/inference.sushi"
```

Output:

```
identity gives 7 and towel
make_pair built (answer, 42)
```

`identity@(T)(nom T x) T` gives back the value that it gets. At the call site you write
`identity(nom 7)`. You do not write the type. The compiler sees that the argument `7` is an
`i32`, and it sets `T = i32`. This is **type inference**. `make_pair` does the same with
two type parameters.

The `nom` is the **mode** of the parameter. It tells that the function takes ownership of
the argument. `identity` must take ownership, because it gives the value back to the
caller. A parameter without a mode is a borrow, and a function cannot return a borrow. The
mode is part of the signature, so it is the same for each instance: `nom T` stays `nom T`
also when `T = i32` and nothing is owned. See
[Chapter 12](12-memory-management.md#passing-a-value-to-a-function).

### Where inference finds the type

The rule is simple: the compiler finds `T` in **each argument whose declared type contains
`T`**. The parameter can be a bare `T`, but also a generic type, an array, a borrow or a
function type.

```sushi
--8<-- "docs/tutorial/examples/10-generics/inference-sources.sushi"
```

Output:

```
head of a T[]: 5
last of a T[3]: 2.5
second of a peek Pair@(A, B): 42
apply with fn(T) -> U: 42
```

- `head@(T)(T[] xs)` gets `T = i32` from the element type of an `i32[]`.
- `last_of_three@(T)(T[3] xs)` gets `T = f64` from a fixed array.
- `second_of@(A, B)(peek Pair@(A, B) p)` gets two type parameters from one borrowed
  `Pair@(string, i32)`.
- `apply@(T, U)(T x, fn(T) -> U f)` gets `T` from `21`, and `U` from the return type of the
  lambda `|i32 x| x * 2`.

A generic function can call other generic functions. `count_leaves` in the previous section
calls itself, and a generic body can give the result of one generic call directly to the
next one.

## Explicit type arguments

Sometimes no argument contains the type parameter. Then you write the type arguments at
the call site, in `@(...)` between the function name and the arguments.

```sushi
--8<-- "docs/tutorial/examples/10-generics/explicit-type-args.sushi"
```

Output:

```
identity@(i32) gives 5
empty_list@(string) now holds 1 name
```

`identity@(i32)(nom 5)` writes the type argument that inference would find. `empty_list`
has no parameters, so only the explicit form can tell `T`. The `let List@(string)`
declaration does not tell it: a call without type arguments is [CE2060](../error-catalog.md#ce2060) ("cannot infer type
arguments").

Two rules apply to explicit type arguments:

- **All or nothing.** Write each type argument, or none. `pair@(i32)(1, 2)` for a
  `pair@(T, U)` is [CE2062](../error-catalog.md#ce2062) ("expects 2 type argument(s), got 1").
- **Only on a direct call of a named function.** A method call has no `@(...)` slot:
  `b.get@(i32)()` is [CE6102](../error-catalog.md#ce6102). A method gets its types from inference only.

## Constraints

A type parameter without a constraint accepts any type. A **constraint** limits the
parameter to types that implement a perk. Perks are the subject of
[Chapter 11](11-perks-and-extensions.md). Here you need only this: a perk is a list of
methods, and `extend Player with Scored:` gives those methods to `Player`.

```sushi
--8<-- "docs/tutorial/examples/10-generics/constraints.sushi"
```

Output:

```
Arthur has 42 points
key towel: always know where it is
```

- `best@(T: Scored)` accepts each type that implements `Scored`, and its body can call
  `score()`.
- `announce@(T: Scored + Named)` needs two perks. Join them with `+`.
- `struct Keyed@(K: Hashable)` puts a constraint on a **struct**. A generic enum accepts
  constraints in the same way. `Hashable` is predefined, and each type with a derived hash
  (`string` here) satisfies it.

The compiler checks the constraint where you write the concrete type. For a function, this
is the call site. For a struct or an enum, it is the written type: `let Holder@(i32) h`
for a `struct Holder@(T: Show)` is [CE4006](../error-catalog.md#ce4006) ("type i32 does not implement perk Show required
by constraint") when `i32` does not implement `Show`.

## Methods on generic types

An extension method can target a generic type. `extend Box@(T)` adds the method to each
instance of `Box`, and the body can use `T`.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-methods.sushi"
```

Output:

```
current gives 42
a box of the number 42
a box of the text 'Mostly Harmless'
```

- `current()` returns a copy of the item. A field read is a borrow, so the method calls
  `.clone()` to return an independent value. `.clone()` works for each `T`.
- `replace(poke self, nom T fresh)` changes the box. `poke self` makes the receiver
  writable, and `nom T` takes ownership of the new item.
- `extend Box@(i32) describe()` and `extend Box@(string) describe()` each apply to **one**
  instance. A concrete type argument in the target is a constraint on the target. Two
  concrete targets can use the same method name.

Two rules keep this clear:

- A method name on one type is a template (`extend Box@(T)`) or a set of concrete versions
  (`extend Box@(i32)`), never both. The two together are [CE0101](../error-catalog.md#ce0101).
- A target is fully generic or fully concrete. `extend Pair@(i32, B)` mixes the two, and it
  is [CE2098](../error-catalog.md#ce2098).

An extension can also target a built-in generic type, for example `extend List@(T)`.

## Static constructors on generic types

A static method has no receiver, so no value can tell `T`. The compiler takes `T` from the
position that holds the call: the declared type of a `let`, the type of a parameter, a
field or a `return`.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-static.sushi"
```

Output:

```
the cage holds 9
an argument gives the type too: 12
first 3, 0 more
```

- `let Cage@(i32) c = Cage.holding(9)` gets `T` from the argument and from the declaration.
- `weight(Cage.holding(12))` needs no declaration. The parameter type `Cage@(i32)` of
  `weight` gives `T`.
- `Pair.of_first(3)` gets `A = i32` from its argument. No argument contains `B`, so the
  declaration `Pair@(i32, string)` gives `B = string`.

When no argument and no position gives a type parameter, for example
`println(Cage.empty_of().item)`, the compiler stops with [CE2060](../error-catalog.md#ce2060) ("no argument names 'T',
and this position declares no type").

## A generic stack

The next program puts the parts together. `Stack@(T)` holds a `List@(T)`, has a static
`new`, and has methods that change it and read it.

```sushi
--8<-- "docs/tutorial/examples/10-generics/stack.sushi"
```

Output:

```
the stack holds 3 names
popped Zaphod
a name of 4 bytes
a name of 6 bytes
```

- `Stack.new()` gets its `T` from the declaration `let Stack@(string) crew`.
- `push(poke self, nom T v)` takes the new item, and `pop(poke self) Maybe@(T)` gives it
  back. `pop` returns `Maybe.None()` when the stack is empty.
- `mapped@(U)` has a **method-level type parameter**, explained in the next section.

## Method-level type parameters

A method can have type parameters of its own, after the method name. In
`extend Stack@(T) mapped@(U)(fn(T) -> U f) Stack@(U) | StdError`, `T` comes from the
receiver, and `U` comes from the function argument. The call
`crew.mapped(|string s| s.size())` gets `U = i32` from the return type of the lambda.

A method call has no `@(...)` slot, so the compiler must infer `U`. The lambda must write
the types of its parameters: `|string s| ...`. A lambda with a bare parameter, `|s| ...`,
gives nothing to infer from, and the call is [CE2063](../error-catalog.md#ce2063) ("cannot infer method type parameter
'U'"). The method writes the `| StdError` channel, so the call gives a
`Result@(Stack@(U), StdError)`, and `main` unwraps it with `.realise(...)`. The parameter
`fn(T) -> U` is a bare function type, so `f(x)` in the body gives a `U` directly.

The standard library uses this form for `.map`, `.filter` and `.fold` on `List@(T)` and
`T[]`. [Chapter 19](19-higher-order-combinators.md) shows them.

## Array targets

An extension can target an array type. `extend T[]` applies to arrays of each element type,
and `extend i32[]` applies to `i32[]` only.

```sushi
--8<-- "docs/tutorial/examples/10-generics/array-target.sushi"
```

Output:

```
second number: 2
second name: Ford
sum: 42
```

`self.get(1)` returns a borrow into the array, so `second()` returns a `.clone()` of it.
The element position of an array target accepts two forms only: a new name, which is a
type parameter (`extend T[]`), or the name of a declared type (`extend i32[]`,
`extend string[]`). A generic type in that position, for example `extend Maybe@(T)[]`, is
[CE2101](../error-catalog.md#ce2101). An array target cannot have a static method ([CE2104](../error-catalog.md#ce2104)), because an array type has no
name that you can write before a dot.

## Perk implementations on generic types

A perk implementation can target a generic type too. Each instance then satisfies the
perk, and a constrained generic function accepts it.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-perk-impl.sushi"
```

Output:

```
rendered Box(42)
rendered Box(towel)
rendered label number 7
```

`extend Box@(T) with Show` makes `Box@(i32)` and `Box@(string)` both satisfy `Show`, so
`render@(S: Show)` accepts both. `extend Label@(i32) with Show` applies to `Label@(i32)`
only. A `Label@(string)` does not implement `Show`, and `render` refuses it with [CE4006](../error-catalog.md#ce4006).

The compiler checks the header of a template implementation one time, as it is written.
If the perk declares `fn size(i32 x) i32`, the implementation `fn size(T x) i32` is [CE4004](../error-catalog.md#ce4004),
also when each instance uses `T = i32`. To implement the perk for one instance, write that
instance as the target: `extend Box@(i32) with Sized`.

## Drop on a generic type

The predefined perk `Drop` gives a type a clean-up method, `fn drop(poke self) ~`. A generic
type can implement it, and each instance gets its own copy of the body.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-drop.sushi"
```

Output:

```
guarding a towel and 42
releasing the answer guard
releasing the towel guard
```

When `main` ends, the compiler destroys its values in **reverse declaration order**:
`answer` first, then `towel`. `drop()` runs first, then the compiler frees the fields that
own memory. Only the unit that declares a type can implement `Drop` for it ([CE4012](../error-catalog.md#ce4012)).

## Errors through generics

A generic function can declare an error channel with `| E`, as an ordinary function does.
The error type can itself be a generic enum.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-channel.sushi"
```

Output:

```
accepted, score 20
refused: no words
refused: 4 words is too many
```

`first_below@(T)` returns `i32 | ParseError@(i32)`. `check` declares the same channel, so it
can use `??` on the call. The `match` in `report` then reads the variant and its payload
directly: `Result.Err(ParseError.TooLarge(count))`.

### Nested generics

Generic types nest to any depth. A common case is a function that can fail and that can
also have no value: `Result@(Maybe@(T), E)`.

```sushi
--8<-- "docs/tutorial/examples/10-generics/nested.sushi"
```

Output:

```
Counted 42
No count available
```

`parse_count` writes its return type as `Result@(Maybe@(i32), StdError)`. The short form
`fn parse_count(i32 raw) Maybe@(i32) | StdError` gives the same type; the long form only
shows the nesting. To read the value, use two nested `match` statements: the first removes
the `Result`, the second removes the `Maybe`.

## Constants, unit variables and derived methods

A generic type can be the type of a `const` or of a unit-level `var`. The compiler builds
the value against the declared type. Each generic struct and enum also gets a derived
`hash()` and `clone()`, as a concrete type does.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-constants.sushi"
```

Output:

```
42 true true
a clone hashes like the original: true
filled the cache
42
read the cache
42
```

- `const Pair@(i32, bool) ANSWER = Pair(42, true)` is a constant struct. The declared type
  tells which instance `Pair(...)` builds.
- `const Maybe@(i32) NO_LIMIT = Maybe.None` is a constant enum variant.
- `var Maybe@(HashMap@(string, i32)) cache = Maybe.None` is storage for the whole program.
  The first call of `lookup` fills it, and the second call reads it. A `HashMap.new()` is
  not a constant, so the variable starts as `Maybe.None`.
- `p.clone()` makes an independent copy, and `p.hash()` reads each field.

## Generic functions as values

You can use a generic function as a function value when a position gives all its type
arguments.

```sushi
--8<-- "docs/tutorial/examples/10-generics/generic-fn-value.sushi"
```

Output:

```
a let gives T = string: <towel>
a parameter gives T = i32: <42>
a generic callee gives T = f64: <2.5>
```

- `let fn(string) -> string g = describe` makes the instance `describe@(string)`.
- `call_with_42(describe)` makes `describe@(i32)`, from the parameter type
  `fn(i32) -> string`.
- `run(describe, 2.5)` first gets `T = f64` from `2.5`, and then makes `describe@(f64)`.

When no position gives the type, the compiler cannot make an instance, and the reference
is [CE2093](../error-catalog.md#ce2093) ("cannot take a function value"). [Chapter 17](17-first-class-functions.md)
tells you more about function values.

## Packs in short

A **type pack** `@(...Ts: Show)` accepts any number of arguments, each of a different type.
`expand` repeats its body one time for each argument, at compile time.

```sushi
--8<-- "docs/tutorial/examples/10-generics/packs.sushi"
```

Output:

```
i32 42
string 'towel'
i32 7
```

[Chapter 15](15-variadic-functions.md) explains packs in full.

## How it compiles: monomorphization

When you use `Box@(i32)` and `Box@(string)`, the compiler does not keep one general `Box`
that finds its types while the program runs. (Java does that, with type erasure, and pays
for it with boxing and casts.) At compile time, Sushi makes a **separate, fully concrete
copy** of the code for each set of type arguments that the program uses: one `Box` for
`i32`, one for `string`. This process is **monomorphization**.

Thus generic code is as fast as code that you write by hand for each type. There is no
run-time type information, no dynamic dispatch and no hidden allocation. Each instance is
real code, so many instances make a larger binary. Usually this is not a problem.

Monomorphization also tells you **when** the compiler checks a generic body: it checks
each instance, with the concrete types. A constraint is a check at the call site, not a
limit on the body. Thus a body can call a method that only some types have:

```sushi
--8<-- "docs/tutorial/examples/10-generics/per-instance-check.sushi"
```

Output:

```
a string has len(): 4
```

`size@(T)` has no constraint, but its body calls `x.len()`. The instance `size@(string)` is
correct, because a `string` has `len()`. A call `size(4)` makes the instance `size@(i32)`,
and the compiler reports the error in that instance: [CE2008](../error-catalog.md#ce2008) ("undefined function
'i32.len'"). To tell the caller which types are correct, write a constraint.

## Generics across units

A generic type or function from another unit or from a library works as a local one. The
compiler makes the instance in your program. Behind an alias, the type keeps its
arguments: `geo.Box@(i32)` after `use "geometry" as geo`. A public generic function can
take a private type of your own unit. [Chapter 14](14-stdlib-ffi-libraries.md) tells you
how to build and use a library.

## What generics cannot do yet

These limits are true today. Each one has a diagnostic.

| You write | Diagnostic | Do this |
|---|---|---|
| A perk with type parameters, `perk Conv@(T):` | [CE4010](../error-catalog.md#ce4010) | Constrain the function: `@(T: Conv)` |
| A method-level type parameter in a perk method, `fn make@(U)(...)` | [CE6001](../error-catalog.md#ce6001) (parse error) | Use a generic free function |
| A static method in a perk | [CE4014](../error-catalog.md#ce4014) | Put the static on the type with `extend` |
| A lambda with a bare parameter as a generic argument, `apply(\|x\| x + 1, 3)` | [CE2060](../error-catalog.md#ce2060) (function), [CE2063](../error-catalog.md#ce2063) (method) | Write the parameter type: `\|i32 x\|` |
| `@(...)` on a method call, `b.get@(i32)()` | [CE6102](../error-catalog.md#ce6102) | Let inference find the type |
| A `T` that only the return type contains, with no type arguments | [CE2060](../error-catalog.md#ce2060) | Write them: `empty_list@(i32)()` |
| A generic static where no position declares the type | [CE2060](../error-catalog.md#ce2060) | Declare the type with a `let`, or pass the value to a typed parameter |
| Some of the type arguments, `pair@(i32)(1, 2)` | [CE2062](../error-catalog.md#ce2062) | Write all of them |
| A partly concrete target, `extend Pair@(i32, B)` | [CE2098](../error-catalog.md#ce2098) | Make it fully generic or fully concrete |
| A template and a concrete target for one method name | [CE0101](../error-catalog.md#ce0101) | Use one form |
| A static on an array target | [CE2104](../error-catalog.md#ce2104) | Use a free function |
| A variadic `...T` parameter in an extension or perk method | [CE0115](../error-catalog.md#ce0115) | Use a free function |
| Pack forwarding `inner(xs...)`, pack indexing, tuples | [CE2060](../error-catalog.md#ce2060) and others | Use `expand` in the function that holds the pack |
| A nested array as an array target, `extend T[][]` | [CE2101](../error-catalog.md#ce2101) | Write `extend T[]`: `T` is then the inner array type |

A perk also has no inheritance, no default method bodies and no `Self` type
([Chapter 11](11-perks-and-extensions.md)).

## What you learned

- Generic **structs** and **enums** write their type parameters in `@(...)` and use them in
  the place of concrete types. Named construction still works.
- A variant of a generic enum writes a **bare** payload type (`Leaf(T)`). A constructor
  gets its type from its payload or from its position; [CE2112](../error-catalog.md#ce2112) when nothing gives it.
- A generic type can hold containers, and a generic enum can refer to itself through
  `Own@(...)`.
- A generic **function** infers its type parameters from each argument whose type contains
  them: `T`, `T[]`, `T[N]`, `Pair@(A, B)`, `peek T`, `fn(T) -> U`.
- **Explicit type arguments** (`identity@(i32)(nom 5)`) are all or nothing, and only on a
  direct call of a named function.
- **Constraints** (`@(T: Perk)`, `@(T: A + B)`) apply to functions, structs and enums.
- Extension methods, statics, method-level type parameters, array targets, perk
  implementations and `Drop` all work on generic types.
- A generic can carry an **error channel**, and a generic enum can be the error type.
- A generic function is a **function value** when a position gives its type arguments.
- The compiler **monomorphizes** generics: no run-time cost, and each instance is checked
  with its concrete types.

Next, we give behaviour to types, both our types and the built-in ones, with extension
methods and perks. Go to [Perks & Extensions](11-perks-and-extensions.md).
