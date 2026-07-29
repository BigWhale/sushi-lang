# Type Identity

**Status: DECIDED** (issue #240). A named type is identified by its name.

## The rule

`StructType` and `EnumType` compare and hash on `name` alone. The struct/enum
table is the sole authority for a named type's fields/variants, and nothing
manufactures a named type outside its interning path.

Everything else — `ArrayType`, `DynamicArrayType`, `GenericTypeRef`,
`FunctionType`, `ReferenceType`, `PointerType` — stays structural. Those have no
declaration to be identified by.

## Why the name is enough

Sushi monomorphizes, and every instantiation gets a unique mangled name. So the
interned name already *is* the pair every nominal type system keys on:

| language | identity |
|---|---|
| Go | `(type-name object, type argument list)` |
| Rust | `(DefId, GenericArgs)` |
| Sushi | the interned name — `Own<Node>`, `Maybe<i32>`, `HashMap<string, i32>` |

`Own<Node>` and `Own<Leafy>` are different names because they are different
types; `Own<Node>` written twice is the same name because it is the same type.
There is nothing left for a structural comparison to add.

## Why not structural

Two reasons, and the second is the one that bites.

**It cannot terminate.** A recursive type's fields contain the type. Comparing
two of them structurally walks the cycle forever. Go and Rust both avoid this by
construction rather than with a visited set:

> *"Two named types are identical if their type names originate in the same type
> declaration; if they are instantiated they must have identical type argument
> lists."*
> — `go/types/predicates.go`, the `*Named` case of `identical`, which returns
> `identicalOrigin(x, y)`, i.e. `x.Origin().obj == y.Origin().obj`

> *"`AdtDef` does not actually include the types of its fields; it includes just
> their `DefId`s."*
> — rustc-dev-guide, *ADTs and Generic Arguments*. Field types come from a
> separate `tcx.type_of(field_def_id)` query.

**It makes "how resolved is it?" part of identity.** This is the subtle half.
A type flows through the compiler being progressively resolved: a field starts as
`UnknownType("Node")` and later becomes `StructType("Node", fields=(...))`. Under
structural equality, the *same* type at two different resolution depths is two
different types.

Sushi hashed on the name but compared on the contents, so those two instances
hash-matched and compared **unequal** — a silent dict miss, never a crash. That
produced:

- `CE2002: cannot assign Own@(Holder) to Own@(Holder)` — one `Own<Holder>`
  interned as a `Maybe` type argument, one from a bare annotation
- **CE0126** — the same shape for `Result`: a duplicate monomorphization rather
  than a reused one
- the #240 ICE itself, because resolution deep-walked struct fields *in order to
  make structural equality agree*, and that walk cycles

All three are one defect. Nominal identity removes the category.

## Consequences

- **Never rebuild a named type.** If you have a name, look it up. Pass 1.7
  (`ast_transform.resolve_struct_field_types`) resolves table entries **in
  place**; the monomorphizer publishes a shell into the cache and patches it in
  place. Both are correct because the table entry is the identity.
- **Resolution stops at a named type.** `resolve_type_recursively` returns the
  table entry and does not descend. Name mangling uses a shallow resolver that
  enters only type arguments and array elements — positions that appear in the
  rendered name.
- **A wrong table entry is now invisible to `==`.** That is the trade. It is the
  same trade Go and Rust make, and the table is built once by the collector, so
  the exposure is small — much smaller than a silent cache miss on every
  comparison.

## What this does *not* decide

Whether a recursive type is well-*formed* is a separate question, answered by a
separate pass. A type that contains itself **by value** has no finite size and is
rejected with **CE2095** (`semantics/passes/infinite_types.py`), the way Rust
reports E0072 and Go reports "invalid recursive type". Unbounded *generic*
instantiation is a third mechanism again, **CE0122**.

Identity, finiteness, and instantiation depth are three different questions. Do
not merge them.
