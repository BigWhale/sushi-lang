"""The `.slib` manifest writers and readers agree on every record's keys (#985).

A source scan of the Python modules, never a build. For each record kind the scan
collects the string keys the writers put into a record and the string keys the readers
take out of one, and it fails when a reader reads a key that no writer writes. The
Sushi reader (`toolchain/src/slib_info.sushi`) is listed by hand in `SUSHI_READER`.

A writer is a whole function: every string key of a dict literal in it, and every
string key it stores by subscript, counts for each kind the function is listed under.
A reader is a `(function, variable)` pair: `var.get("k")`, `var["k"]` and `"k" in var`
count for the kind the pair names. Every string-key read in a reader function must be
classified, so a new read cannot slip past the table.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

MANIFEST = "sushi_lang/backend/library_manifest.py"
TEMPLATES = "sushi_lang/semantics/library_templates.py"
PIPELINE = "sushi_lang/compiler/pipeline.py"
REGISTRATION = "sushi_lang/semantics/library_registration.py"
REGISTRY = "sushi_lang/semantics/library_registry.py"
LIB_INFO = "sushi_lang/compiler/lib_info.py"
NAMESPACES = "sushi_lang/semantics/passes/namespaces.py"

G = "LibraryManifestGenerator."
R = "LibraryRegistration."
Y = "LibraryRegistry."

_DOCUMENTED = (TEMPLATES, "with_doc")

WRITERS: dict[str, list[tuple[str, str]]] = {
    "manifest": [(MANIFEST, G + "generate"), (PIPELINE, "_resolve_library_imports")],
    "templates": [(MANIFEST, G + "_extract_templates"), (MANIFEST, G + "_closure_records")],
    "function": [(MANIFEST, G + "_extract_public_functions"),
                 (MANIFEST, G + "_closure_records"), (MANIFEST, G + "_generic_templates"),
                 (TEMPLATES, "signature_record"), (TEMPLATES, "serialize_generic_function"),
                 _DOCUMENTED],
    "param": [(TEMPLATES, "signature_record")],
    "type_param": [(TEMPLATES, "_type_param_records")],
    "target_bound": [(TEMPLATES, "target_bound_records")],
    "constant": [(MANIFEST, G + "_extract_public_bindings"),
                 (MANIFEST, G + "_closure_records"), _DOCUMENTED],
    "struct": [(MANIFEST, G + "_extract_public_types"), (MANIFEST, G + "_struct_members"),
               _DOCUMENTED],
    "field": [(MANIFEST, G + "_struct_members"), _DOCUMENTED],
    "enum": [(MANIFEST, G + "_extract_public_types"), (MANIFEST, G + "_enum_members"),
             _DOCUMENTED],
    "variant": [(MANIFEST, G + "_enum_members"), _DOCUMENTED],
    "generic_type": [(TEMPLATES, "serialize_generic_struct"),
                     (TEMPLATES, "serialize_generic_enum"),
                     (MANIFEST, G + "_generic_templates"), _DOCUMENTED],
    "private_type": [(MANIFEST, G + "_closure_records")],
    "perk": [(TEMPLATES, "serialize_perk"), (MANIFEST, G + "_shipped_perks"), _DOCUMENTED],
    "perk_impl": [(TEMPLATES, "serialize_perk_impl"),
                  (TEMPLATES, "serialize_generic_perk_impl"),
                  (MANIFEST, G + "_generic_perk_impl_templates"),
                  (MANIFEST, G + "_concrete_perk_impls"), _DOCUMENTED],
    "method": [(TEMPLATES, "method_record"), (TEMPLATES, "signature_record"),
               (TEMPLATES, "serialize_perk_impl"), _DOCUMENTED],
    "extension": [(TEMPLATES, "serialize_extension"),
                  (TEMPLATES, "serialize_generic_extension"),
                  (TEMPLATES, "method_receiver_record"), (TEMPLATES, "method_record"),
                  (TEMPLATES, "signature_record"),
                  (MANIFEST, G + "_concrete_extensions"),
                  (MANIFEST, G + "_generic_extension_templates"),
                  (MANIFEST, G + "_extract_templates"), _DOCUMENTED],
    "doc": [(TEMPLATES, "doc_record")],
    "example": [(TEMPLATES, "doc_record")],
    "reexport": [(MANIFEST, G + "_extract_reexports")],
    "not_exported": [(MANIFEST, G + "_extract_not_exported")],
    "foreign_extension": [(MANIFEST, G + "_extract_foreign_extensions")],
    "conversion": [(MANIFEST, G + "_extract_conversions"), _DOCUMENTED],
    "dependency": [(MANIFEST, G + "_extract_dependencies")],
    "closure_summary": [(MANIFEST, G + "_closure_records")],
}

# (module, function) -> {variable: kind}. A tuple of kinds is a variable that holds a
# record of any of them, and a key it reads must be written for one. A module in `WHOLE_MODULE_READERS` must have
# every string-key read of every function classified here; the other modules are read
# only in the functions listed.
READERS: dict[tuple[str, str], dict[str, str | tuple[str, ...]]] = {
    (REGISTRATION, R + "seed_perks"): {"record": "perk"},
    (REGISTRATION, R + "_template_records"): {"manifest": "manifest"},
    (REGISTRATION, R + "_build_registry"): {"manifest": "manifest"},
    (REGISTRATION, R + "_library_file"): {"manifest": "manifest"},
    (REGISTRATION, R + "_register_constants"): {
        "manifest": "manifest", "templates": "templates"},
    (REGISTRATION, R + "_register_one_constant"): {"record": "constant"},
    (REGISTRATION, R + "_register_private_types"): {"record": "private_type"},
    (REGISTRATION, R + "_register_perk_impls"): {"record": "perk_impl", "m": "method"},
    (REGISTRATION, R + "_register_generic_perk_impls"): {
        "record": "perk_impl", "manifest": "manifest"},
    (REGISTRATION, R + "_record_public_types"): {
        "manifest": "manifest", "record": ("struct", "enum")},
    (REGISTRATION, R + "_register_extensions"): {"record": "extension"},
    (REGISTRATION, R + "_register_conversions"): {"record": "conversion"},
    (REGISTRATION, R + "_register_functions"): {
        "manifest": "manifest", "record": "function"},
    (REGISTRATION, R + "_register_generic_extensions"): {
        "record": "extension", "manifest": "manifest"},
    (REGISTRATION, R + "_register_generic_functions"): {
        "record": "function", "manifest": "manifest", "rec_tp": "type_param"},
    (REGISTRATION, R + "_register_generic_types"): {
        "record": "generic_type", "manifest": "manifest"},
    (REGISTRY, "manifest_reexports"): {},
    (REGISTRY, "reexported_libraries"): {"record": "reexport"},
    (REGISTRY, "manifest_dependencies"): {},
    (REGISTRY, "manifest_conversions"): {},
    (REGISTRY, "library_dependencies"): {"record": "dependency"},
    (REGISTRY, "stdlib_dependencies"): {"record": "dependency"},
    (NAMESPACES, "build_compiled_library_namespaces"): {"record": "dependency"},
    (NAMESPACES, "_dependency_use"): {"record": "dependency"},
    (REGISTRY, Y + "register_library"): {
        "manifest": "manifest", "templates": "templates", "func_info": "function",
        "record": "not_exported"},
    (REGISTRY, Y + "_parse_structs"): {"struct_info": "struct", "f": "field"},
    (REGISTRY, Y + "_parse_enums"): {"enum_info": "enum", "v": "variant"},
    (REGISTRY, Y + "_parse_functions"): {"func_info": "function"},
    (REGISTRY, "parse_signature"): {"func_info": ("function", "extension"), "p": "param"},
    (TEMPLATES, "deserialize_perk_impl"): {"record": "perk_impl"},
    (TEMPLATES, "deserialize_extension"): {"record": "extension"},
    (LIB_INFO, "_render_params"): {"param": "param"},
    (LIB_INFO, "_render_type_params"): {"tp": "type_param"},
    (LIB_INFO, "_render_signature"): {"func": ("function", "method", "extension")},
    (LIB_INFO, "_reexport_target"): {"record": "reexport"},
    (LIB_INFO, "_doc_tags"): {"doc": "doc", "p": "param"},
    (LIB_INFO, "_print_doc_record"): {"doc": "doc", "example": "example"},
    (LIB_INFO, "_print_doc"): {"owner": "function"},
    (LIB_INFO, "_render_impl_target"): {"impl": "perk_impl"},
    (LIB_INFO, "_bounded_args"): {"record": ("extension", "perk_impl", "foreign_extension"),
                                  "bound": "target_bound"},
    (LIB_INFO, "_render_extension_target"): {"ext": ("extension", "foreign_extension")},
    (LIB_INFO, "_named_suffix"): {"record": ("struct", "enum")},
    (LIB_INFO, "_reexport_line"): {"record": "reexport"},
    (LIB_INFO, "_constant_line"): {"const": "constant"},
    (LIB_INFO, "_variable_line"): {"var": "constant"},
    (LIB_INFO, "_struct_line"): {"struct": "struct"},
    (LIB_INFO, "_field_line"): {"field": "field"},
    (LIB_INFO, "_enum_line"): {"enum": "enum"},
    (LIB_INFO, "_variant_line"): {"variant": "variant"},
    (LIB_INFO, "_perk_line"): {"perk": "perk"},
    (LIB_INFO, "_impl_line"): {"impl": "perk_impl"},
    (LIB_INFO, "_extension_line"): {"ext": "extension"},
    (LIB_INFO, "_foreign_line"): {"claim": "foreign_extension"},
    (LIB_INFO, "_conversion_line"): {"conv": "conversion"},
    (LIB_INFO, "_dependency_line"): {"dep": "dependency"},
    (LIB_INFO, "_own_doc"): {"record": "function"},
    (LIB_INFO, "_unit_doc"): {"metadata": "manifest"},
    (LIB_INFO, "_generic_named"): {"record": "generic_type"},
    (LIB_INFO, "_print_section"): {"metadata": "manifest"},
    (LIB_INFO, "print_library_info"): {"metadata": "manifest"},
}

WHOLE_MODULE_READERS = (REGISTRATION, REGISTRY)

# `toolchain/src/slib_info.sushi`, by hand: the keys it reads, per record kind.
SUSHI_READER: dict[str, set[str]] = {
    "manifest": {"library_name", "library_version", "kind", "requires_compiler",
                 "platform", "compiler_version", "compiled_at", "sushi_lib_version",
                 "units", "unit_docs", "reexports", "public_functions",
                 "public_constants", "public_variables", "structs", "enums",
                 "templates", "foreign_extensions", "conversions", "dependencies"},
    "templates": {"generic_functions", "generic_structs", "generic_enums", "perks",
                  "perk_impls", "generic_perk_impls", "extensions", "generic_extensions"},
    "function": {"name", "params", "return_type", "error_type", "type_params", "doc"},
    "param": {"name", "type", "mode"},
    "type_param": {"name", "constraints"},
    "constant": {"name", "type", "doc"},
    "struct": {"name", "fields", "is_generic", "type_params", "doc"},
    "field": {"name", "type", "doc"},
    "enum": {"name", "variants", "is_generic", "type_params", "doc"},
    "variant": {"name", "has_data", "data_types", "doc"},
    "generic_type": {"name", "type_params", "doc"},
    "perk": {"name", "methods", "doc"},
    "perk_impl": {"type", "type_args", "target_bounds", "perk", "methods", "doc"},
    "target_bound": {"param", "perks"},
    "method": {"name", "params", "return_type", "error_type", "self_mode", "doc"},
    "extension": {"type", "static", "name", "params", "return_type", "error_type",
                  "type_params", "target_bounds", "self_mode", "doc"},
    "doc": {"summary", "body", "params", "examples", "returns", "errors"},
    "example": {"code", "caption"},
    "reexport": {"unit", "path", "kind"},
    "foreign_extension": {"type", "method", "target_bounds"},
    "conversion": {"source", "target", "doc"},
    "dependency": {"path", "kind", "library_name", "library_version", "units"},
}

# A read no writer answers, known and not yet fixed. It may only shrink.
KNOWN_UNWRITTEN: set[tuple[str, str, str, str]] = set()


def _tree(path: str) -> ast.Module:
    return ast.parse((REPO / path).read_text(encoding="utf-8"))


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Every function of a module by qualified name: `Class.method` or `fn`."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[f"{prefix}{child.name}"] = child

    visit(tree, "")
    return found


def _str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _module_str_tuples(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    """Module-level `NAME = ("a", "b")` tables, so `for k in NAME: r[k] = ...` counts."""
    tables: dict[str, tuple[str, ...]] = {}
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, (ast.Tuple, ast.List))):
            items = [_str(e) for e in stmt.value.elts]
            if items and all(i is not None for i in items):
                tables[stmt.targets[0].id] = tuple(i for i in items if i is not None)
    return tables


def _written_keys(path: str, qualname: str) -> set[str]:
    tree = _tree(path)
    fn = _functions(tree).get(qualname)
    assert fn is not None, f"writer {path}:{qualname} does not exist"
    tables = _module_str_tuples(tree)
    loop_tables = {
        node.target.id: tables[node.iter.id]
        for node in ast.walk(fn)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
        and isinstance(node.iter, ast.Name) and node.iter.id in tables
    }
    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            keys.update(k for k in (_str(key) for key in node.keys) if k is not None)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            literal = _str(node.slice)
            if literal is not None:
                keys.add(literal)
            elif isinstance(node.slice, ast.Name) and node.slice.id in loop_tables:
                keys.update(loop_tables[node.slice.id])
    return keys


def written_by_kind() -> dict[str, set[str]]:
    return {kind: set().union(*(_written_keys(p, q) for p, q in rows))
            for kind, rows in WRITERS.items()}


def _annotation_nodes(fn: ast.AST) -> set[int]:
    """The nodes inside a type annotation: `list['Unit']` is not a manifest read."""
    marked: set[int] = set()
    for node in ast.walk(fn):
        for attr in ("annotation", "returns"):
            ann = getattr(node, attr, None)
            if isinstance(ann, ast.AST):
                marked.update(id(sub) for sub in ast.walk(ann))
    return marked


def _reads_in(fn: ast.AST) -> list[tuple[str, str]]:
    """Every `(variable, key)` string-key read in one function."""
    reads: list[tuple[str, str]] = []
    annotations = _annotation_nodes(fn)
    for node in ast.walk(fn):
        if id(node) in annotations:
            continue
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.args and _str(node.args[0]) is not None):
            reads.append((node.func.value.id, _str(node.args[0]) or ""))
        elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
              and isinstance(node.value, ast.Name) and _str(node.slice) is not None):
            reads.append((node.value.id, _str(node.slice) or ""))
        elif (isinstance(node, ast.Compare) and len(node.ops) == 1
              and isinstance(node.ops[0], (ast.In, ast.NotIn))
              and _str(node.left) is not None
              and isinstance(node.comparators[0], ast.Name)):
            reads.append((node.comparators[0].id, _str(node.left) or ""))
    return reads


def _kinds(kind: str | tuple[str, ...]) -> tuple[str, ...]:
    return kind if isinstance(kind, tuple) else (kind,)


def python_reads() -> tuple[list[tuple[str, str, str, tuple[str, ...], str]], list[str]]:
    """`(module, function, variable, kinds, key)` per classified read, and the faults."""
    reads: list[tuple[str, str, str, tuple[str, ...], str]] = []
    faults: list[str] = []
    modules = {path for path, _fn in READERS}
    for path in sorted(modules):
        functions = _functions(_tree(path))
        for row_path, qualname in READERS:
            if row_path == path and qualname not in functions:
                faults.append(f"reader {path}:{qualname} does not exist")
        for qualname, fn in functions.items():
            variables = READERS.get((path, qualname))
            if variables is None:
                if path in WHOLE_MODULE_READERS and _reads_in(fn):
                    faults.append(f"{path}:{qualname} reads manifest keys and has no row")
                continue
            for var, key in _reads_in(fn):
                kind = variables.get(var)
                if kind is None:
                    faults.append(f"{path}:{qualname} reads {var}[{key!r}] unclassified")
                    continue
                reads.append((path, qualname, var, _kinds(kind), key))
    return reads, faults


def test_every_reader_read_is_classified():
    _reads, faults = python_reads()
    assert not faults, "\n".join(faults)


def test_every_kind_a_reader_names_has_a_writer():
    kinds = {k for variables in READERS.values() for kind in variables.values()
             for k in _kinds(kind)}
    kinds |= set(SUSHI_READER)
    assert not kinds - set(WRITERS), sorted(kinds - set(WRITERS))


def test_every_python_read_key_is_written():
    written = written_by_kind()
    reads, _faults = python_reads()
    unwritten = {
        (path, qualname, var, key) for path, qualname, var, kinds, key in reads
        if not any(key in written[kind] for kind in kinds)
    }
    assert unwritten <= KNOWN_UNWRITTEN, sorted(unwritten - KNOWN_UNWRITTEN)
    assert KNOWN_UNWRITTEN <= unwritten, (
        "a known unwritten read is gone; shrink KNOWN_UNWRITTEN: "
        f"{sorted(KNOWN_UNWRITTEN - unwritten)}")


def test_every_sushi_read_key_is_written():
    written = written_by_kind()
    unwritten = sorted((kind, key) for kind, keys in SUSHI_READER.items()
                       for key in keys if key not in written[kind])
    assert not unwritten, unwritten


def test_the_sushi_reader_table_names_keys_the_reader_spells():
    text = (REPO / "toolchain/src/slib_info.sushi").read_text(encoding="utf-8")
    missing = sorted({key for keys in SUSHI_READER.values() for key in keys
                      if f'"{key}"' not in text})
    assert not missing, missing


# `check_manifest` reads the manifest through `MANIFEST_SCHEMA`: each row names a record
# kind and a key. Two schema kinds are writer kinds under another name.
SCHEMA_KIND_WRITER = {"helper": "function", "closure_constant": "constant"}


def test_every_schema_key_is_written():
    from sushi_lang.backend.library_format import MANIFEST_SCHEMA
    written = written_by_kind()
    unwritten = sorted((kind, key) for kind, key, _type, _required in MANIFEST_SCHEMA
                       if key not in written[SCHEMA_KIND_WRITER.get(kind, kind)])
    assert not unwritten, unwritten


def test_the_sushi_schema_is_the_python_schema():
    """`<toolchain/slib>` checks a manifest against the same rows, in the same order."""
    import re

    from sushi_lang.backend.library_format import MANIFEST_SCHEMA
    text = (REPO / "sushi_lang/sushi_stdlib/src_sushi/toolchain/slib.sushi").read_text(
        encoding="utf-8")
    block = re.search(r"const SchemaRow\[(\d+)\] SLIB_MANIFEST_SCHEMA = \[\n(.*?)\n\]",
                      text, re.S)
    assert block is not None, "SLIB_MANIFEST_SCHEMA is gone from toolchain/slib.sushi"
    lines = block.group(2).split("\n")
    row = re.compile(r"^    SchemaRow\('([^']*)', '([^']*)', '([^']*)', "
                     r"Presence\.(Required\(\)|Optional\(\)|RequiredWhen\('([^']*)'\))\),?$")
    matched = [row.match(line) for line in lines]
    assert all(matched), [line for line, m in zip(lines, matched, strict=True) if m is None]
    presence = {"Required()": "yes", "Optional()": "no"}
    rows = [(m.group(1), m.group(2), m.group(3), presence.get(m.group(4), m.group(5)))
            for m in matched if m is not None]
    assert rows == list(MANIFEST_SCHEMA)
    assert int(block.group(1)) == len(MANIFEST_SCHEMA)


def test_every_report_section_key_is_written():
    """The report reads its sections through the `_SECTIONS` table, by a key the scan
    above cannot see: each section key and each member key must be written."""
    from sushi_lang.compiler.lib_info import _SECTIONS
    written = written_by_kind()
    member_kinds = {"fields": ("struct",), "variants": ("enum",),
                    "methods": ("perk", "perk_impl")}
    unwritten = [(section.title, key) for section in _SECTIONS for key in section.keys
                 if key not in written["templates" if section.in_templates else "manifest"]]
    for section in _SECTIONS:
        if section.members is not None:
            key = section.members[0]
            unwritten += [(section.title, key) for kind in member_kinds.get(key, ("",))
                          if key not in written.get(kind, set())]
    assert not unwritten, unwritten
