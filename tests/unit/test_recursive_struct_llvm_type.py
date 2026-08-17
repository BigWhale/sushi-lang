"""A user-declared struct is an LLVM *identified* type, not a literal one (#257)."""
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
    """Each monomorphization gets its OWN identified type."""
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
    """Strings, dynamic arrays and enums are NOT promoted to identified types."""
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
    assert "Colour" not in decls, "enums keep their {i32 tag, [K x i64]} literal layout"
    assert not any(n.startswith("List<") or n.startswith("HashMap<") for n in decls), (
        f"container layout descriptors must stay literal, got {sorted(decls)}"
    )


def test_identified_type_is_set_body_not_recached():
    """The mechanism itself: set_body fills IN PLACE, so a mid-walk pointer stays valid."""
    module = ir.Module(name="pin")
    handle = module.context.get_identified_type("Tree")
    ptr_taken_before_body = ir.PointerType(handle)  # what a field walk would capture

    handle.set_body(ir.IntType(32), ir.LiteralStructType(
        [ir.IntType(32), ir.IntType(32), ir.PointerType(handle)]
    ))

    assert not handle.is_opaque
    assert ptr_taken_before_body == ir.PointerType(module.context.get_identified_type("Tree"))
    assert '%"Tree" = type {i32, {i32, i32, %"Tree"*}}' in str(module)
