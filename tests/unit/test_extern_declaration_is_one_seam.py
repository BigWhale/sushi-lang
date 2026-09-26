"""A stdlib generator declares an external function through `declare_extern` (#910).

`libc_declarations.declare_extern(module, name, ret, args, var_arg=False)` is the one
accessor: it returns the global of that name when the module has one, and declares it
otherwise. Outside `libc_declarations.py`, this gate refuses the hand-written spellings of
the same job in `sushi_lang/sushi_stdlib/src/**`:

- a function that constructs an `ir.Function(...)` and appends no basic block;
- a `get_global(...)` in a `try` that catches `KeyError`;
- an `if` that reads `.globals` and constructs an `ir.Function(...)` in a branch;
- `if X in <m>.globals: return <m>.globals[...]` in a function that constructs an
  `ir.Function(...)`;
- a `declare_intrinsic(...)` call.

A "define once" guard (`if not func.is_declaration: return func`) defines a body, and a
guard in front of an `ir.GlobalVariable` declares a variable: neither is matched.

The gate has no exceptions (#911).
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "sushi_lang" / "sushi_stdlib" / "src"

SEAM_MODULE = "libc_declarations.py"

def _is_ir_function_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Function"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ir")


def _is_method_call(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name)


def _reads_globals(node: ast.AST, from_get: set[str]) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "globals":
            return True
        if isinstance(sub, ast.Name) and sub.id in from_get:
            return True
    return False


def _names_bound_from_globals_get(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign)
                and _is_method_call(node.value, "get")
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Attribute)
                and node.value.func.value.attr == "globals"):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _catches_key_error(handler: ast.ExceptHandler) -> bool:
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(k, ast.Name) and k.id == "KeyError" for k in kinds)


def _is_return_of_globals(stmts: list[ast.stmt]) -> bool:
    return (len(stmts) == 1
            and isinstance(stmts[0], ast.Return)
            and isinstance(stmts[0].value, ast.Subscript)
            and isinstance(stmts[0].value.value, ast.Attribute)
            and stmts[0].value.value.attr == "globals")


def offences_in(source: str) -> list[tuple[int, str]]:
    """Every hand-written declaration in one module, as (line, kind)."""
    found: list[tuple[int, str]] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if _is_method_call(node, "declare_intrinsic"):
            found.append((node.lineno, "declare_intrinsic"))
        if isinstance(node, ast.Try) and any(_catches_key_error(h) for h in node.handlers):
            if any(_is_method_call(s, "get_global") for b in node.body for s in ast.walk(b)):
                found.append((node.lineno, "get_global in try/except KeyError"))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(ast.walk(fn))
        builds = any(_is_ir_function_call(n) for n in nodes)
        if builds and not any(_is_method_call(n, "append_basic_block") for n in nodes):
            found.append((fn.lineno, f"ir.Function with no body in {fn.name}"))
        from_get = _names_bound_from_globals_get(fn)
        for n in nodes:
            if not (isinstance(n, ast.If) and _reads_globals(n.test, from_get)):
                continue
            branch = [s for stmt in n.body + n.orelse for s in ast.walk(stmt)]
            if any(_is_ir_function_call(s) for s in branch):
                found.append((n.lineno, "ir.Function behind a .globals guard"))
            elif builds and _is_return_of_globals(n.body):
                found.append((n.lineno, "return .globals[...] guard"))
    return sorted(set(found))


def _offending_files() -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == SEAM_MODULE:
            continue
        hits = offences_in(path.read_text())
        if hits:
            result[rel] = hits
    return result


def test_no_hand_written_declaration_outside_the_seam():
    offending = _offending_files()
    assert not offending, (
        "declare an external function with libc_declarations.declare_extern: "
        + "; ".join(f"{f}:{line} {kind}" for f, hits in offending.items() for line, kind in hits))


def test_the_seam_module_exists():
    assert (SRC / SEAM_MODULE).is_file()
    assert "def declare_extern(" in (SRC / SEAM_MODULE).read_text()


_DECLARATION_SPELLINGS = [
    'def f(module):\n'
    '    if "x" in module.globals:\n'
    '        return module.globals["x"]\n'
    '    return ir.Function(module, ty, name="x")\n',
    'def f(module):\n'
    '    try:\n'
    '        return module.get_global("x")\n'
    '    except KeyError:\n'
    '        return ir.Function(module, ty, name="x")\n',
    'def f(module):\n'
    '    callee = module.globals.get("x")\n'
    '    if callee is None:\n'
    '        callee = ir.Function(module, ty, name="x")\n'
    '    func = ir.Function(module, ty, name="y")\n'
    '    func.append_basic_block("entry")\n',
    'def f(module):\n'
    '    if "x" not in module.globals:\n'
    '        g = ir.Function(module, ty, name="x")\n'
    '    else:\n'
    '        g = module.globals["x"]\n'
    '    func = ir.Function(module, ty, name="y")\n'
    '    func.append_basic_block("entry")\n',
    'def f(module):\n'
    '    return ir.Function(module, ty, name="x")\n',
    'def f(builder):\n'
    '    return builder.module.declare_intrinsic("llvm.memcpy", [a, b, c])\n',
]

_NOT_DECLARATIONS = [
    'def f(module):\n'
    '    if name in module.globals:\n'
    '        func = module.globals[name]\n'
    '        if not func.is_declaration:\n'
    '            return func\n'
    '    func = ir.Function(module, ty, name=name)\n'
    '    func.append_basic_block("entry")\n'
    '    return func\n',
    'def f(module):\n'
    '    if "environ" in module.globals:\n'
    '        return module.globals["environ"]\n'
    '    return ir.GlobalVariable(module, ty, name="environ")\n',
    'def f(module):\n'
    '    if name not in module.globals:\n'
    '        g = ir.GlobalVariable(module, ty, name=name)\n'
    '    return module.globals[name]\n',
    'def f(module):\n'
    '    callee = module.globals.get("x")\n'
    '    if callee is None:\n'
    '        raise RuntimeError("x must be defined first")\n'
    '    func = ir.Function(module, ty, name="y")\n'
    '    func.append_basic_block("entry")\n',
]


def test_every_declaration_spelling_is_matched():
    for source in _DECLARATION_SPELLINGS:
        assert offences_in(source), source


def test_a_definition_or_a_variable_is_not_matched():
    for source in _NOT_DECLARATIONS:
        assert not offences_in(source), (source, offences_in(source))
