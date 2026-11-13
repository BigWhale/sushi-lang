# Syntax cheatsheet

This file holds one short program text that shows the common syntax of Sushi in one place:
strings, doc blocks, the error channel, conversions, imports, visibility, structs,
generics, parameter modes, extensions and statics, arrays, iteration, constants and
HashMap. Read this file when you write Sushi source and need the spelling of a form. The
rules behind each form are in `docs/agents/language-rules.md`; the full grammar and
semantics are in `docs/language-reference.md` and `docs/language-guide.md`. Use
"Mostly Harmless" in an example, never "Hello World".

<!-- docs-sweep: skip (a cheatsheet of fragments, not one program) -->
```sushi
# Strings: double quotes interpolate ("Hello, {name}!"), single quotes do not ('can\'t')

# Doc blocks are part of the declaration: above it, first in a body, or first in a file
# (the unit block). A blank line or a `#` comment breaks the attachment.
##: The answer to life, the universe and everything. :##
const i32 ANSWER = 42

##:
Adds two numbers.

- Parameter a: The first addend.
- Returns: The sum.
:##
fn sum_two(i32 a, i32 b) i32:
    return a + b

# The error channel is OPT-IN (docs/design/error-channel.md): `| E` gives a Result, and
# no `| E` is BARE. A function that can fail writes the channel -- that is the normal form.
error DivError:
    DivisionByZero
fn divide(i32 a, i32 b) i32 | DivError:     # call yields Result@(i32, DivError)
    if (b == 0):
        return Result.Err(DivError.DivisionByZero)
    return Result.Ok(a / b)
fn parse_port(string s) i32 | StdError:     # no default error type: write StdError
    return Result.Ok(s.len())
# BARE is the exception, for a TOTAL function (a checksum, pure arithmetic, a path join):
fn add(i32 a, i32 b) i32:                   # call yields i32; ?? on it is refused
    return a + b

# The E of every Result is an ERROR TYPE: an enum declared with `error`. A conversion
# lives in the unit of its TARGET; `??` calls it when the two error types differ.
error ParseError:
    Empty
error AppError:
    Parse(ParseError)
extend ParseError as AppError:              # bare body, nom self, answers AppError
    return AppError.Parse(self)
fn parse(string s) i32 | ParseError:
    if (s == ""):
        return Result.Err(ParseError.Empty)
    return Result.Ok(42)
fn run(string s) i32 | AppError:
    let i32 n = parse(s)??                  # ParseError -> AppError through the conversion
    return Result.Ok(n)
# `e as AppError` calls it on a value; `r.map_err(f)??` converts at one site;
# a Maybe gives an error value first: `m.or_err(nom ParseError.Empty)??`.

# Imports stand above every declaration. `as` puts the names behind a dot.
use "geometry" as geo
public use "shapes"                          # my importers get shapes' PUBLIC names too
fn area(geo.Vec v) i32:                      # a parameter, and `let geo.Vec v = ...`
    let geo.Sign s = geo.Sign.Plus           # a constructor
    match s:
        geo.Sign.Plus -> return v.x * v.y
        geo.Sign.Minus -> return 0
# The scope rules (per unit, not transitive; `as`; `public use`) are in
# language-rules.md, Units, imports and namespaces.

# Visibility: `public` on fn/const/var/struct/enum/error/perk/extension; PRIVATE is the default.
# A perk implementation and a conversion carry no marker -- each is as visible as its target type.
public const i32 MAX_DEPTH = 32              # another unit may read it
const i32 SCRATCH = 4096                     # this unit only
var i32 next_id = 0                          # unit-level STORAGE: an address, rebindable,
public var List@(string) log = List.new()    # never moved out of, never dropped
public struct Crate:                         # another unit may name the type
    i32 weight
public extend Crate heavy() bool:            # travels with Crate (this unit declares it)
    return self.weight > 100
public extend i32 doubled() i32:             # R6: visible where this unit is imported
    return self * 2
enum Cursor:                                 # this unit only; variants follow the enum
    Start

# Structs (positional or named construction — all-or-nothing), generics
struct Pair@(T, U):
    T first
    U second
let Pair@(i32, string) p = Pair(second: "answer", first: 42)

# Generic functions, inference + explicit type args
fn identity@(T)(nom T x) T:                 # pass-through needs nom
    return x
let i32 a = identity(nom 42)
let List@(i32) e = empty_list@(i32)()       # explicit args when only the return names T

# Error propagation: only in a body with a channel (a bare body, main included, refuses it)
let File f = open("config.txt", FileMode.Read())??   # <io/fs>, `| IoError`; f closes on drop
let string body = f.read_all()??                     # one channel from open to read

# The io contracts: a function names a capability, not a type (<io/contracts>)
fn emit@(W: Writer)(poke W dst, string line) ~ | IoError:
    dst.write(line)??                                # every byte, or an error
    dst.flush()??
    return Result.Ok(~)
# emit(poke stdout, "Mostly Harmless\n") -- a console handle is a `public var File`

# Pattern matching -- a binding carries a MODE: bare borrows, `poke` writes through,
# `nom` takes (and needs a scrutinee the match owns: a temporary, or `match nom r:`)
match result:
    Result.Ok(value) -> println("Got: {value}")
    Result.Err(_) -> println("Failed")
match make_rows():                       # a temporary: the match owns it
    Result.Ok(nom rows) -> eat(nom rows)
    Result.Err(_) -> println("Failed")

# Parameter modes -- marked at BOTH ends or neither
fn look(string s) i32:                      # borrow (default)
    return s.len()
fn eat(nom string s) ~:                     # callee owns and frees; a bare ~ may end
    println(s)
fn increment(poke i32 counter) ~:           # read-write borrow
    counter := counter + 1
# call sites: look(s) / eat(nom s) / increment(poke n)

# BARE extension methods (no `| E`), the free-function rule: bare return, NOT Result.Ok;
# no ??
extend i32 squared() i32:
    return self * self
extend Counter bump(poke self) ~:           # opt-in mutable receiver
    self.n := self.n + 1

# A STATIC method has no receiver and is called on the TYPE name (#542). One namespace
# behind a type's dot: a variant, or a static, never both.
extend Crate static empty() Crate:
    return Crate(0)
extend Crate static weighing(i32 weight) Crate:
    return Crate(weight)
# let Crate c = Crate.weighing(42) -- and `List.new()` is the same rule, built in

# The opt-in error channel `| E`: the call yields Result@(T, E), ?? is
# legal in the body, and BOTH constructors are spelled, as in a free function: a bare
# `return value` is refused (#848), a `~` body ends `return Result.Ok(~)`. A chain stops
# at an unhandled channel (append ??). A PERK method declares `| E` the same
# way, on the contract and on every implementation alike; the two must agree.
extend i32 half_checked() i32 | OddError:
    if (self % 2 == 1):
        return Result.Err(OddError.TooOdd)
    return Result.Ok(self / 2)               # spelled; nothing is wrapped
# call site: n.half_checked()?? / .realise(0) / match

# Method-level type params (solved from arguments; no call-site @() on methods) and
# array targets (`extend T[]` binds the element; `extend i32[]` is concrete; anything
# else in the element position is refused):
extend List@(T) mapv@(U)(fn(T) -> U f) List@(U):
    let List@(U) out = List.new()
    foreach(x in self.iter()):
        out.push(f(x))
    return out
# <collections/iter> ships BARE eager .map/.filter/.fold on List@(T) and T[], forwarders over the built-in Iterator adapters
# (`xs.map(f).fold(0, g)`, no ??); the free functions stay. A function type is bare
# unless it writes `| E`, and a lambda takes its channel from its type, never its body.
# Design record: docs/design/ufcs-combinators.md

# Arrays and iteration
let i32[] arr = from([1, 2, 3])
foreach(n in arr.iter()):
    println(n)
# foreach also walks any type with next() -> Maybe@(T); `??` on the binder propagates
# the item's Err. `r.lines()` over a BufReader is the line loop.
foreach(line?? in r.lines()):
    println(line)
foreach(i in 0..10):                        # ranges: .. exclusive, ..= inclusive
    println(i)
let i32 v = arr.get(0).or_err(nom StdError.Error)??   # safe (in a `| StdError` body); arr[0] traps
arr[1] := 42                                # indexed assignment (consuming position)

# Constants (compile-time expressions only; writing to one is refused)
const i32[3] PRIMES = [2, 3, 5]
const i32[6] COUNTS = [0..=5]               # a range element fills the slots it spans

let i32 n = 4
let i32[] slots = from([0; n])              # a run-time count: `from()` only
let i32[] index = from([0..n])              # the same rule for a range bound

# HashMap
use <collections/hashmap>
let HashMap@(i32, string) map = HashMap.new()
map.insert(1, "one")
foreach(entry in map.entries()):
    println("{entry.key}: {entry.value}")
map.free()

# main is bare: it returns the exit code (`| E` on it is refused)
fn main() i32:
    return 0
```

