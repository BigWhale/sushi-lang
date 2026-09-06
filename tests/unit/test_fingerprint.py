"""Unit tests for semantic fingerprinting (compiler/fingerprint.py)."""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.units import Unit, UnitManager
from sushi_lang.compiler.fingerprint import (
    compute_unit_fingerprint,
    compute_stdlib_fingerprint,
    compute_lib_fingerprint,
)


CLEAN = "fn main() i32:\n    return Result.Ok(0)\n"

# Constant file bytes shared by the AST-isolation tests: the file content is
# identical across variants, so any fingerprint difference comes from the AST.
_FIXED_BYTES = "# fixed source bytes for AST-isolation tests\n"


def _unit_with_ast(tmp_path, src, name="main"):
    """Build a Unit whose file bytes are constant but whose AST comes from `src`."""
    fp = tmp_path / f"{name}.sushi"
    fp.write_text(_FIXED_BYTES, encoding="utf-8")
    text = src if src.endswith("\n") else src + "\n"
    program, _tree = parse_to_ast(text)
    return Unit(name=name, file_path=fp, ast=program, dependencies=[], public_symbols={})


# Determinism and source sensitivity

def test_fingerprint_is_deterministic(make_unit):
    unit = make_unit(CLEAN)
    assert compute_unit_fingerprint(unit) == compute_unit_fingerprint(unit)


def test_fingerprint_changes_with_source(make_unit):
    # Distinct names so the two units are backed by distinct files (the source
    # bytes are what distinguish function-body changes; _hash_ast_structure does
    # not hash function bodies).
    a = make_unit("fn main() i32:\n    return Result.Ok(0)\n", name="a")
    b = make_unit("fn main() i32:\n    return Result.Ok(1)\n", name="b")
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


# AST-structure sensitivity (file bytes held constant, only AST varies)

def test_fingerprint_changes_with_struct_field(tmp_path):
    a = _unit_with_ast(tmp_path, "struct P:\n    i32 x\n" + CLEAN)
    b = _unit_with_ast(tmp_path, "struct P:\n    i32 x\n    i32 y\n" + CLEAN)
    assert a.file_path.read_bytes() == b.file_path.read_bytes()  # same source bytes
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


def test_fingerprint_changes_with_enum_variant(tmp_path):
    a = _unit_with_ast(tmp_path, "enum Status:\n    Active()\n    Inactive()\n" + CLEAN)
    b = _unit_with_ast(tmp_path, "enum Status:\n    Active()\n    Inactive()\n    Pending()\n" + CLEAN)
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


def test_fingerprint_changes_with_extension_method(tmp_path):
    a = _unit_with_ast(tmp_path, CLEAN)
    b = _unit_with_ast(tmp_path, "extend i32 squared() i32:\n    return Result.Ok(self * self)\n" + CLEAN)
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


def test_fingerprint_changes_with_use_statement(tmp_path):
    # Guards the USES: component of _hash_ast_structure specifically.
    a = _unit_with_ast(tmp_path, CLEAN)
    b = _unit_with_ast(tmp_path, "use <io/files>\n" + CLEAN)
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


# Cross-unit visibility (a dependency's public signature affects dependents)

def test_fingerprint_changes_when_dependency_signature_changes(make_unit, tmp_path):
    dependent = make_unit(CLEAN, name="dependent")
    dependent.dependencies = ["dep"]

    dep_v1 = make_unit("public fn helper(i32 a) i32:\n    return Result.Ok(a)\n", name="dep")
    um = UnitManager(root_path=tmp_path)
    um.units = {"dep": dep_v1, "dependent": dependent}
    fp1 = compute_unit_fingerprint(dependent, um)

    # Dependent's own source is unchanged; only the dependency's public signature is.
    dep_v2 = make_unit("public fn helper(i32 a, i32 b) i32:\n    return Result.Ok(a)\n", name="dep")
    um.units["dep"] = dep_v2
    fp2 = compute_unit_fingerprint(dependent, um)

    assert fp1 != fp2


# stdlib / library fingerprints

def test_stdlib_fingerprint_is_order_independent(tmp_path):
    a = tmp_path / "a.bc"
    a.write_bytes(b"AAA")
    b = tmp_path / "b.bc"
    b.write_bytes(b"BBB")
    assert compute_stdlib_fingerprint([a, b]) == compute_stdlib_fingerprint([b, a])


def test_stdlib_fingerprint_changes_with_content(tmp_path):
    a = tmp_path / "a.bc"
    a.write_bytes(b"AAA")
    b = tmp_path / "b.bc"
    b.write_bytes(b"BBB")
    before = compute_stdlib_fingerprint([a, b])
    b.write_bytes(b"CHANGED")
    assert compute_stdlib_fingerprint([a, b]) != before


def test_lib_fingerprint_changes_with_content(tmp_path):
    slib = tmp_path / "lib.slib"
    slib.write_bytes(b"LIB1")
    fp1 = compute_lib_fingerprint(slib)
    slib.write_bytes(b"LIB2")
    assert compute_lib_fingerprint(slib) != fp1


# Imported-library template sensitivity (Phase 2 cross-library generics)

def test_unit_fingerprint_changes_with_imported_library_digest(make_unit):
    """A consumer unit's fingerprint must change when an imported library's digest changes, so a
    library generic-template edit invalidates the consumer's cached .o (otherwise a
    monomorphized instance would be stale).
    """
    unit = make_unit(CLEAN)
    fp_a = compute_unit_fingerprint(unit, library_fingerprints={"lib/math": "DIGEST_A"})
    fp_b = compute_unit_fingerprint(unit, library_fingerprints={"lib/math": "DIGEST_B"})
    assert fp_a != fp_b


def test_unit_fingerprint_stable_for_same_library_digest(make_unit):
    """Identical library digests must produce identical fingerprints (no spurious cache misses).
    """
    unit = make_unit(CLEAN)
    digests = {"lib/math": "DIGEST_A", "lib/strings": "DIGEST_X"}
    assert (
        compute_unit_fingerprint(unit, library_fingerprints=dict(digests))
        == compute_unit_fingerprint(unit, library_fingerprints=dict(digests))
    )


def test_unit_fingerprint_no_library_matches_empty_mapping(make_unit):
    """Passing no library digests and an empty mapping are equivalent (the section is skipped when
    there is nothing to fold in).
    """
    unit = make_unit(CLEAN)
    assert (
        compute_unit_fingerprint(unit)
        == compute_unit_fingerprint(unit, library_fingerprints={})
    )


# Generic exporters (regression: BoundedTypeParam join TypeError)

def test_definition_signature_with_generic_type_params():
    """A FuncDef whose type_params are BoundedTypeParam objects must produce a signature, not a
    TypeError. Before the fix, ",".join(defn.type_params) made every incremental build exporting
    a generic function a CE0000 ICE.
    """
    from sushi_lang.compiler.fingerprint import _definition_signature
    program, _ = parse_to_ast("public fn identity@(T)(T x) T:\n    return Result.Ok(x)\n")
    sig = _definition_signature(program.functions[0])
    assert "<T>" in sig

    # A constraint is part of the signature (a constraint change must invalidate).
    program2, _ = parse_to_ast(
        "perk Hashable:\n    fn hash() u64\n\n"
        "public fn identity@(T: Hashable)(T x) T:\n    return Result.Ok(x)\n"
    )
    sig2 = _definition_signature(program2.functions[0])
    assert sig2 != sig
    assert "Hashable" in sig2


def test_fingerprint_generic_struct_and_enum_do_not_crash(tmp_path):
    """Generic struct/enum type_params went through the same broken join."""
    src = (
        "struct Wrap@(T):\n    T inner\n\n"
        "enum Slot@(T):\n    Filled(T)\n    Empty\n" + CLEAN
    )
    unit = _unit_with_ast(tmp_path, src)
    plain = _unit_with_ast(tmp_path, CLEAN, name="plain")
    assert compute_unit_fingerprint(unit) != compute_unit_fingerprint(plain)


# Monomorphized-extension key: signature AND body, span-insensitive

def _parse_extension(src):
    program, _ = parse_to_ast(src)
    return program.extensions[0]


def test_mono_ext_fingerprint_covers_body(make_unit):
    """The old key was target::name only, so a body edit reused a stale .o."""
    unit = make_unit(CLEAN)
    ext_a = _parse_extension("extend i32 squared() i32:\n    return Result.Ok(self * self)\n")
    ext_b = _parse_extension("extend i32 squared() i32:\n    return Result.Ok(self + self)\n")
    fp_a = compute_unit_fingerprint(unit, monomorphized_extensions=[ext_a])
    fp_b = compute_unit_fingerprint(unit, monomorphized_extensions=[ext_b])
    assert fp_a != fp_b


def test_mono_ext_fingerprint_covers_signature(make_unit):
    unit = make_unit(CLEAN)
    ext_a = _parse_extension("extend i32 scaled(i32 k) i32:\n    return Result.Ok(self * k)\n")
    ext_b = _parse_extension("extend i32 scaled(i64 k) i32:\n    return Result.Ok(self)\n")
    assert (
        compute_unit_fingerprint(unit, monomorphized_extensions=[ext_a])
        != compute_unit_fingerprint(unit, monomorphized_extensions=[ext_b])
    )


def test_mono_ext_fingerprint_ignores_source_position(make_unit):
    """Shifting an extension down a line must NOT invalidate (span-insensitive): otherwise every
    unrelated edit above it would rebuild all consumers.
    """
    unit = make_unit(CLEAN)
    ext_a = _parse_extension("extend i32 squared() i32:\n    return Result.Ok(self * self)\n")
    ext_b = _parse_extension("# shifted\n\n\nextend i32 squared() i32:\n    return Result.Ok(self * self)\n")
    assert (
        compute_unit_fingerprint(unit, monomorphized_extensions=[ext_a])
        == compute_unit_fingerprint(unit, monomorphized_extensions=[ext_b])
    )


# Stdlib generator-source coverage (regression: silently-skipped dead path)

def test_stdlib_generator_sources_all_exist():
    """Every path the stdlib source fingerprint hashes must exist. The hasher silently skips
    missing paths, so a generator that moves (types/primitives.py became the types/primitives/
    package) drops out of the digest without a trace - and editing it no longer invalidates the
    shipped .bc.
    """
    from sushi_lang.compiler.fingerprint import _stdlib_generator_sources

    sources = _stdlib_generator_sources()
    missing = [str(p) for p in sources if not p.exists()]
    assert not missing, f"fingerprinted generator sources do not exist: {missing}"


def test_stdlib_generator_sources_cover_primitives_package():
    """build.py generates core/primitives from backend/types/primitives/ (a package); its files
    must be part of the stdlib source digest.
    """
    from sushi_lang.compiler.fingerprint import _stdlib_generator_sources

    sources = _stdlib_generator_sources()
    prim = [p for p in sources if "backend" in p.parts and "primitives" in p.parts]
    assert prim, "no backend/types/primitives/ files in the stdlib source fingerprint"


# A dependency's declaration SHAPE, not just its public signatures (#593)

def _two_units(make_unit, tmp_path, dep_src, dependent_src=CLEAN, dep_name="dep"):
    """A dependent that imports `dep`, and a manager holding both."""
    dependent = make_unit(dependent_src, name="dependent")
    dependent.dependencies = [dep_name]
    um = UnitManager(root_path=tmp_path)
    um.units = {dep_name: make_unit(dep_src, name=dep_name), "dependent": dependent}
    return dependent, um


def _dep_moves(make_unit, tmp_path, before, after):
    """The dependent's fingerprint before and after an edit to the dependency alone."""
    dependent, um = _two_units(make_unit, tmp_path, before)
    fp1 = compute_unit_fingerprint(dependent, um)
    um.units["dep"] = make_unit(after, name="dep")
    return fp1, compute_unit_fingerprint(dependent, um)


def test_a_dependency_struct_field_moves_the_dependent(make_unit, tmp_path):
    """The dependent bakes the LAYOUT in: a reordered field reads the other slot."""
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public struct Box:\n    i32 w\n    i32 h\n",
        "public struct Box:\n    i32 h\n    i32 w\n")
    assert before != after


def test_a_dependency_struct_field_type_moves_the_dependent(make_unit, tmp_path):
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public struct Box:\n    i32 w\n    i32 h\n",
        "public struct Box:\n    i64 w\n    i32 h\n")
    assert before != after


def test_a_dependency_enum_variant_order_moves_the_dependent(make_unit, tmp_path):
    """A variant's TAG is its position, and the dependent's match compares tags."""
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public enum Sign:\n    Plus\n    Minus\n",
        "public enum Sign:\n    Minus\n    Plus\n")
    assert before != after


def test_a_dependency_enum_payload_moves_the_dependent(make_unit, tmp_path):
    """The widest variant sizes the payload, so a new wide variant resizes the enum."""
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public enum Slot:\n    Filled(i32)\n    Empty\n",
        "public enum Slot:\n    Filled(i32)\n    Empty\n    Big(i64, i64, i64)\n")
    assert before != after


def test_a_dependency_constant_value_moves_the_dependent(make_unit, tmp_path):
    """A constant is FOLDED into the dependent, so the VALUE is part of the interface."""
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public const i32 LIMIT = 10\n",
        "public const i32 LIMIT = 20\n")
    assert before != after


def test_a_dependency_extension_signature_moves_the_dependent(make_unit, tmp_path):
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public struct Box:\n    i32 w\n\nextend Box scaled(i32 k) i32:\n    return self.w * k\n",
        "public struct Box:\n    i32 w\n\nextend Box scaled(i64 k) i32:\n    return self.w\n")
    assert before != after


def test_a_dependency_perk_implementation_moves_the_dependent(make_unit, tmp_path):
    """A perk implementation carries no marker, so it is in no unit's public symbols."""
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public perk Sized:\n    fn size() i32\n\npublic struct Box:\n    i32 w\n",
        "public perk Sized:\n    fn size() i32\n\npublic struct Box:\n    i32 w\n\n"
        "extend Box with Sized:\n    fn size() i32:\n        return Result.Ok(self.w)\n")
    assert before != after


def test_a_dependency_nom_marker_moves_the_dependent(make_unit, tmp_path):
    """`nom` says the CALLEE frees; the caller's cleanup changes with it, and the mode
    rides on the parameter and not on its type."""
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public fn eat(string s) ~:\n    println(s)\n    return Result.Ok(~)\n",
        "public fn eat(nom string s) ~:\n    println(s)\n    return Result.Ok(~)\n")
    assert before != after


def test_a_dependency_error_channel_moves_the_dependent(make_unit, tmp_path):
    """`| E` changes the Result the call site sees, and `ret` alone does not carry it."""
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public enum Bad:\n    Nope\n\npublic fn half(i32 n) i32:\n    return Result.Ok(n / 2)\n",
        "public enum Bad:\n    Nope\n\npublic fn half(i32 n) i32 | Bad:\n    return Result.Ok(n / 2)\n")
    assert before != after


def test_an_unchanged_dependency_leaves_the_dependent_alone(make_unit, tmp_path):
    """No spurious miss: re-parsing the same dependency must give the same digest."""
    src = "public struct Box:\n    i32 w\n\npublic fn make() Box:\n    return Result.Ok(Box(1))\n"
    before, after = _dep_moves(make_unit, tmp_path, src, src)
    assert before == after


def test_a_dependency_body_edit_leaves_the_dependent_alone(make_unit, tmp_path):
    """The digest is the INTERFACE: a private body is the dependency's own business."""
    before, after = _dep_moves(
        make_unit, tmp_path,
        "public fn twice(i32 n) i32:\n    return Result.Ok(n * 2)\n",
        "public fn twice(i32 n) i32:\n    return Result.Ok(n + n)\n")
    assert before == after


# The dependency EDGE: an injected unit and a re-exported one (#593)

def test_an_injected_library_unit_is_a_dependency_edge(make_unit, tmp_path):
    """A source library's units join the unit table; the import spells no user path, so
    `Unit.dependencies` never names them and only the graph knows the edge."""
    consumer = make_unit('use <lib/boxlib>\n' + CLEAN, name="consumer")
    assert consumer.dependencies == []          # the edge is not stored on the unit

    um = UnitManager(root_path=tmp_path)
    lib_v1 = make_unit("public struct Box:\n    i32 w\n    i32 h\n", name="lib/boxlib/boxlib")
    um.units = {"lib/boxlib/boxlib": lib_v1, "consumer": consumer}
    fp1 = compute_unit_fingerprint(consumer, um)

    um.units["lib/boxlib/boxlib"] = make_unit(
        "public struct Box:\n    i32 h\n    i32 w\n", name="lib/boxlib/boxlib")
    assert compute_unit_fingerprint(consumer, um) != fp1


def test_a_shape_two_units_away_moves_the_dependent(make_unit, tmp_path):
    """`public use` re-exports, so a unit names a type its own import does not declare.
    The digest has to walk the whole dependency closure, not the first ring."""
    dependent = make_unit('use "middle"\n' + CLEAN, name="dependent")
    middle = make_unit('public use "far"\n', name="middle")
    um = UnitManager(root_path=tmp_path)
    um.units = {
        "far": make_unit("public struct Box:\n    i32 w\n    i32 h\n", name="far"),
        "middle": middle,
        "dependent": dependent,
    }
    fp1 = compute_unit_fingerprint(dependent, um)

    um.units["far"] = make_unit("public struct Box:\n    i32 h\n    i32 w\n", name="far")
    assert compute_unit_fingerprint(dependent, um) != fp1
