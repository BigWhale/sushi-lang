# Changelog

All notable changes to Sushi Lang will be documented in this file.

## [Unreleased]

### Language
- **`public use X` re-exports what an import brings** (#586). The unit takes X's PUBLIC
  names as its own and hands them to its importers in the same place its own names land:
  flat behind a flat `use "U"`, behind the dot of `use "U" as u`. Only a `public use`
  re-exports -- a plain `use` stays the unit's own -- and re-exports compose along
  `public use` chains; a cycle terminates. A re-exported name is a candidate exactly as a
  flat import's is: the unit's own declaration wins, two re-exports offering different
  declarations of one name are CE3012 at the use, and one declaration reached down two
  paths is one candidate. Identity is untouched: `IoError`, `fs.IoError` and a name
  reached through a two-hop chain are one type, and a `Result` over it interns once. New
  refusals: **CE3016** a `public use` with an `as` (a re-export is of names, not of a
  namespace; the alias still binds); **CW3005** a `public use` that hands on no public
  name, the CW3004 rule; **CE3514** a `public use` in a library built with `--lib-kind
  binary` or `hybrid`, refused before any bitcode is compiled, because the manifest has
  no record for a re-export yet (#585). A source `.slib`, the default kind, re-exports by
  construction. Design record: `docs/design/unit-namespaces.md` section 8.1 (Ruling 7).
- **A static method: a name behind the TYPE's dot.** `extend Vec static at(i32 x, i32 y)
  Vec:` declares a method with NO receiver, called on the type name -- `Vec.at(3, 4)` --
  which is how a user type carries its own constructor (#542). A name behind a type's dot
  is a MEMBER of that type: a variant, or a static method, never both; a local of the same
  name still wins first. Everything but the receiver is an ordinary extension method: the
  four parameter modes, the owning return, the `| E` channel, and no visibility marker,
  because a static is as visible as its target type. `new` is a legal static name and is
  still not a legal free-function name. A struct, an enum, a primitive (`extend f64 static
  of_int(i32 v) f64:`), a built-in generic and a generic target are all legal -- on a
  generic target the type arguments are solved from the ARGUMENTS first, for every type
  parameter a parameter names, and from the declared type at the call site for the rest
  (#573; the stamp alone was the rule from #542, and it left a `| E` static unwritable
  because a Result-valued call is never stamped) -- and the call folds through an alias
  (`geo.Vec.origin()`) with nothing added. `List.new`, `List.with_capacity`,
  `HashMap.new`, `Own.alloc` and `f64/f32.from_bits` are static methods under the same
  rule, named in one table. `static` is now a reserved word, so `v.static()` and a method
  named `static` are no longer writable. New refusals: **CE0134** a static naming a
  receiver, in the signature or in the body (one fault, two positions); **CE2102** a type
  whose dot holds no such member, which replaced a CE1001 "use of undeclared identifier"
  for a struct that IS declared -- the fault was the position, not the name; **CE2103** a
  static spelling a VARIANT of the enum it extends, relational, because the variant would
  always win; **CE2104** a static on an ARRAY target, which no expression could ever call;
  **CE2060** a generic static with a type parameter that no argument names and that the
  position declares no type for -- the text names both sources -- which used to reach
  the backend as an ICE;
  **CE4014** a static inside a perk implementation, because a perk has no `Self`. A static
  beside an instance method of one name on one type stays CE0101, and CE2045 grew a help
  line naming both members an enum's dot can hold. A SOURCE `.slib` exports a static; a
  BINARY one exports no extension method at all, as before.
- **A constant may construct an enum variant.** `const Sign DEFAULT = Sign.Plus`,
  `Shape.Circle(5)` and `Maybe.None` against a declared `Maybe@(T)` are constants now,
  in `.rodata` like a struct constant: a payload-free variant is its tag over a zero
  payload, and a payload-carrying one is the tag plus its constant payloads -- a string,
  a struct or another enum included -- written at the offsets a run-time construction
  uses (#551). A generic struct or enum is built against the DECLARED type, so
  `const Pair@(i32, bool) P = Pair(3, true)` is a constant too, and so is a
  `Result@(T, E)`, which is an interned enum like `Maybe@(T)`. A unit variable takes
  the same initializer, which makes `var Maybe@(HashMap@(K, V)) cache = Maybe.None` the
  cache-filled-on-first-use shape the `var` ruling named. A variant the enum does not
  declare, a payload count that does not fit and a payload of the wrong type read the
  body's codes (CE2045, CE2050, CE2049); a function call in a payload stays CE0108.
- **A reference-typed `let` is a checked borrow binding.** `let poke Holder h = o.get()`
  binds a POINTER into the Own's payload, so `h.items.push(9)` and `h.n := 42` reach the
  heap cell with no copy -- the zero-copy mutation path a bare `Own@(T)` local had none
  of (#409). `let peek T x = <place>` is the read-only twin. The place is a local, a
  member or index chain off one, a unit variable, or an `Own@(T).get()`; a call result is
  CE2404 and a constant CE2400. The binding is block-scoped and freezes its owner (CE2412
  on a later mutation), one `poke` binding of an owner at a time (CE2403), a `peek` beside
  a live `poke` is CE2407, a write through a `peek` binding is CE2408, and consuming the
  binding stays CE2411. CE2413, which refused the form while it was untracked, is
  retired.
- **Unit-level storage: the `var` declaration.** `var i32 counter = 0` at the top of a
  unit is storage in the data segment -- one per program, initialized from a constant
  expression before `main`, never destroyed at exit -- where a `const` is a value the
  compiler folds into every use. A variable has an ADDRESS, so a rebind, a field
  assignment, a mutating method, a `poke` argument and a `poke` foreach binding all reach
  it, from its own unit or through an alias (`t.count := 3`). It is private by default and
  `public var` crosses the unit boundary, like the five marked kinds; a public variable of
  a private type is CE3009. The initializer accepts everything a constant does plus an
  EMPTY container (`List.new()`, `from([])`, `new()`), which allocates nothing;
  `HashMap.new()`, a non-empty `from(...)` and another variable are CE0108. A variable is
  never moved out of: a `nom` argument, a `let` bound straight from it, a `return` of it
  and a `nom self` method are the new **CE2436** when the type owns a resource, and a
  plain value copies out. A binary `.slib` carries a `public_variables` record with the
  storage's `link_symbol`; `--lib-info` prints `var T name`. Design record:
  `docs/design/unit-storage.md`; reference: the Unit Variables section of
  `docs/language-reference.md`.
- **A condition is a bool, and nothing else is one.** A `Result@(T, E)` used to answer
  for its Ok tag, so `if (f())` meant "did the call succeed". A `Result@(bool, E)` then
  had two readings and the compiler took one in silence: `Ok(false)` ran the true branch
  and the bool was never read — the defect that made `<io/path>`'s `extension()` answer
  an empty extension for every path. The exception is removed, so `Result` now agrees
  with `Maybe@(T)`, which was refused from the start. The new **CE2516** covers every
  condition position — an `if`, a `while`, and the operands of `and`, `or`, `xor` and
  `not` — and names the escape: `.is_ok()` / `.is_err()` / `.is_some()` / `.is_none()`
  answer with a bool, while `??`, `.realise(default)` and `match` take the value.
- **A logical operand is a bool too.** `and`, `or`, `xor` and `not` checked no operand
  at all, so the hole sat *around* CE2005 rather than under it: `if (n)` reported and
  `if (n and true)` did not. An integer operand got C truthiness (`not 5` answered `0`),
  and a string, a float, a struct, an enum or an array reached the backend and became a
  CE0017 internal error — the shape #449 removed from the comparisons. All nine
  condition positions go through one rule now, and CE2005 offers the `== 0` escape only
  to an integer, where it spells something. A `not` also reports its own type: it is a
  bool whatever it wraps, so `{not n}` no longer renders through its operand.
- **Extensions get an opt-in error channel.** `extend bool checked() bool | StdError:`
  gives the method the Result ABI: the call yields `Result@(T, E)`, `??` works in the
  body, `return Result.Err(e)` is the one spelled constructor, and the bare success
  wraps into Ok at the return seam (`return Result.Ok(x)` stays refused, CE2091). A
  bare extension is unchanged, CE0131 and CE2091 included. A perk method declaring
  `| E` is the new CE0133 instead of a silent drop. The chain gate is the new CE2515:
  a method missing on an unhandled Result/Maybe and present on its payload names both
  and spells the `??` fix.
- **Extensions get method-level type parameters.** `extend List@(T) map@(U)(fn(T) -> U f)
  List@(U)` declares `U` on the METHOD; the call site solves it from the arguments,
  and two different solved types on one receiver are two symbols and two bodies. An
  unsolvable parameter is the new CE2063 (annotate the lambda); a name that repeats a
  receiver parameter is the new CE2064.
- **Array extension targets.** A concrete element extends one array type
  (`extend i32[] sum()` — silently unregistered before, now works), and a bare
  undeclared name binds a type parameter: `extend T[]` applies to every element type.
  Any other element spelling is the new CE2101.
- **Recorded:** `??` on a `Maybe@(T)` propagates `None` as a payload-free `Result.Err`
  — Maybe is data, Result is the channel. The decision record for all of the above is
  `docs/design/ufcs-combinators.md`.
- **A type can declare that it owns a resource.** The built-in `Drop` perk
  (`fn drop(poke self) ~`) is how a `File` or a `TcpStream` says it owns a descriptor
  no field walk can see. A type that implements it MOVES like a `string` or a
  `List@(T)`: one owner, a `nom` parameter to hand it over, destruction when the owner
  leaves scope. Only the unit that declares the type may implement `Drop` (**CE4012**),
  and `.clone()` on a resource type is **CE2431**, because a copy verb would hide the
  second descriptor; the operation that means one is `share()`. Scope exit destroys in
  reverse declaration order, deterministically -- block exit used to go in hash order
  and could change between two compilations.
- **`nom self` exists, and a consuming receiver spends its value.** A method may
  declare `nom self`; `close()` on every handle does, so a read after a close is
  refused while compiling -- **CE2435**, naming the method -- rather than answering
  EBADF at run time. A `nom` ARGUMENT keeps CE2405.
- **A pattern binding carries a mode.** Bare borrows, `poke` points into the payload,
  and `nom` TAKES it: `match make_rows(): Result.Ok(nom rows) -> eat(nom rows)`. Taking
  needs a scrutinee the match owns -- a temporary, or `match nom r:` -- so a `nom`
  binding under a plain `match r:` is **CE2432**, an arm takes the variant whole
  (**CE2433**), and `Own(nom x)` is **CE2434**. CE2404 narrowed to a read through a
  live owner.
- **A marked field take.** `nom s.field`, in a `let` initializer or a `return`, is the
  one field read that is not a borrow. It spends the WHOLE receiver: the other owning
  fields are destroyed at the take, `drop()` does not run, and a later mention is
  CE2405. `nom a.b.c` and a take through a borrow are CE2411.
- **A perk method gets the error channel, and a name has one home.** A perk method may
  declare `| E`, on the contract and on every implementation alike; a mismatch is
  **CE0133**, relational. A perk method beside an extension method of the same name on
  one type is **CE4007**, so a name is a contract method or a convenience and never
  both. A perk method's argument mismatch reads CE2006/CE2009 like an extension's.
- **A generic type can implement a perk, and can declare a resource.** `extend Box@(T)
  with Show` is a template, one copy per instantiation; CE4013 is retired.
- **`foreach` walks any type with `next() -> Maybe@(T)`.** No type to implement and no
  perk to name. The protocol carries no error channel: a fallible iterator puts the
  failure in its ITEM, `Maybe@(Result@(T, E))`, and `??` on the binder --
  `foreach(line?? in r.lines())` -- is the short form. A `??` binder over a non-Result
  item is **CE2517**; an unwalkable iterable is CE2033, re-texted. A protocol iterator
  is destroyed on every exit path.

### Standard Library
- **The constructor vocabulary: a static is `new`, a free function is a bare verb**
  (#571). A function that builds a value of a type from its arguments and allocates for
  it is a static named `new`; a function that DOES something -- opens, connects, listens,
  binds, decodes, reads a header -- is a free function named by its verb, without its
  unit as a leading prefix, because the module is the namespace and `use <m> as m` tells
  two verbs apart. `<io/buf>`: `BufReader.new(nom src, cap)` and `BufWriter.new(nom dst,
  cap)` replace `buf_reader` and `buf_writer`, the shape the handles epic planned and
  could not build; the `Reader`/`Writer` constraints move onto the structs. `<net/tcp>`:
  `connect`, `listen`. `<net/udp>`: `bind`. `<net/ip>`: `v4_any`, `v4_loopback`,
  `v6_any`, `v6_loopback`. `<encoding/msgpack>`: `decode`, `map_get`, `show`.
  `<toolchain/slib>`: `read_metadata`, `sizes`, `bitcode_size`. The fifteen old names
  are deleted, not aliased. `open()`, `parse_ip`, `parse_ip_v4`, `parse_ip_v6` and
  `parse_url` do not move: a verb's OBJECT is not a prefix, and neither are the `fd_*`,
  `sock_*` and `zlib_*` layer and format prefixes.
- **`Reader`, `Writer` and `Seek` take `poke self`, and the buffered types implement
  them.** `BufReader@(R)` is a `Reader` and `BufWriter@(W)` is a `Writer`, so one generic
  written `@(W: Writer)` takes the console, a `File`, a `TcpStream` and a
  `BufWriter@(File)` alike (#546, route 2). The mode is on the contract and on every
  implementation, so `File` and `TcpStream` moved with it, and the conveniences over the
  contract (`read_all`, `readch`, `writeln`, `tell`) too. A generic over a contract now
  takes its handle `poke` (`poke W dst`, called as `emit(poke stdout, ...)`), and a write
  through an unmarked `File` parameter is CE2422, the rule every borrow already answered
  to. The console handles `stdin`, `stdout` and `stderr` became `public var File`
  declarations, which is what gives `stdout.write(...)` an address to reach; they can be
  rebound for a run (`stdout := open(...)??`) and are never moved out of, so
  `stdout.close()` is CE2436 (it was CE2400) and a buffered writer over the console
  takes `stdout.share()` or a fresh `File(fd: STDOUT_FD, owned: false)`.
- **Sushi can talk to the network.** Six new modules cover the first seven rows
  of the net gap list: `<net/socket>` is the low-level half, a Python IR
  generator, because an array or a struct may not cross the C ABI, a `u8[]` has
  no way to yield a pointer, and a `ptr` is opaque, so `sockaddr` could neither
  be built nor read from Sushi. `<net/tcp>`, `<net/udp>`, `<net/dns>`,
  `<net/ip>` and `<net/url>` are bundled Sushi source on top.
  - `<net/tcp>`: `TcpStream` and `TcpListener`, with `.send_all()` and
    `.recv_exact()` for the loops a byte count needs. There is no RAII for a
    socket, exactly as there is none for a `file`, so `s.close()` (a
    `poke self` receiver) writes `-1` into the binding: one binding owns a
    socket, and closing that binding again is a success.
  - The API is method form. The two constructors per module and the parsers
    are free functions; everything with a receiver is an extension method,
    with the `| NetError` channel where the operation can fail. The
    infallible `<net/ip>` and `<net/url>` questions are BARE methods, so a
    predicate goes straight into a condition: `if (a.is_loopback()):`.
  - `<net/udp>`: `UdpSocket`, answering a `Datagram` that carries its sender,
    because an unconnected datagram socket has no `getpeername` and the sender
    exists only at the instant its datagram arrives.
  - `<net/ip>`: `IpAddr` with a numeric payload -- `V4(u32)` and `V6(u64, u64)`,
    so it owns no heap and copies. Reading follows RFC 4291 and writing follows
    RFC 5952, `::` over the longest zero run, leftmost on a tie, never over a
    run of one.
  - `<net/dns>`: `resolve()` into `IpAddr[]`. A numeric text answers itself with
    no network request.
  - `<net/url>`: a lexical RFC 3986 split. A port of 0 means the text carried
    none, and the scheme's default is a separate question.
  - `NetError` is a predefined enum. `EAGAIN` maps to `TimedOut`, because every
    socket here is blocking and that is the only thing it can mean; `EBADF` maps
    to `Closed`. `getaddrinfo` gets no errno table at all: its `EAI_*` codes are
    not errno and do not share a sign between the platforms.
- **`to_i32`, `to_i64` and `to_f64` free the buffer they parse from.** Each copies
  its receiver into a NUL-terminated buffer for `strtol` and none of them freed
  it, so every parse leaked the length of the text it read.
- **The combinators exist in method form.** `use <collections/iter>` now also ships
  `.map`/`.filter`/`.fold` as extension methods on `List@(T)` and `T[]`, each with the
  `| StdError` channel, so the fluent chain works end to end:
  `xs.map(f)??.filter(p)??.fold(0, g)??`. The free functions stay. The method-form
  `filter` is fully general — it clones each kept element, so owning element types
  work; `map`/`fold` stay copy-element like the free functions.
- **A program can read the clock.** `<time>` gains `now() -> Result@(i64)` (unix
  seconds) and `monotonic_ns() -> Result@(i64)` (nanoseconds that never go
  backward). One `clock_gettime` read each; the clockid values live in the
  platform modules because Darwin and Linux disagree on them.
- **The file system around the file exists now.** `<io/files>` gains
  `read_dir(path) -> Result@(string[])` (entry names, `.` and `..` skipped),
  the stat fields `mtime`/`ctime` (unix seconds), `mode` (raw `st_mode`) and
  `is_symlink` (through `lstat`, so the link itself answers), and `flush()` on
  `file`, `stdout` and `stderr`.
- **`use <io/path>`: lexical path algebra in pure Sushi.** `join`, `basename`,
  `dirname`, `extension`, `normalize` — string work only, POSIX separators,
  mirroring `posixpath` under a 110-vector differential test.
- **`use <io/fs>`: the recursive forms, in pure Sushi.** `stat()` composes the
  per-field reads into a `FileStat` struct, `walk()` collects the files under a
  tree without following directory symlinks, `mkdir_all()` builds every missing
  prefix, `remove_all()` takes a tree down and is idempotent. The first bundled
  source module that exports a `public struct`.
- **Six array methods, on the road to the array top 10.** `first()` and `last()` are
  `get()` with the index built in, answer `Maybe@(T)`, and BORROW like `get()`.
  `contains(v)` and `index_of(v)` search with the `==` the language already defines,
  so the element type must have equality -- numeric, `bool`, or `string`; anything
  else is the new CE2100. `index_of` answers the first match as `Maybe@(i32)`.
  `clear()` and `truncate(n)` (dynamic only) shrink the length, destroy the dropped
  elements, and KEEP the buffer -- a scratch array in a loop empties without a
  realloc. Truncate never grows, and a negative count clamps to 0 the way the slice
  family clamps.
- **`file` becomes `File`.** A `File` is an ordinary struct in `<io/fs>` with an
  `owned` bit; the builtin type, the `file`/`stdin`/`stdout`/`stderr` keywords,
  `<io/stdio>` and `FileResult` are gone. The console handles are `File` CONSTANTS over
  descriptors 0, 1 and 2, and every route to the console is the descriptor, `print` and
  `println` included -- `printf` buffered beside a descriptor write and put the console
  out of order. A handle owns its descriptor, moves to one owner and closes on drop;
  `close()` consumes, for the caller who has to see the failure.
- **`<io/contracts>`: `Reader`, `Writer` and `Seek`.** What a handle can DO, named
  apart from what it is. `File` implements all three, `TcpStream` implements `Reader`
  and `Writer`, `TcpListener` none. Every contract method answers `IoError`, because a
  contract carries one signature and there is no `Self`; construction, addressing and
  options keep the domain enum, converted inside the stdlib by `FileError.to_io()` and
  `NetError.to_io()`. A contract write answers `~` and never a count. `read(max)` is
  one read of `max` BYTES, and a caller that must not cut a multi-byte sequence
  accumulates bytes and converts once.
- **`<io/buf>`: buffered reading and writing, in pure Sushi.** `BufReader@(R)` over any
  `Reader`, `BufWriter@(W)` over any `Writer`, one system call per window. `buf_reader`
  and `buf_writer` take the handle; `finish()` is the checked last drain and consumes;
  a dropped writer flushes and loses the error; `into_inner()` hands the handle back.
  `r.lines()` answers `Lines@(R)` and is the line loop; a `File` keeps none.
- **`File.readln()` answers `Maybe@(string)`.** A blank line is `Some("")` and the end
  of file is `None`, so the two are never the same answer.
- **Positional and shared I/O.** `File.read_at(offset, count)` and
  `File.write_at(offset, data)` are `pread`/`pwrite`: the offset is an argument and
  nothing moves, which is the answer for concurrent reads of one file. `share()` on a
  `File` and on a `TcpListener` is `dup(2)`: a second OWNER over a SHARED open file
  description.
- **The descriptor layer under it.** `<io/files>` gains `fd_open` (an INTENT rather
  than raw `O_*` flags, whose values differ per platform), `fd_read`, `fd_write`,
  `fd_write_str`, `fd_readln`, `fd_seek`, `fd_isatty`, `fd_pread`, `fd_pwrite`,
  `fd_dup` and `fd_close`; `<net/socket>` gains `sock_dup`. A read and a write retry
  EINTR.
- **`TcpStream.recv` retires.** One read on a socket is the contract's `read_bytes`,
  and every `NetError` a read can answer has its `IoError` twin. `send` stays: a
  socket's partial write says what the peer's window took, and `write_bytes`, which
  writes everything, cannot.

### Tooling
- **`--lib-info` lists a library's perks.** The report named a public perk and nothing
  under it, printed an implementation's methods as bare names, and printed a generic-target
  implementation (`extend Box@(T) with Show`) nowhere -- so since the io contracts became
  shipped perks, the one report a consumer reads hid them (#537). Every perk method is now
  a manifest record with its signature and receiver mode (templates schema 7; a
  version-6 binary library is refused and rebuilt), the `Perks` section prints each
  contract's methods -- `fn read(poke self, u8[] buf) i32 | IoError` -- and `Perk
  Implementations` lists the concrete implementations and the generic-target templates in
  one section, each method with the same signature line. The Python fallback and the
  `slib-info` tool change together, and `--docs` prints a perk method's block under its
  signature, which the record could not carry before.

### Fixed
- **A named argument is refused where it names nothing.** `p.shifted(dx: 5)` compiled and
  the compiler read the arguments by position, so `p.moved(dy: 5)` against
  `(i32 dx, i32 dy)` wrote the wrong slot on a program the compiler accepted (#563). A
  name in an argument list names a FIELD, and a struct construction is the only
  declaration that gives a name to each position, so every other callee answers CE6104: a
  function, a method, a built-in method, a stdlib function, a generic function, an
  indirect call through a function value, an enum variant and a call behind a `use ... as`
  alias. The rule is read once, after the call is validated, because only then is the
  callee known -- a struct construction SPENDS its names, and names still on the node
  belong to a callee with no field names to match. The AST builder dropped a method call's
  names entirely; it carries them now, to be refused.
- **A `match` arm's inline body takes a rebind.** `Maybe.Some(v) -> kept := v` was
  CE6001 "unexpected token ':='" while the same line under an indented arm compiled: the
  inline body accepted a call, a `print`, a `return`, a `break`, a `continue` and a bare
  expression, and a rebind is none of those (#552). It takes one now, so
  `Result.Ok(nom rows) -> kept := rows` -- a move out of a temporary the match owns --
  is a one-line arm. The target is any place a rebind takes: a name, a field, an array
  slot. A `let` still needs the block form.
- **An unhandled `Result` in a `let` answers CE2505, once.** The general CE2002 ("type
  mismatch: cannot assign X to Y") was asked first and names no fix, so a call
  right-hand side printed both codes at one location and a channel method
  (`let i32 x = c.read_one()`) printed only the unhelpful one (#535). The Result
  question is asked before the compatibility question now, and it reads every
  right-hand side the same way: a `Result` constructor, a call, an extension or perk
  method with an unhandled `| E` channel. The exclusion that skipped every method call
  is gone -- `.realise()` and `.clone()` answer the UNWRAPPED type, so neither ever
  reaches the diagnostic. Ruling 5's assignment row in
  `docs/design/ufcs-combinators.md` was already CE2505; the compiler agrees with it.
- **A generic call's result is inferable as a generic function's argument.** The
  typecheck pass types a call through the callee's concrete signature, and a generic
  callee has none until its instance is built, so `show_it(wrap(nom "towel").realise(Box("none")))`
  was CE2060 "cannot infer type arguments" while the same value bound by a `let` or a
  `match` arm inferred (#556). A generic call is typed from its SUBSTITUTED signature
  now -- one derivation, shared with the instantiate pass, which already read it for a
  `match` scrutinee -- so `.realise()`, `??` and any method chained on a generic call
  carry a type into the outer call site. A `Result` whose payload names an instance the
  monomorphize pass has not built yet is handed back and kept OUT of the enum table,
  beside the abstract-payload rule, so an early answer cannot park an unresolved
  payload under a name whose resolved form arrives later.
- **A local or a constant named `PI`, `E` or `TAU` is what it says.** The three math
  constants were asked before every local and every constant, in the scope pass, the
  inference visitor and the back end, so `let i32 PI = 3` printed 3.14159 and a unit's
  own `const f64 E = 1.5` read as 2.71828, with no `use <math>` needed (#560). A stdlib
  constant is a name the module brings now, at the rung of the name ladder a flat import
  occupies: a unit that did not import `<math>` has no `PI` (CE1001), a unit's own
  declaration wins, `use <math> as m` keeps them behind the dot, and a `const`
  initializer folds `PI / 2.0`. The registry constant carries its value, and one
  scope-aware lookup answers every reader.
- **A constant reaches another unit.** A `const` initializer could not name another
  unit's constant (CE1002), a qualified constant, constructor or variant (CE0108), or a
  qualified type (CE2001 with a help line naming an import the unit already had) (#561).
  All of it works now, flat and behind an alias: `const sh.Shape SMALL = sh.UNIT`,
  `const i32 D = sh.SIZE * 2`, `const sh.Point O = sh.Point(y: 0, x: 0)`,
  `const sh.Shape T = sh.Shape.Circle(2)`, and a `var` initializer alike. A private
  constant is CE3005 as in a body, a call behind the alias stays CE0108, and the other
  unit's initializer is folded in its own scope, so a name inside it never reads as this
  unit's. Two declarations of one name are two constants and no longer a false cycle
  (CE0109). A named struct construction behind an alias used to land positionally in a
  body too, because the builder dropped the names on the method-call parse; it keeps them.
- **A generic enum's constructor works behind an alias.** `slot.Slot.Filled("x")` with
  `use "slot" as slot` reached the backend as a CE0113 compiler bug -- "semantic
  analysis should have set resolved_enum_type" -- and so did the bare `slot.Slot.Empty`.
  Propagation stamps a generic enum's constructor with the instantiation its position
  declares, and it read the receiver as a bare name only; the fold that turns the alias
  into `Slot` runs later, during validation. Propagation now reads the enum's name
  through the alias for itself, so both qualified spellings take the type of a `let`, a
  return, a struct argument, a call argument and a rebind exactly as the bare ones do.
  Found under #545.
- **A late instantiation gets its generic-target templates.** A type named only inside a
  generic body -- `let Box@(T) b` in `outer@(T)`, or the return of a generic it calls --
  exists only once that body is substituted, after the extension and perk-implementation
  copies were cut, so `b.show()` and `b.label()` were CE2008 on a `Box@(string)` that
  plainly existed (found under #555). Three gaps in the same path closed with it: a copy's
  body walk bound no `let` local, so a generic called with one was never collected
  (CE2061); a `let` annotation's instantiation lived in the substitutor's cache and was
  never interned (CE2008 on the type itself); and a `@(S: Show)` constraint on a late type
  depended on the order the copies were cut in. Every instantiation the tables hold with
  no copy yet is cut after the functions, to a fixpoint, and the constraint check reads
  the templates beside the registered copies.
- **A generic call's substituted signature reaches the instantiation collector.**
  `match buf_reader(nom f, 4096): Result.Ok(nom r) -> r.into_inner()` was CE2008
  "undefined function" while the `let BufReader@(File) r = ...` form compiled (#549), and
  `wrap(nom "x").show()` over `fn wrap@(T)(nom T v) Box@(T)` with `extend Box@(T) with
  Show` was the same CE2008 (#555). The instantiate pass recorded a generic call's
  `Result` wrapper and nothing else, through a substitution that rewrote a top-level type
  parameter and left `Box@(T)` untouched -- so an instantiation named only by a generic's
  return or parameter never reached the set the generic-target extension and
  perk-implementation copies are cut from. The pass now walks the whole substituted
  signature, and a `match` over a generic call types its arm bindings from it (the
  typecheck pass's inferrer has no monomorphized copy to read yet), so a generic called
  with such a binding is collected too.
- **A bare `Maybe.None` on a generic enum constructs the value.** A payload-free variant
  spelled without parentheses parses as a field read on the type name, and only the
  parenthesized spelling reached the typecheck pass. On a MONOMORPHIZED generic enum
  the bare spelling then carried no type -- the borrow pass looked `Maybe` up by its
  bare name where the table holds `Maybe<string>`, read the initializer as a borrow, and
  moving the binding was CE2411 (#545); a program that never moved it reached the
  backend and was a CE0056 internal error. The hole was wider than the report: a bare
  variant of a PLAIN enum skipped the variant check, so `Shape.Nope` compiled. The bare
  spelling now takes the same path as `Maybe.None()`: propagation stamps the interned
  instance on it, validation checks the variant (CE2045), and the borrow pass and the
  backend read the stamp. Every position that hands a value its type is covered -- a
  `let`, a `return`, a constructor argument, a call argument, a rebind.
- **An empty `from([])` takes its element type from the position, everywhere.** As a
  `.realise()` default and as a bare extension's `return` it reached the backend with no
  element type and was CE0000, a compiler crash on ordinary syntax (#544). Three
  positions -- a `let`, a `Result.Ok` payload, a struct argument -- worked only because
  each derived the element type for itself in the backend. The typecheck pass now stamps
  the position's `T[]` on the `from([])` node, exactly as it does for `new()`, and the
  one emitter reads it; the payload and struct derivations are gone. A `.realise(from([]))`
  over a `Result@(u8[], E)` or a `Maybe@(T[])`, `return from([])` in a bare or a channel
  extension, and `count(from([]))` against a `u8[]` parameter all compile and run.
- **A generic-target perk implementation travels through a binary `.slib`.**
  `extend Box@(T) with Show` is a template, and the manifest walked the monomorphized
  copies and exported it nowhere -- and a perk shipped only when a generic constraint
  named it, so a public contract with a concrete implementation was invisible too
  (#543). Every public perk now ships, the template ships as source in a new
  `generic_perk_impls` record (templates schema 6; a schema-5 library is refused with
  CE3512 and must be rebuilt), the consumer files it through the collect pass's own
  perk collector and cuts one copy per instantiation, for the library's own
  `Box@(i32)` and for a `Box@(string)` the bitcode never saw alike. The library's
  copies stay out of `perk_impls`: a copy's source slice is the template's. Under the
  same issue, a binary library's function SIGNATURES are now read for instantiations:
  `fn make_box(i32 v) Box@(i32)` is a manifest record no unit walk sees, so `Box<i32>`
  was never interned and the backend answered CE0020 for any consumer of it, perk or
  no perk.
- **A binary `.slib` keeps a function's error channel.** The manifest wrote
  `error_type` beside `return_type` and the consumer never read it: two `FuncSig`
  builders read the return alone, so `fn risky(i32 x) i32 | MyErr` was typed
  `Result@(i32, StdError)` at every consumer and `risky(4)??` inside a `| MyErr`
  function was CE2511 (#541). The registry's `_parse_functions` is now the one reader
  of a manifest signature and reads the channel; the analyzer's and the backend's
  fallback builders are gone. The typecheck pass and the backend derive a call's
  `Result` from a signature through one function, `signature_result_arms`, so an
  explicit `Result@(i32, MyErr)` return is not wrapped again on the binary path either
  -- it used to arrive as `Result@(Result@(i32, MyErr), StdError)`. A manifest type
  string that names an instantiation the consumer has not interned (`Box<i32>`,
  `Result<i32, MyErr>`) now reads back as the generic reference it was written from.
- **`??` over a named `Result` or `Maybe` local spends it.** `let Result@(string, E) r =
  make()` then `let string got = r??` gave `got` the buffer and left `r` registered to
  free it as well: one buffer, two frees, and the printed value was garbage (#548). The
  unwrap moves the payload out of its wrapper, so `??` is now a consuming position of its
  own (`ConsumingUse.TRY`): a wrapper the function owns is marked moved at the `??`,
  before the propagation path's cleanup -- the Err arm travels too -- and a later mention
  of it is CE2405. A wrapper that owns nothing stays usable; a BORROWED wrapper (a
  parameter, a binding) is read through, so the `let` binds a borrow and consuming it is
  CE2411. The `foreach` `??` binder is the same rule: a protocol iterator's item is a
  value the iteration owns, the generated `let T x = <item>??` spends it through the
  ownership seam, the body may hand a protocol item away, and the backend's no-owner
  special case for the item is gone. Design record: `docs/design/borrow-model.md` §10d.
- **An interpolated string in an argument position has exactly one owner.** An
  interpolation always builds a fresh buffer, and in argument position no binding names
  it, so who frees it is the compiler's decision. Two seams were each making that
  decision alone, and which one fired depended on whether an enclosing `println(...)`
  had a temp frame open: inside one, the #141 registry claimed every buffer the
  interpolation built, including a buffer built for a NESTED call's argument that the
  call site had already given an owner — two frees, and `println(look("{tail}!"))`
  aborted with exit 133 on a HashMap key, a declared borrow parameter and a `nom`
  parameter alike. Outside one, nothing claimed it: `own_temporary` reads the argument's
  type and `infer_expr_semantic_type` had no answer for an interpolation, so a stdlib
  call marshalling a C string freed the marshalled copy and leaked the string it copied
  from. An interpolation now emits inside a frame of its OWN — it frees each intermediate
  as the next concat copies its bytes, an early exit out of a later part still frees what
  the earlier ones built (#295), and the RESULT belongs to the position it lands in.
- **A bool prints as a word wherever it is printed.** #514 moved the interpolation hole
  to `true`/`false` and left `println(flag)` on `%d`, so the two spellings disagreed
  about one value; both go through the bool formatter now. A hole holding a plain
  function call was a second half of the same defect: the typecheck pass stamped its
  return type on a method call but not on a plain one, so `{exists(p)}` and
  `{not exists(p)}` fell back to the integer rendering and printed `1` and `0`. Every
  call carries the stamp now, which also gives the backend a truthful answer for the
  signedness of a call result.
- **An `<io/files>` failure names its reason.** Every utility function used to
  return a zeroed Err payload, which reads as `FileError.NotFound` whatever
  happened; errno is now read and mapped, so `mkdir` on an existing path answers
  `AlreadyExists` and `rmdir` on a full directory stops claiming NotFound. The
  mapping table is shared with `open()` and takes the platform value for
  `ENAMETOOLONG`/`ELOOP`, which also corrects `open()`'s mapping on Linux.
- **The stat offsets know the architecture.** `st_mode` sits at 16 on glibc
  aarch64, not 24, so `is_file`/`is_dir` read `st_uid` in an arm64 Linux
  container; the offsets are keyed on (os, arch) now and probe-verified. On
  macOS x86_64 the bare `stat` symbol is the legacy 32-bit-inode layout, so
  `stat`, `lstat` and `readdir` select their `$INODE64` names there.
- **Two bundled source modules can share one program.** The compiler-emitted
  string helpers (`llvm_strlen`, `llvm_string_is_empty`, `llvm_strcmp`) carried
  external linkage in every unit that needed them, so the second source-stdlib
  unit collided at link; the bodies are `linkonce_odr` now.
- **A generic function's error channel survives the call.** `fn f@(T)(T v) i32 |
  IoError` was typed `Result@(i32, StdError)` at the call site, so `??` answered
  CE2511 (#538).
- **A generic called inside a `match` arm is instantiated.** It used to be CE2061
  (#539).
- **A local named `open` is credited with its uses.** The scope pass exempted four
  names before the local-wins check, so `let i32 open = 4` warned CW1001 on a
  variable the program reads (#536). The exemption went with the keywords.
- **Monomorphization keeps a receiver's mode.** A `poke self` method on a generic
  target was copied without its mode, twice over -- #253's shape on a generic target.

### Changed
- **One signature table per stdlib layer, and every reader takes its row from it.** The
  `<net/socket>` function list was spelled in four Python places beside its own name
  list -- the registry's parameter specs, the backend's Ok type and error enum, the
  interned `Result` the instantiate pass asks for, and the emission special cases -- and
  `<io/files>` spelled its own names in three if/elif chains inside one file. A name
  missing from one of them answered CE2008 for a function the compiler can emit (#550).
  `SOCKET_SIGNATURES` and `FILES_SIGNATURES` are now the one spelling of what each
  primitive takes and answers, beside their generators, and the arity, the return type,
  the registry spec, the interned Result and the whole of code emission are derived from
  them. A parameter carries its Sushi type AND whether it crosses as a C string, because
  one type crosses two ways: `fd_open` takes a PATH and `fd_write_str` a string VALUE.
  The backend gained one seam that turns a row into an LLVM signature and emits the call,
  so the two layer emitters are a table lookup and a symbol prefix -- 372 lines net came
  out. `tests/unit/test_stdlib_signature_tables.py` is the gate: 250 checks that every
  name in a table has a row in every reader. `<time>`, `<sys/env>`, `<sys/process>` and
  `<random>` keep their own shape and are the follow-up.

### Testing
- **The docs sweep compiles the FILES too, and there are 109 of them.** A third
  collector, `--only files`: nothing compiled a `.sushi` file under `docs/` before it,
  because one collector reads fences out of Markdown and the other compiles fences OUT OF
  a file (#547). Three files had been broken for two phases of the handles epic before
  anybody noticed. Two rules make the whole corpus pass with no marker on a single file: a
  file with no `fn main(` at the start of a line is a LIBRARY and is built as one
  (`--lib`), so "no main" is a category and never drift, and every library of a directory
  is built BEFORE that directory's programs into a temporary directory the programs get on
  `SUSHI_LIB_PATH` -- which is what checks the tutorial's `use <lib/guidelib>` page end to
  end, against the library the page ships. A file is compiled and never run, exit 1 is a
  PASS as everywhere else in the sweep, and the escape hatch is the same vocabulary in a
  comment of the file's leading block (`# docs-sweep: skip (reason)`,
  `# docs-sweep: error CExxxx`). Every page's `--8<--` snippet include is checked and
  COUNTED, so a page naming a file that is not on disk fails and the summary says the
  check ran. Output and cache go to a temporary directory, so a sweep leaves the tree
  clean. The attribute vocabulary now has ONE reader that the fence and the file both
  call.
- **A release no longer waits for the cross-platform suite, and one gate decides for
  every job.** `pyproject.toml` and `uv.lock` are inside the workflow's code filter and
  have to be -- a dependency, a ruff rule, `requires-python` and the hatch packaging
  table each decide whether the compiler still builds and still passes. A release PR
  touches exactly those two files and changes exactly the version, which decides nothing,
  so it was paying for the full Linux and macOS suites to re-prove the commit it was cut
  from.

  `.github/scripts/version_bump_only.py` tells the two apart. It parses both sides,
  sets the version aside, and asks whether anything is left, so a reordered table or a
  new comment rides along and any real edit does not. It fails closed: a file that will
  not parse, a base blob git cannot produce, or a manifest with no version all answer
  "run the suite". A push to main and the merge queue are never skipped, so main is still
  proved in full and the badges still read the run that gated it.

  The path filter itself was written out six times, once per job, each deciding for
  itself; it is one `changes` job now whose output the others read.

## [0.12.0] - 2026-08-29

Two libraries written in Sushi itself, and the distribution form that carries them:
a `.slib` is now Sushi source plus an index, so one library file works on every
platform, and `use <compression/zlib>` is a complete DEFLATE codec with no C behind it.

And a unit can now keep something to itself. `public` reaches every declaration that has
a name, private is the default, and a public signature may not hand out a private type --
so a library exports what it means to export and nothing else.

A unit is also a namespace now. `use "math" as m` puts what the import brings behind a
dot, a unit sees what it imported and no further, and two units may each declare one
`helper` -- so a name says where it came from, and one program's names stop crowding
another's.

A declaration can also carry its own documentation. `##: ... :##` is part of the
declaration, the compiler checks it against the signature it describes, and
`--lib-info --docs` prints it -- so a library says what it does in the file that
defines it.

### Added
- **A static call composes through an alias.** `use <collections/hashmap> as hm` gates
  the name, so the static obeys it like the type does: `hm.HashMap.new()` is the
  qualified form, and the bare `HashMap.new()` behind an aliased import is refused
  exactly as the bare type is, with the same help line. A flat import changes nothing,
  and `List.new()` and `f64.from_bits(b)` stay bare, because nothing imports those
  names.

  ```sushi
  use <collections/hashmap> as hm

  fn main() i32:
      let hm.HashMap@(i32, string) m = hm.HashMap.new()
      m.free()
      return Result.Ok(0)
  ```

- **A constant can interpolate.** A string constant takes holes, and a hole takes any
  constant expression -- another constant, arithmetic, a cast. Each hole prints exactly
  as the same expression prints at run time, an integer at its declared width and a
  float as `%g`, so a constant and a body never disagree about a value's text. A call in
  a hole is still CE0108, like a call anywhere else in a constant.

- **A library that extends a type it does not declare is told so.** `CW3003`, at `--lib`
  build time only: the method name is claimed on that type for every consumer, and a
  second library claiming it makes the two unusable together. A builtin target is not
  exempt -- `i32` is the most collidable type there is -- and a perk implementation never
  warns, because the consumer's own implementation is the sanctioned override. An
  extension inside an ordinary program stays silent. The manifest records the claims as
  `foreign_extensions`, and `--lib-info` prints them under `Foreign Extensions`. When two
  UNITS extend one type with one method name, `CE0101` is relational now: a note names
  each unit, and no side is blamed.

- **`public` reaches every declaration, and private is the default.** The marker used to
  reach one declaration out of six. A `const`, a `struct`, an `enum` and a `perk` carry it
  now, and each of the five is private to the unit that declares it unless it says
  otherwise. `public const` was a parse error; it is the spelling.

  ```sushi
  public const i32 MAX_DEPTH = 32     # another unit may read it
  const i32 SCRATCH = 4096            # this unit only

  public struct Point:                # another unit may name the type
      i32 x

  enum Cursor:                        # this unit only
      Start
  ```

  An enum VARIANT carries no marker: it is as visible as its enum, because a private
  variant would make a total `match` unwritable across a unit boundary. An extension and a
  perk implementation carry none either -- each is exactly as visible as the type it is
  attached to, so nothing is threaded into method resolution and `extend i32 squared()`
  stays public because `i32` is. Writing `public` on an implementation method is
  **CE6103**.

  A private PERK hides the contract, not the method. Another unit may not implement it and
  may not constrain a type parameter with it (**CE4011**), while a method it provides stays
  callable on any type you publish.

  **A public thing may not hand out a private one.** A public signature that names a
  private type is **CE3009**, and a public constraint that names a private perk is
  **CE3010** -- covering a return, an error arm, a parameter, a constant's type, a public
  struct's field and a public enum's variant payload. A single-unit file never notices any
  of this: an extension on a builtin inherits no marker, so it promises nothing.

  A `.slib` exports what it marks. Three manifest extractors had no gate at all, so a
  decoder detail shipped as frozen API and `--lib-info` printed its whole field layout;
  `zlib` goes from 19 exported names to 7. What a library keeps is NAMED, so a consumer
  writing the name hears "private struct, defined in that library" and not "unknown type".
  A private type a public generic's body needs still travels, in the export closure, and
  the consumer still cannot name it. The manifest protocol is 2.1, so an older `.slib` is
  rebuilt.

  Closes #466. The design and the reasoning for each ruling are
  `docs/design/visibility.md`.

- **A range element in an array literal, and a run-time count in `from()`.** An element can
  already fill more than one slot with `value; count`. It can now be a RANGE, and the count
  no longer has to be readable at compile time.

  ```sushi
  let i32[6] table   = [0..=5]           # 0 1 2 3 4 5
  let i32[]  down    = from([5..0])      # 5 4 3 2 1, the direction `foreach` uses
  let i32[]  mixed   = from([-1, 0..3, 99])

  fn zeros(i32 n) i32[]:
      return Result.Ok(from([0; n]))     # a length known only at run time
  ```

  **Where a count must be readable depends on the position, not on the element.** A fixed
  array's length is part of its TYPE and a constant's evaluator needs the values, so both
  still need a count the compiler can read. A `from()` array carries its length in its
  descriptor, so a count or a bound there may be any `i32` expression. A bound that is not
  readable where it must be is **CE2019**, and so is a readable range that yields nothing;
  a range carrying a repeat count is **CE2020**. A run-time count that is negative is
  clamped to zero and gives the same empty array a zero count gives.

  A readable count never pays for the run-time mechanism. llvmlite does not fold, so a
  short readable range emits literal stores with no arithmetic, a longer one walks a
  constant trip count, and only an unreadable one computes anything.

- **A bulk array copy: `.extend()`, `.extend_range()` and `.ss()`.** Copying a range of one
  array into another had no spelling, so every site wrote an element loop and paid a bounds
  check, a capacity check and an amortized realloc per element.

  ```sushi
  out.extend(body)                  # append all of body
  out.extend_range(src, pos, len)   # append a range, no temporary
  let i32[] part = src.s(2, 5)      # a fresh array, by exclusive END index
  let i32[] same = src.ss(2, 3)     # the same, by LENGTH
  ```

  Four spellings over ONE emitter: `extend` is `extend_range(src, 0, src.len())`, `s(a, b)`
  is `ss(a, b - a)`, and `.ss()` is a fresh array of a run-time length plus the same range
  copy. `.s()` and `.ss()` are named for `string.s(start, end)` and
  `string.ss(start, length)`, which already mean this for text.

  A range outside the source is CLAMPED, exactly as it is for the string twins: a start
  before the beginning becomes 0, a start past the end gives an empty array, a run past the
  end stops at the end, and an end before the start gives an empty array. That is unlike
  `arr[i]`, which traps -- an index names ONE element and either has it or does not, while a
  range asks for what overlaps and can always answer.

  The source is a **borrow**, and every copied slot takes its own `copy_out` -- one value
  and N slots, or N values and N slots, is the same rule. A plain element type copies with a
  `memcpy`. The destination grows once, to exactly the length it needs. A source that
  aliases its destination is **CE2430** -- growing the destination may reallocate the buffer
  the copy is reading.

  `compression/zlib` loses 30 lines of hand-rolled loops, five copies and two fills.

- **A binary library's public constants reach the consumer.** `--lib-info` printed them
  and nothing could read them: the manifest carried a name and a type, and a constant has
  no body to link, so `LIMIT` next to a linked binary library was `CE1001`. Each published
  constant now carries its own declaration source, and the consumer registers it exactly as
  the export closure's private constants have always been registered.

  ```sushi
  # lib.sushi, built with --lib-kind binary
  public const i32 LIMIT = 100

  # main.sushi
  use <lib/lib>
  println("{LIMIT}")                  # 100
  ```

  A consumer's own constant of the published name is **CE0105**, the answer a source
  library gives for the same program. A constant the library KEEPS is **CE3005** and no
  longer `CE1001`: the manifest records the name, so "not yours" is the true sentence and
  "no such name" was not.

- **`use ... as`, a qualified name, and a scope that stops at the import.** A `use` put a
  whole unit into one flat namespace for the whole program, and nothing could say which
  unit a name came from. An import binds a namespace now, and the unit that wrote the
  `use` is the unit that sees it.

  ```sushi
  use "geometry" as geo
  use <math>

  fn area(geo.Vec v) i32:                  # a type, and `let geo.Vec v = ...`
      let geo.Sign s = geo.Sign.Plus       # a constructor
      match s:                             # and a match arm
          geo.Sign.Plus -> return Result.Ok(v.x * v.y)
          geo.Sign.Minus -> return Result.Ok(0)
  ```

  `as` decides WHERE the names land: an aliased import puts nothing into the flat scope,
  so a unit may declare `sin` beside `use <math> as std_math` and call both. The dot works
  in every position where a name is written -- a type, a constructor, a match arm, a perk
  constraint, a value and a call. The one refusal is a fixed array's size (**CE2099**),
  because a size is read while the unit's own AST is built.

  A `fn` and a `const` carry the unit that declared them, so **two units may each declare
  one `helper`**, and each unit's call answers itself. Two units may also export one name:
  writing it bare with two candidates is **CE3012** at the use, with a note at each
  candidate, and an alias is the answer. A unit's own declaration always wins, `use <math>`
  included, so your own `sin` is the `sin` your unit calls. A TYPE is still one name for
  the whole program.

  New codes: **CE3012** (two candidates), **CE3013** (the alias is taken), **CE3014** (a
  `use` below a declaration), **CW3004** (`as` bound an empty namespace). **CE3003**
  retires: two libraries that both export `sine` are usable together now, and the refusal
  stands at the ambiguous use instead of over the whole program. **CE3011** narrows to a
  TYPE, so a consumer may declare a function beside a library's private one.

  Closes #490, #487 and #503. The design is `docs/design/unit-namespaces.md`.

- **`--color=always|never|auto`, and one colour decision behind it.** Everything the
  compiler prints to a terminal -- a diagnostic, the version banner and the `--lib-info`
  report -- now reads one ladder: the flag, then `NO_COLOR` (present at any value, which
  is no-color.org's rule), then `CLICOLOR_FORCE`, then `TERM=dumb`, then whether the
  stream is a terminal.

  Three sites is what made this a seam. The diagnostics implemented three of the five
  rungs and the banner implemented one, so `NO_COLOR` silenced every diagnostic and left
  the banner above them painted.

  **The `--lib-info` report is coloured**: a section header and a symbol name bold, a
  count and a size dim, a tag keyword blue and a parameter name cyan, and prose left
  alone. Sixteen colours only -- the report has seven kinds of thing in it, so
  256-colour buys nothing and `COLORTERM` needs no reading. Colour changes no text:
  strip the escapes and the plain report comes back byte for byte.
- **`is_terminal()` on `stdin`, `stdout` and `stderr`.** A program can ask whether a
  stream is attached to a terminal, which is what a tool needs before it decides to use
  colour. The answer is about that stream alone: output piped into a pager still leaves a
  terminal on `stderr`.

  It returns a bare `bool`, because the question has no failure -- `isatty` answers 0 for
  "not a terminal" and sets `ENOTTY`, which is the same answer. The name is `is_terminal`
  and not `is_tty`: "tty" abbreviates teletype, a device file and an era, and the plain
  word matches the `is_empty` / `is_ok` / `is_err` shape the rest of the library uses.

  This is the first stdio method valid on all three streams -- the rest of the table is
  split, with `stdin` reading and the other two writing -- so the validator, the generator
  and the emitter each resolve it before the split rather than three times over.

  A free `isatty(i32 fd)` was rejected. The language hides file descriptors completely, so
  a function taking a raw fd would be the only place a number stands for a stream.
- **The compiler understands documentation blocks: `##: ... :##`**. A doc block is part of
  the declaration, not a comment near it: the grammar sees it, the AST carries it, and the
  compiler checks what it says against the declaration beside it.

  One construct serves three positions -- above a declaration, first in a body, or first in
  a file, where it documents the unit. A block attaches to the declaration on the NEXT line,
  which is Go's rule; a blank line or a `#` comment breaks the attachment, and a block that
  attaches to nothing is **CW7001**. The text is dedented and not reflowed, so a block
  written inside a body renders flush and a fenced example keeps its own indent.

  The delimiters are asymmetric on purpose, and that is what lets the compiler say which
  mistake was made: an opener with no closer is **CE6011**, a closer with no opener is
  **CE6012**, and a line-initial `##:` inside a block is **CE6013**, reported at the inner
  opener with a note on the outer one. The closer is line-initial or the block is a
  one-liner, so a lazy match can no longer swallow the declarations between two blocks.

  Four tags are recognised, each an ordinary Markdown list item: `- Parameter <name>:`,
  `- Returns:`, `- Errors:` and `- Example:`. A new `docs` pass checks them: a tag that
  names no parameter of this callable is **CE7001**, one parameter documented twice is
  **CE7002**, a second `- Returns:` or `- Errors:` is **CE7003**, and a keyword within two
  edits of a real one is a typo rather than prose, **CE7004** with the tag it meant. A block
  in a body that is not the first item is **CE7005**, and a declaration documented both from
  above and from inside its body is **CE7006**.

  **A library carries the text.** Every `.slib` record that names a symbol an author can
  document gains an optional `doc` key -- a public function, a public constant, a struct and
  each of its fields, an enum and each of its variants, a generic function, struct or enum, a
  perk, a perk implementation and each of its methods -- and a `unit_docs` map beside `units`
  carries each unit's own block. The record holds the block in parsed parts: `summary`,
  `body`, a `params` map keyed by parameter name, `returns` and `errors`. Every field is
  optional and the whole key is absent when a symbol has no block, so the container version
  does not move and an undocumented library grows by nothing.

  **`--lib-info --docs` prints the block** under the symbol it documents, indented two
  spaces, in both implementations -- the Python fallback and the Sushi-written
  `toolchain/bin/slib-info` -- and the two are locked byte for byte in both modes. The
  blocks are opt-in because prose is what makes a report long: a library of forty
  documented functions runs to ten screens with them and one and a half without, so a
  plain `--lib-info` is the API surface and `--docs` is the manual. The switch is spelled
  the same at both ends and travels through the delegation as itself, and the tool answers
  `--help` on its own.

  **The documented report has room to breathe.** A blank line before the first tag, one
  between tags, one closing each record, and a hanging indent under a wrapped tag's TEXT
  rather than under its dash. Measured on a realistic library -- 40 documented functions,
  8 structs, 16 fields -- the report is ten terminal screens with no blank line anywhere
  between one symbol and the next, and whitespace is the only thing that makes a stream
  that long scannable. Nothing is reflowed: a tag wraps where the author wrote a newline
  and nowhere else, which is what keeps a fenced example intact. A symbol with no block
  still prints as one bare line, so the plain report stays exactly as dense as it was.

  **Inline Markdown renders, and an example finally prints.** On a terminal `` `code` ``
  is cyan, `**bold**` is bold and `*italic*` is italic, each with its marks removed; a
  captured report keeps every mark, so nothing piped into a file loses the signal that
  `` `spin_up` `` is a symbol. The subset is closed and everything outside it -- a link, a
  table, a heading, a nested list -- prints as it was written, which is what every
  construct did before. The scanner leaves prose alone: a mark that never closes is
  punctuation, an empty span is punctuation, and `2 * 3 * 4` stays arithmetic.

  An `- Example:` prints last, with the tag's own text as its caption and the code
  indented under it and dim. The caption is new in the index -- the library carried the
  code alone -- and it pairs with its code by position.

  Parameters print in DECLARATION order, and not in the order the block documents them. The
  report also gained a second thing, which prints either way: a parameter's `nom` mode now
  shows beside its type. That is the one mode a type cannot spell; `peek` and `poke` were
  always part of the type string.

  Two things do not travel in the index: an extension has no manifest record of any kind,
  and a private symbol carries no doc because it is not part of the documented API.

  The index is bigger for it, and that is expected rather than a cost to weigh. Measured on a
  stdlib-sized library of 83 documented symbols, it grew from 5,787 to 22,578 bytes -- about
  200 bytes a symbol, of which 35 is msgpack framing. The metadata blob is plain text and
  uncompressed today; compressing it is its own change.

  **The toolchain runs the examples.** An `- Example:` tag introduces a fenced code block,
  and `python tests/docs_sweep.py` compiles and runs it -- an example that stops compiling
  is documentation that has drifted, and this is what says so. The sweep grew a second
  collector for it and a `--only {all,docs,examples}` selector; it stays a by-hand tool and
  deliberately not a CI job.

  The block is now PARTITIONED before its tags are read, which removes three silent
  defects: a line-initial `- Returns:` inside example code was read as a tag and truncated
  the example there, a blank line inside a fence ended the tag that introduced it, and the
  fold that joins a tag's continuation lines destroyed the indentation a program needs. An
  example is its own structure, kept verbatim, and many `- Example:` tags are legal.

  Two new codes, both always on, because each is a claim that contradicts itself: a tag
  with no fenced block after it is **CE7007**, and a fence a block's own `:##` truncates is
  **CE7008**. An example that is merely ABSENT stays a matter of policy.

  A snippet with no `fn main(` is wrapped -- two lines of intent stay two lines. It goes
  into a helper and a generated `main` matches on the result, so an example whose `??` fails
  exits non-zero without a bare `??` in `main` to warn about. Every `use` line is hoisted,
  because a `use` inside a body does not parse. A snippet that declares its own `main` is
  compiled as written. The instruction to the harness rides on the fence itself, because a
  `.sushi` file cannot carry an HTML comment: ` ```sushi ` runs, `no_run` compiles only,
  `skip (reason)` does neither, and `error CExxxx` must exit 2 and name every code.

  An example is compiled from OUTSIDE the unit it documents, the way a Rust doctest links
  its crate. Two things are then out of reach and each is a printed, counted SKIP rather
  than a failure: a private declaration, which the generated file cannot call, and a unit
  that declares `main`, which cannot be imported beside a second one. An example that calls
  what a reader cannot call is not documentation, so the answer to the first is `public`
  and not a second mechanism.

  A `.slib` carries the code. The `doc` record gained an `examples` array -- the code of
  each example in source order, the fence attributes left behind, because an attribute is a
  harness instruction and not documentation. Measured on a three-example library: 184 bytes,
  of which 148 is the code the author wrote and 36 is framing, 12 bytes an example.
  `--lib-info` prints none of it; a fenced program inside a plain dump would bury the
  signature the reader came for.

  Two fixes came with it. A doc-block mistake in a bundled Sushi-source stdlib module was
  reported in every program that imported the module, against code the user never wrote --
  measured with a `CW7001` in `collections/iter.sushi`; the module now carries a
  provenance, like a source library's unit, so the diagnostic reaches us and not a user.
  And the syntax highlighter had no rule for a doc block at all: `#.*$` took the opening
  `##:` line and the code rules took the rest of the block.

  **`--warn-missing-docs` says what is missing.** Every check above is always on, because
  each one finds a claim that CONTRADICTS the declaration beside it. What a block leaves
  OUT is policy, so it waits for one opt-in flag, and a codebase that has not been
  documented yet does not become a wall of warnings on the day the feature lands.

  Five warnings behind the switch. A declaration with no block is **CW7002**; a documented
  callable with a parameter no `- Parameter` tag names is **CW7003**; a documented callable
  that returns a value with no `- Returns:` is **CW7004**; a documented function that
  declares `| E` with no `- Errors:` is **CW7005**; and a unit with no block of its own is
  **CW7006** -- a unit block travels in the `.slib` as `unit_docs`, so a library whose units
  say nothing is the first hole a reader meets.

  A private declaration warns too. The `public` marker is not the test, because an internal
  API is documented surface as much as an exported one. A struct field and an enum variant
  are each asked on their own, because each carries its own `doc` key in the manifest and
  `--lib-info` prints each one under its owner.

  Two exemptions, and nothing else. `fn main()` is nobody's API, and a library cannot
  declare one at all. An `unsafe external` block and the declarations inside it carry
  `because "..."`, which acknowledges the contract that matters at that seam.

  A block lint presupposes a block: CW7003, CW7004 and CW7005 fire only where a block
  already exists, so a declaration with none collects CW7002 and stops. One omission stays
  one diagnostic. A library's units are never linted, either way.

  A `.sushi` test fixture can turn a compiler flag on now. The runner gained a
  `COMPILER_FLAGS:` directive, which appends to the `sushic` command line; a flag the runner
  owns -- `-o`, `--lib`, `--lib-info`, `--clean-cache`, `--build-stdlib`, `--cache-dir` --
  is refused. Without it a flag-gated diagnostic had no fixture at all.

  `docs/documentation-blocks.md` is the reference and `docs/design/documentation.md` carries
  the phases that follow.
- **An array literal element may repeat: `[value; count]`** (#446). A table of one value
  no longer has to be spelled out or built at run time. The form is an ELEMENT form, so
  runs mix with plain elements and repeat within one literal: `[0; 19]`, `[1, 0;3, 9, 7]`,
  and the RFC 1951 fixed literal code as `[8;144, 9;112, 7;24, 8;8]` on one line. It works
  in a `const`, in a fixed local, and inside `from(...)`.

  The count is a positive integer the compiler reads: a literal in any base, an integer
  constant, or an expression of them. Unlike a fixed array's SIZE, it is read at the
  typecheck pass rather than while the AST is built, so it may name a constant of another
  unit. A count that is not a count -- zero, negative, or unreadable -- is **CE2017**.

  The value is evaluated once and copied, so the element type must copy. Repeating a type
  that owns heap memory would need a deep copy per slot and `.clone()` is the only deep
  copy in Sushi, so it is **CE2018**.

  A long run emits a counted loop, never a line of stores: `from([-1; 32768])` is 223 lines
  of IR with one store in it. **CE2011** now compares the EXPANDED count, and when a literal
  has runs it lists each one with the absolute span it fills. That listing is what the count
  form otherwise lacks -- a literal one element short gives the compiler no way to know
  which run is wrong, since either could be, so it prints what it does know and the reader
  matches a boundary against it.

  Ruled in `docs/design/compile-time-evaluation.md`. The measurement behind the ruling: every
  long table in the repository is a run of one value, so this closes them all and Sushi needs
  no constant function or compile-time loop yet.
- **`<`, `>`, `<=` and `>=` order two strings, and a comparison now decides its operands**
  (#449). The four order operators read the UTF-8 bytes, which is what Rust and Go do:
  `memcmp` over the common prefix, then the length as the tiebreak, so `"apple" < "apples"`
  is true and `"Zoo" < "apple"` is true because a capital is a lower byte. This is a byte
  order and never a collation -- it does not normalize, so the two spellings of `é` are
  neither equal nor adjacent, and a list a person reads still needs a locale-aware
  comparison. Both a variable and a literal work on either side, and a constant folds the
  same answer a body computes.

  The crash this closes was one hole in a wider one: the typecheck pass never asked what a
  comparison may compare, so every pair it did not look at reached the backend and tried to
  compare a string, a struct, an enum or an array value as an `i32`. `"a" < "b"` was
  **CE0000**, and `string < i32`, `struct == struct`, `enum == enum` and `i32[] < i32[]`
  were each **CE0017** -- four internal errors telling the user to report a compiler bug for
  ordinary invalid code. One closed rule now answers all six operators: equality takes the
  numeric types, `bool` and `string`, an order takes the numeric types and `string`, a mixed
  pair is **CE2513** and a type carrying no such comparison is **CE2514**, which names the
  escape (`match` for an enum, the fields for a struct). Two numeric widths keep CE2510.

  A `bool` deliberately loses its order. It compiled quietly before and answered
  `false < true`, where the code almost always meant `!=` or a missing `and`. Rust and Go
  both accept it; Sushi does not.

- **A constant can index an array constant, and can compare two bools or two strings** (#441).
  `const i32 SMALLEST = PRIMES[0]` folds, because every element of an array constant is
  already an evaluated value. A bound is checked while compiling -- a constant cannot trap --
  so past the end is **CE2012** and a negative index is **CE2056**. `==` and `!=` on two bools
  and on two strings fold as well; both work at run time and only the constant evaluator
  refused them. Ordering two strings was still rejected here, and #449 has since made it
  work in a constant and in a body alike.

  `+` on two strings in a constant now reports **CE2509**, the code the rest of the language
  reports, in place of CE0110 "arithmetic on non-numeric type". Sushi has no concatenation
  operator anywhere -- interpolation is the way to combine strings -- so the old wording read
  as a constant-only limit rather than the language rule it is. Interpolation inside a
  constant is still not evaluated (#447), and there is still no compile-time loop, so a
  generated table has to be spelled out (#446).

- **A fixed array's size may be written in any base, or name a constant** (#439, #440). The
  grammar took a decimal literal and nothing else, so `u8[0x100]` came back as
  `CE6001: unexpected token '0x100'` while `u8[256]`, the same size, compiled. Every base a
  numeric literal has now works, with the underscore rule (CE6006) and the C-octal rule
  (CE2071) applying exactly as they do to a literal anywhere else, because the size goes
  through the same token seam.

  A size may also name an integer constant, so a size that repeats across declarations can
  be written once: `const i32 MAX_BITS = 15` then `i32[MAX_BITS]`, in a local, a struct
  field, a parameter or a return type. The constant may be an expression
  (`HALF * 2`) and may name another constant, because the real constant evaluator reads it.
  It must be declared in the SAME unit: a size is read while that unit's AST is built, long
  before any pass holds a program-wide constant table, so a constant next door is reachable
  as a value but not as a size. That limit is now in the Known Limitations list, where #440
  asked for it.

  A size that cannot count elements is **CE2099**, one code carrying the reason: an unknown
  name, a constant that is not an integer, or a zero. A zero used to leave the type unbuilt
  and surface as CE2007, a missing type annotation on a line that has one. A zero-length
  array stays illegal -- ruled, not overlooked.

- **`use <compression/zlib>`: DEFLATE and the zlib container, written in Sushi.** No C
  library and no FFI -- the whole codec is Sushi. `zlib_compress` and `zlib_uncompress`
  handle the RFC 1950 container with its Adler-32 trailer; `deflate_raw` and
  `inflate_raw` handle a bare RFC 1951 stream; `adler32` is the checksum on its own.
  Errors are values: `ZError` carries the byte offset, the symbol or the two checksums
  that explain the failure, and `zlib_error_text` renders any of them as one stable line.

  The decoder is complete -- stored, fixed Huffman and dynamic Huffman blocks -- so it
  reads anything a conforming encoder writes. It follows puff.c, so a Huffman code is a
  count per bit length plus the symbols sorted by length, and needs only flat integer
  arrays. The encoder emits stored and fixed-Huffman blocks with an LZ77 hash-chain
  matcher, and falls back to stored when coding would make the data bigger, so the output
  never exceeds the input by more than five bytes per 65535. It never emits a dynamic
  Huffman block, so its ratio is short of a full encoder's: about 1.2x `zlib -6` on prose.

  Validated differentially against Python `zlib` in both directions
  (`tests/unit/test_zlib_differential.py`, 318 cases): every stream Sushi writes is read
  back by a real zlib, and every stream a real zlib writes is read back by Sushi.

- **Source is the primary distribution form for a Sushi library.** A `.slib` carries the
  complete source text of its units next to the MessagePack index, and the consumer
  compiles those units and caches their object files. One artifact works on every
  platform, because text has no target triple. No AOT-compiled language ships a portable
  binary library: Rust, Go and Zig ship source, Apple ships per-platform slices, and only
  bytecode ecosystems have portable binaries. Sushi had already crossed half the line --
  a generic can never be pre-compiled, so generics always travelled as source text, and
  the compiler already compiled bundled stdlib modules that arrive as text. The design is
  `docs/design/libraries.md`.
- **`--lib-kind {source,binary,hybrid}`**, defaulting to `source`. Binary distribution is
  the opt-in and keeps the old behaviour exactly: concrete bodies as bitcode, generics as
  source slices. It buys less than it looks: a binary library still carries the source of
  its generics, because monomorphization needs the consumer's type arguments, so only
  concrete bodies are hidden.
- **A library states its own version.** `--lib-version X.Y.Z` supplies it, unless a
  `nori.toml` beside the sources does; the manifest never recorded one before, because
  `library_name` came from the output filename and nothing stated a version at all.
  Neither present is **CE3505**, and so is a `--lib-version` that contradicts the
  `nori.toml` -- silently preferring one would let a package ship under a version it does
  not claim.
- **A library states which compilers can build it.** A source library is compiled by the
  CONSUMER's compiler, so one that built cleanly under 0.11 can fail under 0.12. That is
  the standard cost of source distribution and it is not fixable, only declarable. Every
  build stamps `requires_compiler`, by default `~<major>.<minor>` of the building
  compiler, because pre-1.0 semver makes the minor the breaking unit. A consumer outside
  that range is **CE3503**, a hard error rather than a warning: an incompatibility that is
  only warned about surfaces later as a confusing error inside library source the consumer
  never wrote. The escape is **`--ignore-compiler-version`**, build-wide and obviously
  temporary.
- **`sushi_lang/internals/semver.py`**: `Version` with parsing and ordering, and a
  constraint matcher covering exact, `~X.Y`, caret and comparator ranges. It sits in
  `internals` rather than `packager` because the compiler needs it on the library-load
  path; the Nori resolver reuses it rather than growing a second implementation.
- **`--lib-info` reports the version-4 fields.** The report states the kind, the library
  version, the compiler constraint and the unit index, and prints only the lines the kind
  can answer for: no `Platform` and no `Bitcode` for a source library, no `Source` for a
  binary one. Both readers changed together -- the Sushi tool `toolchain/src/slib_info.sushi`
  and the Python fallback -- and `tests/unit/test_slib_info_parity.py` locks them to the
  same bytes.
- **`slib_sizes` in `use <toolchain/slib>`**, which reads the length of both payload
  sections in one pass and never reads a blob. `slib_bitcode_size` is now one line on top
  of it.
- **CE3506**, the source section's sibling of CE3510 and CE3511. One truncation code per
  section, because the message names which section is short, and that is what tells a
  reader where the file was cut.

### Changed
- **Scope is per unit, and it is not transitive.** This is the one change here that stops
  a program compiling. A unit sees its own declarations, plus what its OWN `use` statements
  bring, and nothing else. An import is not re-exported: `top` importing `mid` no longer
  reaches what `mid` imported, and `use "geometry" as geo` reaches what `geometry`
  DECLARES.

  A registry stdlib module and the built-in generic it activates follow the same rule, so
  `use <math>` in one unit no longer answers a `sqrt` in another, and `HashMap` needs the
  import in the unit that names it. An `unsafe external` namespace is bound in the unit
  that DECLARES the block.

  **To name a type, import the unit that declares it.** A public signature may name a type
  its caller cannot write, and a `let` needs a written type, so the caller adds the import
  the name asks for. A name out of scope is refused as "no such name" -- **CE2008**,
  **CE2001** or **CE1001** -- with a help line that names the import which would bring it.

- **CE2018 retires: a repeated value may own heap memory.** `[towel; 3]` was refused
  because N copies of an owning value would need N-1 deep copies and the compiler never
  inserts one. That stopped being true when `.fill()` gained a per-slot `copy_out`, and the
  language answered one question two ways: `a.fill(towel)` was legal beside
  `from([towel; 2])`, which was not.

  A repeated value is now a **borrow**, as `.fill()`'s argument is, and every slot takes its
  own copy. A run has one value and N slots, so it has no single position to consume into --
  the general rule is that a bulk write borrows its source. A plain element still consumes,
  because it still occupies one slot.

- **A diagnostic that spans a whole construct is rendered without a caret, and a help
  is rendered inside the box.** A caret separates one thing from the rest of its line.
  When the span covers a whole construct there is nothing to separate, and the header
  already carries the line and column, so `CW7001`, `CE7005` and `CE7006` now report the
  location alone. A help used to hang below the box in the plain-text form even when the
  box was drawn; it now sits inside it, wrapped. The label stays, because a note with no
  location is a fact and a help is advice, and inside the box nothing else tells them
  apart. The plain renderer, which is what a pipe and every test sees, is unchanged.
- **A constant's type mismatch marks the assignment, not the declaration.** `CE2002` was
  handed the declaration's own span, where the `let` path hands it the value's, so
  `const str x = "FOO"` marked from `const` to the end of the line. It now marks
  `x = "FOO"`: the declared type is not the half that is wrong, and it already carries a
  note of its own on the same diagnostic.
- **`compression/zlib` is rewritten onto the language it now has.** The module landed
  before four of the fixes in this release and carried a workaround for each. It opened
  with a CAUTION block telling the reader that a bitwise operator truncates in silence and
  to cast every operand -- the opposite of what #438 made true -- and it pointed at an
  untracked working document from a file that ships to users.

  Six fill loops become one repeated element each: the RFC 1951 fixed literal code is
  `from([8;144, 9;112, 7;24, 8;8])` instead of four `while` loops and 288 bounds-checked
  appends, and the encoder's 32768-entry hash head is `from([-1; 32768])`. Since Sushi has
  no module-level state, `zfixed_lit` and `zfixed_dist` run once per fixed block in a
  stream, so this is about 700 appends per block replaced by two allocations and six
  counted fills. Two fills keep their loop, and say why in a comment: their length is the
  input size or a parameter, so no count is readable at compile time.

  The encoder stops scanning for a code. `zlen_index` walked a 29-entry base table
  backwards and `zdist_index` a 30-entry one, once per emitted match. The inverse tables
  are now written directly -- 256 slots in 29 runs for the length code, and the range split
  zlib'"'"'s own encoder uses for the distance code, 256 slots in 16 runs each side -- so a
  lookup is one array read. Both keep their range check and their `ZError` channel, and a
  program compares every table against the scan it replaces across all 256 lengths and all
  32768 distances: no disagreement.

  Five shift counts drop a cast that #438 made unnecessary, since a shift count'"'"'s type is
  free; the operand casts stay, because CE2510 requires them. `zinflate_lengths` reads its
  three repeat symbols with a `match`, the form `inflate_raw` already used for the block
  type. No behaviour changes: 354 differential cases against Python zlib pass in both
  directions, over both containers.
- **An operation the compiler reads computes at the declared width, and a result that
  leaves the type is CE2077** (#446, Ruling 1 of `docs/design/compile-time-evaluation.md`).
  The evaluator held a Python integer of unlimited size and stamped the exact result with
  the type of the left operand, so `const u8 A = 200 + 100` held 300 while the program
  printed 44. Truncation hid that for `+`, `-`, `*`, `<<` and `~`, and exposed it wherever
  something read the held value: `A as u32` was 300, `A > 255` was true, and
  `(200 + 100) / 2` was 150 in a constant and 22 in a body.

  Sushi now follows Rust. The overflow-checked operators are `+`, `-`, `*`, `/`, `%` and
  unary minus, and each reports **CE2077** with the operator, the value and the type;
  division and unary minus have one case each, the smallest signed value. The width-defined
  operators -- `~`, `&`, `|`, `^`, `<<`, `>>` -- compute at the width and never report,
  because the bits that leave the type are lost by design. A cast is the escape and
  truncates. This is a breaking change: `let u8 x = 200 + 100` no longer compiles, and no
  test in the suite of 1782 needed a change for it.

  Two divergences close with it. A held value is now inside its own range, so a right shift
  of an unsigned constant no longer fills from a sign bit the type does not have --
  `~(0 as u32) >> 1` was 4294967295 and is 2147483647, which is what a body always read.
  And a cast in a constant truncates as the machine does, so `300 as u8` holds 44 rather
  than 300. One consequence for #447: a constant now always holds a value its type can
  hold, so interpolation in a constant has nothing left to reconcile.
- **The `.slib` container is at version 4.** `SPARE_1` and `SPARE_2` became `FLAGS` and
  `KIND`, so the fixed 52-byte header did not change size, and a length-prefixed source
  section sits between the metadata and the bitcode. `KIND` states which payload is
  present, so a reader can branch before it unpacks any MessagePack. `FLAGS` bit 0 is
  reserved for source compression and is always written as zero: Nori archives are already
  `tar.gz`, so the wire is compressed regardless. A version-3 file is **CE3509**; there is
  no upgrade shim, because Sushi has no users in the wild.
- **The platform gate applies to a binary library only.** `_check_library_platform` read
  one `platform` field and rejected the whole file, so a library carrying nothing
  platform-bound was refused anyway. **CE3504** is now never raised for a source library,
  and `--lib-info` prints no `Platform` line for one. That single condition is the whole
  cross-platform fix.
- **A bare `./sushic --lib` no longer builds.** A library must state a version, so a build
  with no `nori.toml` and no `--lib-version` is CE3505.
- **A source library's units are ordinary compilation units at the consumer.** They enter
  as `lib/<library>/<unit>`, so they can never collide with a consumer unit name; privacy
  stays the existing unit mechanism, with no new machinery; each one caches its own `.o`
  under `__sushi_cache__/units/lib/<library>/`, with the materialized source in
  `__sushi_cache__/libsrc/`. Four rules make it sound, and not one of them was in the
  plan -- each appeared when real libraries went through the new path: library units are
  COLLECTED first, because
  the compilation order puts dependents before dependencies; a consumer definition SHADOWS
  a library one silently, for functions, generics and perk impls, so `--lib-kind` changes
  distribution and never semantics; a monomorphized instance of a library generic goes
  home to the unit that DECLARED the generic, which makes its call to a library-private
  helper an intra-unit call and is what replaces the export closure; and that instance is
  folded into the unit's fingerprint, because which instances a library unit carries
  depends on what the consumer asked for and its own source hash cannot say so.
- **CE5007 cannot arise on the source path.** It exists because an export-closure private
  shares the consumer's flat namespace; namespaced units remove the shared namespace, and
  the instance-goes-home rule removes the need to ship privates at all.

- **A shift by a count that empties the type answers 0.** The count that CE2512 cannot
  read -- a loop index, a byte from a file -- used to reach LLVM as written, and a count
  at or above the width made the result poison: the program printed whatever the
  optimiser left behind, and on a signed `>>` the arm64 hardware answered with the value
  unchanged. A shift now has an answer everywhere. A count at or above the width moves
  every bit out, so the result is 0; an arithmetic right shift fills from the sign bit and
  leaves it behind, 0 for a positive value and -1 for a negative one; a negative count is
  out of range at the other end and answers the same way.

  This is Go's rule, chosen over the masking that Java and Rust-in-release expose, because
  masking answers `value << 8` on a u8 with the value itself -- a wrong answer that reads
  like a working shift, and one that silently aliases every eighth iteration of a
  byte-assembly loop onto the same result. Sushi now rejects more than any of the five
  languages measured at compile time, and leaves no undefined shift at run time.

  The range is read from the count as written, before it is narrowed to the value's width:
  a u64 count of 256 narrowed to a u8 is 0, which would have answered with the value
  unchanged. The shift itself is given a masked count so LLVM never emits an out-of-range
  shift, whose poison it is free to use. The cost is one compare and one conditional move,
  and nothing when the count is a constant -- a 5 MB zlib round trip measured 0.28s before
  and 0.28s after.

### Fixed
- **A bool in an interpolation hole prints `true`/`false`, whatever the hole holds.** A
  variable, a literal and a logical expression printed `1` while a method-call result
  printed `true`: the rendering followed the expression's SHAPE, because only a stamped
  call routed to the bool formatter. The semantic type decides now, on every path, and
  the constant evaluator renders a bool hole the same way.

- **A double-quoted string inside an interpolation hole is named for what it is.** The
  inner quote closes the outer literal, so the parse failed on a token nobody wrote.
  CE6001 and CE6002 now carry the shape and both escapes: single quotes inside the hole,
  or bind the expression to a local first.

- **Four diagnostics said the wrong thing, and a gate now forbids the class.** A perk
  arity error was emitted as CE2007 with another code's kwargs, an array bulk-copy arity
  error keyed its method name wrongly, and two missing-symbol sites raised CE0027 with
  CE0024's shape. A static check over every emit site now demands that call-site kwargs
  match the registered template exactly, and that no unformatted brace reaches an
  emitter.

- **Two units may each declare a generic function, and every instance knows its home**
  (#495, #494). A generic function now carries the unit that declared it, exactly as a
  concrete function does: two units' `twin@(T)` coexist, each unit's call reaches its
  own, and a monomorphized instance takes its declaring unit's symbol prefix
  (`main$twin__i32` beside `helper$twin__i32`), `internal` when the generic is private.
  Within one unit a duplicate stays CE0101. The same identity fixes a silent wrong
  answer in a binary library: the export closure ships one record per `(unit, name)` --
  two library units may each keep a private `helper` -- and every source-shipped
  template carries a `bindings` map from each free name in its body to the symbol the
  producer resolved, so a template can never call another unit's body. The templates
  schema moves to version 5, and a binary `.slib` built with the old schema is refused
  (CE3512) and must be rebuilt. A private generic in a helper unit, instantiated from
  its own unit, no longer dies with CE0000.

- **Two ordinary units may implement each other's perks.** A perk declared next door and
  implemented at home was `CE4003: unknown perk`, and the same shape across a `.slib`
  worked. Nothing about the perk was wrong: the compilation order puts a dependent before
  its dependency, so the perk table was empty when the implementation was collected. Every
  unit's perk DEFINITIONS are collected before any implementation now, which is the rule
  the library path had all along.

  ```sushi
  # helpers/traits.sushi
  public perk Heavy:
      fn weigh() i32

  # main.sushi
  use "helpers/traits"

  extend Pallet with Heavy:           # was CE4003
      fn weigh() i32:
          return self.crates
  ```

  A private perk next door answers **CE4011** now, which is the contract rule, and not
  "unknown perk". Two units declaring one perk name are still **CE4001**.

- **A contested name gets one diagnostic, and it is aimed at the right unit.** Two units
  declaring one name heard the duplicate AND a second diagnostic measuring the loser's own
  code against the winner's declaration: `CE3005` telling a unit it may not call the
  function it wrote itself, `CE2027` about a struct shape it never spelled, `CE2045` and
  `CE2040` about an enum it did not declare, or `CE2060` about a generic it never wrote.
  The loser of a contested name is recorded now, and no rule speaks about a declaration
  the unit did not write.

  A consumer that declares a name a source library declares PRIVATELY is refused cleanly
  with a new **CE3011**: one flat namespace means it collides with a name it cannot see, so
  renaming is its only move. The branch that used to let it try deleted the library's entry
  and registered no replacement, so the consumer lost its own declaration as well -- which
  is why a program's own `fn map` beside `use <collections/iter>` heard CE2060 about
  `iter`'s generic.

- **A consumer's own declaration answers its own call, and says it shadows.** A program may
  declare a name a library exports; that is the documented symbol priority. The frontend
  did not agree with the linker: every table merges first-wins and library units merge
  first, so the library's signature answered the consumer's call and a replacement with a
  different signature was refused with a spurious CE2009.

  It is safe because a private function has internal linkage -- the two are separate
  symbols, the consumer's call binds to its own definition and the library's body keeps
  calling its own -- and the one combination that could break the link, both public, is
  CE3003 already. A new **CW3002** says the name is shadowed, because it is legal and
  rarely intended.

- **A borrow and a function type reach the type funnel.** `fn look(peek Nope x)` printed
  `CE0020: unresolved type 'Nope' - semantic analysis should have caught this`, telling the
  user their program was a compiler bug; `fn apply(fn(Nope) -> i32 f)` compiled clean. Four
  spellings now give the same `CE2001: unknown type 'Nope'` with a caret. One walk over a
  type answers for every predicate over types, with a gate on it.

- **The public-signature `ptr` fence covers what it always claimed to.** CE5008 read a
  `public fn`'s return and parameters and nothing else, so a public function's ERROR arm, a
  public GENERIC, an extension method and a perk method each carried a foreign `ptr` across
  a unit boundary and compiled clean. One walk over every signature closes all four, and
  the diagnostic says which kind of declaration it refused.

- **A perk implementation checks its target type once.** Two methods and one unknown target
  printed the same CE2001 twice, because the check sat inside the per-method loop. And
  `extend ~ shout()` was CE2032 while `extend ~ with Loud` compiled clean: two copies of
  one validator had drifted, and only one of them refused the blank type.

- **CE5013 now sees a symbol the standard library generates.** An `unsafe external "C"`
  whose link-name is a symbol this build defines is CE5013. The rule read the function
  table, the constant table and the library registry -- and the stdlib GENERATORS emit
  about 146 symbols that live in none of them, so the rule was blind to every one.

  ```sushi
  use <collections/strings>

  unsafe external "C" as raw because "naming a generated stdlib symbol":
      fn slen(string s) i64 = "string_len"   # now CE5013
  ```

  The real declaration is `i32 string_len({ptr, i32, i8})`. Declared as above, three
  things could happen: the program that also called `.len()` got a CE0000 TypeError; the
  program that linked the symbol and never called it BUILT CLEAN and died with a bus
  error; and the program that linked nothing got a linker error late, by symbol name.

  The generators are the only authority on those names, so `--build-stdlib` now writes
  them down beside the bitcode it produced (`dist/<platform>/symbols.json`), and the
  manifest is part of a built platform directory -- a stale one rebuilds. A second, small
  set in `semantics/externs_manifest.py` covers the three the backend emits INLINE into
  the module it compiles (`llvm_strlen`, `llvm_strcmp`, `utf8_char_count`), which no
  bitcode file holds; the backend reads that same set to give them their linkage.

  A generated name is refused whether the program links that stdlib unit or not.
  Otherwise adding a `use` line would break a build that compiled a minute ago. No libc
  name is affected: 140 of the 146 already carry a `sushi_` prefix, and none of them is
  a bare C name, so `= "strlen"` and friends still bind.

- **`.iter()` and `.hash()` on a dynamic array now work through every receiver.** This was
  the half of the fixed-array receiver fix below that the DYNAMIC side kept. `as_array_address`
  already gave a `T[]` one address rule, so a field read hands over a GEP and every other
  method worked; these two arms went looking for their ELEMENT type in the name tables
  instead, and a receiver with no name has no entry there.

  ```sushi
  foreach(x in h.nums.iter())          # a struct field -- was CE0072
  foreach(x in from([1, 2, 3]).iter()) # a temporary    -- was CE0072
  foreach(x in src.ss(1, 3).iter())    # a chained call -- was CE0072
  let u64 h = h.nums.hash()            # the same field -- was CE0056
  ```

  The dispatcher already unwraps the receiver's semantic type once for every dynamic arm,
  which is where `pop`, `free`, `clone` and `fill` read their element type. The two arms that
  looked it up now read it from there, and the check that it IS an array type moved beside
  the unwrap, so no arm repeats it.

  **CE0072 retires.** It only ever refused a receiver whose element type the name tables did
  not hold, so nothing reaches it now. The number is not reused.

- **`new()` is a value in every position, not only a declaration form.** `new()` spells the
  empty dynamic array. It named no element type, so the backend had no descriptor to build
  and emitted a scalar placeholder -- which meant `new()` worked in a `let` and in a struct
  constructor, because both special-cased it, and was broken everywhere else. As an argument
  and as a `.realise()` default it was CE0017; as a `Result.Ok()` payload it packed the
  placeholder, printed a length of zero and aborted at scope exit; a rebind crashed the
  compiler outright.

  ```sushi
  let i32[] taken = mk().realise(new())   # the natural default for an array payload
  let i32 none    = count(new())??
  r := new()
  ```

  The typecheck pass stamps `new()` with the `T[]` its position expects -- the same
  propagation that gives an array literal's elements their declared type -- and one emitter
  builds the `{0, 0, null}` descriptor an empty array is. The struct constructor's own copy of
  that descriptor is gone with it.

- **Every built-in method on a fixed array now works through every receiver.** A `T[N]`
  reached as a struct field, a nested field or an array element had no address rule of its
  own, so nine sites re-derived one and each fell back to a stack COPY of the receiver.
  `b.slots.fill(9)` and `b.slots.reverse()` therefore compiled, ran, and left the field
  unchanged -- no diagnostic was possible, because the store is legal. `.iter()` and
  `.hash()` on a field refused instead, with CE0072 and CE0056, because they looked the
  element type up by NAME and a field has none. Through a `peek` or `poke` parameter every
  method was CE0000: such a receiver arrives as `[N x T]*`, which the dispatch gate did not
  accept, so the call fell through to the user-extension lookup and mangled the type name
  into `i32[3]_len`.

  `as_fixed_array_address` is the one rule now, the fixed twin of `as_array_address`. It
  resolves the address from the AST, and it takes one flag: a READ may spill a value that
  names no storage, and a WRITE may not. That is what keeps a store out of `.rodata` -- a
  constant resolves for a read and to nothing for a write, so no such binary can be built
  even if CE2096 were bypassed. There is no fallback behind the write arm: reaching it means
  a typecheck rejection did not fire, and that is the new CE0132.

- **`.fill()` on an array of an owning element type double-freed and leaked.** The
  emitter stored ONE value into every slot with no copy. That is right for a plain
  element type, where a shallow store IS the value, and wrong for an owning one: two
  slots then held the same pointer and the array destructor freed it twice. A named
  local source was worse, because the local freed it a third time at scope exit. The
  same store also overwrote whatever a slot already held without destroying it, so an
  array of owning elements leaked one buffer per slot. The fixed arm had all three.

  `fill` now copies per slot, through the one deep-clone entry, and destroys the old
  element first -- the shape `arr[i] := v` already used. Its argument is a **borrow**,
  which makes `fill` the one container write that does not consume: it has N slots to
  satisfy and one value, so it cannot take ownership the way `.push()` does. The value
  therefore stays usable, and one string can fill two arrays. An owning element type
  costs one allocation per slot; a plain one copies nothing and emits what it did before.

  The argument also joins the built-in borrow seam, so the temporary behind
  `arr.fill(s.s(2, 5))` gets an owner instead of leaking.
- **`slib-info` printed angle brackets, half a generic's signature, and none of four
  sections.** Four faults in one renderer, so they are fixed in one pass.

  A generic printed `fn pick_bigger<T: Doubler> (template)`. Angle brackets are the
  INTERNAL identity spelling and never user-visible text. The MANIFEST keeps them, because
  a consumer reads every type string back with `parse_type_string` and converting them at
  the producer would break every library already built; the report converts, by the same
  four rules `display_type_name` already applied to diagnostics.

  `(template)` stood where the parameter list belongs, and a generic's record had no
  parameter list at all -- so its `- Parameter` tags were stored by the library and
  rendered by nothing. The record now carries the same three signature keys a concrete one
  does, built by one function, and a template prints the signature a concrete function
  prints.

  Generic Structs, Generic Enums, Perks and Perk Implementations were carried and printed
  by neither implementation. Each has a section now, suppressed when empty, and each
  generic section stands beside its concrete twin.
- **A function's error arm did not travel.** `fn improbability(i32) i32 | DriveError`
  reached the manifest with no error field, so the report printed `i32` and dropped the
  rest. This was not a render fault -- the information was uncarried, and `--lib-info` may
  never read source to recover it. The record gains an optional `error_type`, absent when
  the declaration does not spell one.
- **An owning temporary handed to a built-in method was never freed.**
  `src.contains(src.s(2, 5))` leaked one block a call, and `src.replace(src.s(2, 4),
  src.s(5, 7))` leaked two -- one for each owning argument. About fourteen `string`
  methods took an argument, so every one of them leaked; so did a `HashMap` key lookup,
  a `stdout.write`, a `file.write`, a `run()` command and every C-string callee. The
  workaround was to bind each temporary to a `let` first, which is a rule no author can
  be expected to know.

  Ownership at a call boundary is decided from the argument's provenance, which is what
  #358 built. A DECLARED callee reaches that seam and reads the parameter's mode off the
  signature; a built-in declares no parameters, so its emitter built its own argument
  list and there was no mode to read. `emit_borrowed_arg` is the built-in half of the
  seam and the twin of the receiver's: every built-in argument goes through it, and a
  built-in that TAKES ownership -- `List.push`, `HashMap.insert`, `Own.alloc` -- goes
  through `consume` as before.

  A PERK method was leaking for a different reason. It carries the same declared modes an
  extension method does, and its emitter simply never read them; it now settles its
  arguments through the same function the extension emitter calls.
- **Every diagnostic about a callee rendered as text with no caret.** The builder parsed
  the callee into a `Name` carrying its span, then rebuilt one from the bare identifier
  and dropped it, so `call.callee.loc` was `None` for every ordinary call. The head line
  named the file and stopped there: `shape.sushi: error [CE2009]: function 'add' expects 2
  arguments, got 1.` -- no line, no column, no source line, nothing marked.

  The builder passes the `Name` it already has. Every code anchored to a callee is tier 2
  now, with the caret under the name: **CE2008**, **CE2009** (eight emit sites),
  **CE3005**, **CE2060**, **CE2061**, **CE2062**, **CE2001** and **CE2027**. No emitter
  and no message changed.
- **A call to a function the compiler could not find was judged against a signature it
  invented.** The mode resolver answers `borrow` for a name it does not carry, so the
  borrow pass compared the call-site marker against that answer: `no_such(nom s)` reported
  **CE2008** and then **CE2427**, whose help line said to drop the `nom` -- advice that
  breaks correct code, because the callee it could not find may well declare `nom`. The
  loudest case was a private generic of a binary `.slib`, where the whole reason the name
  does not resolve is that the library kept it.

  The type checker now records that it found no callee (CE2008, CE2092), and the borrow
  pass applies no mode to the arguments of such a call. It says nothing about them, which
  is what it already did for a method call the type checker left unresolved. A callee that
  DOES resolve is judged as before, `open` included -- it carries no signature record, but
  the type checker resolves it.
- **A cross-unit `collect` diagnostic was reported against the entry file** (#473). The
  collect pass is the one whole-program pass that walks every unit's AST through a SINGLE
  reporter; the per-unit passes each build their own. A span is meaningless without its file,
  so a declaration in a non-entry unit was reported against the entry unit: the head line
  named a line the user never wrote, and the caret landed on whatever text sat at that
  column. A duplicate constant inside a helper unit marked `return Result.Ok(0)` in the entry
  file, and a `CE5001` in a helper marked a correct `println(...)` -- for a declaration in
  another file, which the diagnostic never mentioned.

  `CollectorPass.run` now names the unit it is reading, and `Reporter._record` -- the one
  place every diagnostic passes through -- stamps it. That answers for every emit site in the
  pass at once: **CE0004**, **CE0006**, **CE0101**, **CE0105**, **CE2046**, **CE4001**,
  **CE5001** and their siblings, head line and caret.

  A `first defined here` note needed one thing more, because it points at a table entry that
  may have been made while a different unit was being collected. Each record remembers its own
  file now -- `files` beside `spans` on the struct and enum tables, `PerkTable.files`, and a
  `filename` on `ConstSig` and `ExternalSig`, which `FuncSig` already had for exactly this
  reason. So both halves of a cross-unit duplicate name their own file, each with its own
  caret.

  No new codes, no message changes, and nothing legal became illegal. The `Origin` record
  that #471 introduced for a library template carries this too: its `provenance` note is now
  optional, because a unit of the program being compiled needs no explanation.
- **An `unsafe external "C"` could name a symbol the program itself defines** (#470). A
  program's units share one LLVM module and a linked library's module is merged into it, so
  a `declare` and a `define` of one name unify. There was no rule against it, and the
  consequences split two ways. Naming a private a binary `.slib` kept COMPILED, LINKED and
  RAN: the library's private body was entered from consumer code that may not call it, and
  because the declaration promised a bare `i32` where the body returns a `Result`
  aggregate, the answer came out of the wrong register -- `0` instead of `21`. Naming
  anything the compiler already held a declaration for was a **CE0000** internal error
  (`DuplicatedNameError`) instead: a private of another unit, a function of the extern's own
  unit, a PUBLIC function of another unit, a shipped export-closure private, or a constant.

  One rule now answers for all of it, **CE5013**: an `unsafe external` reaches out of the
  program, so its link-name may not be a symbol this build defines. It is relational -- the
  note carries the definition's own file, line and caret for a symbol this program declares,
  and names the library otherwise, saying whether the library exports the symbol, ships it in
  its export closure, or keeps it. `CE2008`-style guessing is not involved: the rule reads
  the func table, the constant table and the library registry, so it is a new named step
  `ffi-clash`, running after `libraries` because that is when the program's symbols are all
  in place.

  What stays legal is what FFI is for. A genuinely foreign symbol binds as before, and two
  namespaces may still declare the same one -- LLVM deduplicates identical declarations, and
  `CE5001` remains the rule for a mismatched one.

  One gap is left and it is narrow: a symbol the stdlib GENERATORS emit (`string_len` and
  its ~180 siblings) is named in no semantic table, so the rule cannot see it. It is not
  catchable in the backend either -- externs are declared before any of it is in the module.
- **Every diagnostic from a binary library's template body was reported against the
  CONSUMER's file** (#471). A binary `.slib` ships a public generic as a re-parsable source
  slice, and the consumer monomorphizes it. The instance lands in one of the consumer's own
  unit ASTs, so the consumer's reporter rendered it -- while its spans came from parsing the
  slice, where the declaration is on line 1. An error landed on a blank consumer line with a
  caret under nothing, and one warning's caret marked the consumer's own `fn main() i32:` for
  a mistake in code they cannot see. Two passes were affected, `scope` and `typecheck`, so it
  was never one emitter's quirk.

  A source library was always right, because it arrives as a unit with a `provenance` and
  every per-unit pass runs against that unit's reporter. The binary path now reads the same:

  ```
  <template:errlib:add_one>:2:22: error [CE2509]: operator '+' cannot be used with string types.
    |     return Result.Ok(a + 1)
    `                      --+--
    = note: 'errlib' 0.1.0 ships this template; it is monomorphized here because of `use <lib/errlib>`
  ```

  `report.Origin` carries the three things such a body needs -- what to call it, what text
  the caret marks, and why it is being compiled here -- and it is set on the template beside
  `is_library_template`, then copied onto every instance and every lambda lifted out of one.
  The mark answers who may be called from the body; the origin answers how a failure reads.
  `Reporter._record` applies it, because that is the one place every diagnostic passes
  through, and a diagnostic that names a file of its own keeps it.

  A name in angle brackets is no longer given a `./` prefix. `<input>` and
  `<template:lib:name>` are not paths, and one rendered as `./<input>`.
- **A binary library said "undefined function" where a source library said "private"**
  (#469). The export closure walks what a public GENERIC's body needs, because that body is
  monomorphized at the consumer. A private that only a concrete function calls, or that
  nothing public calls, ships nowhere and reaches the consumer's tables under no name at
  all -- so a consumer naming it heard **CE2008**, "undefined function", for a function the
  library defines on the next line of the build script and deliberately kept. The same call
  against a source `.slib` was **CE3005**. Both reject and both exit 2, so the two kinds
  disagreed about the wording and not about the legality.

  A `.slib` now carries one additive key, `not_exported`: what the library declares and does
  not export, as a name and its kind. No signature, no body and no source travel with it,
  because a name is all the diagnostic needs. Each private is named in exactly one place --
  the export closure, with a signature, or this list, with nothing else. The consumer's
  registry reads the key, and the `CE2008` site asks it before it emits, so the answer comes
  out of the one **CE3005** gate with the library named in place of a unit. The callee stays
  unresolved on that path, so no argument is judged against a mode the compiler invented.

  A kept name ships nowhere, so it clashes with nothing: a consumer may declare its own
  function of the same name and that is what the call resolves to. `CE2008` is left for
  what it is for -- a name that no unit and no linked library declares.

  Nothing else moves. The container version stays at 4 and `sushi_lib_version` at `"2.0"`:
  the metadata blob is an open msgpack dict read through `.get()`, so an older consumer
  ignores the key and a newer consumer reading an older `.slib` finds none, and both keep
  answering as they did. `--lib-info` prints no new section, in either implementation.

  This buys WORDING, not enforcement, and should not be read as hardening. Privacy across a
  binary `.slib` is still defeatable by a route the manifest has nothing to do with: an
  `unsafe external "C"` declaration can name a Sushi symbol, the library's module is merged
  into the consumer's, and internal linkage does not stop the call.
- **A binary library's private helpers were callable from consumer code** (#468). The
  export closure ships what a public generic's body needs -- a private concrete helper, a
  private generic template, a constant -- because the body is monomorphized at the
  consumer and still has to call them. Those names landed in the consumer's tables as
  ordinary callable symbols: the concrete records were registered `public`, and the gate
  exempted a template for being one. So a consumer could call `scale_up(2)` or
  `pick_first(a, b)` by name and it linked and ran, while the same call against a source
  `.slib` was CE3005.

  The call SITE now decides, not the symbol. A monomorphized instance of a library
  template is marked as the library's code, a lambda lifted out of such a body carries the
  mark with it, and the typecheck pass exempts those bodies alone. Consumer code naming a
  shipped private is **CE3005**, which names the library it belongs to. An instance of the
  *consumer's* own generic is synthesized by the same machinery and is deliberately not
  exempt -- it is code the user wrote, so it reaches no further than the user's own
  symbols.

  A shipped constant stays readable. There is no private constant to refuse yet, which is
  #466.
- **A source library's private generic crashed the compiler, and two build paths disagreed
  about it** (#467). `public` gates a concrete function across units, and the typecheck
  pass says so with **CE3005**. A generic had no such gate. The units of a source `.slib`
  arrive at the consumer as ordinary units, so the consumer resolved the private symbol,
  and only the backend found out that no template was ever exported -- as a `KeyError`,
  which the top-level guard rendered as **CE0000**. The default incremental path raised
  that internal error, `--no-incremental` accepted the same program and ran it, and a
  binary `.slib` answered **CE2008**.

  One gate now answers for both kinds of callee, in the typecheck pass, where the
  consumer's units and the library's exported templates are both visible. A private
  generic of another unit is **CE3005** wherever the call is written, on either path: a
  flag that controls rebuilds no longer decides which programs are legal. The same hole
  was open in an ordinary multi-unit program, where a private generic next door was
  callable; it is closed too. An export-closure template stays callable, because the
  consumer registers it so that a transplanted library body can call it -- which is what
  the concrete half of the closure already does.

  The bundled `<collections/iter>` combinators -- `map`, `filter`, `fold` and `compose` --
  were reachable only because the gate was missing. They are `public fn` now.
- **Every caret was one character wider than the thing it marked.** `end_col` is
  exclusive -- Lark reports it that way, and every span the compiler builds by hand
  spells it `col + len(text)` -- but the width was computed as the difference plus one.
  `str` was underlined with four dashes, `Maybe@(i32)` with twelve, `##:` with four. The
  width is now the difference. The one span that counted inclusively was the indent run
  behind `CE6004`, which now spells its end exclusively like the rest, and both marker
  paths share the arithmetic so a note underlines like a caret.
- **A span that ended on a later line drew a caret measured against a line it was not
  drawn under.** Only the span's first line is rendered, but the width came from its
  last: a `const_def` reaches column 1 of the line after the statement, so a whole
  declaration was marked with a single character under its first keyword. Such a span now
  underlines to the end of the line it is drawn under.
- **A msgpack length at or above 2^31 decoded as an empty value instead of an error**
  (#463). Each length-prefixed tag narrowed its `u64` prefix with a bare cast, so
  `0xffffffff` became -1, every taker looped `while (i < count)` zero times, and a
  five-byte buffer of nothing but a hostile header came back as `Ok`: `dd ff ff ff ff`
  was an empty array, `df ...` an empty map, `c6 ...` an empty bin and `db ...` an empty
  string. Python's `msgpack` rejects all four.

  The module's own header stated that such a length "casts to a negative count, loops zero
  times, and the next read reports Truncated". There is no next read when the header ends
  the buffer, so nothing reported anything; with a byte after it the result was `Trailing`,
  which named the wrong fault.

  One reader now takes every length prefix and rejects one the remaining buffer cannot
  supply. Every element of an array or a map needs at least one byte and a str or bin
  payload needs exactly its count, so `count > remaining` is unsatisfiable for all four
  families and `Truncated` is the accurate report -- no new `MpError` variant. It also
  catches an over-long POSITIVE length at the header rather than part way through the loop.
- **A declared type reaches a bare literal through `~`, and through a nested negation**
  (#448). `let u8 b = ~0` was **CE2002**, "cannot assign i32 to u8", and `const u8 MASK = ~0`
  said the same, so "every bit set" at any width other than `i32` needed an `as` cast --
  which is why the bitwise constant test carried `~(0x00 as u32)`. The literal now takes the
  declared type and the complement happens at that width: a `u8` reads 255, an `i8` reads -1,
  a `u64` reads all 64 bits.

  `~` was not a missing special case. The recursion that carries a declared numeric type
  down to the literal leaves had an arm for a binary operator and nothing else, and unary
  minus worked only through a LEAF case one function below it, which unwraps a negated bare
  literal to fold the sign into its range check. So the other half of the same hole was
  `let i8 x = -(1 + 2)`, a negation over anything that is not itself a literal. The
  recursion now descends through a unary operator whose result is its operand's type --
  `neg` and `~`, never `not`, which answers a `bool` -- and the negated bare literal stays
  one leaf, so `-128` still fits an `i8` and `-1` still misses a `u8`.

  The operand-driven half reads the same way. A bare literal takes its sibling's type
  through those operators too, so the bit test `flags & ~0x0F` on a `u8` is one width rather
  than the mixed `u8`/`i32` pair of **CE2510**, which is the shape a mask check inside an
  `if` always had. Nothing about `as` moved: `~wide` on a `u32` assigned to a `u8` is still
  CE2002, because this types LITERALS and never converts a typed value. Two diagnostics get
  better instead of disappearing -- `let u8 b = ~300` is now **CE2073**, the range check
  answering for itself rather than a type mismatch downstream of it, and `~1.5` is still
  **CE2004**.

- **A rejected library build no longer reports a spurious CE0000 over the real diagnostic**
  (#436). Three rejection sites in the manifest producer emitted their diagnostic and then
  raised `ValueError` to stop the build. Nothing caught it, so the top-level guard rendered
  it as an internal compiler error on top of the correct message, and a legitimate
  rejection of the user's own program came with an invitation to file a compiler bug. Each
  site now emits and returns, and `pipeline.py` decides control flow through
  `reporter.has_errors`, the way it already does before codegen.

  Two gates, and the placement is the point: the first sits after the export closure and
  before the bitcode compilation, so a CE5006 rejection still costs nothing, and the second
  sits after the manifest, which extracts the public API first and returns without writing
  once the reporter holds an error. A rejected build leaves no `.slib` behind and prints no
  success line. The exit code was already 2 and stays 2.

  Every rejected public export is now named in one build rather than one per build, because
  the loop reports instead of stopping at the first. Two findings came with the fix: the
  `_reject` helper inside the export-closure walk had been non-returning, which left the
  code after each call site unreachable, and the producer's **CE5002 cannot be reached from
  a CLI build at all** -- the typecheck pass's public-fn `ptr` fence (CE5008) tests the same
  condition and exits earlier. That shadowing is now pinned by a test, so a missing CE5002
  is never read as a regression.

- **A negative array index compiled, and reached run time as a trap** (#450). `numbers[-1]`
  and `numbers.get(-1)` both passed the compiler and RE2020 caught them at run time, although
  the compiler held the answer the whole time. A `-1` parses as a unary negation over a
  literal and not as a literal, so the guard in front of the bounds check asked for the wrong
  node, never matched, and left the `index < 0` branch behind it as dead code -- in both
  indexing forms, written out twice. One reader now takes a literal index, negated or not, and
  one validator carries the pair of codes for every indexing form, so a third form cannot
  inherit half the rule. A negative index is **CE2056**, whose text says exactly that; past
  the end stays CE2012.

  CE2056 had never been emitted anywhere. The constant evaluator in #441 reached for CE2014
  instead, which is the CAST code, so a negative index in a constant rendered as
  `invalid cast from '<missing:source>' to '<missing:target>'`. Its test asserted only the
  code, which is what let a broken message through; the negative-index cases now assert the
  text as well.

- **A string constant that was not a bare literal reported an internal compiler error**
  (#441). The backend matched a string constant by the SHAPE of its expression, so
  `const string ALIAS = GREETING` -- a program the typecheck pass accepts -- registered no
  global at all, and every use of `ALIAS` came back as `CE0055: unknown variable or constant`,
  a diagnostic that asks the user to report a compiler bug. A string array holding one
  non-literal element failed the same way. Constant emission now routes on the VALUE the
  evaluator produced and never on the expression that produced it, which is the same fix #260
  made for a literal array, applied at the seam instead of one shape at a time. A string is
  still finished in the backend: its bytes need a module to live in, and the evaluator has
  none.

- **A constant divided and took a remainder differently than a body did** (#441). The
  evaluator used Python's floor division and floor modulo, so `-7 / 2` was `-4` in a constant
  and `-3` everywhere else, and `-7 % 2` was `1` against `-1`. Integer division now truncates
  toward zero and a remainder takes the sign of its dividend, as `sdiv` and `srem` do; a float
  remainder is `fmod`, as `frem` is.

- **A bitwise operator on a float crashed the compiler.** `a & b` on two `f64` values
  reached the backend, where LLVM has no such instruction, and a program a user wrote came
  back as `CE0000: internal compiler error ... instruction requires integer or integer
  vector operands`. The operand gate asked for a numeric type, and a float is one; the
  test beside it has said "bitwise operators only work with integer types" since the day
  it was written. `& | ^ ~ << >>` now require an integer on every operand and report
  **CE2004** otherwise -- including a float next to an integer, which used to report the
  width rule (CE2510) for an operand that has no width to reconcile. A float's bits are
  reached the way they always were, through `f64.to_bits()` and `from_bits()`.

- **An enum payload slot allocated inside a loop leaked stack until the function
  returned.** A `??` unwrap, an enum construction, a match that binds a payload, and
  `List.push`/`List.pop` each allocated their scratch slot at the point of use, so in a
  loop the `alloca` landed in the loop body. LLVM only releases an alloca at function
  return, so every iteration took another 16 or 32 bytes and a loop of a few hundred
  thousand walked off the stack guard page with SIGSEGV. Optimisation did not help.

  All seven sites now take the slot from `entry_alloca`, which puts it in the function
  entry block and reuses it. Reuse is safe because each slot is scratch: the payload is
  loaded back out before the expression finishes, and a reference binding deliberately
  points into the scrutinee rather than the copy.

  The regression test drives all four shapes past a million iterations
  (`tests/error_handling/stack/test_run_try_in_long_loop.sushi`). Found while building
  `compression/zlib`, whose encoder died above 53 KB of ordinary text.

- **The library documentation described a container that no longer exists.**
  `docs/library-format.md` disagreed with itself about the version -- the layout diagram
  said 2 and two other places said 3 -- and its manifest schema presented
  `is_generic`/`type_params` on the concrete declaration lists as if they vary, when the
  producer filters every generic out of those lists. `docs/libraries.md` still cited
  `CW3505` for a platform mismatch, a warning deleted long ago; the error is CE3504, and a
  source library is not checked at all. Every bare `./sushic --lib` example in the docs was
  a command that now fails with CE3505, in six files.

- **The pre-push hook blocked every push** (#442). Git exports `GIT_DIR` to every hook
  it runs, and a push from a worktree always sets it. `GIT_DIR` overrides an explicit
  `-C`, so `read_commit` in the documentation-footer hook stopped answering about the
  directory it was given and answered about the repository being pushed. Its unit test
  hands it an empty directory and expects no commit, so the test failed, the hook's
  pytest gate failed with it, and `git push --no-verify` became the only way through --
  which turns off the whole gate, including the parts that work. CI never saw it, because
  there pytest is an ordinary step with no `GIT_*` set.

  `read_commit` now runs git with the `GIT_*` variables removed, so the argument it takes
  is the question it asks. `.githooks/pre-push` scrubs the same variables from every check
  it starts, which keeps the hook an honest mirror of CI.

- **A bitwise operator did not check operand width** (#438). `& | ^` accepted two
  different numeric types where `+` refuses them, and the backend then made the two sides
  agree without a word: it widened or **truncated** the right operand to the width of the
  left, and gave the result the type of the left operand. `low | wide`, a `u8` and a
  `u32`, compiled clean and printed 255, because 0x1FF had been cut to 0xFF. So did the
  byte-assembly shape behind every bit reader, checksum and length field:
  `accumulator := accumulator | (byte << 8)` never moved the accumulator, because the
  shift kept the `u8` width of `byte`.

  CE2510 is now one rule for every operator whose two operands must agree -- arithmetic,
  comparison and `& | ^` all reach `reject_mixed_numeric_operands`, which also retires the
  copy of the numeric-type list that the arithmetic path carried. `as` is the escape, as it
  is everywhere else. A shift is deliberately exempt: its right operand is a count, not a
  second value, so `value << places` with two widths stays legal and the result keeps the
  type of the left operand. `docs/language-reference.md` states the rule under Operators,
  where it was not written down before.

- **A shift count at or above the width of the value was accepted.** Found while fixing
  the mixed-width defect above, and it is what makes that report's own example wrong:
  `high << 8` on a `u8` moves every bit out of the type. LLVM answers such a shift with
  poison, so nothing was lost loudly -- the program printed whatever the optimiser left
  behind, 32 for `0x12 << 8` and 255 once an `|` was wrapped around it.

  A count the compiler can read -- a literal, a constant, an expression of them -- is now
  **CE2512**, which also covers a negative count. This is the rule Rust applies to the same
  program (`deny(arithmetic_overflow)`), and the escape is a cast on the VALUE:
  `(high as u32) << 8`. A computed count is still not checked, at compile time or at run
  time; `docs/language-reference.md` says so where it states the rule.


## [0.11.1] - 2026-08-22

The clean-up release after 0.11.0: the first two libraries written in Sushi itself, the
conditional-move leak, digit grouping in every numeric base, named semantic passes, and a
documentation site that says which version it documents -- with a highlighter that
knows the syntax it renders.

### Added
- **`use <encoding/msgpack>`: a MessagePack decoder written in Sushi** (R1.1). The first
  library written in the language itself, bundled as a Sushi-source stdlib module.
  `mp_decode` turns one buffer into a `MsgValue` tree, `mp_map_get` scans a map in wire
  order and clones the value out, and `mp_show` renders a value on one line (a float
  prints its IEEE-754 bits, so the rendering is deterministic). A decode error is a
  value, not a trap: `MpError` is one of Truncated, Unsupported, BadUtf8 or Trailing.
  Decode only. The single-byte tags dispatch through one integer `match`, which is why
  that feature landed first.
- **`use <toolchain/slib>`: a `.slib` metadata reader in Sushi.** Reads the 52-byte
  header and the msgpack metadata map of a version-3 library into a `MsgValue` tree
  (`slib_read_metadata`), plus the bitcode length (`slib_bitcode_size`); mirrors the
  Python `LibraryFormat.read_metadata_only` and never reads the bitcode. First stdlib
  source module that imports another source module.
- **The `toolchain/` directory: compiler tools written in Sushi.** `toolchain/src/`
  holds tool programs, `./toolchain/build.py` compiles them into the gitignored
  `toolchain/bin/`, and `sushic` delegates to a built tool when one exists. First tool:
  `slib-info`, which owns the full `--lib-info` report; `sushic --lib-info` runs it and
  returns its exit code, falling back to the built-in Python reader when no binary is
  present (always the case in a wheel install). `SUSHI_TOOLCHAIN=off` forces the
  fallback; `SUSHI_TOOLCHAIN_BIN` overrides the tool directory. A relative `--lib-info`
  path now resolves against the caller's directory through the `./sushic` wrapper.
- **Integer literal match arms** (#415). A `match` on an integer scrutinee dispatches on
  literal arms (`0xc0 ->`, `-1 ->`), compiled to one LLVM `switch`. A literal takes the
  scrutinee's type under the usual context-typing rule (a non-decimal literal is a bit
  pattern; out of range is CE2073). New diagnostics: CE2074 (an integer match needs a
  trailing `_` arm), CE2075 (duplicate literal arm by value), CE2076 (literal arm on an
  enum scrutinee, or enum-pattern arm on an integer scrutinee). Motivated by the tag
  dispatch in the first Sushi stdlib decoder.
- **Underscores in decimal and float literals**. `1_000_000`, `3.141_592` and
  `1_0.2_5e1_0` now group their digits like the prefixed bases always could. Decimal was
  the one base without them because the grammar imported Lark's stock `INT` (`/\d+/`)
  while hex, binary and octal were written by hand — an omission rather than a decision,
  and it landed on the common case. Underscores work in all three parts of a float, and
  in every position a literal appears: an array size and a match arm normalize through
  the same seam as an expression.

### Changed
- **The documentation site names its version.** The footer of every page on
  <https://bigwhale.github.io/sushi-lang> now reads
  `Sushi Lang <version> - documentation generated <date> - commit <sha>`. The site is
  built from main and carried no version, so a reader could not tell which compiler the
  pages describe. A MkDocs hook (`docs/hooks/version_footer.py`) reads the version from
  `pyproject.toml`, because the docs build has no `sushi_lang` install; a version it
  cannot read stops the build rather than publishing "unknown". The site stays
  unversioned: it always shows main, and the stamp says which main.
- **The semantic passes have names, not numbers.** The numbering had gone out of order:
  `Pass 1` ran after `Pass 1.5`, `1.6`, `1.7`, `1.75` and `1.8`, five steps carried no
  number at all, and `Pass 0` and `Phase 0` named one pass in two spellings. The fifteen
  passes are now `collect`, `externs`, `libraries`, `entrypoint`, `instantiate`,
  `monomorphize`, `resolve`, `finite-types`, `derive`, `shadowing` and `effects` over the
  whole program, then `scope`, `typecheck`, `lift` and `borrow` per unit. A name cannot go
  out of order. Four modules take their pass's name (`passes/resolve.py`,
  `finite_types.py`, `derive.py`, `lift.py`), and the `derive` pass drives both halves of
  its work — the clone loops moved out of `semantic_analyzer.py` into
  `derive.register_all_clones`. "Phase" is now reserved for the three sub-steps of the
  `typecheck` pass. The order lives in the `SemanticAnalyzer.check()` docstring;
  `docs/internals/semantic-passes.md` documents each pass and no longer states the wrong
  order. No compiler behaviour changed: no diagnostic, CLI flag or `EXPECT_*` directive
  ever carried a pass number.
- **The underscore rule is now one underscore between two digits**, checked for every
  base. The old terminals allowed any run of underscores in the interior, so `0xD__E`
  compiled and meant 222, and `0o7__7` meant 63; both are now **CE6006**. The rule
  matches Java, Python, Go, Ruby and JavaScript; Rust and Swift are the permissive
  camp Sushi has left. No literal in the test suite or the docs used a doubled
  underscore, so nothing valid changed meaning.
- **A malformed underscore reports what is wrong** (**CE6006**), instead of the token the
  lexer happened to stop on. The grammar's numeric terminals now match a permissive
  superset and placement is checked in one seam, because a terminal that simply fails to
  match reports the NEXT token: `0x_FF` used to be "unexpected token 'x_FF'" and `1_`
  was "unexpected token '_'". Each diagnostic carries the corrected spelling as a `help`.

### Removed
- **CW2409 (re-borrowing as `poke`) is retired.** Its only trigger was forwarding a whole
  `poke` parameter to another function -- the composition idiom the borrow model
  mandates. The call-site borrow dies with the statement, and CE2403, CE2407 and CE2411
  carry the safety, so the warning marked idiomatic code and guarded nothing. The first
  stdlib consumer fired it 40 times in every importing program. The clean-compile gate is
  `tests/memory/poke_forwarding/test_poke_pass_through_clean.sushi`.

### Fixed
- **A recursive type destroyed in two units no longer crashes the compiler.** The
  out-of-line destructor and clone caches held `ir.Function` handles across unit modules,
  so every fresh-cache multi-unit build that destroys such a type died with a CE0000 ICE
  ("use of undefined value @__sushi_dtor_..."). The caches are per unit now. A
  source-stdlib import makes every consumer a multi-unit build, so the first Sushi
  library met this first.
- **A conditional move no longer leaks the non-moving paths** (#414). A move inside an if
  arm, a match arm, or a loop body cancelled the owner's scope-exit free statically, so
  every path that skipped the move leaked the value — returning a local from one match
  arm leaked it on the other arm, and which arm leaked depended on emission order. The
  borrow pass now stamps conditionally moved owners, and the backend guards exactly their frees with
  a runtime drop flag; an unconditional move keeps the zero-cost static skip.
- **The documentation highlighter knows the current syntax again.** The Pygments lexer
  is not on the compiler's path, so the language moved under it in silence: its last real
  refresh targeted 0.10.0, and the version in its docstring was bumped twice over a lexer
  nobody had read. Three defects, all now fixed and gated:
  - **`@` had no rule at all.** The 0.11.0 generic form renders it unstyled, 1679 times
    across the corpus -- every `List@(i32)`, every `fn id@(T)`. It is punctuation now,
    like the parenthesis it always precedes.
  - **Two numeric shapes split in half.** An exponent-only float lexed as an integer
    followed by an identifier (`1e10` was `1` and `e10`), and the underscores 0.11.1
    added never reached the exponent (`1_0.2_5e1_0` lost its `_0`). The numeric rules
    mirror the grammar terminals now, in one place and with the same permissive
    superset.
  - **An interpolation was lexed by a second, smaller expression lexer** that knew
    neither `[`, `??` nor `==`, so `"{arr[0]}"` and `"{x.get(0)??}"` came out unstyled.
    `{...}` holds an ordinary expression and is lexed as one.

  `tests/unit/test_pygments_lexer.py` is what stops the next drift: every keyword in the
  grammar must be known to the lexer, every numeric shape the grammar accepts must lex as
  one token, and no character in the 1840-file corpus may reach the catch-all rule. The
  version claim is gone from the docstring -- it lied twice, and the gate is the honest
  version of it.

### Testing
- **The badges report both test suites, and report the run that gated the commit.** The
  `badges` job ran the corpus without `--enhanced`, so "Total Tests" counted compile-only
  checks while the real gate ran the binaries; pytest was not in the badges at all; and
  `|| true` plus `continue-on-error` plus a bare `json.load` left a crashed run showing
  its last green. Four badges become three -- `tests`, `sushi tests`, `python tests` --
  fed by the artifacts of the gating run. The badge goes red if either layer fails or if
  a job dies with nothing to say, and yellow when a leak check is skipped rather than run.
  The job publishes from main only, and the test jobs now run on a push to main as well,
  which closes a hole where a merge to main ran no test job at all. The renderer moved
  out of a heredoc into `.github/scripts/make_badges.py`, with tests.
- **Nine dead test files removed, and the eleven unexplained pytest skips resolved.** A
  measurement of the suite prompted this: pytest is 651 hand-written functions (the
  larger count is `parametrize` expansion), about 37 of 114 modules are the one-seam
  gates, and 14 assert-free candidates were all false positives. The `.sushi` corpus lost
  eight near-duplicates out of 1707 files, and `tests/types/test_types_f32.sushi` -- which
  claimed to verify an f32 declaration and had an empty body -- got a real one. Nine of
  the eleven skips were vacuous-case filters and left their `parametrize` lists. CI now
  fails when `sushic` is off PATH instead of skipping silently, the bug-dodging gate reads
  Python skip reasons and `xfail` markers as well as `.sushi` comments, and a `slow`
  marker gives `pytest -m "not slow"` in 67s against 162s.

## [0.11.0] - 2026-08-20

Three breaking language changes (generic syntax, ownership, borrow by default), the
reference-seam programme that made every borrow rule enforced in both directions, the Tier 0-6
remediation programme, the leak/RAII cluster, and the sixteen-round bug-clearing campaign that
emptied the ranked bug table. Everything below landed after the 0.10.0 release on 2026-07-07.

### Breaking
- **Borrow by default: a parameter mode is DECLARED, not derived from the callee** (#354;
  normative spec: `docs/design/borrow-model.md`). The compiler used to read the convention off the
  callee's implementation, so six kinds of callee gave four different answers -- and a stdlib call
  and its Sushi-source twin gave OPPOSITE ones. Four modes now, marked at both ends or at neither:

  | declaration | call site | who frees |
  |---|---|---|
  | `fn f(string x)` | `f(s)` | caller -- the default |
  | `fn f(nom string x)` | `f(nom s)` | **callee**; a later use of `s` is `CE2405` |
  | `fn f(peek string x)` | `f(peek s)` | caller; by pointer, read only |
  | `fn f(poke string x)` | `f(poke s)` | caller; by pointer, read/write |

  The user-visible consequences:
  - **`peek` and `poke` lose the `&`.** `&peek T` is now `peek T`, in every position: a parameter,
    a receiver (`poke self`), a call site, a `foreach` binding, a match binding, a function type.
    The three words are reserved
  - **A plain call no longer consumes.** `f(x)` leaves `x` yours, so the `.clone()` that used to be
    mandatory at ~20 call sites is not. Handing a value over is `nom` at both ends: missing or extra
    is **CE2427**
  - **The #298 method rule became the general rule.** Every parameter of every callable is a borrow
    unless it says `nom`, so writing through one is `CE2422` and consuming one is `CE2411` in a plain
    function too. `nom` works on a method parameter; `nom self` does not exist
  - **A pass-through needs `nom`.** `fn identity@(T)(nom T x) T` -- the mode is declared, so it does
    not vary per instantiation
  - **`nom` on an FFI extern parameter is CE2428.** A C callee never receives a Sushi value
  - **`.slib` format version 2 to 3.** A parameter record carries its `mode`, so a library can
    declare a borrow; an older container is rejected with `CE3509` rather than read with a guess
  - Fixed: the false `CE2405` at every stdlib call site that passed an owning value (#355); a method
    rebinding an owning parameter freed the caller's value (#356); `run()` leaked its collected argv
    (#357); an owning temporary passed to a method leaked (#358)
- **The ownership model is unified** (the ownership-refactor branch; normative spec:
  `docs/design/ownership-conventions.md`). One predicate answers "does this type own heap"
  (`typesys.owns_heap`), the compiler inserts NO implicit deep copy, and `.clone()` is the only
  deep copy in a program. The user-visible consequences:
  - **`string` moves.** A `string` owns heap and transfers at every ownership sink. A string bound
    directly from a literal owns no heap and still copies freely.
    *(Superseded within this release: `f(s)` does not move it -- `f(nom s)` does.)*
  - **A field read, an index and a container get-out are BORROWS.** Consuming one -- a call
    argument, a constructor field, a container insert, a return, a capture -- is `CE2411`; the
    escape is `.clone()`. `HashMap.get()` / `List.get()` / `arr[i]` no longer return independent
    copies
  - **A `let` binds; it does not take ownership.** A `let` bound from a borrow owns nothing, and
    mutating, freeing, rebinding or moving its owner while that binding is live is `CE2412`
  - **A by-value parameter is owned by the callee**, which frees it at scope exit -- uniformly for
    strings, function values, arrays, structs and enums, over direct, variadic and indirect calls.
    Extension/perk-method parameters stay borrows.
    *(Superseded within this release by borrow by default, above: the parameter is a borrow unless
    it says `nom`, and the method exception became the rule.)*
  - **A reference-typed `let` is rejected** (`let peek T x = ...`, `CE2413`, #252). It used to
    compile as an unchecked alias
- **Generic syntax is now `@(...)`, not `<...>`** (#235). This applies to every user-facing position:
  type references (`List@(i32)`, `Result@(T, E)`, `Maybe@(T)`, `Own@(T)`, `HashMap@(K, V)`), generic
  struct/enum declarations (`struct Pair@(T, U):`), generic function declarations
  (`fn id@(T)(T x) T:`), perk bounds (`@(T: Hashable + Displayable)`) and type packs
  (`@(...Ts: Display)`). Angle brackets no longer parse as a generic anywhere, so every existing
  source file must be updated. `use <io/stdio>` import paths are unchanged. Internally, interned type
  names and mangled symbols keep the `<...>` form; `display_type()` renders them back to `@(...)` for
  diagnostics, so user-visible output is consistent. The `>>`-splitting postlexer
  (`internals/generic_lexer.py`) is deleted -- ambiguity between `>>` and a nested generic close is
  gone by construction
- **Move semantics are now compositional** (#134). A value moves iff it *transitively* contains an
  owning resource -- a dynamic array, `List@(T)`, `Own@(T)`, a capturing closure, **or any
  struct/enum/fixed array holding one of those**. Previously only the three bare owning types moved
  and a struct wrapping one silently deep-copied, so refactoring `f(list)` into `f(Wrapper)` turned a
  move into a hidden O(n) copy. Passing such a value by value, rebinding it, placing it in a
  constructor field or array literal now moves it; reusing the source is `CE2405`. Plain-data and
  string-only composites still copy. The normative spec is `docs/design/move-semantics.md`
- **The test harness flag `--leaks` is removed** (#241). There are two modes now, not three:
  `--enhanced` runs everything and enforces everything it declares, and `--leaks-only` selects just
  the leak-annotated subset for CI's fast pre-gate. Both apply the identical check, so the flag that
  used to mean "also enforce leak assertions" no longer names anything. `--leaks` is an argparse
  error rather than a silent alias, and both harness parsers set `allow_abbrev=False` so it cannot
  resolve as a prefix of `--leaks-only` and quietly narrow a 1293-test run to 96
- **`arr.pop()` returns `Maybe@(T)`** (#377), matching `arr.get()` and `List@(T).pop()`. Popping
  an EMPTY array used to invent a value -- a real `0` for an `i32[]`, indistinguishable from a
  genuinely popped one -- and for any aggregate element (`string[]`, a struct with an owning
  field) it was a `CE0000` before the program ever ran. `a.pop() + 1` becomes `a.pop()?? + 1`;
  the popped element moves out inside the `Maybe.Some`

### Added
- **The read-only receiver gate: a write cannot reach through a borrow, and each kind says so**
  (`docs/design/borrowing.md` S5 -- six kinds, one dispatcher, each with its own code and its own
  escape). Every one of these shapes used to COMPILE CLEAN and lose the write, corrupt memory, or
  both:
  - **`CE2414`** (#253): a write through a `match`/`foreach` binding -- a mutating method
    (`p.push(9)`), a field assignment (`b.w := 99`), a `poke` borrow of it -- landed on a private
    deep copy and was silently discarded. A rebind of the binding itself (`n := 99`) stays legal
  - **`CE2408` widened from one shape to all of them** (#302, #307): only the rebind of a `peek`
    reference was checked; a mutating method (`a.push(9)`, `m.free()`), a field chain
    (`h.items.push(9)`), a field assignment and the `poke`-of-`peek` upgrade (#307) all wrote
    straight through to the caller's value
  - **`CE2421` / `CE2422`** (#326): a write through `self` or a by-value parameter -- a plain
    field write was silently lost, an owning field write was a double free plus a leak, and
    `self.items.push(9)` did not even reach codegen. Escapes: `poke self` / `poke T` / `nom T`
  - **`CE2426`** (#344): a write through a `let`-borrow binding (`let i32[] v = h.items` then
    `v.push(9)`, `v.destroy()`, `f(poke v)`). A `let`-borrow shares the owner's DATA, so a
    reallocating push freed the owner's buffer, which the owner then freed again
  - **`CE2429`** (#352 ruling, #407): a write through an UNBOUND CHAINED borrow. The rule: a
    write receiver must reach its root -- a name -- through member and index steps only;
    everything past a call boundary is a temporary copy. `o.get().items.push(9)` compiled,
    printed the old length, and the leak counters balanced; the indexed and field-assign faces
    were internal errors. Reads through the chain stay legal, and the chained indexed READ
    (`o.get().items[0]`, a `CE0000`) was fixed with the gate. A FRESH temporary
    (`from([1, 2]).push(9)`) is rejected by the same rule -- the statement discards the value.
    Escapes: clone-mutate-rebuild (`o := Own.alloc(h)`), or a nested `Own(poke inner)` binding
  - `CE2096` (a constant) completes the set; `tests/unit/test_readonly_receiver_matrix.py` pins
    every kind x shape cell
- **Reference bindings: `poke`/`peek` in a binding position mutates or reads IN PLACE**
  (#300, shipped in three phases; #327). `foreach(poke r in rows.iter())`, `Own(poke x)`, and --
  on the new aligned enum payload layout -- a top-level match binding `Shape.Poly(poke p)` bind a
  POINTER into the owner's storage, so `p.push(9)` and `r.n := 5` reach the owner with no copy.
  The binding registers as a reference, so the existing rules apply by construction: a `peek`
  write is `CE2408`, a consume is `CE2411`, and the OWNER IS FROZEN while the binding lives
  (`CE2412` -- mutating the container inside the loop, or rebinding the scrutinee under a payload
  borrow, Rust's E0506). Fences: `CE2423` (a ref binding over an iterable whose items have no
  address -- a range, `HashMap.entries()`), `CE2424` (a ref binding in a NESTED pattern).
  **`poke self`** (#327) is the receiver's spelling: `extend Counter bump(poke self) ~:` makes
  `self.n := v` and `self.items.push(9)` reach the CALLER's value; `peek self` states the
  read-only default; a receiver parameter anywhere else is `CE2425`. The enum layout that makes
  the match phase possible is `{i32 tag, [K x i64] data}` -- 8-aligned payload, one offset
  authority, the `align=1` access family retired
- **`CE2415`-`CE2420`** (#314-#319): a reference type in the six positions that have no semantics
  for one -- a struct field (was a `CE0017` ICE), an enum payload (was silently untracked), a
  RETURN type (**compiled into a dangling read of a dead frame**), a nested `peek peek T`, a
  generic type argument (was a `CE0022` ICE; no `Maybe`/`Result` exemption, because those two are
  exactly how a returned borrow would escape into a `match`), and an extension/perk target (was
  permanently unreachable dead code). Two positions promoted to tested support by the same batch:
  a reference parameter in a function type (`fn(peek i32) -> i32`) and the lambda satisfying it
  (`|peek i32 x|`)
- **Consuming a borrow is `CE2411` everywhere, references and method parameters included**
  (#301, #310, #311, #333, #338). A `peek`/`poke` parameter handed to an owning position
  (`eat(a)`, `return Result.Ok(a)`, `Holder(a)`, `l.push(s)`) used to be a `CE0129` ICE, and a
  `let` bound from one was a **compile-clean double free**. `return self` / `eat(self)` /
  `sink.push(self)` -- and the explicit-parameter twins -- were compile-clean double frees too
  (#333). The `string` carve-out was removed (#338): the view it let escape DANGLED when the
  receiver was a caller's local, so the `Display` idiom is `return self.clone()` -- and
  `string.clone()` itself copies UNCONDITIONALLY now (#340; it used to pass an `owned = 0` source
  through as an alias, so the escape dangled exactly like the thing it escaped). `.clone()` and
  `.hash()` work on a reference receiver (#312, #308 -- both were ICEs)
- **`CE0131`** (#398): `??` in an extension or perk method body. These bodies return a bare value
  (`CE2091`), so there is no error channel to propagate into. It used to be a misleading `CE2508`
  ("change the return type" -- which nothing can satisfy), and a TEMPLATE nobody instantiated was
  accepted silently; the check runs at Phase 0 collection, so it covers both. A lambda inside such
  a body keeps its own error channel, so `??` inside one is legal (#399)
- **The HashMap key gate actually fires** (#272): `CE2054` (unhashable key) was dead code -- an
  unhashable key compiled and crashed at codegen; a key type reaching a foreign `ptr` was a raw
  `NotImplementedError`, now `CE2055`. A perk `hash` implementation on the key type satisfies the
  gate, and the map probes with it
- **Indexed assignment: `arr[i] := value`** (#261). Both array kinds, every element type, and an
  array that is a struct field. The form parsed already and fell through the rebind dispatch to a
  `CE0022` internal error. The index is bounds-checked through the same helper the READ uses, so
  `RE2020` at run time and `CE2012` for a literal index past the end of a fixed array come for
  free. It is an ownership sink: the element the write replaces is freed first, an owned source is
  moved (`CE2405` on a later use), and a source that reads through an owner is `CE2411` with
  `.clone()` as the escape. The write must be able to reach the owner, so it is rejected through a
  `peek` parameter (`CE2408`), a `match`/`foreach` binding (`CE2414`), a method receiver without
  `poke self` (`CE2421`), an unmarked parameter (`CE2422`), a `let` binding that borrows from an
  owner (`CE2426`), and a constant (`CE2096`). `ConsumingUse` gains its twelfth member
- **`const string[N]` works** (#260). A `string`-element array constant emits a
  `[N x {i8*, i32, i8}]` global with `owned = 0` on every element, so RAII never frees a literal.
  Index, `.len()`, `.get()`, `.iter()`, `.hash()`, copying into a local and shadowing all work. It
  used to emit NO global at all -- the constant evaluator cannot build a string value, because one
  needs a global to point at and the evaluator has no module -- so every USE reported `CE0055`
  while the declaration reported nothing
- **`.clone()` is total over types.** Every struct and enum has an auto-derived clone, and so do a
  primitive (a plain copy), a `string`, a fixed array, a dynamic array, `List@(T)`, `Own@(T)`,
  `HashMap@(K, V)` and a function value (its heap environment is duplicated through the fat
  pointer's `clone_ptr` -- the fat pointer is four words now). `extend i32 clone()`,
  `extend f64 clone()` and `extend string clone()` are `CE2097`, on the same footing as
  `extend P clone()`. A method call on a function value is validated at all now: `g.shout()`
  reports `CE2008` instead of an internal error
- **Type-argument inference through a borrow.** `fn f@(T)(peek T x)` called as `f(peek v)` infers
  `T`; `peek Pair@(A, B)` binds both. The instantiation collector also infers from a `.clone()`
  argument (`f(p.clone())`). A generic that only reads its argument takes it as `peek T`
- **A rebind re-initializes its binding.** `f(s); s := "new"; println(s)` compiles and runs: the
  rebind clears the move and the scope exit frees the new value. The rebind also frees the OLD
  value where the binding still owned it (a string buffer and a closure environment were not freed
  at this position before). A rebind on one branch of an `if` stays conservative
- **An owned temporary that no binding names is freed.** `println(go().realise("err"))`, a `??`
  unwrap, a string-method result, and a string-typed interpolation part (`"{s.upper()}"`) are
  registered into the print-argument frame and freed after output through the owned-bit-guarded
  destructor
- **The ownership seam has a no-bypass gate.** Every transfer in the backend goes through
  `backend/ownership.py`; `tests/unit/test_consuming_use_coverage.py` fails the build when any
  other backend module touches a move-mark primitive. The clone/destroy pair per type kind lives in
  one handler table (`backend/lifecycle.py`), with a totality test asserting both halves per kind
- **`CE2097`: an extension method cannot shadow a built-in** (#239). Every layer of method
  resolution -- validation, inference and code generation -- picks a built-in *before* falling back
  to extension methods, so a colliding extension is compiled and then never called. It is not
  lower-priority, it is unreachable, which is the bar the `CW3505` note sets for erroring rather
  than warning. Verified silently dead beforehand: `extend P hash()` returned the compiler's FNV
  hash, `extend i32 to_str()` printed `5` rather than the user's string, `extend Box@(i32) hash()`
  was never even examined, and `extend string len()` collided at *link* time with the stdlib symbol
  (`CE0007`). The check covers every built-in family -- the compiler-derived `hash()`/`clone()`, the
  primitive and string methods, the array methods, and the `Result`/`Maybe`/`Own`/`List`/`HashMap`
  methods -- keying on whether a built-in genuinely exists for that exact (type, name) pair, never
  on the bare method name. Perk implementations are the sanctioned override and are unaffected:
  they take precedence at every layer by design. The rule is now written down in
  `docs/design/method-resolution.md`
- **`CE2096`: an in-place array method cannot target a constant** (#248). `.fill()` and `.reverse()`
  mutate their receiver, and a constant is emitted as a `global_constant` -- i.e. `.rodata` -- so a
  store through its address would be undefined behaviour rather than a diagnostic. Both used to be a
  `CE0000` ICE; a naive address fallback would have turned that into a runtime SIGBUS instead. Copy
  into a local and mutate that; a local *shadowing* a constant is freely mutable
- **`CE2095`: a type that contains itself by value has no finite size** (#240). Reported with the
  cycle chain (`A refers to B refers to A`), the way Rust reports `E0072` and Go reports "invalid
  recursive type". Indirection stays legal (`Own@(T)`, a dynamic `T[]`, `List@(T)`); a *fixed*
  `T[N]` is by value and is rejected. `struct S: S inner` used to escape as a bare `ValueError`
  rendered as `CE0000`, and the fixed-array and struct-to-enum-payload cycles compiled silently.
  A pure-enum cycle is still `CE2052`, which is more specific
- **Explicit call-site type arguments** (#235, closing #137 part 2): `identity@(i32)(5)`. All-or-
  nothing (`CE2062` on a partial list; a trailing pack relaxes the count), and only on a direct call
  to a named free function (`CE6102` on a method or indirect call). This makes a type parameter that
  appears only in return position expressible: `fn empty_list@(T)() List@(T):`
- **Auto-derived `.clone()` on every struct and enum** (#134) -- the explicit deep copy that
  complements move-by-value, registered in Pass 1.8 alongside the hash derivation
- **A syntax-error diagnostic family, `CE6001`-`CE6010`, `CE6101`, `CE6102`.** A syntax error used to
  bypass the reporter entirely: Lark's raw dump went to stderr with no code, no span and no caret,
  and it did not even set `has_errors`. Syntax errors now carry a code, a location, caret art, the
  expected-token list (capped at 8) and, where one applies, a `help`
- `CE4010`: generic perks (`perk Foo@(T):`) are rejected at declaration. They used to be silently
  accepted and inert. `CE4008`/`CE4009` were deleted as unreachable once this landed
- `CE0119` is now actually raised for a malformed `expand` -- a non-`Name` iterable, a name that is
  not the function's pack parameter (both previously unrolled silently to zero statements), or an
  `expand` in a function with no pack at all (previously a backend ICE)
- `CE2410`: moving `main`'s `string[] args` by value is now a compile error. It is a borrowed view of
  the process argv, so a by-value move made the callee free argv and double-free
- `CE0125`, `CE0121`, `CE0123`, `CE0127`, `CE2016`, `CE2062`, `CE6102` registered; `RE2023` replaces
  an unregistered `RE9999`
- Nested generic element types now render correctly in `List.debug()` / `HashMap.debug()` headers
  (#236) -- `List@(List@(i32)) {` rather than `List@(List<i32>) {`

### Changed
- **No Python traceback can reach a user.** Every failure -- grammar, AST builder, semantics, backend,
  stdlib -- renders as a structured diagnostic. `--traceback` opts one back in and appends it after
  the diagnostic. A compiler crash is a reported `CE0000` ICE and **exits 2**, not 1 (1 is the
  warnings code, which the test harness scored as success)
- The borrow checker now has an arm for every expression node (`CE0125` backstop). The previous
  silent fall-through meant *no borrow checking at all* for unhandled nodes -- the root cause of a
  bloom use-after-free that segfaulted, an unchecked range bound, and unchecked perk bodies
- `.destroy()` through a `poke` parameter now reaches the caller, via a cross-unit destroy-effect
  summary. This also fixed a pre-existing false positive where a `.destroy()` in one `if` arm leaked
  into its sibling arms
- The incremental cache key folds in a compiler-source digest, so editing any compiler `.py` is a
  cache miss by construction; `--clean-cache` is never needed for correctness. The cache is also safe
  for concurrent compilers
- `./nori` no longer swallows exit codes (#234) -- `./nori install nosuchpkg` exits 1
- **A user-declared struct is emitted as an LLVM identified type** (`%Point = type {i32, i32}`)
  rather than an anonymous literal (#257). Internal, with no surface-language effect, but it
  changes the shape of emitted IR and two rules follow from it. `IdentifiedStructType` is a
  *sibling* of `LiteralStructType`, not a subclass, so an `isinstance(..., ir.LiteralStructType)`
  check now means specifically "an anonymous fat pointer" -- use `ir.types.BaseStructType` for
  "any struct". That distinction also removes a latent shape collision: while user structs were
  literal, a `struct S: i32 a; i32 b; ptr p` matched `is_dynamic_array_type`'s `{i32, i32, T*}`
  sniff. The identified types live in a context owned by the codegen, not llvmlite's
  process-wide `global_context`, so two compilations in one process cannot inherit each other's
  layouts; symbol tables now also carry their module's type declarations so the `.slib` merge
  path re-emits them
- `destructors.py` no longer threads an explicit `builder` parameter through its 12 emitters
  (#257). The ambient `codegen.builder` is the backend's convention -- 1388 uses across 78 files
  -- and this was the only file imposing a second one, which is what let an out-of-line body
  emit into two functions at once
- **The borrow checker is a package** (`semantics/passes/borrow/`, the same shape as
  `passes/types/`), deduplicated and dispatched with `match`; diagnostic text verified identical
  across the split. Three diagnostics moved to where they can be answered: `CE2400` is emitted by
  Pass 1 now, so borrowing a constant reports "cannot borrow a non-local" instead of `CE1001`
  "undeclared identifier" about a constant declared two lines up (#330); `CE2401` (move of a value
  borrowed in the same statement, `both(poke s, s)`) is emitted at the consuming use, where it can
  actually fire -- its old site ran before the value walk, so the shape was a compile-clean double
  free (#329); `CE2402` is retired as unreachable. The scope pass gains the `CE0130` totality
  backstop (#245 -- `expand` statements used to get no scope analysis at all)
- **Method resolution has one answer site per question** (#269, #273, #296): the return-type
  tables and the family resolution order are unified, and LOCAL-WINS holds everywhere -- a local
  named after an enum used to resolve to the enum, so a method call on it was a `CE0017` ICE and
  a field read a `CE0034` ICE

### Fixed
- **Silent wrong data, five separate roots:**
  - an UNSIGNED value read through an index, a struct field, a container `get()`, a `??` result
    or `Maybe.realise()` printed as SIGNED -- `u8 255` printed `-1`, `u16 40000` printed `-25536`
    (#379)
  - `open(2)` was declared with a FIXED third parameter while the libc function is variadic, so
    on macOS arm64 the file mode was read off the stack: `copy()` gave its destination whatever
    happened to be there -- `0140` in one program, `0540` in another (#363). Variadic libc
    functions are declared `var_arg` now, and `tests/unit/test_stdlib_copy_mode.py` asserts the
    mode from pytest, because CI structurally cannot reach the macOS-arm64-only miscompile
  - two units with one lambda each collided on `__lambda_0`, so the second unit's closure
    **silently ran the first unit's body** (#402)
  - a moved-out flag leaked across an `if` join (`owns_no_heap`), so a program compiled, moved
    the value, and printed an EMPTY string with no diagnostic (#321)
  - the return-type stamp at a perk boundary was left unresolved, so
    `8.label().expect("boom")` on a `Maybe@(string)` printed an INTEGER (#387)
- **The leak cluster, second round.** Every stdlib call that passes a `string` where the callee
  wants a C string leaked the marshalled copy -- literals as much as owning values, 13 generated
  sites plus `open()`; marshalling now happens at the call site through one seam and is freed at
  scope exit (#291, #292). Every UNBOUND owning temporary leaked -- `give()??[0]`,
  `from([1, 2]).len()`, `a.clone()[0]`, `make_list()??.len()`, `make_own()??.get()` -- because
  the narrow registration router knew only the kinds whose storage is the alloca; one complete
  router now serves them all (#382). Three more owned temporaries had no owner: a string
  temporary outside a print argument (`go().realise("err")` -- unbounded in a loop), the print
  frame on a `??` early-exit path, and a bare enum constant initializer (`Shape.Empty`) (#293,
  #295, #289). A rebind through a `poke i32[]` parameter leaked the old buffer, and the `string`
  twin was a DOUBLE FREE, exit 133 (#304, #303; a `poke List@(i32)` parameter was a false
  `CE2002` "cannot assign List@(i32) to List@(i32)", #305)
- **The borrow checker's control-flow joins** (#287, #294, #321-#324): a rebind now clears every
  stale fact, so five false positives are gone -- `CE2406` after re-initializing a destroyed
  value, `CE2405` after a returning `if` arm and in a second `match` arm, `CE2412` in a sibling
  arm, `CE2411` after re-initialization -- and the one SILENT case (#321) is under "silent wrong
  data" above. A match/foreach binding shadowing an outer local gets its own scope (#337 -- a
  false `CE2411` after the match; #341 -- the item's type leaked out of the loop, a false
  `CE2006`)
- **Function types carry their modes, in both directions.** The `poke` -> `peek` coercion used to
  travel INTO a stored function type, so `let fn(peek i32) -> i32 g = poker` accepted a `poke`
  callee and `g(peek n)` wrote through a read-only borrow with no diagnostic (#335) -- borrow
  modes in a function type are invariant now (`CE2002` both ways). And `nom` was dropped by 8 of
  the 9 places that build a `FunctionType` and compared by none, so a `nom` in a fn-type
  annotation was a false `CE2427`, and dropping the marker to satisfy it was a compile-clean
  DOUBLE FREE (#368); every rebuild preserves the modes and `types_compatible` compares them.
  An indirect call through a fn-typed STRUCT FIELD skipped borrow registration entirely, so a
  program that is `CE2407` through a fn-typed local compiled clean and READ FREED MEMORY (#365)
- **A method call on a chained or indexed receiver works** (#285, #286, #284, #288, #265): a
  field or method chained onto `.realise(x)` was a `CE0000`; `rows[0].hash()` /
  `rows[0].clone()` on a struct or enum element was a `CE0019`; a dynamic array of a user
  struct/enum could not even be FILLED (`arr.push(P(3))` -- a false "expected P, got P"), and
  `Row[]` where `Row` has an `i32[]` field now hashes -- the old `CE0052` rejection was this bug
  wearing a different code; a fn-typed rebind was a false `CE2002`; and a static constructor as
  a PLAIN enum-constructor argument (`Boxed.Wrap(Own.alloc(h))`) was a false `CE0055`
- **A dynamic array has ONE value convention** (#281, #283, and two unfiled twins): `emit_expr`
  yields the `{len, cap, data}` descriptor BY VALUE everywhere, so `Own.alloc(from([1, 2, 3]))`
  and `l.push(from([1, 2]))` no longer die with `cannot store {i32,i32,i32*}* ...`, and
  `o.get().len()` / `o.get()[0]` work. Separately, `List@(i32[])` and `HashMap@(K, i32[])`
  failed with `CE0124` because each container hand-rolled its element-type reader and none had
  an array case -- one shared reader now (`docs/design/array-representation.md` is the spec)
- **An array literal's elements are context-typed in every position that has an element type**
  (#378): a wide literal in a `let`/`const`/`from([...])` was a false `CE2070`, and a plain
  `[1, 2]` against an `i64[2]` field, argument, return or payload was a false
  `CE2028`/`CE2006`/`CE2031`. A NEGATED literal was re-checked against i32 after it was stamped,
  so `let i64 a = -4294967303` was a false `CE2070` with no array anywhere
- **Extension and perk boundaries propagate the declared type** (#387): a generic constructor at
  a method's RETURN (`extend i32 halved() Maybe@(i32)`) or as a method-call ARGUMENT
  (`x.method(Maybe.Some(41))`) was a `CE2008`/`CE0113`/`CE0017`/`CE0055` depending on the type;
  one resolve-then-propagate seam answers all of them
- **Generic-target extensions are read like any other** (#389, #391, #394, #392): an extension
  spelled `extend Box@(T)` or `extend List@(i32)` was never COLLECTED from (false `CE2001`);
  every instantiation SHARED the template's body, so `extend Box@(T) unwrap() T` at
  `Box@(string)` was a compile-clean DOUBLE FREE (it is `CE2411` there now, and legal at
  `Box@(i32)`); an extension on a generic ENUM target compiled into dead code (every call a
  false `CE2008`); and a generic call typed through `self` in such a body was a `CE2061` --
  the monomorphized copies feed a second collection round now
- **Lambdas lift everywhere a statement lives** (#400, #403, #404, #399): a lambda declared in
  an `if`/`elif` arm was a `CE0055` ICE; `|i32 x| may_fail(x)??` drew a false `CW2511`/`CE2511`
  (a lambda has its own error channel); a multi-unit program whose generic extension and
  instantiation sat in different units failed at LINK with a duplicate symbol (`weak_odr` now);
  and a lambda in an extension/perk body lifts too
- **A concrete type argument in an extension target now CONSTRAINS** (#393; the ruling and the
  mechanism are in `docs/design/method-resolution.md`). `extend Box@(i32)` applies to
  `Box@(i32)` and to nothing else, exactly as a perk implementation on the same target already
  did. The argument was stored as a type-parameter NAME and substituted positionally, so the
  method registered for every instantiation of the base type: `extend Box@(i32) tag()` answered
  a `Box@(string)` receiver and printed its answer, and the same declaration with
  `self.value * 2` in the body was a `CE0000` on that receiver. Four consequences, all user
  visible:
  - **Two fully-concrete targets are two methods**, so one method name serves both.
    `extend Box@(i32) tag()` beside `extend Box@(string) tag()` used to be a false `CE0101`,
    because both were one template for a single `(base name, method)` slot
  - **A template plus a concrete target for one name is rejected** (`CE0101`, relational, either
    declaration order). Sushi has no specialization: an unreachable declaration is a diagnostic
    (`CE2097`'s rule), and under most-specific-wins whether the template's body is dead code
    would depend on which instantiations exist elsewhere in the program. The escape is a perk
    implementation on the concrete target
  - **A partially-concrete target is `CE2098`** (`extend Pair@(i32, U)`). It used to compile and
    run. Rejecting it is what keeps a specificity-ordering rule from ever being needed
  - **The diagnostic names the target it turns on** -- `extension method 'tag' for 'Box@(i32)'`,
    where it used to elide the arguments as `Box@(...)`
  Found by the test batch, and fixed here too: **a perk implementation on a concrete generic
  target with more than one argument never registered.** The perk-impl table built the target
  name itself and joined the arguments on `','` where the interned type name joins on `', '`, so
  `extend Pair@(i32, string) with Tagger` registered under `Pair<i32,string>` while the receiver
  resolved to `Pair<i32, string>` -- every call was `CE2008 undefined function`. The
  single-argument case matched by coincidence, which is what hid it
- **A dynamic array or a `List@(T)` of `i16`/`i64`/`u16`/`u64` was a `CE0079` internal error**
  (#375). `get_element_size_constant` was a hand-written chain of `==` comparisons naming i32,
  i8, pointer, float, double and struct -- and no other integer width -- so eight ordinary
  instantiations crashed the compiler. Its sibling `calculate_llvm_type_size` read the width off
  `ir.IntType` all along, which is what made "the two disagree" the shape of the bug rather than
  "the language does not support i64". It reads the width now.
  `tests/unit/test_element_size_is_total.py` asserts the two agree over every integer width, so
  the table cannot go partial again. `HashMap@(K, V)` was never affected: its entry type is a
  struct, which takes the `getelementptr(null, 1)` arm
- **A missing `main()` is a real diagnostic** (#251, `CE3007`), and it names `--lib`. The missing
  `_main` used to reach the LINKER, so the user got raw `cc` stderr followed by a `CE0000`
  "this is a bug in the Sushi compiler" -- for a condition in their own program. A failing link is
  `CE3008` now, carrying the linker's own output as notes; `subprocess.run(..., check=True)` let
  `CalledProcessError` escape to the top-level guard, which renders any uncaught exception as an
  ICE. One helper serves both link paths, and the intermediate `.o` is removed on the failure path
  as well as the success path
- **Five `CE0096` diagnostics printed their own placeholders** (#270) -- an f-string body passed
  without the `f` prefix, so `{receiver.id}.{method}()` reached the user literally. One is
  reachable from ordinary source: a stdio method call without `use <io/stdio>`
- **`CE0004` had two unrelated meanings** (#271). It is registered as `duplicate struct '{name}'`
  and was also emitted as a file-utility ARITY error, with `func`/`expected`/`got` -- so the
  message was about the wrong thing and none of the three parameters matched the template. The
  arity case is `CE2009`, the code the auto-derived builtins already use
- **A foreach binding shadowing an outer struct of a different type read the wrong field index**
  (#279, silent wrong data) -- the backend now resolves the binding's type through the scope-aware
  resolver, which also fixes `CE0056` on a field of a List/array iterator binding (#263) and on a
  nested `Own(inner)` pattern binding with a struct payload (#258)
- **Re-wrapping an enum-payload match binding into a constructor no longer double-frees** (#277,
  was exit 133 with no diagnostic). A match binding is a borrow and a constructor field takes
  ownership, so the shape is rejected at compile time with `CE2411`; the escape is
  `Shape.Poly(p.clone())`
- **A by-value `string` or `fn(...)` parameter no longer leaks.** The seam marked the caller's
  value moved while the callee freed nothing, so a heap string passed by value (`take(b)`) and a
  closure passed by value (`apply(make(2)??, 6)`) leaked their buffers. The callee owns and frees
  them now (see Breaking)
- **Built-in method calls have a return type again** (#239). A method call whose type Pass 2 cannot
  infer is not reported -- `validate_assignment_compatibility` treats an unknown value type as
  nothing to check -- so the annotation goes unvalidated and the mismatch surfaces in the backend as
  a `CE0017` internal error. Three families were affected, for three different reasons:
    - **struct/enum `.hash()` / `.clone()`**: `METHOD_TYPE_REGISTRY` had no checker for a plain
      `StructType`/`EnumType` at all. The new one is registered *ahead* of the container checkers
      (`check_result_methods` claims any method name on a `Result@(T, E)` receiver, and those carry
      an auto-derived `clone()`), and declines `Own@(T)`/`List@(T)`/`HashMap@(K, V)`, whose
      registered hash Pass 2 validation rejects anyway.
    - **every primitive method**: a regression. The inference path read the builtin-method registry,
      which the *backend* populates at import time -- and the pipeline imports codegen lazily, after
      semantic analysis, so it is empty when Pass 2 asks. It had been dead since the commit that
      removed the last `semantics -> backend` import, which was also what had accidentally been
      warming that registry. `let u32 b = f64val.to_bits()` did not even ICE: it compiled and
      silently truncated the 64-bit IEEE-754 pattern to 32 bits. Primitive return types now come
      from a semantics-side table keyed per (method, receiver), since `to_bits` is
      receiver-dependent (`f32 -> u32`, `f64 -> u64`).
    - **`string.to_str()` / `string.hash()`**: `infer_method_type` is first-match-wins, and the
      string checker matched on the receiver type alone -- so it claimed every method name on a
      `string`, and a claim whose inferrer then returns `None` ends the chain instead of falling
      through. Each checker now claims only what it can actually type.

  A perk implementation of the same name still wins at inference, matching validation and dispatch
- **`EXPECT_NO_LEAKS` is enforced by `--enhanced`** (#241). It was gated on the separate `--leaks`
  flag, so a plain `--enhanced` run skipped all 96 leak-annotated tests in silence -- no check, no
  skip notice, nothing in the summary -- and a leaking program passed the full suite. The issue
  described the `--enhanced` check as "weaker" than `--leaks-only`; it did not run at all. The
  interposer is now built on every enhanced run (gating that too meant a fresh checkout degraded to
  96 "not built" skips), a skipped assertion is recorded against the **test** name rather than the
  throwaway temp-binary name that maps back to nothing, and the summary groups skips by reason
  instead of claiming a hardcoded "no leak checker on `<platform>`" that was already wrong for the
  timeout and no-report cases. The two `test_warn_shadow_owning_*` tests, which `--enhanced` did not
  execute at all, now run. Cost of the new default: the full suite goes from 343s to 400s
- **A self-referential container field can be constructed, not just declared** (#257).
  `struct Tree: List@(Tree) kids` declared since #240 but `Tree(1, List.new())` was a `CE0000`
  ICE, so half the feature was unreachable; the `Node[]` spelling failed the same way with a
  different message. The issue described one root cause; there were **two**, and the `List`
  shape hit both. (1) A struct's LLVM type tied its recursive knot by caching an empty
  `LiteralStructType([])` placeholder and re-caching a new literal after walking the fields —
  which cannot work, because a literal struct type is a structural *value* with nothing to
  fill in, so the `{}` the walk had already embedded stayed empty forever and every element
  GEP through it had stride **zero**. User structs are now LLVM *identified* types
  (`%Tree = type {i32, {i32, i32, %Tree*}}`), whose `set_body` fills in place so the knot
  ties itself; `List`/`HashMap`/`Own`/`Entry` stay literal, being anonymous layout
  descriptors whose shape other backend code builds directly. (2) An out-of-line destructor
  body is emitted lazily, mid-emission of another function, and threaded its own builder
  down while the helpers it calls reach for the *ambient* `codegen.builder`/`codegen.func` —
  so the element loop landed in the caller's function and the element-destructor call in the
  destructor, referencing a value defined elsewhere. Both are now swapped for the duration,
  as the clone twin and the closure env destructor already did. Fixing either alone was
  unsafe: (2) without (1) would have replaced a compile error with a **double-free**
- **An array constant is usable directly** (#248): `PRIMES[0]`, `.len()`, `.get()`, `.iter()` and
  `.hash()` all worked only after copying the constant into a local, and were otherwise
  `CE0000: KeyError: 'undefined name: PRIMES'` -- so the documented feature was half-usable. The
  cause was an asymmetry inside name resolution, not a missing fallback: `emit_name` had always known
  about global constants, but every path that needed a name's *address* went straight to the
  local-alloca table. Copying into a local worked precisely because it is the only shape that never
  asks for the address. `backend/expressions/names.py` now owns both halves (`resolve_name_slot`,
  `resolve_name_semantic_type`) and six sites route through them, so the two cannot drift again.
  Reads compile to a `getelementptr` on the read-only global -- no copy, so constants stay zero-cost.
  Fixed with it: constants **shadowed locals** rather than the reverse, so with a `const i32[3] X` in
  scope a local `let i32[4] X` read the constant (`CE0017`) while `X[0]` read the local, and the
  scope pass never marked the shadowing local used (a bogus `CW1001`). Mutating a constant is now
  `CE2096`. Two adjacent gaps found here are filed separately: `const string[N]` never emits its
  global at all (#260), and `arr[i] := v` is unsupported and ICEs for locals too (#261)
- **`find_local_slot` fails as a diagnostic, not a bare `KeyError`** (#248). A name the semantic
  passes already accepted must resolve in the backend; when it did not, the `KeyError` reached the
  top-level guard as an anonymous `CE0000`, which is how five separate missed sites all reported
  identically with nothing naming the gap. It now raises the registered `CE0055`, with
  `try_find_local_slot` as the interrogative form for callers that have a real answer for "not a
  local" -- a global constant, a function reference, a struct field. `tests/unit/`
  `test_find_local_slot_invariant.py` gates it from the AST, so a future caller cannot quietly opt
  back out by catching `KeyError`
- **`Own@(T).get()` no longer hands a borrow to an ownership sink** (#256). `get()` is a
  dereference: it loads the payload uncopied, so the value is a view of storage the `Own` still
  frees. Nothing downstream knew that, so every by-value sink took a second owner of the same heap
  and both freed it. `let Holder back = o.get()`, `takes(o.get())` and `return Result.Ok(o.get())`
  died with SIGTRAP, and `match o.get():` on an owning enum freed the container's payload outright
  via the unowned-temporary path (#159). A `.get()` source is now treated exactly like a
  `MemberAccess` field read — cloned at the sink, per `docs/design/move-semantics.md` §3 (*"ownership
  sinks move; reads from a continuing owner copy"*) — which also retires the narrow name-based
  exception #106 added at the `let` sink, the only sink that knew. Reading straight through a
  get-out (`o.get().field`) takes no owner and still copies nothing. Deep-copying a payload that
  owns nothing is free, so only owning payloads pay; nested `Own@(Own@(T))` now costs one copy per
  level at a sink, the same residual copy §3.1 already accepts for `s.field` and on the same terms
- **Recursive structs compile** (#240). A struct referring to itself through any indirection --
  `Own@(Node)`, `Maybe@(Own@(Node))`, `List@(Node)`, `Node[]` -- was a `CE0000` `RecursionError` on
  *declaration alone*, which broke `docs/examples/20-ownership.sushi` and the linked-list pattern it
  teaches. The root cause was type identity, not the wrapper chain the issue named: `StructType` and
  `EnumType` hashed on the name but compared on the fields/variants, so two instances of one type
  resolved to different depths hash-matched and compared *unequal*, and resolution deep-walked struct
  fields to make structural equality agree. That walk cannot terminate on a cyclic type. The same
  defect was visible with no recursion at all (`CE2002: cannot assign Own@(T) to Own@(T)`) and is the
  root of the `CE0126` class. Named types are now identified nominally, as in Go and Rust; see
  `docs/design/type-identity.md`
- Reading a field straight off `Own@(T).get()` (`o.get().x`) reported `CE0069`, an internal-error
  code, for ordinary user code. Binding the value first already worked
- A generic-enum constructor nested inside a concrete-struct constructor
  (`Own.alloc(Holder(0, Maybe.None()))`) was never given its type and reached the backend as
  `CE0113`. Type propagation into constructor arguments only recursed for *generic* structs, so how
  deep it went depended on whether an intermediate type happened to be generic
- Declaration spans were dropped when per-unit symbol tables merged into the global table, silently
  demoting every diagnostic reported against it to tier 1 (no file:line:col, no caret)
- A cluster of RAII/leak defects: `Result@(T, E)` payloads are destroyed at scope exit (a
  `Result@(string, E)` used to leak unless the caller unwrapped it), `Own@(T)`/`List@(T)` nested
  inside a composite are destroyed, fixed-size arrays and their elements are destroyed, `getcwd`'s
  buffer is freed, an owning element is deep-copied out of `List.get()`, a `Result`/`Maybe` method
  receiver is emitted exactly once, and unowned `Result`/`Maybe` temporaries are destroyed
- `HashMap` and `List`/`Own` struct-field clone and destroy (#181)
- `Result@(T, E)` is now an ordinary interned `EnumType` like `Maybe@(T)`; the separate `ResultType`
  dataclass is deleted. The dual representation was the root of a Result-payload leak and of two
  identically-printing Results comparing unequal
- The `HashMap` probe loops are bounded, fixing a segfault on a destroyed map
- Indexing a fixed-size array through a struct field; inferring a struct type through an array index;
  loading a dynamic-array default in `realise()`; inferring `Own@(T)` method return types so an
  inline `match Own.get()` resolves; propagating the element type to an inline `Own.alloc` constructor
  argument
- Extension and perk-impl return expressions are now type-checked
- Quality gates: `ruff` clean at F,E,W,B and `mypy` clean over `internals`/`compiler`/`packager`/
  `sushi_stdlib`, both blocking in CI, alongside the cross-platform malloc-interposer leak gate
- **The wheel ships the stdlib bitcode** (#413; fixed after the section above was written,
  and shipped in the published 0.11.0). hatchling honours `.gitignore` when it selects
  files and the prebuilt bitcode has been gitignored since #155, so the first wheel carried
  zero `.bc` files and every stdlib-using program died at the missing-module rebuild.
  `tool.hatch.build.targets.wheel.artifacts` is the designed escape for a VCS-ignored build
  output. Two more holes went with it: `upload-artifact` drops hidden files, so the
  `.build_fingerprint` marker never reached the wheel; and excluding `sushi_stdlib/build.py`
  made an installed wheel compute a digest its own marker did not match, because the
  freshness fingerprint hashes that file. The release workflow now asserts all three on the
  built wheel before it publishes, and prints the inventory when one is missing.

### Testing
- The leak gate tells the truth in three more cases: a freed address reallocated by an untracked
  library no longer reports a false `DOUBLE_FREE` (#359); a colliding insert no longer consumes a
  LIVE tombstone, which made a genuine double free go unreported (#371); and an unsupported
  platform is a recorded skip instead of "interposer not built" (#275)
- The test harness itself is ruff-linted, in CI and pre-push -- 38 real harness defects fixed
  (#274) -- and a misconfigured stdlib registry entry raises loudly instead of registering
  nothing (#247)
- `tests/unit/test_path_references_exist.py`: every backtick-quoted `dir/file.ext` reference in
  documentation and comments must name a real file (#366). Its first run found 13 stale
  references beyond the 7 the issue filed

### Documentation
- **`tests/docs_sweep.py`** (#297): compiles every self-contained ```sushi block under `docs/`
  with a four-way outcome -- pass (exit 0 or 1; a warning is not a failure), expected-error
  (`<!-- docs-sweep: error CExxxx -->` on the line above the fence), skip
  (`<!-- docs-sweep: skip (reason) -->`), fail. A tool to run BY HAND, periodically --
  deliberately not a CI job. At close: 249 candidate blocks, 0 failing; the 13 genuinely
  drifting blocks were FIXED, not marked
- The seven stale references to the deleted `semantics/passes/borrow.py` are fixed, preferring
  symbol names over line numbers (#366)
- The borrow model is documented as one table across the language reference, the guide, the
  memory-management page and the tutorial; `docs/design/borrow-model.md` is the normative spec
  for how a value crosses a call boundary, and `docs/design/borrowing.md` for the reference
  mechanisms (#360)

## [0.10.0] - 2026-07-07

The closures release. Tier 1 closures are complete, and three follow-ups land together: an
opt-in combinators module, call-through arbitrary function values, and generic-function
references. This supersedes the 0.9.0 note that there were "no closures in v1".

### Added
- `use <collections/iter>`: `map`, `filter`, `fold`, and `compose` over `List<T>`, as ordinary
  generic free functions. This is the first Sushi-source standard-library module -- a bundled
  `.sushi` file merged as a compilation unit and monomorphized through the normal generic
  pipeline (no bitcode; nothing emitted unless a combinator is instantiated). Element types are
  copy/primitive for now
- Call-through arbitrary expressions that evaluate to a function value (T2.4): a captured closure
  called in a lambda body (`env.f(x)`, the basis for `compose` and capturing-and-calling a
  closure), a fn-typed struct field called directly as `obj.handler()` (a same-named method
  wins), and call-through a `List` get-out or parenthesized expression (`fns.get(0)??(x)`,
  `(e)()`)
- Generic-function references (T2.3): a generic function may be referenced as a value when an
  explicit function type is present, e.g. `let fn(i32) -> i32 g = identity` with
  `fn identity<T>(T x) T`. Passing a generic-fn reference into a higher-order function works via
  such a typed binding

### Changed
- The `??` operator now unwraps a first-class function call's result and a `Maybe` `Some`
  payload during type inference, so a function value called in a lambda body infers its return
  type. As a result, two type mismatches that previously surfaced as a backend cast failure now
  report the precise front-end `CE2002` at the assignment
- `CE2094` no longer rejects capturing-and-calling a closure value; an owning closure value is
  move-captured into the environment like `List`/`Own`
- `CE2093` is lifted for a generic-fn reference that has an explicit expected function type; a
  bare reference with no expected type (an argument position without a typed binding) still
  reports `CE2093`

## [0.9.1] - 2026-07-06

A maintenance release: a new safe process-spawn stdlib primitive (the last self-hosting
linchpin), the correctness fixes surfaced by the 0.9.0 stdout-correctness test baseline, and a
documentation-link fix.

### Added
- Safe process spawning in `sys/process`: `run(string cmd, string[] args) -> Result<ProcessOutput,
  ProcessError>`. Built on `posix_spawnp` — argv, no shell — it returns the child's exit code plus
  separately-captured stdout/stderr in `ProcessOutput`; a non-zero exit is `Ok`, and `SpawnFailed`
  / `SignalReceived` are the `ProcessError` variants. `run("clang", [...])` is verified end to end,
  completing the text-IR -> toolchain path for self-hosting

### Fixed
- `Maybe<T>` / `Result<T, E>` `.realise(default)` crashed for a struct payload with a nested
  aggregate field
- Nested `Own<Own<T>>` was double-freed on cleanup; ownership is now coherent through the nested box
- `HashMap.debug()` corrupted string keys/values in its output

### Testing
- Established a stdout-correctness test baseline: the runtime-output ratchet is at gap 0
  (`BASELINE = 0`) with a quarantine guard, so a wrong codegen result can no longer pass the suite
  silently
- Fixed stale hard-coded paths in the file-I/O tests

### Documentation
- README documentation links now point to the rendered docs site

## [0.9.0] - 2026-07-04

A maintenance release: one new language feature — context-typed numeric literals — plus a
batch of correctness fixes surfaced by a documentation audit, a diagnostic-wording change, and
CI/docs improvements.

### Added
- Context-typed numeric literals (Rust/Go untyped-constant model): a bare numeric literal takes
  its type from its expected/context type — annotation, const, function argument/return, struct
  field, array element, and binary-op operand (`a + 1` where `a: u8` types the `1` as `u8`) —
  range-checked at compile time. A literal with no numeric context still defaults to `i32`/`f64`.
  This is literal *typing*, not value coercion (an already-typed value still needs `as`)
  - New diagnostic **CE2073** (context-typed literal out of range for its target type); **CE2070**
    retained for a context-free literal that overflows its `i32` default

### Changed
- Diagnostic wording: user-facing "rebind" renamed to "reassign" — **CE1002** now reads
  "assignment to undeclared variable" and **CE2401** "cannot move/reassign"

### Fixed
- One-argument `Result<T>` annotation was rejected (CE2001); it now normalizes to
  `Result<T, StdError>` at parse time
- `foreach` / `.iter()` over a borrowed dynamic array (`peek`/`poke T[]`) failed with CE0042
- Higher-order function call-through (`??` through an `fn`-typed parameter) was order-dependent
  (CE0055 depending on definition order)
- `Maybe<enum>.realise(default)` with a non-primitive (enum) payload misrouted to the `Result`
  handler and failed to lower (CE0017)
- Loop-body locals abandoned via `break`/`continue` were never freed (RAII leak); a bounded
  loop-exit cleanup now frees them exactly once without double-freeing locals that outlive the loop
- Inline `match` / `??` on stdlib builtins (`getcwd`, `getenv`, `.find_last`) failed with CE0055 /
  "undefined name" unless the call was first bound to a `let`

### Documentation
- Language reference updated for context-typed numeric literals
- `sys/process` guide examples corrected (all 20 examples compile with zero errors/warnings)
- First-class-functions design note reconciled with the implemented compiler behavior
- CI: docs site auto-deploys after the Tests workflow passes on `main`

## [0.8.0] - 2026-06-11

A milestone release centered on self-hosting enablers: a foreign function interface, three
forms of variadic functions, generic instantiation across library boundaries, and first-class
functions — alongside a hardened test suite, asserted diagnostics, and a large batch of
correctness fixes.

### Added
- Foreign Function Interface (FFI) for calling C
  - `unsafe external "C" as <ns> [because "..."]:` blocks with namespaced call sites
    (`libc.strlen(s)`)
  - Opaque, unmanaged foreign `ptr` type (LLVM `i8*`), exempt from borrow checking and RAII
  - `string` arguments auto-marshalled to C `char*` and freed at scope exit (no leak)
  - Variadic externs: a bare trailing `...` binds libc varargs (`printf` family)
  - Full `ptr` quarantine — unit gate, and rejection of `ptr` in public APIs, operators, method
    calls, and generic arguments; diagnostics CW5001 and CE5001–CE5012
- Variadic functions in three deliberately separate forms
  - Native homogeneous `...T` — trailing arguments collected into an owned `T[]`
  - Parameter packs `...Ts` with a compile-time-unrolled `expand` block (heterogeneous, generic,
    zero-cost) — including perk-constrained packs
  - Parameter packs ship and monomorphize across `.slib` library boundaries
  - Diagnostics CE0114–CE0119, CE2090, CE2091
- Generic instantiation across `.slib` library boundaries
  - Generic functions, generic structs/enums, and variadic-generic packs ship as source templates
    and monomorphize at the consumer
  - Concrete perk implementations ship and register at the consumer
  - Export closure of library-private symbols an exported generic transitively references;
    CE5006 narrowed and CE5007 added
- First-class functions
  - Function types `fn(P...) -> T [| E]` and function values: reference a top-level function,
    store it in a variable/struct field/`List`, pass it, and call through it (no closures in v1)
  - Diagnostics CE2092 (call-through mismatch) and CE2093 (illegal/generic function reference)
- Testing and tooling
  - `pytest` layer for compiler internals (fingerprints, cache, semantic errors)
  - Dedicated enum suite and end-to-end incremental-compilation/cache tests
  - Performance regression harness (report mode)
  - Error codes asserted on the compilation path, with a broad diagnostics backfill

### Changed
- Runtime validation is the default for tests; CI runs the enhanced runner on a macOS + Linux
  matrix
- Internal refactors: `stdlib.py` split into a `stdlib/` package; `TypeValidator` decomposed into
  focused collaborator modules
- CI: GitHub Actions bumped to Node 24 runtimes; tests run on `merge_group`

### Fixed
- i64 width correctness across inference, printing, comparisons, and literals
- Unsigned `/` and `%` no longer emit signed `sdiv`/`srem`
- CE0021 crash on `Result<ptr>`; `ptr` is confined to its declaring unit (CE5008)
- Heap-owning struct value semantics (independent buffer per by-value argument)
- Local `List<T>` variables freed via RAII; dynamic-array RAII leak on the all-but-one return
  path
- `string[]` heap corruption from an element alloc-size mismatch
- `List<EnumType>.destroy()` internal compiler error
- `compute_unit_fingerprint` crash on enums
- `bool` string methods rendering as `0`/`1` in interpolation
- Direct `.hash()` on an enum value (CE0019 internal compiler error)

### Documentation
- Unified MkDocs site combining the guided tutorial and the reference, with Sushi syntax
  highlighting
- New guides and tutorial chapters for FFI / foreign pointers, variadic functions, and
  first-class functions
- Design notes for variadics and first-class functions
- CHANGELOG backfilled for 0.7.0 and 0.7.1

## [0.7.1] - 2026-03-21

### Added
- Glossary section in README

### Fixed
- Nori banner rendering

## [0.7.0] - 2026-03-21

### Added
- Nori package manager (`nori` CLI), shipped alongside the compiler
  - Commands: `init`, `build`, `install`, `list`, `info`, `remove`, `publish`, `search`, `status`, `login`, `help`
  - Packages install to `~/.sushi/bento/` with executable symlinks in `~/.sushi/bin/`
  - `.nori` archive format for distributable library packages
  - Compiler auto-discovers installed libraries without `SUSHI_LIB_PATH`
- Project-level dependency environments
  - Global versioned store (`~/.sushi/store/`) with per-project `.sushi_bento/` symlinks
  - Compiler resolves project-local dependencies before global packages
- Omakase package repository integration (omakase.lubica.net)
  - `nori login <api-key>` with credentials stored in `~/.sushi/credentials.toml`
  - `nori publish` uploads packages; `nori search` browses the registry; `nori status` reports login and published packages
  - Omakase API contract specification: authentication, users, groups, ownership, stats, and package storage layout

### Changed
- CI split into separate test and badges jobs

### Documentation
- New documentation: `docs/package-manager.md` for the Nori package manager

## [0.6.1] - 2026-02-22

### Added
- End-to-end release tests that validate wheels before publishing
  - Installs wheel into clean venv, compiles and runs 5 sushi programs
  - Tests basic compilation, stdlib linking, generics, HashMap, multi-file builds
  - Runs on both Linux and macOS in CI

### Changed
- Release workflow restructured: wheels are tested on both platforms before publishing to GitHub Releases

## [0.6.0] - 2026-02-22

### Added
- Incremental compilation with per-unit object file caching
  - Each .sushi unit compiles to its own .o file, cached in `__sushi_cache__/`
  - Cache invalidation via content-based SHA-256 semantic fingerprints
  - Stdlib and library imports also cached as separate .o files
  - Monomorphized generics use `linkonce_odr` linkage for linker deduplication
- New CLI flags: `--no-incremental`, `--clean-cache`, `--cache-dir`
- HashMap `.entries()` method returning `Iterator<Entry<K, V>>` for key-value pair iteration
- `Entry<K, V>` struct with `.key` and `.value` fields
- Error diagnostic infrastructure with note/help sub-diagnostics
  - Polished rendering with T-junction markers, continuous box drawing, colored notes
- `--platform` flag for stdlib build script

### Changed
- Multi-unit compilation now uses two-path architecture: monolithic (single-file) and incremental (multi-unit)
- Semantic analysis remains whole-program; only LLVM codegen is cached per-unit

### Fixed
- Struct type propagation in `Result.realise()` method
- Missing Linux stdlib .bc files
- CI workflow: always run tests on push, skip only for docs-only PRs
- Reliable code change detection using dorny/paths-filter

### Documentation
- Stdlib build and library format documentation
- Updated compiler reference with incremental compilation section
- Updated architecture docs with incremental compilation internals

## [0.5.0] - 2025-12-20

### Changed
- Restructured package into single `sushi_lang` directory for cleaner pip installation
- Updated all internal imports to use new package structure
- Release workflow now extracts changelog for GitHub release notes automatically
- Test workflow installs dev dependencies (tqdm) via `--extra dev`

### Added
- Package entry points (`__init__.py`, `__main__.py`) for proper Python module support
- PACKAGE.md with packaging and release documentation

## [0.4.2] - 2025-12-17

### Added
- Constant folding for arithmetic and bitwise operations
- Content-based string constant deduplication
- AST type annotations for TryExpr error propagation
- LibraryRegistry to centralize library metadata parsing
- AST visitor base class with complete node coverage
- Semantic analysis pipeline with timing instrumentation
- Progress bar for test runner (tqdm)

### Changed
- Enforce type propagation single entry point via private functions
- Implement type-level COW for monomorphization transformer
- Increase default test timeout from 5s to 10s
- Extend enum_utils with variant data packing and unpacking utilities
- Extract shared type resolution helpers for TypeMapper and TypeSizing
- Extract symbol merging into dedicated SymbolTableMerger class
- Refactor scope manager to use flat cache as primary storage
- Consolidate Result type handling into ResultBuilder class
- Consolidate type resolution functions into TypeResolver class
- Use --frozen flag when invoking sushi compiler

### Fixed
- Reference parameter type resolution and array indexing
- HashMap and generic types passed by reference to functions

### Examples
- Added rudimentary Markov chain example

## [0.4.1] - 2025-12-07

### Changed
- Refactored library_format.py to eliminate DRY violation
- Reorganized tests into appropriate subdirectories
- Test cleanup, added test lib build to run_tests.py

## [0.4.0] - 2025-12-07

### Added
- Unified binary library format (`.slib`)
  - Single file combines LLVM bitcode and MessagePack-encoded metadata
  - Magic bytes: sushi emoji surrounding "SUSHILIB"
  - Version field and reserved space for future extensions
- `--lib-info` CLI command for library introspection
  - Displays library name, platform, compiler version, compile timestamp
  - Lists public functions with full signatures
  - Shows structs, enums, constants, and dependencies
  - Reports bitcode size
- New error codes for library format validation
  - CE3508: Invalid magic bytes
  - CE3509: Unsupported format version
  - CE3510: Metadata section truncated
  - CE3511: Bitcode section truncated
  - CE3512: Invalid metadata (MessagePack decode error)
  - CE3513: File too large

### Changed
- Library output now uses `.slib` extension instead of `.bc`
- Removed separate `.sushilib` JSON manifest files
- Updated CE3500 error message for `.slib` extension requirement

### Dependencies
- Added `msgpack>=1.0` for binary metadata serialization

## [0.3.0] - 2025-12-06

### Added
- Library system for creating and using precompiled libraries
  - `--lib` flag compiles source to reusable bitcode (`.bc`) with manifest (`.sushilib`)
  - `use <lib/name>` syntax imports precompiled libraries
  - `SUSHI_LIB_PATH` environment variable for library search paths
  - Two-phase linking with priority-based symbol resolution (Main > Library > Stdlib > Runtime)
  - Dead code elimination removes unused library functions
  - Platform mismatch warnings (CW3505)
- Library error codes (CE35xx)
  - CE3500: Library output path must have .bc extension
  - CE3502: Library not found in search paths
  - CE3503: Invalid library manifest
  - CE3507: Failed to link library
- Library type registration from manifests
  - Structs, enums, and functions from libraries are registered in semantic analysis
  - Local definitions take precedence over library definitions
- Library integration tests in `tests/libs/`
  - Runtime symbol deduplication
  - Circular function calls
  - Symbol priority/override
  - Dead code elimination
- GenericTypeProvider interface for plugin-style generic types
  - HashMap now conditionally loaded via `use <collections/hashmap>`
- Math module enhancements (`use <math>`)
  - Trigonometric: `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`
  - Hyperbolic: `sinh`, `cosh`, `tanh`
  - Logarithmic: `log`, `log10`, `log2`
  - Exponential: `exp`, `pow`

### Changed
- Removed `--link` flag (breaking change)
  - Libraries are now imported via `use <lib/...>` statements in source code
  - This simplifies the compilation model to a single mechanism

### Documentation
- New documentation: `docs/libraries.md` - Complete library system guide
- Updated `docs/compiler-reference.md` with library options
- Updated `docs/examples/26-libraries.sushi` library usage example
- Updated `docs/stdlib/math.md` with new math functions

## [0.2.0] - 2025-11-27

### Added
- Dual-mode borrow syntax replacing single `&` operator
  - `peek T`: Read-only borrow (multiple allowed simultaneously)
  - `poke T`: Read-write borrow (exclusive access)
- Type coercion: `poke T` can be passed where `peek T` is expected
- New error codes for borrow checking:
  - CE2407: Cannot have peek and poke borrows simultaneously
  - CE2408: Cannot modify through peek reference (read-only)

### Changed
- Reference syntax now requires explicit borrow mode (peek or poke)
  - Old: `fn process(&i32 x) ~`
  - New: `fn process(poke i32 x) ~` or `fn process(peek i32 x) ~`
- Borrow checker updated for dual-mode semantics
- All tests migrated to new peek/poke syntax (29 files)

### Breaking Changes
- Plain `&T` syntax removed (no backward compatibility)
- All existing code using `&T` must be updated to `peek T` or `poke T`

## [0.1.0] - 2025-11-27

### Added
- Result<T, E> error type system implementation
  - Custom error types with | syntax: fn foo() T | ErrorType
  - Explicit Result<T, E> syntax for nested Results
  - Six built-in error enums: StdError, MathError, FileError, IoError, ProcessError, EnvError
  - Error type validation: CE2085 prevents mixing explicit Result with | syntax
- sys/process stdlib module for process control
  - exit(code) - Terminate process with exit code
  - getpid() - Get current process ID
  - sleep(seconds) - Sleep for N seconds (POSIX-compliant)
- Hash functions for Result<T, E> types
  - Enables Result types as HashMap keys
  - FNV-1a combining hash for Ok/Err variants
- Equality operations for Result<T, E> types
  - Enables == and != comparisons between Result values
- Warning CW2511 for ?? operator usage in main()
  - Encourages explicit error handling in entry point
  - Prevents propagation from top-level function

### Fixed
- Result<T, E> enum type size calculation for unit error types
- Result type resolution for stdlib file operations in match statements
- Result<T, E>.realise() type inference and integer conversions
- Result<T, E> recursive type propagation and stdlib backend integration
- Result<T, E> type propagation in Let statements
- Result method validation with user-defined generic enums
- Result<T, E> support for generic return types
- Nested Result pattern matching with enhanced type resolution
- Explicit Result<T, E> double-wrapping prevention
- Struct field extraction from Result enums with LLVM padding handling
- Result.Err() error type validation requiring error values
- FuncSig parameter types synchronization when resolving GenericTypeRef
- Result<T, E> boolean conditionals support (if/while statements)

### Changed
- Result.Err() now requires error value argument
  - Old: Result.Err()
  - New: Result.Err(StdError.Error)
- Result type annotation syntax requires explicit error type
  - Old: Result<T>
  - New: Result<T, E>
- Refactored type validation into modular components
  - semantics/passes/types/resolution.py - Type resolution
  - semantics/passes/types/propagation.py - Type propagation
  - semantics/passes/types/result_validation.py - Result pattern validation
- Refactored backend constants into modular architecture
- Refactored stdlib string operations into modular structure
- Refactored generics system (instantiate.py, monomorphize.py, collect.py)
- Complete test suite migration to Result<T, E> syntax

### Documentation
- Updated all 25 documentation examples to Result<T, E> syntax
- Updated language-reference.md with Result<T, E> type system
- Comprehensive error handling documentation in docs/error-handling.md
- sys/process module documentation in docs/stdlib/process.md
- ?? operator usage guidelines and best practices

## [0.0.12] - 2025-11-17

### Added
- Named parameters for struct constructors
  - Order-independent syntax: Point(y: 20, x: 10)
  - All-or-nothing: cannot mix positional and named arguments
  - Zero-cost abstraction resolved at compile-time
  - Error codes: CE2080-CE2083 for validation
- Single-quote string literals ('...') for plain strings without interpolation
  - Double quotes ("...") support {expr} interpolation
  - Single quotes provide literals for use in interpolation arguments
  - Example: {text.pad_left(10, '*')} uses single quotes for arguments
- File utilities in io/files module
  - remove(path) - Delete files
  - rename(old_path, new_path) - Rename/move files
  - mkdir(path, mode) - Create directories with permissions
  - rmdir(path) - Remove empty directories
  - copy(src, dest) - Copy files
- String methods in collections/strings module
  - reverse() - UTF-8 aware character-level reversal
  - repeat(n) - Repeat string n times
  - count(needle) - Count non-overlapping occurrences
  - find_last(needle) - Find last occurrence index
  - join(separator, array) - Join string array with separator
  - pad_left(width, pad_char) - Left-pad to width
  - pad_right(width, pad_char) - Right-pad to width
  - strip_prefix(prefix) - Remove prefix if present
  - strip_suffix(suffix) - Remove suffix if present

### Fixed
- Pattern matching segfault on Result<T> function calls
  - Added Call node handling in _get_scrutinee_type()
- HashMap array key implementation
  - Corrected GEP indexing for fixed and dynamic array equality
  - Fixed arrays now use gep_fixed_array_element() utility
- Directory-based stdlib imports now include submodules
  - use <collections> properly provides collections/strings
- String methods now UTF-8 aware
  - ss(start, length) works with character indices instead of bytes

### Changed
- Migrated to uv-only dependency management with direnv integration
- Refactored version management to use pyproject.toml as single source of truth
- Refactored file utilities to use fat_pointer_to_cstr() helper function
- Reorganized stdlib documentation into modular structure (docs/stdlib/)

### Validation
- Dynamic arrays disallowed as HashMap keys at compile time (error CE2058)
- Dynamic arrays disallowed in enum variants at compile time (error CE2059)
- Fixed arrays remain supported in both contexts

### Documentation
- Comprehensive documentation for named struct constructors
- Single-quote string literal syntax documented across language reference
- File utilities documentation in docs/stdlib/io/files.md
- String methods documentation in reorganized stdlib docs

## [0.0.11] - 2025-11-11

### Added
- Range expressions with .. (exclusive) and ..= (inclusive) operators
  - Zero-cost iteration that compiles to optimized for-loops
  - Automatic direction detection (ascending vs descending)
  - Supports break/continue statements
  - Returns Iterator<i32> for consistency with array iteration
- Random number generator module (<random>)
  - rand() -> u64: Random 64-bit unsigned integer
  - rand_range(i32, i32) -> i32: Random integer in range
  - srand(u64) -> ~: Seed RNG for reproducibility
  - rand_f64() -> f64: Random float in [0.0, 1.0)
  - POSIX-compliant using libc random()/srandom()
- Manual workflow dispatch trigger for CI

### Fixed
- GitHub Actions badges display

## [0.0.10] - 2025-11-11

### Added
- Full Linux platform support with CI testing
- Platform-specific errno access (`__errno_location` on Linux, `__error` on macOS)
- Platform-specific linker flags (`-lm` for math library on Linux)
- Stdio platform abstraction (stdin/stdout/stderr handles)
- Docker-based Linux build testing script

### Fixed
- Generic function enum handling in Match statements
- Perks with nested generic functions
- Buffer overflow in Maybe<T> for struct field method calls
- Method calls on borrowed variables inside functions
- Array access with .get() in structs
- Test suite compatibility with array fixes

### Changed
- Moved stdio platform implementations to `stdlib/src/_platform/{darwin,linux}/stdio.py`
- Updated backend to use platform detection for linker flags and errno functions
- Updated documentation to reflect Linux support status

## [0.0.9] - 2025-11-07

### Added
- Compile-time constant expression evaluation
  - Arithmetic operations: +, -, *, /, %
  - Bitwise operations: &, |, ^, ~, <<, >>
  - Logical operations: and, or, xor, not
  - Comparison operations: ==, !=, <, <=, >, >=
  - Type casts: as operator
  - Constant references with cycle detection
  - Array constants with constant elements
- Hexadecimal numeric literals (0xFF, 0xDEAD_BEEF)
- Binary numeric literals (0b1111, 0b1010_1010)
- Octal numeric literals (0o755, 0o644)
- Comprehensive constant expression test suite (19 tests)
- Test metadata guide (tests/TEST_METADATA_GUIDE.md)
- Error codes CE0108-CE0112 for constant expression validation

### Fixed
- ArrayType attribute error in const_eval.py (element_type -> base_type)
- Test logic bugs in test_constants_logical.sushi and test_constants_complex.sushi

### Changed
- Refactored AST builder into modular architecture (40+ modules)
  - semantics/ast_builder/ with utils/, types/, expressions/, statements/, declarations/
- Reorganized test suite into logical directory structure
  - tests/basic/, tests/constants/, tests/control_flow/, tests/error_handling/
  - tests/generics/, tests/io/, tests/literals/, tests/memory/, tests/operators/
  - tests/stdlib/, tests/strings/, tests/types/, tests/array/, tests/list/, tests/perks/
- Updated docs/language-reference.md with constant expressions section
- Updated docs/compiler-reference.md with constant expression error codes
- Bumped version to 0.0.9

### Documentation
- Complete constant expression syntax and examples
- All 5 constant expression error codes documented
- Test metadata format and requirements

## [0.0.8] - 2025-11-05

### Added
- Perks (traits/interfaces) system with static dispatch
  - Phase 1: Grammar, AST, and parsing support
  - Phase 2: Type system integration (PerkTable, PerkImplementationTable)
  - Phase 3: Implementation validation and checking
  - Phase 4: Constraint validation for generic structs, enums, and functions
  - Phase 5: Code generation with bare return types
- Generic functions with automatic type inference
- Generic functions with perk constraints (T: PerkName)
- Multiple perk constraints support (T: Perk1 + Perk2)
- Synthetic perk implementations for primitive types
- Comprehensive perks test suite (38 tests)
- Comprehensive perks documentation in docs/perks.md
- Example 23: Basic perks usage (docs/examples/23-perks-basic.sushi)
- Example 24: Generic constraints with perks (docs/examples/24-perks-constraints.sushi)
- Complete examples catalog in docs/examples/README.md
- Standard library documentation
  - docs/standard-library.md
  - docs/stdlib/env.md (environment variables)
  - docs/stdlib/math.md (mathematical functions)
  - docs/stdlib/platform.md (platform-specific APIs)
- Enhanced internals documentation
  - docs/internals/architecture.md
  - docs/internals/backend.md
- GitHub Actions workflow for automated testing
- Test badges in README.md showing test status
- Linux stdlib support in test builds

### Fixed
- f64 arithmetic and comparison operations
- Generic extension methods resolution during type validation
- Mixed-type numeric operations and validation
- Struct/enum extension method lookup
- Perk method argument type validation
- Result handling in perk tests
- BoundedTypeParam compatibility with generics system

### Changed
- Refactored name mangling to use shared utility functions
- Updated README.md with perks section and example
- Updated README.md with improved feature descriptions
- Updated docs/README.md to include perks in language reference
- Improved error messages for perk constraint violations

### Removed
- Various obsolete markdown documentation files

## [0.0.7] - 2025-11-03

First public release.
