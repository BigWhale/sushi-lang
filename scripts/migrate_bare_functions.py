#!/usr/bin/env python3
"""Rewrite the fixtures under tests/ to the bare-function surface.

A one-time migration: a callable has an error channel only when its signature writes
`| E`. The implicit `Result@(T, StdError)` goes. Rules of the rewrite:

- A function without `| E` that cannot fail becomes BARE when every call of it only
  unwraps it (`??`, `.realise(...)`) or discards it. Its `return Result.Ok(x)` becomes
  `return x`, and the unwraps at its call sites go.
- Every other function without `| E` gains `| StdError`, and nothing else changes.
- `fn main() i32:` answers the code: `return Result.Ok(n)` becomes `return n` and
  `return Result.Err(...)` becomes `return 1`. A main that still propagates moves its
  body into `fn run(...) i32 | StdError:`.
- A unit whose function values can fail keeps them: every `fn(A) -> B` gains
  `| StdError` there. In every other unit a lambda and a function value are bare.
- The stdlib calls that become bare lose their unwraps: `map`, `filter`, `fold`,
  `compose`, `adler32`, `join`, `basename`.

A case the script cannot decide is kept and listed in the report for review by hand.
The script is deleted when the migration is complete.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Union

from lark import Token, Tree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sushi_lang.internals.parser import build_parser, parse_hole  # noqa: E402

HELPERS = ROOT / "tests" / "libs" / "helpers"
STDLIB_SOURCES = ROOT / "sushi_lang" / "sushi_stdlib" / "src_sushi"

# The corpus being rewritten. In the stdlib corpus a `use <m>` is a unit of the corpus,
# and a public function keeps its channel unless it is named here (rule 11: a public
# function keeps a channel when there is any doubt).
CORPORA = {
    "tests": "tests/**/*.sushi",
    "stdlib": "sushi_lang/sushi_stdlib/src_sushi/**/*.sushi",
    "toolchain": "toolchain/src/**/*.sushi",
    "docs": "docs/**/*.sushi",
    "other": "benchmark/**/*.sushi",
}
PUBLIC_BARE = {"adler32", "join", "basename", "map", "filter", "fold", "compose"}
# Private stdlib helpers that write `| E` and whose documentation says they do not fail.
# They become bare when the analysis agrees, and lose the `| E`.
EXPLICIT_BARE = {"signed", "fixed_lit", "fixed_dist", "put_code", "hash3", "match_len",
                 "deflate_fixed", "header_flg", "le_uint", "value_kind", "type_words",
                 "record_fault", "is_required", "value_fault"}
MODE = {"corpus": "tests"}

# Fixtures whose subject is CW2511 or a `??` in main. They are rewritten by hand.
CW2511_FIXTURES = {
    "tests/types/result/warnings/test_warn_propagation_in_main.sushi",
    "tests/error_handling/try_in_main/test_warn_try_operator_in_main.sushi",
    "tests/error_handling/try_in_main/test_warn_try_in_main_nested_return.sushi",
    "tests/diagnostics/call_span/test_warn_propagation_in_main_span.sushi",
    "tests/closures/try_channel/test_closure_try_in_main_no_warn.sushi",
}

# Stdlib functions that are bare after the migration, by the module that exports them.
STDLIB_BARE_FUNCTIONS = {
    "collections/iter": {"map", "filter", "fold", "compose"},
    "compression/zlib": {"adler32"},
    "io/path": {"join", "basename"},
}
COMBINATOR_METHODS = {"map", "filter", "fold"}

Node = Union[Tree, Token]


def start_of(node: Node) -> int:
    return node.start_pos if isinstance(node, Token) else node.meta.start_pos


def end_of(node: Node) -> int:
    return node.end_pos if isinstance(node, Token) else node.meta.end_pos


def is_tree(node: object, *names: str) -> bool:
    return isinstance(node, Tree) and (not names or node.data in names)


def is_name(node: object, value: Optional[str] = None) -> bool:
    return (isinstance(node, Token) and node.type == "NAME"
            and (value is None or node.value == value))


def method_name(method_call: Tree) -> str:
    return str(method_call.children[0].children[0])


def own_args(call: Tree) -> Optional[Tree]:
    """The `positional_args` of this call or method call, not of one nested in it."""
    for sub in call.iter_subtrees_topdown():
        if sub.data == "positional_args":
            return sub
    return None


def trees(node: Node) -> Iterator[Tree]:
    return (c for c in node.children if isinstance(c, Tree)) if isinstance(node, Tree) else iter(())


# ----------------------------------------------------------------------------------
# The model


@dataclass(eq=False)
class Callable:
    unit: "Unit"
    kind: str                       # "fn", "main", "method", "lambda"
    node: Tree
    parent: Optional["Callable"] = None
    fn_names: set[str] = field(default_factory=set)
    combinator_arg: bool = False
    explicit_channel: bool = False  # a lambda with `-> T | E`, a method with `| E`

    def lookup_fn_name(self, name: str) -> bool:
        c: Optional[Callable] = self
        while c is not None:
            if name in c.fn_names:
                return True
            c = c.parent
        return False


@dataclass(eq=False)
class Function(Callable):
    name: str = ""
    public: bool = False
    ret: Optional[Tree] = None
    err: Optional[Tree] = None
    params: Optional[Tree] = None

    @property
    def implicit(self) -> bool:
        return (self.kind == "fn" and self.err is None and self.ret is not None
                and not returns_result(self.ret))

    @property
    def drops_its_channel(self) -> bool:
        return (MODE["corpus"] == "stdlib" and not self.public and self.kind == "fn"
                and self.err is not None and self.name in EXPLICIT_BARE)


def returns_result(ret: Tree) -> bool:
    return (is_tree(ret, "generic_type_t") and is_name(ret.children[0], "Result"))


@dataclass(eq=False)
class Call:
    """A call whose follow-up decides how its result is used."""
    unit: "Unit"
    target: object                  # Function, "stdlib", ("fnvalue", Callable)
    followup: str                   # "try", "realise", "method", "discard", "value"
    drop: Optional[tuple[int, int]] # the span that removes the unwrap
    where: Callable


@dataclass(eq=False)
class Try:
    target: object                  # what the `??` unwraps, None for anything else
    where: Callable


@dataclass(eq=False)
class Return:
    node: Tree
    expr: Node
    shape: str                      # "ok", "ok_blank", "err", "other"
    where: Callable


@dataclass(eq=False)
class Unit:
    path: Path
    rel: str
    src: str
    tree: Optional[Tree] = None
    error: str = ""
    functions: dict[str, Function] = field(default_factory=dict)
    flat_imports: list[Path] = field(default_factory=list)
    public_imports: list[Path] = field(default_factory=list)
    aliases: dict[str, Union[Path, str]] = field(default_factory=dict)
    stdlib: set[str] = field(default_factory=set)
    own_methods: set[str] = field(default_factory=set)
    fn_fields: set[str] = field(default_factory=set)
    callables: list[Callable] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    tries: list[Try] = field(default_factory=list)
    returns: list[Return] = field(default_factory=list)
    errs: list[Callable] = field(default_factory=list)
    value_uses: list[tuple[Function, Callable]] = field(default_factory=list)
    fn_types: list[Tree] = field(default_factory=list)
    lambda_rets: list[Tree] = field(default_factory=list)
    channel_mode: bool = False
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------------
# Reading a unit


def parse_unit(unit: Unit) -> None:
    try:
        unit.tree = build_parser().parse(unit.src, start="start")
    except Exception as e:  # a fixture about a parse error
        unit.error = type(e).__name__


def resolve_user_import(unit: Unit, spec: str) -> Optional[Path]:
    dirs = [unit.path.parent] + [d for d in unit.path.parent.parents
                                 if d.is_relative_to(ROOT / "tests") and d != ROOT / "tests"]
    for d in dirs:
        p = (d / f"{spec}.sushi").resolve()
        if p.exists():
            return p
    return None


def resolve_lib_import(unit: Unit, name: str) -> Optional[Path]:
    fixture_dir = unit.path.parent.parent if unit.path.parent.name == "v2" else unit.path.parent
    for d in ([unit.path.parent] if unit.path.parent != fixture_dir else []) + [fixture_dir]:
        found = sorted(d.rglob(f"{name}.sushi"))
        if found:
            return found[0].resolve()
    for p in (HELPERS / f"{name}.sushi", HELPERS / name / f"{name}.sushi"):
        if p.exists():
            return p.resolve()
    return None


def read_imports(unit: Unit) -> None:
    assert unit.tree is not None
    for top in unit.tree.find_data("use_stmt"):
        public = any(isinstance(c, Token) and c.type == "PUBLIC" for c in top.children)
        target = next(trees(top))
        alias_tok = [c for c in top.children if is_name(c)]
        alias = str(alias_tok[-1]) if alias_tok else None
        resolved: Union[Path, str, None]
        if target.data == "stdlib_import":
            path_node = next(trees(target))
            module = "/".join(str(t) for t in path_node.children)
            unit.stdlib.add(module)
            resolved = module
            if MODE["corpus"] == "stdlib":
                source = STDLIB_SOURCES / f"{module}.sushi"
                if source.exists():
                    resolved = source.resolve()
        elif target.data == "lib_import":
            path_node = next(trees(target))
            name = str(path_node.children[-1])
            resolved = resolve_lib_import(unit, name)
        else:
            spec = str(target.children[0])[1:-1]
            resolved = resolve_user_import(unit, spec)
        if resolved is None:
            unit.notes.append(f"unresolved import {top.children}")
            continue
        if alias:
            unit.aliases[alias] = resolved
        elif isinstance(resolved, Path):
            unit.flat_imports.append(resolved)
            if public:
                unit.public_imports.append(resolved)


def function_parts(node: Tree) -> Function:
    public = any(isinstance(c, Token) and c.type == "PUBLIC" for c in node.children)
    name = next(str(c) for c in node.children if is_name(c))
    types = [c for c in node.children if isinstance(c, Tree)
             and c.data not in ("parameters", "type_params", "block")]
    params = next((c for c in node.children if is_tree(c, "parameters")), None)
    return Function(unit=None, kind="fn", node=node, name=name, public=public,  # type: ignore[arg-type]
                    ret=types[0] if types else None,
                    err=types[1] if len(types) > 1 else None, params=params)


def fn_typed_names(node: Tree) -> set[str]:
    """The parameters and `let` names in `node` whose declared type is a function type."""
    names: set[str] = set()
    for sub in node.iter_subtrees():
        if sub.data in ("typed_param", "lambda_typed_param", "let_stmt"):
            kids = [c for c in sub.children if not (isinstance(c, Token) and c.type == "NOM")]
            if kids and is_tree(kids[0], "fn_type_t") and len(kids) > 1 and is_name(kids[1]):
                names.add(str(kids[1]))
    return names


def collect_declarations(unit: Unit) -> None:
    assert unit.tree is not None
    for top in unit.tree.find_data("toplevel"):
        decl = top.children[0]
        if is_tree(decl, "function_def"):
            f = function_parts(decl)
            f.unit = unit
            if f.name == "main":
                f.kind = "main"
            if f.name in unit.functions:
                unit.notes.append(f"two functions named {f.name}")
            unit.functions[f.name] = f
        elif is_tree(decl, "extend_stmt"):
            for sub in decl.iter_subtrees():
                if sub.data in ("extend_def",):
                    unit.own_methods.add(str(next(trees(sub)).children[0]))
                elif sub.data == "function_def":
                    unit.own_methods.add(next(str(c) for c in sub.children if is_name(c)))
        elif is_tree(decl, "struct_def"):
            for fld in decl.find_data("struct_field"):
                if is_tree(fld.children[0], "fn_type_t"):
                    unit.fn_fields.add(str(fld.children[1]))


# ----------------------------------------------------------------------------------
# Name resolution across units


class Corpus:
    def __init__(self, units: dict[Path, Unit]):
        self.units = units

    def exported(self, path: Path, seen: Optional[set[Path]] = None) -> dict[str, Function]:
        seen = seen if seen is not None else set()
        if path in seen or path not in self.units:
            return {}
        seen.add(path)
        u = self.units[path]
        out = {n: f for n, f in u.functions.items() if f.public and f.kind == "fn"}
        for p in u.public_imports:
            for n, f in self.exported(p, seen).items():
                out.setdefault(n, f)
        return out

    def flat(self, unit: Unit, name: str) -> Optional[Function]:
        f = unit.functions.get(name)
        if f is not None:
            return f if f.kind == "fn" else None
        for p in unit.flat_imports:
            g = self.exported(p).get(name)
            if g is not None:
                return g
        return None

    def stdlib_bare(self, unit: Unit, name: str, module: Optional[str] = None) -> bool:
        modules = [module] if module else sorted(unit.stdlib)
        return any(name in STDLIB_BARE_FUNCTIONS.get(m, ()) for m in modules)

    def resolve_call(self, unit: Unit, where: Callable, name: str) -> object:
        if where.lookup_fn_name(name):
            return ("fnvalue", where)
        f = self.flat(unit, name)
        if f is not None:
            return f
        if self.stdlib_bare(unit, name):
            return "stdlib"
        return None

    def resolve_member(self, unit: Unit, alias: str, name: str) -> object:
        target = unit.aliases.get(alias)
        if isinstance(target, Path):
            return self.exported(target).get(name)
        if isinstance(target, str) and self.stdlib_bare(unit, name, target):
            return "stdlib"
        return None


# ----------------------------------------------------------------------------------
# The walk: every call, `??`, return, lambda and function type in a unit


class Walker:
    def __init__(self, corpus: Corpus, unit: Unit):
        self.corpus = corpus
        self.unit = unit

    def run(self) -> None:
        assert self.unit.tree is not None
        for top in self.unit.tree.find_data("toplevel"):
            decl = top.children[0]
            if is_tree(decl, "function_def"):
                f = self.unit.functions[function_parts(decl).name]
                if f.node is not decl:
                    continue
                f.fn_names = fn_typed_names(decl)
                self.unit.callables.append(f)
                self.visit(decl, f, 0, None)
            elif is_tree(decl, "extend_stmt"):
                self.visit_extend(decl)
            else:
                self.visit(decl, None, 0, None)

    def visit_extend(self, decl: Tree) -> None:
        for sub in decl.iter_subtrees_topdown():
            if sub.data in ("extend_def", "function_def"):
                types = [c for c in sub.children if isinstance(c, Tree)
                         and c.data not in ("parameters", "type_params", "block", "method_name")]
                m = Callable(unit=self.unit, kind="method", node=sub,
                             explicit_channel=len(types) > 1)
                m.fn_names = fn_typed_names(sub)
                self.unit.callables.append(m)
                self.visit(sub, m, 0, None)

    def visit(self, node: Node, where: Optional[Callable], base: int,
              parent: Optional[Tree]) -> None:
        if isinstance(node, Token):
            if node.type == "STRING":
                self.visit_string(node, where, base)
            return
        data = node.data
        if data in ("function_def", "extend_def") and where is not None and where.node is not node:
            return  # a nested declaration is walked on its own
        if data == "fn_type_t":
            if base == 0:
                self.unit.fn_types.append(node)
        elif data in ("lambda_expr", "lambda_block"):
            where = self.enter_lambda(node, where, parent)
        elif data == "lambda_ret":
            if base == 0:
                self.unit.lambda_rets.append(node)
        elif data == "return_stmt" and where is not None:
            self.record_return(node, where)
        elif data == "maybe_call" and where is not None:
            self.record_postfix(node, where, base, parent)
        for child in node.children:
            self.visit(child, where, base, node)

    def enter_lambda(self, node: Tree, where: Optional[Callable], parent: Optional[Tree]) -> Callable:
        lam = Callable(unit=self.unit, kind="lambda", node=node, parent=where)
        lam.fn_names = fn_typed_names(node)
        ret = next((c for c in node.children if is_tree(c, "lambda_ret")), None)
        lam.explicit_channel = ret is not None and len(list(trees(ret))) > 1
        lam.combinator_arg = self.is_combinator_arg(node)
        self.unit.callables.append(lam)
        return lam

    def is_combinator_arg(self, lam: Tree) -> bool:
        return id(lam) in self.combinator_lambdas

    combinator_lambdas: set[int] = set()

    def visit_string(self, tok: Token, where: Optional[Callable], base: int) -> None:
        raw = str(tok)
        if not raw.startswith('"') or "{" not in raw:
            return
        content = raw[1:-1]
        i = 0
        while i < len(content):
            if content[i] == "{":
                depth, j = 1, i + 1
                while j < len(content) and depth:
                    depth += {"{": 1, "}": -1}.get(content[j], 0)
                    j += 1
                text = content[i + 1:j - 1]
                stripped = text.strip()
                if stripped:
                    offset = base + tok.start_pos + 1 + i + 1 + (len(text) - len(text.lstrip()))
                    try:
                        hole = parse_hole(stripped)
                    except Exception:
                        self.unit.notes.append(f"unparsed hole {stripped!r}")
                    else:
                        self.visit_hole(hole, where, offset)
                i = j
            else:
                i += 1

    def visit_hole(self, hole: Node, where: Optional[Callable], offset: int) -> None:
        self.visit(hole, where, offset, None)

    # --- postfix chains

    def record_postfix(self, node: Tree, where: Callable, base: int, parent: Optional[Tree]) -> None:
        kids = node.children
        atom = kids[0]
        ops = kids[1:]
        if not ops:
            if is_name(atom):
                if where.lookup_fn_name(str(atom)):
                    return
                f = self.corpus.flat(self.unit, str(atom))
                if f is not None:
                    self.unit.value_uses.append((f, where))
            return
        if is_name(atom, "Result") and is_tree(ops[0], "method_call") and method_name(ops[0]) == "Err":
            self.unit.errs.append(where)
        if len(ops) == 1 and is_name(atom) and is_tree(ops[0], "member_access"):
            f = self.corpus.resolve_member(self.unit, str(atom), str(ops[0].children[0].children[0]))
            if isinstance(f, Function):
                self.unit.value_uses.append((f, where))
        for k, op in enumerate(ops):
            target = None
            if k == 0 and is_name(atom) and is_tree(op, "call"):
                target = self.corpus.resolve_call(self.unit, where, str(atom))
            elif is_tree(op, "call"):
                target = ("fnvalue", where)
            elif k == 0 and is_name(atom) and is_tree(op, "method_call") and str(atom) in self.unit.aliases:
                target = self.corpus.resolve_member(self.unit, str(atom), method_name(op))
            elif is_tree(op, "method_call") and not (k == 0 and is_name(atom) and str(atom) in self.unit.aliases):
                name = method_name(op)
                if (name in COMBINATOR_METHODS and "collections/iter" in self.unit.stdlib
                        and name not in self.unit.own_methods):
                    target = "stdlib"
                elif name in self.unit.fn_fields:
                    target = ("fnvalue", where)
            if isinstance(target, (tuple, Function)) or target == "stdlib":
                if self.is_combinator_call(atom, op, k):
                    self.mark_combinator_lambdas(op)
                self.record_call(node, k, target, where, base, parent)
            if is_tree(op, "try_op"):
                prev_target = self.target_at(node, k - 1, where)
                self.unit.tries.append(Try(target=prev_target, where=where))

    def is_combinator_call(self, atom: Node, op: Tree, k: int) -> bool:
        if is_tree(op, "method_call"):
            return method_name(op) in COMBINATOR_METHODS and "collections/iter" in self.unit.stdlib
        return k == 0 and is_name(atom) and str(atom) in STDLIB_BARE_FUNCTIONS["collections/iter"]

    def mark_combinator_lambdas(self, op: Tree) -> None:
        sub = own_args(op)
        if sub is not None:
                for arg in sub.children:
                    inner = arg
                    if is_tree(inner, "maybe_call") and len(inner.children) == 1:
                        inner = inner.children[0]
                    if is_tree(inner, "lambda_expr", "lambda_block"):
                        self.combinator_lambdas.add(id(inner))

    def target_at(self, node: Tree, k: int, where: Callable) -> object:
        if k < 0:
            return None
        atom, ops = node.children[0], node.children[1:]
        op = ops[k]
        if k == 0 and is_name(atom) and is_tree(op, "call"):
            return self.corpus.resolve_call(self.unit, where, str(atom))
        if is_tree(op, "call"):
            return ("fnvalue", where)
        if k == 0 and is_name(atom) and is_tree(op, "method_call") and str(atom) in self.unit.aliases:
            return self.corpus.resolve_member(self.unit, str(atom), method_name(op))
        if is_tree(op, "method_call"):
            name = method_name(op)
            if (name in COMBINATOR_METHODS and "collections/iter" in self.unit.stdlib
                    and name not in self.unit.own_methods):
                return "stdlib"
            if name in self.unit.fn_fields:
                return ("fnvalue", where)
        return None

    def record_call(self, node: Tree, k: int, target: object, where: Callable, base: int,
                    parent: Optional[Tree]) -> None:
        ops = node.children[1:]
        prev_end = end_of(ops[k])
        nxt = ops[k + 1] if k + 1 < len(ops) else None
        drop = None
        if nxt is None:
            followup = "discard" if is_tree(parent, "call_stmt") else "value"
        elif is_tree(nxt, "try_op"):
            followup, drop = "try", (base + prev_end, base + end_of(nxt))
        elif is_tree(nxt, "method_call") and method_name(nxt) == "realise":
            followup, drop = "realise", (base + prev_end, base + end_of(nxt))
        else:
            followup = "method"
        self.unit.calls.append(Call(unit=self.unit, target=target, followup=followup,
                                    drop=drop, where=where))

    # --- returns

    def record_return(self, node: Tree, where: Callable) -> None:
        expr = [c for c in node.children if not (isinstance(c, Token) and c.type == "NOM")][-1]
        shape = "other"
        if (is_tree(expr, "maybe_call") and len(expr.children) == 2 and is_name(expr.children[0], "Result")
                and is_tree(expr.children[1], "method_call")):
            m = method_name(expr.children[1])
            if m == "Ok":
                args = own_args(expr.children[1])
                if args is None:
                    self.unit.returns.append(Return(node=node, expr=expr, shape="other", where=where))
                    return
                arg = args.children
                blank = len(arg) == 1 and (is_tree(arg[0], "blank_literal") or (
                    is_tree(arg[0], "maybe_call") and len(arg[0].children) == 1
                    and is_tree(arg[0].children[0], "blank_literal")))
                shape = "ok_blank" if blank else "ok"
            elif m == "Err":
                shape = "err"
        self.unit.returns.append(Return(node=node, expr=expr, shape=shape, where=where))


# ----------------------------------------------------------------------------------
# Deciding


def fn_value_bare(target: object) -> bool:
    return isinstance(target, tuple) and not target[1].unit.channel_mode


def target_is_bare(target: object, bare: set[Function]) -> bool:
    if isinstance(target, Function):
        return target in bare
    if target == "stdlib":
        return True
    return fn_value_bare(target)


def lambda_is_bare(lam: Callable) -> bool:
    if lam.explicit_channel:
        return False
    return lam.combinator_arg or not lam.unit.channel_mode


def can_fail(c: Callable, bare: set[Function]) -> bool:
    u = c.unit
    if any(w is c for w in u.errs):
        return True
    if any(t.where is c and not target_is_bare(t.target, bare) for t in u.tries):
        return True
    if any(r.where is c and r.shape in ("other", "err") for r in u.returns):
        return True
    return False


def decide(corpus: Corpus) -> set[Function]:
    units = [u for u in corpus.units.values() if u.tree is not None]
    all_calls = [c for u in units for c in u.calls]
    all_values = [v for u in units for v in u.value_uses]
    bare = {f for u in units for f in u.functions.values()
            if (f.implicit or f.drops_its_channel) and u.rel not in CW2511_FIXTURES
            and not (MODE["corpus"] == "stdlib" and f.public and f.name not in PUBLIC_BARE)}
    for _ in range(100):
        changed = False
        for call in all_calls:
            if isinstance(call.target, Function) and call.target in bare and call.followup in ("method", "value"):
                bare.discard(call.target)
                changed = True
        for f, where in all_values:
            if f in bare and where.unit.channel_mode:
                bare.discard(f)
                changed = True
        for f in list(bare):
            if can_fail(f, bare):
                bare.discard(f)
                changed = True
        for u in units:
            if u.channel_mode:
                continue
            reason = None
            for lam in u.callables:
                if lam.kind == "lambda" and lambda_is_bare(lam) and not lam.combinator_arg and can_fail(lam, bare):
                    reason = "a lambda can fail"
            for call in u.calls:
                if isinstance(call.target, tuple) and call.followup in ("method", "value"):
                    reason = "a function value's Result is used"
            for f, _where in u.value_uses:
                if f not in bare and (f.err is None or str(f.err.children[0]) == "StdError"):
                    reason = f"{f.name} is a function value that can fail"
            if reason:
                u.channel_mode = True
                u.notes.append(f"CHANNEL mode: {reason}")
                changed = True
        if not changed:
            return bare
    raise RuntimeError("no fixpoint")


# ----------------------------------------------------------------------------------
# Editing


@dataclass
class Edits:
    src: str
    items: list[tuple[int, int, str]] = field(default_factory=list)

    def add(self, start: int, end: int, text: str) -> None:
        self.items.append((start, end, text))

    def apply(self) -> str:
        out, pos = [], 0
        items = sorted(set(self.items), key=lambda e: (e[0], -e[1]))
        kept: list[tuple[int, int, str]] = []
        for e in items:
            if kept and e[0] < kept[-1][1]:
                if e[1] <= kept[-1][1]:
                    continue  # inside a span that is already replaced
                raise ValueError(f"overlapping edits {kept[-1]} {e}")
            kept.append(e)
        for s, e, t in kept:
            out.append(self.src[pos:s])
            out.append(t)
            pos = e
        out.append(self.src[pos:])
        return "".join(out)


def unwrap_ok(edits: Edits, ret: Return, last_in_body: bool) -> None:
    """`return Result.Ok(x)` -> `return x`; `return Result.Ok(~)` -> `return ~`, or gone."""
    expr = ret.expr
    assert isinstance(expr, Tree)
    if ret.shape == "ok_blank":
        if last_in_body:
            line_start = edits.src.rfind("\n", 0, ret.node.meta.start_pos) + 1
            line_end = edits.src.find("\n", end_of(expr))
            rest = edits.src[end_of(expr):line_end if line_end >= 0 else len(edits.src)]
            if not rest.strip() and not edits.src[line_start:ret.node.meta.start_pos].strip():
                edits.add(line_start, line_end + 1 if line_end >= 0 else len(edits.src), "")
                return
        edits.add(start_of(expr), end_of(expr), "~")
        return
    mc = expr.children[1]
    args = own_args(mc)
    edits.add(start_of(expr), start_of(args), "")
    edits.add(end_of(args), end_of(expr), "")


def body_statements(fn: Tree) -> list[Tree]:
    block = next(c for c in fn.children if is_tree(c, "block"))
    return [c for c in block.children if isinstance(c, Tree)]


def rewrite_returns(edits: Edits, u: Unit, c: Callable) -> None:
    stmts = body_statements(c.node) if c.kind in ("fn", "main") or is_tree(c.node, "lambda_block") else []
    last = stmts[-1] if len(stmts) > 1 else None
    for r in u.returns:
        if r.where is not c:
            continue
        if r.shape in ("ok", "ok_blank"):
            unwrap_ok(edits, r, r.node is last)
        elif c.kind == "main" and r.shape == "err":
            edits.add(start_of(r.expr), end_of(r.expr), "1")
        else:
            u.notes.append(f"{c.kind} return kept: {u.src[start_of(r.expr):end_of(r.expr)][:60]!r}")


def insert_std_error(edits: Edits, ret: Tree) -> None:
    edits.add(end_of(ret), end_of(ret), " | StdError")


def split_main(edits: Edits, u: Unit, main: Function) -> None:
    node = main.node
    name = "run"
    while name in u.functions:
        name = "run_" + name
    name_tok = next(c for c in node.children if is_name(c))
    assert main.ret is not None
    edits.add(start_of(name_tok), end_of(name_tok), name)
    edits.add(end_of(main.ret), end_of(main.ret), " | StdError")
    params = u.src[end_of(name_tok):start_of(main.ret)].strip()
    arg_names = []
    if main.params is not None:
        for p in main.params.find_data("typed_param"):
            arg_names.append(str([c for c in p.children if is_name(c)][-1]))
    indent = "    "
    tail = "" if u.src[:end_of(node)].endswith("\n") else "\n"
    new_main = (f"{tail}\nfn main{params} {u.src[start_of(main.ret):end_of(main.ret)]}:\n"
                f"{indent}match {name}({', '.join(arg_names)}):\n"
                f"{indent}{indent}Result.Ok(code) -> return code\n"
                f"{indent}{indent}Result.Err(_) -> return 1\n")
    edits.add(end_of(node), end_of(node), new_main)
    u.notes.append(f"main split into {name}")


def rewrite(corpus: Corpus, u: Unit, bare: set[Function]) -> Optional[str]:
    if u.tree is None or u.rel in CW2511_FIXTURES:
        return None
    edits = Edits(u.src)
    for f in u.functions.values():
        if f.kind == "main":
            continue
        if f in bare:
            rewrite_returns(edits, u, f)
            if f.err is not None:
                assert f.ret is not None
                edits.add(end_of(f.ret), end_of(f.err), "")
        elif f.implicit:
            assert f.ret is not None
            insert_std_error(edits, f.ret)
    for call in u.calls:
        if call.drop and target_is_bare(call.target, bare):
            edits.add(call.drop[0], call.drop[1], "")
    for lam in u.callables:
        if lam.kind == "lambda" and lambda_is_bare(lam) and is_tree(lam.node, "lambda_block"):
            rewrite_returns(edits, u, lam)
    if u.channel_mode:
        for t in u.fn_types:
            types = [c for c in t.children if isinstance(c, Tree) and c.data != "fn_param_types"]
            if len(types) == 1:
                insert_std_error(edits, types[0])
        for r in u.lambda_rets:
            types = list(trees(r))
            if len(types) == 1:
                insert_std_error(edits, types[0])
        if any(c.combinator_arg for c in u.callables) or any(
                c.target == "stdlib" for c in u.calls):
            u.notes.append("CHANNEL mode with a bare stdlib call: check by hand")
    main = u.functions.get("main")
    if main is not None:
        rewrite_main(edits, corpus, u, main, bare)
    new = edits.apply()
    return new if new != u.src else None


def rewrite_main(edits: Edits, corpus: Corpus, u: Unit, main: Function, bare: set[Function]) -> None:
    if main.err is not None or main.ret is None or returns_result(main.ret):
        u.notes.append("main has a channel: by hand")
        return
    live_tries = [t for t in u.tries if t.where is main and not target_is_bare(t.target, bare)]
    if live_tries:
        split_main(edits, u, main)
        return
    rewrite_returns(edits, u, main)


# ----------------------------------------------------------------------------------


FENCE = re.compile(r"^```sushi[^\n]*\n(.*?)^```", re.M | re.S)


def markdown_pages() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "docs"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [(ROOT / p).resolve() for p in out if p.endswith(".md")]


def tracked_fixtures() -> list[Path]:
    root = CORPORA[MODE["corpus"]].split("**")[0]
    out = [p for p in subprocess.run(["git", "ls-files", root], cwd=ROOT, capture_output=True,
                                     text=True, check=True).stdout.split()
           if p.endswith(".sushi")]
    if MODE["corpus"] == "other":
        out.append("editor-support/SYNTAX_SHOWCASE.sushi")
    return [(ROOT / p).resolve() for p in out if not p.startswith("tests/unit/")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="write the files (default: dry run)")
    ap.add_argument("--report", type=Path, help="write the review report here")
    ap.add_argument("--only", help="rewrite only paths that contain this text")
    ap.add_argument("--corpus", choices=sorted(CORPORA), default="tests")
    args = ap.parse_args()
    MODE["corpus"] = args.corpus

    units: dict[Path, Unit] = {}
    for p in tracked_fixtures():
        units[p] = Unit(path=p, rel=str(p.relative_to(ROOT)),
                        src=p.read_bytes().decode("utf-8", errors="surrogateescape"))
    fences: dict[Path, tuple[Path, int, int]] = {}
    if MODE["corpus"] == "docs":
        for md in markdown_pages():
            text = md.read_text(encoding="utf-8")
            for i, m in enumerate(FENCE.finditer(text)):
                key = Path(f"{md}::fence{i}")
                fences[key] = (md, m.start(1), m.end(1))
                units[key] = Unit(path=key, rel=f"{md.relative_to(ROOT)}::fence{i}",
                                  src=m.group(1))
    for u in units.values():
        parse_unit(u)
        if u.tree is not None:
            read_imports(u)
            collect_declarations(u)
    corpus = Corpus(units)
    walker_lambdas: set[int] = set()
    for u in units.values():
        if u.tree is not None:
            w = Walker(corpus, u)
            w.combinator_lambdas = walker_lambdas
            w.run()
    # The combinator mark is set while its call is walked, after the lambda may have
    # been entered: read it again now.
    for u in units.values():
        for c in u.callables:
            if c.kind == "lambda":
                c.combinator_arg = id(c.node) in walker_lambdas

    bare = decide(corpus)

    changed = 0
    spliced: dict[Path, list] = {}
    report: list[str] = []
    for u in sorted(units.values(), key=lambda x: x.rel):
        if args.only and args.only not in u.rel:
            continue
        if u.error:
            report.append(f"{u.rel}: not parsed ({u.error}), left alone")
            continue
        if u.rel in CW2511_FIXTURES:
            report.append(f"{u.rel}: CW2511 fixture, rewrite by hand")
            continue
        try:
            new = rewrite(corpus, u, bare)
        except Exception as e:
            report.append(f"{u.rel}: REWRITE FAILED {e}")
            continue
        for n in u.notes:
            report.append(f"{u.rel}: {n}")
        if new is not None:
            changed += 1
            if u.path in fences:
                spliced.setdefault(fences[u.path][0], []).append((fences[u.path], new))
            elif args.write:
                u.path.write_bytes(new.encode("utf-8", errors="surrogateescape"))
    if args.write:
        for md, parts in spliced.items():
            text = md.read_text(encoding="utf-8")
            for (_, start, end), new in sorted(parts, key=lambda x: -x[0][1]):
                text = text[:start] + new + text[end:]
            md.write_text(text, encoding="utf-8")
    implicit = [f for u in units.values() for f in u.functions.values() if f.implicit]
    summary = (f"units {len(units)}, changed {changed}, implicit functions {len(implicit)}, "
               f"bare {len(bare)}, channel-mode units "
               f"{sum(1 for u in units.values() if u.channel_mode)}")
    print(summary)
    if args.report:
        args.report.write_text(summary + "\n" + "\n".join(report) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
