"""The LLVM signature of a function of the program comes from ONE function (#1285, #1287).

`call_signature` (`backend/functions/signature.py`) decides how each parameter and the
answer cross a call boundary: an aggregate of more than 64 bytes crosses by address. The
declaration of a function of this program, of a library function from its manifest, of a
lifted lambda, the type of a function value, the `next` of an iterator, the step of a
HashMap cursor and the SLIR call and return emitters must agree, or a call passes a value
where the callee reads an address.

So in each module of the backend, an `ir.FunctionType(` may stand only in a function
named below with its reason, and each site below calls `call_signature`. A new
`ir.FunctionType(` built from the `ll_type` of the parameters is a second rule, and the
scan refuses it.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

SCANNED = tuple(sorted(str(path.relative_to(BACKEND)) for path in BACKEND.rglob("*.py")))

# (module, enclosing function) -> why it may build an LLVM function type itself.
ALLOWED = {
    ("functions/declarations.py", "_declare_c_main"): "the C entry has the fixed C signature",
    ("runtime/closures.py", "synthesize_thunk"): "the thunk copies the type of its target",
    ("runtime/closures.py", "closure_constant"): "a declaration of a thunk that is used "
                                                 "by its address only",
    ("runtime/closures.py", "get_or_create_env_drop"): "the glue takes the environment only",
    ("runtime/closures.py", "get_or_create_env_clone"): "the glue takes the environment only",
    ("functions/signature.py", "call_signature"): "the seam itself",
    ("destructors.py", "emit_function_value_destructor_from_value"): "the drop glue of a "
                                                                    "function value takes "
                                                                    "the environment only",
    ("expressions/memory.py", "_clone_function_value"): "the clone glue of a function value "
                                                        "takes the environment only",
    ("lifecycle.py", "get_or_emit_lifecycle_func"): "destroy takes an address, and a type "
                                                    "that clones by address takes two "
                                                    "(clones_by_address)",
    ("types/contracts.py", "_contract_func"): "a type that moves by address takes two "
                                              "addresses (takes_address)",
    ("types/display.py", "_fmt_func"): "a type that moves by address takes its address "
                                       "(takes_address)",
    ("runtime/iterators.py", "_drop_type"): "the drop glue takes the state only",
    ("runtime/iterators.py", "_clone_type"): "the clone glue takes the state and answers "
                                             "the iterator value, which is small",
    ("expressions/calls/stdlib/signatures.py", "llvm_function_type"): "a row keeps the "
                                                                      "signature of its "
                                                                      "generator; no row "
                                                                      "crosses the bound",
    ("expressions/calls/stdlib/strings.py", "string_method_function_type"): "a string "
                                                                            "method takes "
                                                                            "strings and "
                                                                            "scalars",
    ("functions/helpers.py", "declare_stdlib_function"): "the declaration of a stdlib "
                                                         "extern, with its fixed C types",
    ("runtime/string_builder.py", "_push_func"): "the push takes a pointer and scalars",
    ("runtime/strings.py", "_declare_and_define_utf8_char_count"): "takes a pointer",
    ("types/arrays/methods/utf8_validate.py", "get_or_emit_utf8_validate"): "takes a pointer "
                                                                           "and a length",
    ("types/primitives/to_str.py", "generate_module_ir"): "a primitive is never above the "
                                                          "bound",
}

# (module, function) that must call `call_signature`.
REQUIRED = (
    ("functions/declarations.py", "emit_func_decl"),
    ("functions/declarations.py", "emit_extension_method_decl"),
    ("codegen_llvm.py", "_declare_library_functions"),
    ("runtime/closures.py", "_env_prepended_signature"),
    ("slir/closures.py", "declare_lifted"),
    ("slir/emit.py", "_open"),
    ("slir/emit.py", "_signature"),
    ("runtime/iterators.py", "_next_type"),
    ("generics/hashmap/cursor.py", "_step_function"),
)

SEAM = "call_signature"


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _is_function_type(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "FunctionType"
            and isinstance(func.value, ast.Name) and func.value.id == "ir")


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Each top-level function and method by name. A nested function belongs to the
    function around it."""
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[item.name] = item
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
    return found


def function_types_outside(source: str, module: str) -> list[str]:
    """`<function>:<line>` of each `ir.FunctionType(` that no ALLOWED row covers."""
    tree = ast.parse(source)
    refused = []
    seen: set[int] = set()
    for name, function in _functions(tree).items():
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and _is_function_type(node):
                seen.add(id(node))
                if (module, name) not in ALLOWED:
                    refused.append(f"{name}:{node.lineno}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_function_type(node) and id(node) not in seen:
            refused.append(f"<module>:{node.lineno}")
    return refused


def _calls_seam(function: ast.AST) -> bool:
    return any(isinstance(node, ast.Call) and _called_name(node) == SEAM
               for node in ast.walk(function))


def _source(module: str) -> str:
    return (BACKEND / module).read_text(encoding="utf-8")


def test_the_scan_sees_a_second_rule():
    """Always-fires control: a declaration that builds its own type is refused."""
    source = (
        "def emit_func_decl(self, fn):\n"
        "    params = [self.codegen.types.ll_type(t) for _, t in fn.params]\n"
        "    return ir.FunctionType(ret, params)\n"
    )
    assert function_types_outside(source, "functions/declarations.py") == [
        "emit_func_decl:3"]


def test_no_declaration_site_builds_its_own_signature():
    refused = {module: function_types_outside(_source(module), module)
               for module in SCANNED}
    refused = {module: sites for module, sites in refused.items() if sites}
    assert not refused, (
        f"an ir.FunctionType outside call_signature: {refused}. Build the signature with "
        f"call_signature (backend/functions/signature.py), or name the site in ALLOWED "
        f"with its reason")


def test_every_allowed_row_is_live():
    """A row for a site that builds no type any more must go."""
    for module, name in ALLOWED:
        function = _functions(ast.parse(_source(module))).get(name)
        assert function is not None, f"{module}: no function {name}"
        assert any(isinstance(node, ast.Call) and _is_function_type(node)
                   for node in ast.walk(function)), f"{module}:{name} builds no type"


def test_every_site_calls_the_seam():
    for module, name in REQUIRED:
        function = _functions(ast.parse(_source(module))).get(name)
        assert function is not None, f"{module}: no function {name}"
        assert _calls_seam(function), f"{module}:{name} does not call {SEAM}"


def test_no_stdlib_row_crosses_the_bound():
    """A stdlib row keeps the signature of its generator, so no row may take or answer an
    aggregate that moves by address."""
    import importlib
    import pkgutil

    import sushi_lang.sushi_stdlib.src as src
    from sushi_lang.backend.expressions.calls.stdlib.signatures import (
        llvm_param_type, llvm_return_type)
    from sushi_lang.backend.types.arrays.runs import moves_by_address
    from sushi_lang.sushi_stdlib.src.signatures import Signature

    crossing = []
    rows = 0
    for info in pkgutil.walk_packages(src.__path__, f"{src.__name__}."):
        module = importlib.import_module(info.name)
        for table in vars(module).values():
            if not isinstance(table, dict):
                continue
            for name, sig in table.items():
                if not isinstance(sig, Signature):
                    continue
                rows += 1
                types = [llvm_return_type(sig)] + [llvm_param_type(p) for p in sig.params]
                if any(t is not None and moves_by_address(t) for t in types):
                    crossing.append(f"{info.name}:{name}")
    assert rows, "the scan found no stdlib row"
    assert not crossing, f"a stdlib row crosses the bound: {crossing}"
