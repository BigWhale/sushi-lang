"""Lambda-lifting pass: turn each lambda literal into a top-level function + env.

Runs between the type pass (which resolved each lambda's param/capture types and
its FunctionType onto the node) and the borrow pass (which then checks the
synthesized functions). For each lambda:

  1. Synthesize an environment struct `__closure_env_N { <captured fields> }` and
     register it in the struct table.
  2. Synthesize the lifted function
        fn __lambda_N(&peek __closure_env_N __closure_env, <lambda params>) -> ok | err
     whose body is the lambda body with every captured-name read rewritten to a
     field read `__closure_env.<name>`.
  3. Register the lifted function through the shared synthesis helper, then annotate
     it with the type validator (it was added after the main type pass, so it needs
     its own resolution for the backend — e.g. Result.Ok concrete types).

The Lambda node stays in place as an expression; the backend (emit_lambda) builds
the runtime value {&__lambda_N, env_ptr, drop} at that site using `lifted_name`,
`env_struct`, and `captures`.

Nested closures (a lambda inside another lambda's body) are lifted best-effort in
T1; the deep capture data-flow is a known limitation.
"""
from __future__ import annotations
import dataclasses
from typing import Callable, List, Optional

from sushi_lang.semantics.ast import (
    Node, FuncDef, Lambda, Block, Return, Name, MemberAccess, Param, DotCall,
)
from sushi_lang.semantics.typesys import StructType, ReferenceType, BorrowMode

ENV_PARAM_NAME = "__closure_env"


class LambdaLifter:
    def __init__(self, structs, func_table, program, annotate: Optional[Callable] = None):
        self.structs = structs
        self.func_table = func_table
        self.program = program
        self.annotate = annotate
        self._counter = 0
        self._lifted: List[FuncDef] = []

    def run(self) -> None:
        for fn in list(self.program.functions):
            if getattr(fn, "type_params", None):
                continue  # generic templates: their instantiations carry the lambdas
            self._walk(fn.body)
        # Annotate the synthesized functions (resolve Result.Ok concrete types, etc.)
        # so the backend sees the same annotations a normally type-checked fn has.
        if self.annotate is not None:
            for lifted in self._lifted:
                self.annotate(lifted)

    def _walk(self, node) -> None:
        """Find and lift Lambda nodes anywhere under `node` (not into their bodies)."""
        if isinstance(node, Lambda):
            self._lift(node)
            return
        if isinstance(node, list):
            for item in node:
                self._walk(item)
            return
        # Only AST nodes can contain a lambda; skip Type objects / params / spans.
        if isinstance(node, Node):
            for f in dataclasses.fields(node):
                self._walk(getattr(node, f.name))

    def _lift(self, lam: Lambda) -> None:
        idx = self._counter
        self._counter += 1
        env_name = f"__closure_env_{idx}"
        lifted_name = f"__lambda_{idx}"
        captures = lam.captures or []

        # 1. Environment struct holding the captured values.
        env_struct = StructType(name=env_name,
                                fields=tuple((c.name, c.ty) for c in captures))
        if env_name not in self.structs.by_name:
            self.structs.by_name[env_name] = env_struct
            self.structs.order.append(env_name)

        # 2. Lifted-function body.
        if lam.is_block_body:
            body = lam.body
        else:
            ok = DotCall(receiver=Name(id="Result", loc=lam.loc), method="Ok",
                         args=[lam.body], loc=lam.loc)
            body = Block(statements=[Return(value=ok, loc=lam.loc)], loc=lam.loc)

        # Rewrite captured reads to env-field reads.
        cap_names = {c.name for c in captures}
        _rewrite_captures(body, cap_names)

        # Lift any nested lambdas found in the (rewritten) body.
        self._walk(body)

        # 3. Lifted FuncDef: leading env reference param + the lambda's own params.
        ok_type = lam.resolved_type.ok_type if lam.resolved_type is not None else lam.ret
        err_type = lam.resolved_type.err_type if lam.resolved_type is not None else lam.err_type
        env_param = Param(
            name=ENV_PARAM_NAME,
            ty=ReferenceType(referenced_type=env_struct, mutability=BorrowMode.PEEK),
            loc=lam.loc,
        )
        lifted = FuncDef(
            name=lifted_name,
            params=[env_param] + list(lam.params),
            ret=ok_type,
            body=body,
            err_type=err_type,
            loc=lam.loc,
        )
        from sushi_lang.semantics.generics.synthesis import register_synthesized_function
        register_synthesized_function(self.func_table, lifted, program=self.program)
        self._lifted.append(lifted)

        lam.lifted_name = lifted_name
        lam.env_struct = env_struct


def _rewrite_captures(node, cap_names: set) -> None:
    """Replace `Name(cap)` reads with `MemberAccess(Name(env), cap)` in-place.

    Does not descend into nested Lambda nodes (each lambda's captures are rewritten
    against its own env when that lambda is lifted).
    """
    if isinstance(node, Lambda):
        return
    if isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, Name) and item.id in cap_names:
                node[i] = _env_access(item)
            else:
                _rewrite_captures(item, cap_names)
        return
    if isinstance(node, Node):
        for f in dataclasses.fields(node):
            val = getattr(node, f.name)
            if isinstance(val, Name) and val.id in cap_names:
                setattr(node, f.name, _env_access(val))
            elif isinstance(val, list) or isinstance(val, Node):
                _rewrite_captures(val, cap_names)


def _env_access(name_node: Name) -> MemberAccess:
    return MemberAccess(
        receiver=Name(id=ENV_PARAM_NAME, loc=name_node.loc),
        member=name_node.id,
        loc=name_node.loc,
    )
