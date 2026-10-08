# Generics

[← Back to Documentation](index.md)

Complete guide to generic programming in Sushi: generic types, generic functions, methods on
generic types, and compile-time monomorphization.

## Table of Contents

- [Overview](#overview)
- [Generic Structs](#generic-structs)
- [Generic Enums](#generic-enums)
- [Generic Functions](#generic-functions)
- [Constraints](#constraints)
- [Methods on Generic Types](#methods-on-generic-types)
- [Errors Through Generics](#errors-through-generics)
- [Packs](#packs)
- [Nested Generics](#nested-generics)
- [Monomorphization](#monomorphization)
- [Complete Example](#complete-example)
- [Best Practices](#best-practices)
- [Known Limitations](#known-limitations)

## Overview

Sushi generics are resolved at compile time. The compiler makes one copy of the generic code
for each set of type arguments that the program uses (monomorphization):

- **Generic structs** - `Pair@(T, U)`, `Box@(T)`
- **Generic enums** - `Result@(T, E)`, `Maybe@(T)`, and your own enums
- **Generic functions** - the compiler infers the type arguments from the call, or you write
  them: `identity@(i32)(nom 5)`
- **Methods on generic types** - `extend Box@(T)`, `extend T[]`, `extend List@(T)`, statics
  and method-level type parameters
- **No runtime cost** - there is no runtime type information and no dynamic dispatch
- **Checked where written** - the compiler checks a generic body one time, where it is
  written, with each type parameter opaque. The body can do with a `T` only what a
  constraint of `T` promises (see [A Template Is Checked Where It Is Written](#a-template-is-checked-where-it-is-written))

The source syntax is `@(...)` in every position: a declaration (`struct Box@(T):`), a type
(`Box@(i32)`), a constraint (`@(T: Hashable)`) and a call (`identity@(i32)(nom 5)`).

## Generic Structs

### Single Type Parameter

```sushi
struct Box@(T):
    T value

fn main() i32:
    let Box@(i32) int_box = Box(value: 42)
    let Box@(string) str_box = Box(value: "Mostly Harmless")

    println("Int: {int_box.value}")
    println("String: {str_box.value}")

    return 0
```

### Multiple Type Parameters

```sushi
struct Pair@(T, U):
    T first
    U second

fn main() i32:
    let Pair@(i32, string) p1 = Pair(first: 42, second: "answer")
    let Pair@(bool, f64) p2 = Pair(first: true, second: 3.14)

    println("First: {p1.first}, Second: {p1.second}")
    println("Flag: {p2.first}")

    return 0
```

### Generic Struct with Arrays and Containers

A field can hold `T[]`, `List@(T)`, `HashMap@(K, V)`, `Maybe@(T)` or another generic type.
The struct owns what its fields own, and the compiler destroys it at scope exit:

```sushi
struct Container@(T):
    T[] items
    i32 capacity

struct Shelf@(T):
    List@(T) items

fn main() i32:
    let Container@(string) names = Container(
        items: from(["Arthur", "Ford"]),
        capacity: 10
    )
    names.items.push("Trillian")
    println("Count: {names.items.len()}")

    let Shelf@(i32) shelf = Shelf(items: List.new())
    shelf.items.push(42)
    println("Shelf: {shelf.items.len()}")

    return 0
```

## Generic Enums

### Defining Generic Enums

A variant's payload is written as a **bare type** — `Some(T)`, not `Some(T value)` — and is
constructed positionally:

```sushi
enum Option@(T):
    Some(T)
    None()

fn main() i32:
    let Option@(i32) num = Option.Some(42)
    let Option@(string) text = Option.None()

    match num:
        Option.Some(v) -> println("Value: {v}")
        Option.None() -> println("No value")

    match text:
        Option.Some(v) -> println("Text: {v}")
        Option.None() -> println("No text")

    return 0
```

### Where a Constructor Gets Its Type

A generic enum constructor takes its type from the position that holds it: a `let`, a
`return`, a parameter, a field or a payload. In a position with no declared type (for example
a `match` scrutinee), the arguments give the type. `Maybe.Some(42)` is a `Maybe@(i32)`. A
constructor whose arguments do not give every type parameter, such as `Maybe.None()` or
`Tree.Empty()`, needs a declared type. If no position gives one, the compiler reports
[`CE2112`](error-catalog.md#ce2112).

A generic STRUCT constructor follows the same rule: `Feed(from([1, 2]), 7)` is a
`Feed@(i32)` when `Feed@(T)` has a `T[]` field, also with no declared type, and a declared
type wins where one exists. Arguments that give one type parameter two types are [`CE2065`](error-catalog.md#ce2065)
at the constructor:

```sushi
enum Tree@(T):
    Leaf(T)
    Empty

fn main() i32:
    match Maybe.Some(42):
        Maybe.Some(v) -> println("Some {v}")
        Maybe.None -> println("None")

    let Tree@(i32) t = Tree.Empty
    match t:
        Tree.Leaf(v) -> println("Leaf {v}")
        Tree.Empty -> println("Empty")

    return 0
```

### Recursive Generic Enums

A variant cannot hold its own enum inline, because the type would have no finite size. Put
the recursive payload in an `Own@(T)`:

```sushi
enum Tree@(T):
    Leaf(T)
    Node(Own@(Tree@(T)), Own@(Tree@(T)))

fn count@(T)(Tree@(T) t) i32:
    match t:
        Tree.Leaf(_) -> return 1
        Tree.Node(l, r) ->
            let i32 a = count(l.get())
            let i32 b = count(r.get())
            return a + b

fn main() i32:
    let Tree@(i32) left = Tree.Leaf(1)
    let Tree@(i32) right = Tree.Leaf(2)
    let Tree@(i32) root = Tree.Node(Own.alloc(left), Own.alloc(right))
    println("Leaves: {count(root)}")
    return 0
```

### Built-in Generic Enums

Sushi has two built-in generic enums.

**Result@(T, E):** a function that writes an error channel returns one.
`fn divide(...) i32 | StdError` returns `Result@(i32, StdError)`, and
`fn divide(...) i32 | MathError` returns `Result@(i32, MathError)`. A function with no
`| E` is bare and returns its value itself; there is no default error type. A bare
function is the exception, not the default style (see
[The error channel is opt-in](design/error-channel.md)):

```sushi
fn divide(i32 a, i32 b) i32 | StdError:  # returns Result@(i32, StdError)
    if (b == 0):
        return Result.Err(StdError.Error)
    return Result.Ok(a / b)
```

**Maybe@(T):**
```sushi
fn find_first_even(i32[] numbers) Maybe@(i32):
    foreach(n in numbers.iter()):
        if (n % 2 == 0):
            return Maybe.Some(n)
    return Maybe.None()
```

See [Error Handling](error-handling.md) for both types.

## Generic Functions

### Type Parameter Syntax

```sushi
fn identity@(T)(nom T value) T:
    return value

fn main() i32:
    let i32 x = identity(nom 42)          # T inferred as i32
    let string s = identity(nom "Ford")  # T inferred as string

    println("x={x}, s={s}")

    return 0
```

!!! note
    `identity` writes no `| E`, so it is bare and its call returns the value: no `??` and
    no `.realise()`. A generic function that can fail writes `| E`, and then its call
    returns a `Result` (see [Errors Through Generics](#errors-through-generics)). `main` is
    bare, so a `??` there is [`CE0131`](error-catalog.md#ce0131); use `.realise(default)` or `match` in `main`.

!!! note "Why `nom`"
    `identity` gives its argument back to the caller, so the parameter declares `nom` and the
    call site writes `nom` too. A parameter borrows by default, and a function cannot return
    a borrow ([`CE2411`](error-catalog.md#ce2411)). The mode is part of the signature, so it is the same for every
    instantiation: `nom T` also where `T` is an `i32` that owns nothing. See
    [docs/design/borrow-model.md](design/borrow-model.md).

### Multiple Type Parameters

```sushi
struct Pair@(T, U):
    T first
    U second

fn make_pair@(T, U)(nom T first, nom U second) Pair@(T, U):
    return Pair(first: first, second: second)

fn main() i32:
    # T=i32, U=string inferred from arguments
    let Pair@(i32, string) p = make_pair(nom 42, nom "answer")
    println("Pair: {p.first}, {p.second}")

    return 0
```

### Type Inference

The compiler solves a type parameter from each argument whose parameter type names it. The
parameter type can be a bare `T`, a named generic (`Pair@(A, B)`, `List@(T)`, `Maybe@(T)`), an
array (`T[]`, `T[N]`), a borrow (`peek T`, `poke T`) or a function type (`fn(T) -> U`). A
generic body can call other generics, and a nested call such as `first_of(singleton(x))`
infers at each level:

```sushi
struct Pair@(A, B):
    A left
    B right

fn first@(T: Clone)(T[] xs) T:
    return xs[0].clone()

fn left_of@(A: Clone, B)(peek Pair@(A, B) p) A:
    return p.left.clone()

fn apply@(T, U)(T x, fn(T) -> U f) U:
    return f(x)

fn singleton@(T)(nom T x) List@(T):
    let List@(T) l = List.new()
    l.push(x)
    return l

fn first_of@(T: Clone)(List@(T) l) T | StdError:
    return Result.Ok(l.get(0).or_err(nom StdError.Error)??.clone())

fn round_trip@(T: Clone)(nom T x) T | StdError:
    return Result.Ok(first_of(singleton(nom x))??)

fn main() i32:
    let i32[] xs = from([5, 6])
    let Pair@(i32, string) p = Pair(left: 9, right: "nine")

    let i32 a = first(xs)                         # T from i32[]
    let i32 b = left_of(peek p)                   # A, B from peek Pair@(A, B)
    let string c = apply(3, |i32 n| "n={n}")     # T from 3, U from the lambda
    let i32 d = round_trip(nom 11).realise(0)                # nested generic calls

    println("{a} {b} {c} {d}")
    return 0
```

A lambda argument must declare its parameter types (`|i32 n| ...`). A bare-parameter lambda
(`|n| ...`) gets its type from the type parameter that the compiler must infer, so the
compiler cannot solve it ([`CE2060`](error-catalog.md#ce2060)).

The variable that receives the result still needs its own type annotation, as every `let`
does.

### Explicit Type Arguments

Write the type arguments in `@(...)` between the function name and the argument list. You
must do this when a type parameter appears only in the return type, because no argument can
give it:

```sushi
fn identity@(T)(nom T x) T:
    return x

fn empty_list@(T)() List@(T):
    return List.new()

fn main() i32:
    let i32 a = identity@(i32)(nom 42)
    let List@(string) names = empty_list@(string)()
    println("{a} {names.len()}")
    return 0
```

The rules:

- Write all the type arguments or none. A wrong count is [`CE2062`](error-catalog.md#ce2062).
- Only a direct call to a named free function takes a type-argument list. A method call has
  no `@(...)` slot ([`CE6102`](error-catalog.md#ce6102)), so a method-level type parameter must come from the arguments.
- The `let` annotation does not give a type parameter to the call. If only the return type
  names `T`, write the type arguments.

### Generic Functions as Values

A generic function can be a [first-class function value](first-class-functions.md) in each
position where the expected function type solves its type parameters: a typed `let`, an
argument to a function-typed parameter, a rebind, a `return`, a field or a payload. The
expected type selects the instantiation:

```sushi
fn same@(T: Clone)(T x) T:
    return x.clone()

fn apply(fn(i32) -> i32 f, i32 x) i32:
    return f(x)

fn twice@(T)(fn(T) -> T f, T x) T:
    return f(f(x))

fn main() i32:
    let fn(i32) -> i32 g = same           # selects same@(i32)
    let i32 a = g(41)
    let i32 b = apply(same, 3)
    let i32 c = twice(same, 7)   # T comes from 7
    println("{a} {b} {c}")
    return 0
```

The parameter modes are part of a function type. `identity@(T)(nom T x)` has the type
`fn(nom i32) -> i32` at `i32`, and it does not fit a `fn(i32) -> i32` parameter ([`CE2006`](error-catalog.md#ce2006)).

A generic function value is refused where nothing solves its type. For example,
`map(xs, identity)` asks the value itself to give the `U` of `map`, so the compiler reports
[`CE2060`](error-catalog.md#ce2060) and [`CE2093`](error-catalog.md#ce2093). Bind the value to a typed local first:

```sushi
let fn(i32) -> i32 id = same
let List@(i32) copy = map(xs, id)       # map is bare: no `??`, no `.realise()`
```

## Constraints

### Constraints on Functions

A constraint `@(T: Perk)` says that every type argument must implement the perk. The body can
then call the perk methods. A `T` has only what its constraints promise. A perk method returns a **bare** value, and so does a generic
function with no `| E`. `Hashable` is predefined: every type with a
derived `hash()` satisfies it, and the implementation below REPLACES the derived hash of
`Point` (see [Perks](perks.md#the-predefined-perks)):

```sushi
fn compute_hash@(T: Hashable)(T value) u64:
    return value.hash()

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return (self.x as u64) + (self.y as u64)

fn main() i32:
    let Point p = Point(x: 10, y: 20)
    let u64 h = compute_hash(p)  # T=Point inferred, Hashable verified

    println("Hash: {h}")
    return 0
```

### Multiple Constraints

Use `+` to require more than one perk:

```sushi
perk Describe:
    fn describe() string

struct Tag:
    i32 id

extend Tag with Hashable:
    fn hash() u64:
        return self.id as u64

extend Tag with Describe:
    fn describe() string:
        return "Tag#{self.id}"

fn process@(T: Hashable + Describe)(T item) ~:
    let u64 h = item.hash()
    let string s = item.describe()
    println("Hash: {h}, Description: {s}")

fn main() i32:
    let Tag t = Tag(id: 7)
    process(t)
    return 0
```

### Constraints on Structs and Enums

A generic struct or enum can constrain its type parameters too. The compiler checks the
constraint at each written type, for example at `Keyed@(HashMap@(i32, i32))` ([`CE4006`](error-catalog.md#ce4006)):

```sushi
struct Keyed@(K: Hashable):
    K key

enum Slot@(T: Hashable):
    Full(T)
    Empty

fn main() i32:
    let Keyed@(i32) k = Keyed(key: 5)
    let Slot@(string) s = Slot.Full("x")
    match s:
        Slot.Full(v) -> println("{k.key} {v}")
        Slot.Empty -> println("empty")
    return 0
```

### What a Constraint Promises

The body of a template sees a `T` as OPAQUE: it has the methods and the operators that its
constraints promise, and nothing more. The predefined perks give these operations:

| Constraint | What the body may do with a `T` |
|---|---|
| `Eq` | `a == b`, `a != b`, `a.eq(b)`, and `contains` / `index_of` on a container of `T` |
| `Ord` | `a < b`, `a <= b`, `a > b`, `a >= b`, `a.compare(b)` |
| `Display` | a hole `"{x}"`, `print(x)`, `println(x)`, `x.to_str()` |
| `Hashable` | `x.hash()` |
| `Clone` | `x.clone()`, and `.clone()` of a `List@(T)`, a `Box@(T)` or a `Maybe@(T)` |
| `Hashable + Eq` | a `HashMap@(T, V)` key |

A user perk gives its own methods. There are no bundles: `T: Ord` does not give `==`, so
write `@(T: Eq + Ord)`. Arithmetic on a `T` has no constraint, and it is refused
([`CE2518`](error-catalog.md#ce2518)). So are a field of `T` ([`CE2106`](error-catalog.md#ce2106)) and a cast of `T` ([`CE2014`](error-catalog.md#ce2014)).

`Clone` is satisfied by every type that holds no resource. A handle (`File`, `TcpStream`)
does not satisfy it, because a deep copy of a handle would close one descriptor two times.
`.share()` is the way to get a second owner of a handle. `.clone()` on a `T` with no `Clone`
is [`CE4018`](error-catalog.md#ce4018):

```sushi
fn twice@(T: Clone)(T x) List@(T):
    let List@(T) out = List.new()
    out.push(x.clone())
    out.push(x.clone())
    return out

fn main() i32:
    let List@(string) l = twice("towel")
    println("{l.len()}")      # 2
    return 0
```

### A Constraint Passes On

A template that calls another generic, writes a generic type, or builds one with a
constructor passes its `T` on. The constraints of `T` must then promise the constraints of
the callee. `outer@(T)` cannot call `inner@(U: Hashable)(x)`: that is [`CE4006`](error-catalog.md#ce4006) at the call
in `outer`, and the help names `@(T: Hashable)`.

For more information on perks, see the [Perks documentation](perks.md).

## Methods on Generic Types

An extension method adds a method to a type with `extend`. A **bare** extension method (no
`| E`) returns its value directly, as a bare function does: there is no `Result.Ok(...)`
wrapper, and you call it without `??` or `.realise()`.

### Basic Extension

```sushi
extend i32 squared() i32:
    return self * self

extend i32 is_even() bool:
    return self % 2 == 0

fn main() i32:
    let i32 x = 7

    println("Squared: {x.squared()}")

    if (x.is_even()):
        println("Even")
    else:
        println("Odd")

    return 0
```

### String Extensions

Strings do not support the `+` operator. Build a new string with interpolation, `"{...}"`.
Interpolation and an extension on `string` need no import. The built-in string methods
(`.len()`, `.upper()` and the others) need `use <collections/strings>`:

```sushi
extend string shout() string:
    return "{self}!!!"

extend string echo(i32 times) string:
    let string result = ""
    let i32 i = 0
    while (i < times):
        result := "{result}{self}"
        i := i + 1
    return result

fn main() i32:
    println("Don't Panic".shout())     # Don't Panic!!!
    println("Ha".echo(3))              # HaHaHa

    return 0
```

### Generic Targets

The target of an extension can be a generic struct, a generic enum, or a built-in generic such
as `List@(T)` or `Maybe@(T)`. The body can name the type parameter. One copy of the method is
made for each instantiation that the program calls:

```sushi
struct Box@(T):
    T value

enum Opt@(T):
    Has(T)
    Nope

extend Box@(T) unwrap(nom self) T:
    return nom self.value

extend Box@(T: Display) describe() string:
    return "Box holding {self.value}"

extend Opt@(T) is_has() bool:
    match self:
        Opt.Has(_) -> return true
        Opt.Nope -> return false

extend List@(T) doubled_len() i32:
    return self.len() * 2

fn main() i32:
    let Box@(i32) b = Box(value: 42)
    println(b.describe())
    println("Unwrapped: {b.unwrap()}")      # unwrap takes the box: nom self

    let Opt@(string) o = Opt.Has("x")
    let List@(i32) l = List.new()
    l.push(1)
    println("{o.is_has()} {l.doubled_len()}")

    return 0
```

`describe()` puts `self.value` in a hole, so its target adds the bound `T: Display`. See
[Bounds in a Target](#bounds-in-a-target).

A **concrete** type argument in the target is a constraint, not a parameter name. So
`extend Box@(i32)` extends `Box@(i32)` alone, and one method name can serve several
instantiations:

```sushi
struct Box@(T):
    T value

extend Box@(i32) tag() string:
    return "int"

extend Box@(string) tag() string:
    return "text"

fn main() i32:
    let Box@(i32) a = Box(value: 1)
    let Box@(string) b = Box(value: "one")
    println("{a.tag()} {b.tag()}")
    return 0
```

A template and a concrete target for the same method name overlap, and the compiler refuses
them ([`CE0101`](error-catalog.md#ce0101)). A partially concrete target such as `extend Pair@(i32, U)` is refused too
([`CE2098`](error-catalog.md#ce2098)). See the Extension Methods section of the
[language guide](language-guide.md#extension-methods) for the full rule.

### Bounds in a Target

An extension on `Box@(T)` inherits every bound that `Box` declares. With
`struct Keyed@(K: Hashable)`, the body of `extend Keyed@(K) code()` can call
`self.key.hash()`. The `HashMap` key rule is a bound of the same kind: `extend
HashMap@(K, V)` gets `K: Hashable + Eq`.

The target can ADD a bound, `extend Box@(T: Display)`, or `(T: Clone)[]` for an array
target. A receiver whose type argument does not satisfy the added bound is [`CE4006`](error-catalog.md#ce4006) at the
call, and the compiler makes no copy of the method for it:

```sushi
struct Keyed@(K: Hashable):
    K key

extend Keyed@(K) code() u64:
    return self.key.hash()                  # inherited: K is Hashable

struct Box@(T):
    T value

extend Box@(T: Clone) dup() Box@(T):
    return Box(value: self.value.clone())   # added: T is Clone

fn main() i32:
    let Keyed@(string) k = Keyed(key: "x")
    let Box@(i32) b = Box(value: 7)
    println("{k.code() == k.key.hash()} {b.dup().value}")   # true 7
    return 0
```

The rules:

- A bound stands only at the top level of an `extend` target. In a `let` type, a
  parameter, a return, a field or inside a target argument, it is [`CE6110`](error-catalog.md#ce6110).
- A bound on a name that is a type (`extend Box@(Point: Clone)`) is [`CE2124`](error-catalog.md#ce2124).
- `Drop` adds no bound: `extend Guard@(T: Clone) with Drop` is [`CE4019`](error-catalog.md#ce4019). Put the bound
  on the type.

### Receiver Modes and Statics

The receiver `self` is a borrow by default. Write `poke self` for a method that changes the
receiver. A **static** method has no receiver and you call it on the type name. On a generic
target, the static gets its type argument from the position that holds the result: the
`let` annotation, the parameter type, or an argument whose type names `T`. A static in a
position that gives no type is [`CE2060`](error-catalog.md#ce2060):

```sushi
struct Stack@(T):
    List@(T) items

extend Stack@(T) static new() Stack@(T):
    return Stack(items: List.new())

extend Stack@(T) push(poke self, nom T v) ~:
    self.items.push(v)

extend Stack@(T) pop(poke self) Maybe@(T):
    return self.items.pop()

extend Stack@(T) size() i32:
    return self.items.len()

fn main() i32:
    let Stack@(i32) s = Stack.new()      # T comes from the annotation
    s.push(nom 1)
    s.push(nom 2)
    println("Top: {s.pop().realise(0)}, left: {s.size()}")
    return 0
```

### Method-Level Type Parameters

An extension method can declare its own type parameters after its name. The compiler infers
them from the arguments, because a method call has no `@(...)` slot. A lambda argument must
declare its parameter types (a bare `|x| ...` is [`CE2063`](error-catalog.md#ce2063)). A method-level parameter cannot
reuse a name of the target ([`CE2064`](error-catalog.md#ce2064)):

```sushi
struct Box@(T):
    T value

extend Box@(T) map@(U)(fn(T) -> U f) Box@(U) | StdError:
    return Result.Ok(Box(value: f(self.value)))

fn main() i32:
    let Box@(i32) b = Box(value: 20)
    let Box@(string) s = b.map(|i32 x| "n={x}").realise(Box(value: ""))
    println(s.value)
    return 0
```

`<collections/iter>` ships `.map`, `.filter` and `.fold` on `List@(T)` and `T[]` in this
form. See [iter](stdlib/collections/iter.md).

### Array Targets

`extend T[]` extends every dynamic array and binds the element type to `T`. `extend i32[]`
extends one element type. A static on an array target is [`CE2104`](error-catalog.md#ce2104), because an array type has
no spelling in an expression:

```sushi
extend (T: Clone)[] second() Maybe@(T):
    return self.get(1).clone()

extend i32[] total() i32:
    let i32 sum = 0
    foreach(n in self.iter()):
        sum := sum + n
    return sum

fn main() i32:
    let i32[] xs = from([4, 5, 6])
    let string[] ws = from(["a", "b"])
    let string w = ws.second().realise("")
    println("{xs.second().realise(0)} {w} {xs.total()}")
    return 0
```

The body returns `self.get(1).clone()`, because `self.get(1)` is a borrow and a `string`
element owns heap. A body that gives a borrowed element away is [`CE2411`](error-catalog.md#ce2411). The clone needs
the bound `(T: Clone)[]`: an array of a handle gets no `second()`.

### Perk Implementations on Generic Types

`extend Box@(T: Display) with Show` implements a perk for every instantiation of `Box` whose
`T` has `Display`. Each of them then satisfies a constraint `@(S: Show)`:

```sushi
perk Show:
    fn show() string

struct Box@(T):
    T value

extend Box@(T: Display) with Show:
    fn show() string:
        return "Box({self.value})"

fn render@(S: Show)(S thing) ~:
    println(thing.show())

fn main() i32:
    let Box@(i32) b = Box(value: 3)
    let Box@(string) c = Box(value: "hi")
    render(b)
    render(c)
    return 0
```

The compiler checks the header of a template implementation one time, on the written
template. `fn f(T x)` against a contract `fn f(i32 x)` is [`CE4004`](error-catalog.md#ce4004), also when every instance
uses `T = i32`. To implement a perk for one instance, write the concrete target:
`extend Box@(i32) with Show`. See [Perks](perks.md#a-generic-type-may-implement-a-perk).

`Drop` works on a generic target in the same way: `extend Guard@(T) with Drop` gives one
`drop()` body for each instantiation. See [Memory Management](memory-management.md).

## Errors Through Generics

A generic function can declare an error channel with `| E`, and `??` in a caller with the same
channel propagates the error. A generic enum can be the error type:

```sushi
error ParseError@(T):
    Bad(T)

fn check@(T)(nom T x, bool ok) T | ParseError@(i32):
    if (not ok):
        return Result.Err(ParseError.Bad(7))
    return Result.Ok(x)

fn both() i32 | ParseError@(i32):
    let i32 a = check(nom 1, true)??
    let i32 b = check(nom 2, false)??
    return Result.Ok(a + b)

fn main() i32:
    match both():
        Result.Ok(v) -> println("Sum {v}")
        Result.Err(ParseError.Bad(code)) -> println("Bad {code}")
    return 0
```

An extension method on a generic target can declare `| E` too. Its body then spells both
constructors, `return Result.Ok(...)` and `return Result.Err(...)`, as a function body does
(see `map@(U)` above).

## Packs

A type pack `@(...Ts: Perk)` takes a list of types of any length, and `expand` unrolls the
body for each element at compile time:

```sushi
perk Show:
    fn show() string

extend i32 with Show:
    fn show() string:
        return "int {self}"

extend string with Show:
    fn show() string:
        return "text {self}"

fn show_all@(...Ts: Show)(...Ts items) ~:
    expand(it in items):
        println(it.show())

fn main() i32:
    show_all(42, "Mostly Harmless")
    return 0
```

See [Variadics](variadics.md) for the full rules.

## Nested Generics

Sushi supports nested generic types.

### Two Levels

A function declared `Maybe@(i32) | StdError` returns `Result@(Maybe@(i32), StdError)`, so you
match the outer `Result` and then the inner `Maybe`:

```sushi
fn parse_optional(string s) Maybe@(i32) | StdError:
    if (s == "42"):
        return Result.Ok(Maybe.Some(42))
    return Result.Ok(Maybe.None())

fn main() i32:
    match parse_optional("42"):
        Result.Ok(maybe) ->
            match maybe:
                Maybe.Some(value) -> println("Value: {value}")
                Maybe.None() -> println("No value")
        Result.Err(_) -> println("Parse error")

    return 0
```

### Three Levels

```sushi
fn main() i32:
    let Maybe@(Maybe@(Maybe@(i32))) deeply_nested = Maybe.Some(Maybe.Some(Maybe.Some(42)))

    match deeply_nested:
        Maybe.Some(level2) ->
            match level2:
                Maybe.Some(level3) ->
                    match level3:
                        Maybe.Some(value) -> println("Value: {value}")
                        Maybe.None() -> println("Level 3 None")
                Maybe.None() -> println("Level 2 None")
        Maybe.None() -> println("Level 1 None")

    return 0
```

### Collections of Generics

```sushi
use <collections/hashmap>

fn main() i32:
    # List@(Maybe@(i32))
    let List@(Maybe@(i32)) optionals = List.new()
    optionals.push(Maybe.Some(1))
    optionals.push(Maybe.None())
    optionals.push(Maybe.Some(3))

    foreach(opt in optionals.iter()):
        match opt:
            Maybe.Some(v) -> println("Value: {v}")
            Maybe.None() -> println("None")

    # HashMap@(string, List@(i32))
    let HashMap@(string, List@(i32)) groups = HashMap.new()
    let List@(i32) evens = List.new()
    evens.push(2)
    groups.insert("evens", evens)
    println("Groups: {groups.len()}")
    groups.free()

    return 0
```

## Monomorphization

The compiler resolves generics at compile time through monomorphization.

### How It Works

The compiler makes a specialized copy of the generic code for each concrete type that the
program uses:

```sushi
struct Box@(T):
    T value

extend Box@(T: Display) describe() string:
    return "Box holding {self.value}"

fn main() i32:
    let Box@(i32) b1 = Box(value: 42)
    let Box@(string) b2 = Box(value: "hello")

    println(b1.describe())  # Specialized describe() for Box@(i32)
    println(b2.describe())  # Specialized describe() for Box@(string)

    return 0
```

The compiler generates a distinct specialization for each instantiation, with no runtime
dispatch.

### Automatic Instantiation Detection

The compiler finds the necessary instantiations from the program: a call, a written type, a
`let` in a generic body, and a return that reaches another generic:

```sushi
fn largest@(T: Ord)(nom T a, nom T b) T:
    if (a > b):
        return a
    return b

fn main() i32:
    # Compiler generates largest() for i32 and for f64
    let i32 mi = largest(nom 3, nom 9)
    let f64 mf = largest(nom 2.5, nom 1.5)

    println("{mi} {mf}")

    return 0
```

### A Template Is Checked Where It Is Written

The compiler checks a generic body ONE time, where it is written, and not in each copy. A
template that nothing calls is checked too. In that check each type parameter is OPAQUE: the
body can do with a `T` only what a constraint of `T` promises
([What a Constraint Promises](#what-a-constraint-promises)). So the signature is the whole
contract between the template and its caller.

The body below calls `x.len()`, and no constraint of `T` gives `len`. The compiler reports
[`CE2008`](error-catalog.md#ce2008) at the template, with a note at `T`, also when nothing calls `size`:

<!-- docs-sweep: error CE2008 -->
```sushi
use <collections/strings>

fn size@(T)(T x) i32:
    return x.len()          # CE2008: 'T' has no method 'len'

fn main() i32:
    return 0
```

```
error [CE2008]: undefined function 'T.len'.
  = note: 'T' is declared here with no constraint
  = help: no perk declares 'len'; a type parameter has the methods of its constraints and nothing more
```

The fix is a constraint that promises the method, or a function for the concrete type.
The rules of the check:

- A fault of the template is reported one time, at the template. The copies of a refused
  template report nothing more.
- A type argument that does not satisfy a constraint is [`CE4006`](error-catalog.md#ce4006) at the call. The body
  reports nothing for it.
- The check runs in every build of the unit that declares the template, a `--lib` build
  included. A library author sees the faults of a template that the library does not call.
- A `T` always moves, because a type argument can own. A body that returns, stores or
  passes on a borrowed `T` is [`CE2411`](error-catalog.md#ce2411): take the value with `nom T` (and `nom self`
  for a field of the receiver), or add `Clone` and hand on `x.clone()`.
- Some facts exist only for a concrete type: whether a value copies or moves, its `drop()`,
  its derived methods, its layout, whether the `E` of a `Result@(T, E)` is an error type,
  and whether a lambda parameter of type `T` owns. The copies keep those, and report only
  those.

The design record is [Checked generics](design/checked-generics.md).

### The Passes

1. **`instantiate`**: collect generic instantiations from the program
2. **`monomorphize`**: make the concrete copies of generic types and functions
3. **`resolve`**: resolve field and variant types to the concrete ones
4. **`typecheck`**: check each template one time, with its type parameters opaque, and
   then check the concrete copies

See [Semantic Passes](internals/semantic-passes.md) for all the passes.

### Code Size Implications

Each unique instantiation generates separate code:

```sushi
let Box@(i32) b1 = Box(value: 1)       # Box@(i32) code
let Box@(i64) b2 = Box(value: 2)       # Box@(i64) code
let Box@(string) b3 = Box(value: "3")  # Box@(string) code
```

**Best practices:**
- Limit the number of distinct instantiations when possible
- Use LLVM optimizations (O2/O3) to deduplicate similar code
- Profile code size if binary size is critical

## Complete Example

Several pieces together: generic structs, a generic function, and a perk-constrained function:

```sushi
perk Describable:
    fn describe() string

struct Pair@(T, U):
    T first
    U second

struct Robot:
    string name

extend Robot with Describable:
    fn describe() string:
        return "Robot {self.name}"

fn make_pair@(T, U)(nom T first, nom U second) Pair@(T, U):
    return Pair(first: first, second: second)

fn announce@(T: Describable)(T item) ~:
    println(item.describe())

fn main() i32:
    let Pair@(i32, string) p = make_pair(nom 42, nom "answer")
    println("Pair: {p.first}, {p.second}")

    let Robot marvin = Robot(name: "Marvin")
    announce(marvin)

    # Nested generics in a List
    let List@(Pair@(i32, string)) pairs = List.new()
    pairs.push(Pair(first: 1, second: "one"))
    pairs.push(Pair(first: 2, second: "two"))
    println("Stored pairs: {pairs.len()}")

    return 0
```

## Best Practices

### 1. Use Descriptive Type Parameters

```sushi
# Good: clear intent
struct KeyValue@(Key, Value):
    Key key
    Value value

# Acceptable: single letter for simple cases
struct Box@(T):
    T value
```

### 2. Provide Concrete Examples

```sushi
# Document with concrete types
# Example: make_pair(nom 42, nom "answer") returns Pair@(i32, string)
fn make_pair@(T, U)(nom T first, nom U second) Pair@(T, U):
    return Pair(first: first, second: second)
```

### 3. Write the Constraint That the Body Needs

A body that calls `.hash()` on a `T` needs `@(T: Hashable)`, and the compiler refuses the
body without it. The error names the constraint to add. Write only the constraints that the
body needs: each one is a requirement on every caller, and a caller whose type argument
does not satisfy it gets [`CE4006`](error-catalog.md#ce4006) at the call.

### 4. Test Multiple Instantiations

```sushi
let Box@(i32) b1 = Box(value: 42)
let Box@(string) b2 = Box(value: "test")
let Box@(bool) b3 = Box(value: true)
```

## Known Limitations

| Limit | Diagnostic |
|---|---|
| A perk cannot have type parameters (`perk Conv@(T):`) | [`CE4010`](error-catalog.md#ce4010) |
| A perk method cannot declare its own type parameters: in the perk declaration the parser refuses `fn make@(U)`, and in an implementation the compiler refuses `fn show@(U)` | [`CE6001`](error-catalog.md#ce6001) (perk), [`CE4010`](error-catalog.md#ce4010) (implementation) |
| A perk has no inheritance, no default implementation, no `Self` and no static method | [`CE4014`](error-catalog.md#ce4014) for a static |
| A bare-parameter lambda to a generic cannot be inferred; declare its parameter types (`\|i32 x\| ...`) | [`CE2060`](error-catalog.md#ce2060) (function), [`CE2063`](error-catalog.md#ce2063) (method-level parameter) |
| A method call has no explicit `@(...)` slot | [`CE6102`](error-catalog.md#ce6102) |
| Explicit type arguments are all or nothing | [`CE2062`](error-catalog.md#ce2062) |
| A `T` that only the return type names is not inferred from the `let` annotation; write the type arguments | [`CE2060`](error-catalog.md#ce2060) |
| A generic static in a position that declares no type | [`CE2060`](error-catalog.md#ce2060) |
| A generic function value where nothing solves its type (`map(xs, identity)`) | [`CE2060`](error-catalog.md#ce2060), [`CE2093`](error-catalog.md#ce2093) |
| No pack forwarding (`g(pack...)`) and no pack indexing; a value pack is not a tuple, so neither `(args...)` nor a tuple bloom exists | [`CE2060`](error-catalog.md#ce2060) |
| No variadic parameter in a perk method or an extension method | [`CE0115`](error-catalog.md#ce0115) |
| A native `...T` function cannot be exported through a `.slib` (a pack can) | [`CE0116`](error-catalog.md#ce0116) |
| A partially concrete target (`extend Pair@(i32, U)`) | [`CE2098`](error-catalog.md#ce2098) |
| A template and a concrete target for one method name | [`CE0101`](error-catalog.md#ce0101) |
| A static on an array target | [`CE2104`](error-catalog.md#ce2104) |
| A function type or a tuple type as an extension or perk-implementation target | [`CE2110`](error-catalog.md#ce2110) |
| A nested array as an array extension target (`extend T[][]`, `extend i32[3][]`); `extend T[]` covers the nested receiver | [`CE2101`](error-catalog.md#ce2101) |
| No arithmetic on a type parameter, and no generic numbers (no constraint gives `+` or a numeric literal of type `T`) | [`CE2518`](error-catalog.md#ce2518) |
| No perk bundles: `T: Ord` does not give `==`; write `@(T: Eq + Ord)` | [`CE2514`](error-catalog.md#ce2514) |
| `Clone` is decided by the compiler; `extend X with Clone` is refused | [`CE4017`](error-catalog.md#ce4017) |
| A bound outside the top level of an `extend` target | [`CE6110`](error-catalog.md#ce6110) |
| A bound in the target of a `Drop` implementation | [`CE4019`](error-catalog.md#ce4019) |

---

**See also:**
- [Language Reference](language-reference.md) - Complete syntax
- [Standard Library](standard-library.md) - Built-in generic types
- [Perks](perks.md) - Trait-like constraints
- [Variadics](variadics.md) - Packs and `expand`
