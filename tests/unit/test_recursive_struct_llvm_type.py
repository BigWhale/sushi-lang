"""A user-declared struct is an LLVM *identified* type, not a literal one (#257).

`_get_struct_type` used to tie the recursive knot by caching an empty
`ir.LiteralStructType([])` placeholder, walking the fields, and then caching a NEW
`LiteralStructType` built from them. That cannot work: a literal struct type is a
structural *value*, so there is nothing to fill in. Re-caching replaces the cache entry,
but the `{}` the field walk already embedded into `{i32, i32, {}*}` stays empty forever --
which is why `struct Tree: List@(Tree) kids` came out as `{i32, {i32, i32, {}*}}` and every
element GEP through it had stride ZERO.

LLVM's identified struct types exist for exactly this: `set_body` fills the type IN PLACE,
so a pointer taken to it mid-walk stays valid. That makes the knot self-tying and lets the
declaration emit as `%Tree = type {i32, {i32, i32, %Tree*}}`.

These tests pin the DECISION rather than either defect's symptom -- specifically that it is
uniform (every user struct, not just self-referential ones), because a future reader looking
at a non-recursive struct is the one most likely to conclude the identified type is
unnecessary and revert it. Uniformity is also what removes the shape-collision class:
`is_dynamic_array_type` sniffs for a literal `{i32, i32, T*}`, so while user structs were
literal, a user struct of that exact shape false-positived as a dynamic array.
"""
from __future__ import annotations

from llvmlite import ir

from tests.unit.test_ffi import _emit_ir


def _struct_decls(ir_text: str) -> dict[str, str]:
    """Map identified-type name -> body, from `%"Name" = type {...}` module lines."""
    out = {}
    for line in ir_text.splitlines():
        line = line.strip()
        if not line.startswith('%"') or " = type " not in line:
            continue
        name, body = line.split(" = type ", 1)
        out[name.strip().strip('%"')] = body.strip()
    return out


def test_plain_struct_is_identified(tmp_path):
    """Uniform: even a struct with no self-reference gets an identified type."""
    src = (
        "struct Point:\n"
        "    i32 x\n"
        "    i32 y\n"
        "\n"
        "fn main() i32:\n"
        "    let Point p = Point(1, 2)\n"
        "    println(p.x)\n"
        "    return Result.Ok(0)\n"
    )
    decls = _struct_decls(_emit_ir(tmp_path, src))
    assert "Point" in decls, f"user structs must be identified types, got {sorted(decls)}"
    assert decls["Point"] == "{i32, i32}"


def test_recursive_list_struct_has_self_pointer_not_empty_placeholder(tmp_path):
    """`List@(Tree)` back-edge is `%Tree*`, never `{}*` (defect A's mechanism)."""
    src = (
        "struct Tree:\n"
        "    i32 value\n"
        "    List@(Tree) kids\n"
        "\n"
        "fn main() i32:\n"
        "    let Tree root = Tree(1, List.new())\n"
        "    println(root.value)\n"
        "    return Result.Ok(0)\n"
    )
    decls = _struct_decls(_emit_ir(tmp_path, src))
    assert "Tree" in decls
    body = decls["Tree"]
    assert '%"Tree"*' in body, f"self-reference must point at the identified type, got {body}"
    assert "{}" not in body, f"empty placeholder leaked into the struct body: {body}"


def test_recursive_dyn_array_struct_has_self_pointer(tmp_path):
    """The `Node[]` twin: the element pointer is `%Node*`, not `{}*`."""
    src = (
        "struct Node:\n"
        "    i32 value\n"
        "    Node[] kids\n"
        "\n"
        "fn main() i32:\n"
        "    let Node root = Node(1, from([]))\n"
        "    println(root.value)\n"
        "    return Result.Ok(0)\n"
    )
    decls = _struct_decls(_emit_ir(tmp_path, src))
    assert "Node" in decls
    body = decls["Node"]
    assert '%"Node"*' in body, f"self-reference must point at the identified type, got {body}"
    assert "{}" not in body, f"empty placeholder leaked into the struct body: {body}"


def test_generic_struct_instantiations_do_not_collide(tmp_path):
    """Each monomorphization gets its OWN identified type.

    Identified types are keyed by name, and the interned name already encodes the type
    arguments (`Pair<i32, i32>`). Sanitising or truncating that name would make two
    instantiations share one identified type -- silently giving one of them the other's
    layout.
    """
    src = (
        "struct Pair@(T, U):\n"
        "    T first\n"
        "    U second\n"
        "\n"
        "fn main() i32:\n"
        "    let Pair@(i32, i32) a = Pair(1, 2)\n"
        "    let Pair@(i32, bool) b = Pair(3, true)\n"
        "    println(a.first)\n"
        "    println(b.first)\n"
        "    return Result.Ok(0)\n"
    )
    decls = _struct_decls(_emit_ir(tmp_path, src))
    pairs = {n: b for n, b in decls.items() if n.startswith("Pair")}
    assert len(pairs) == 2, f"expected one identified type per instantiation, got {pairs}"
    assert len(set(pairs.values())) == 2, f"instantiations must not share a layout: {pairs}"


def test_anonymous_fat_pointers_stay_literal(tmp_path):
    """Strings, dynamic arrays and enums are NOT promoted to identified types.

    They are anonymous layout descriptors, not nominal types, and several backend checks
    identify them by structural shape (`is_string_type`, `is_dynamic_array_type`). Naming
    them would break those, and would also serve no purpose: an enum's payload is a
    `[N x i8]` byte blob, so it never embeds an element type and never had the
    back-fill problem in the first place.
    """
    src = (
        "enum Colour:\n"
        "    Red\n"
        "    Green\n"
        "\n"
        "fn main() i32:\n"
        "    let string s = \"hi\"\n"
        "    let i32[] xs = from([1, 2])\n"
        "    let Colour c = Colour.Red()\n"
        "    println(\"{s} {xs.len()}\")\n"
        "    match c:\n"
        "        Colour.Red() -> println(\"r\")\n"
        "        Colour.Green() -> println(\"g\")\n"
        "    return Result.Ok(0)\n"
    )
    decls = _struct_decls(_emit_ir(tmp_path, src))
    assert "Colour" not in decls, "enums keep their {i32 tag, [N x i8]} literal layout"
    assert not any(n.startswith("List<") or n.startswith("HashMap<") for n in decls), (
        f"container layout descriptors must stay literal, got {sorted(decls)}"
    )


def test_identified_type_is_set_body_not_recached():
    """The mechanism itself: set_body fills IN PLACE, so a mid-walk pointer stays valid.

    This is the property the old placeholder approach lacked, and the reason the fix is a
    different LLVM construct rather than a reordering. Asserted directly against llvmlite
    so the guarantee is pinned even if the compiler's use of it is refactored.
    """
    module = ir.Module(name="pin")
    handle = module.context.get_identified_type("Tree")
    ptr_taken_before_body = ir.PointerType(handle)  # what a field walk would capture

    handle.set_body(ir.IntType(32), ir.LiteralStructType(
        [ir.IntType(32), ir.IntType(32), ir.PointerType(handle)]
    ))

    assert not handle.is_opaque
    assert ptr_taken_before_body == ir.PointerType(module.context.get_identified_type("Tree"))
    assert '%"Tree" = type {i32, {i32, i32, %"Tree"*}}' in str(module)
