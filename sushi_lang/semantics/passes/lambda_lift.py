"""Lambda-lifting pass: turn each lambda literal into a top-level function + env."""
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
        if self.annotate is not None:
            for lifted in self._lifted:
                self.annotate(lifted)

    def _walk(self, node) -> None:
        """Find and lift Lambda nodes anywhere under `node` (not into their bodies)."""
        if isinstance(node, Lambda):
            self._lift(node)
            return
        # `If.arms` holds plain (cond, Block) tuples, so tuples walk too (#400).
        if isinstance(node, (list, tuple)):
            for item in node:
                self._walk(item)
            return
        if isinstance(node, Node):
            for f in dataclasses.fields(node):
                self._walk(getattr(node, f.name))

    def _lift(self, lam: Lambda) -> None:
        idx = self._counter
        self._counter += 1
        env_name = f"__closure_env_{idx}"
        lifted_name = f"__lambda_{idx}"
        captures = lam.captures or []

        env_struct = StructType(name=env_name,
                                fields=tuple((c.name, c.ty) for c in captures))
        if env_name not in self.structs.by_name:
            self.structs.by_name[env_name] = env_struct
            self.structs.order.append(env_name)

        if lam.is_block_body:
            body = lam.body
        else:
            ok = DotCall(receiver=Name(id="Result", loc=lam.loc), method="Ok",
                         args=[lam.body], loc=lam.loc)
            body = Block(statements=[Return(value=ok, loc=lam.loc)], loc=lam.loc)

        cap_names = {c.name for c in captures}
        _rewrite_captures(body, cap_names)

        self._walk(body)

        ok_type = lam.resolved_type.ok_type if lam.resolved_type is not None else lam.ret
        err_type = lam.resolved_type.err_type if lam.resolved_type is not None else lam.err_type
        # The env borrow is `poke`, and the mode is not decoration: a move-captured
        # `List@(T)` is MUTABLE inside the body by design, so the write must persist across
        # calls. Spelled `peek`, it made the language's own closure semantics a CE2408 once
        # the write gate became total. The environment is the closure's own storage.
        env_param = Param(
            name=ENV_PARAM_NAME,
            ty=ReferenceType(referenced_type=env_struct, mutability=BorrowMode.POKE),
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
    """Replace `Name(cap)` reads with `MemberAccess(Name(env), cap)` in-place."""
    if isinstance(node, Lambda):
        return
    if isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, Name) and item.id in cap_names:
                node[i] = _env_access(item)
            elif isinstance(item, tuple):
                # An `If.arms` element. A captured Name can BE a tuple element
                # (the arm's condition), and a tuple cannot be mutated in
                # place -- rebuild it into the list slot (#400).
                rebuilt = []
                for x in item:
                    if isinstance(x, Name) and x.id in cap_names:
                        rebuilt.append(_env_access(x))
                    else:
                        _rewrite_captures(x, cap_names)
                        rebuilt.append(x)
                node[i] = tuple(rebuilt)
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
