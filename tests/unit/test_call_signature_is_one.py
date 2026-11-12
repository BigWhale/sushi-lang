"""The LLVM signature of a function of the program comes from ONE function (#1285).

`call_signature` (`backend/functions/signature.py`) decides how each parameter and the
answer cross a call boundary: a fixed array of more than 64 bytes crosses by address. The
declaration of a function of this program, of a library function from its manifest, of a
lifted lambda, the type of a function value and the SLIR call and return emitters must
agree, or a call passes a value where the callee reads an address.

So in each module that declares or calls such a function, an `ir.FunctionType(` may stand
only in a function named below with its reason, and each site below calls
`call_signature`. A new `ir.FunctionType(` built from the `ll_type` of the parameters is a
second rule, and the scan refuses it.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

SCANNED = (
    "functions/declarations.py",
    "codegen_llvm.py",
    "runtime/closures.py",
    "slir/closures.py",
    "slir/emit.py",
)

# (module, enclosing function) -> why it may build an LLVM function type itself.
ALLOWED = {
    ("functions/declarations.py", "_declare_c_main"): "the C entry has the fixed C signature",
    ("runtime/closures.py", "synthesize_thunk"): "the thunk copies the type of its target",
    ("runtime/closures.py", "closure_constant"): "a declaration of a thunk that is used "
                                                 "by its address only",
    ("runtime/closures.py", "get_or_create_env_drop"): "the glue takes the environment only",
    ("runtime/closures.py", "get_or_create_env_clone"): "the glue takes the environment only",
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
