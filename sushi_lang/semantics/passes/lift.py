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
        # Whose body the walk is inside. A lambda lifted out of a library body becomes a
        # function of its own and is checked as one, so it has to carry the same answer
        # (#468).
        self._owner_is_library = False

    def run(self) -> None:
        for fn in list(self.program.functions):
            if getattr(fn, "type_params", None):
                continue  # generic templates: their instantiations carry the lambdas
            self._owner_is_library = bool(getattr(fn, "is_library_template", False))
            self._walk(fn.body)
        self._owner_is_library = False
        # Extension and perk-impl bodies emit through the same statement paths
        # as a plain fn, so their lambdas lift the same way (#399).
        # program.generic_extensions stays unwalked: templates, like generic
        # fn templates -- their instantiation copies carry the lambdas and are
        # lifted in _check_monomorphized_extensions.
        for ext in list(self.program.extensions):
            self._walk(ext.body)
        for impl in list(self.program.perk_impls):
            for method in impl.methods:
                self._walk(method.body)
        if self.annotate is not None:
            for lifted in self._lifted:
                self.annotate(lifted)

    def lift_body(self, body) -> List[FuncDef]:
        """Lift one body and answer the FuncDefs this call produced (#399).

        The per-instantiation extension copies live in no unit AST, so the
        caller runs the passes the per-unit loop cannot: it annotates and
        borrow-checks exactly what this call lifted.
        """
        before = len(self._lifted)
        self._owner_is_library = False
        self._walk(body)
        produced = self._lifted[before:]
        if self.annotate is not None:
            for lifted in produced:
                self.annotate(lifted)
        return produced

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
        # The counter is per lifter instance and the tables are global, so a
        # taken index means another unit's lifter got there first -- advance
        # past it, or this closure silently aliases that unit's body and env
        # layout (#402).
        while (f"__lambda_{self._counter}" in self.func_table.by_name
               or f"__closure_env_{self._counter}" in self.structs.by_name):
            self._counter += 1
        idx = self._counter
        self._counter += 1
        env_name = f"__closure_env_{idx}"
        lifted_name = f"__lambda_{idx}"
        captures = lam.captures or []

        env_struct = StructType(name=env_name,
                                fields=tuple((c.name, c.ty) for c in captures))
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
        if not register_synthesized_function(
                self.func_table, lifted, program=self.program,
                from_library_template=self._owner_is_library):
            # Unreachable after the free-name search above; a silent False
            # here is exactly the #402 aliasing, so fail loud instead.
            raise RuntimeError(f"lifted lambda name '{lifted_name}' already registered")
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
