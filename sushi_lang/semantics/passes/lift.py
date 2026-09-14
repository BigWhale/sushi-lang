"""Lambda-lifting pass: turn each lambda literal into a top-level function + env."""
from __future__ import annotations
from typing import Callable, List, Optional

from sushi_lang.semantics.ast import (
    Node, FuncDef, Lambda, Block, Return, Name, MemberAccess, Param, DotCall,
)
from sushi_lang.semantics.ast_walk import node_fields, walk_nodes
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
        self._owner_origin = None
        # And whose SOURCE it is. A lambda in a generic body lifts once per instance,
        # every copy carrying the template's spans, so the copies answer one report
        # (#648).
        self._owner_instance_of = None

    def run(self) -> None:
        for fn in list(self.program.functions):
            if getattr(fn, "type_params", None):
                continue  # generic templates: their instantiations carry the lambdas
            self._owner_is_library = bool(getattr(fn, "is_library_template", False))
            self._owner_origin = getattr(fn, "library_origin", None)
            self._owner_instance_of = getattr(fn, "instance_of", None)
            self._walk(fn.body)
        self._owner_is_library = False
        self._owner_origin = None
        self._owner_instance_of = None
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

    def lift_body(self, body) -> List[FuncDef]:
        """Lift one body and answer the FuncDefs this call produced (#399).

        The per-instantiation extension copies live in no unit AST, so the
        caller runs the pass the per-unit loop cannot: it borrow-checks exactly
        what this call lifted.
        """
        before = len(self._lifted)
        self._owner_is_library = False
        self._owner_origin = None
        self._owner_instance_of = None
        self._walk(body)
        return self._lifted[before:]

    def _walk(self, node) -> None:
        """Find and lift Lambda nodes anywhere under `node` (not into their bodies)."""
        walk_nodes(node, self._lift_if_lambda)

    def _lift_if_lambda(self, node: Node) -> bool:
        """Lift a lambda where the walk meets it, and stop at its body.

        The body is walked again from `_lift`, AFTER the annotate hook typed it: a
        nested lambda lifted before that carried no parameter types and no captures
        (#629).
        """
        if isinstance(node, Lambda):
            self._lift(node)
            return False
        return True

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
                from_library_template=self._owner_is_library,
                origin=self._owner_origin):
            # Unreachable after the free-name search above; a silent False
            # here is exactly the #402 aliasing, so fail loud instead.
            raise RuntimeError(f"lifted lambda name '{lifted_name}' already registered")
        self._lifted.append(lifted)

        lifted.instance_of = self._owner_instance_of

        lam.lifted_name = lifted_name
        lam.env_struct = env_struct

        # Annotate FIRST, then look for a lambda nested in this body. The hook is the
        # typecheck pass's `_validate_function`, and it is what types a Lambda node --
        # so a nested one lifted before it ran carried no parameter types, no captures
        # and no channel, and its own body went unchecked (#629). The order is the
        # dependency: type this body, then lift what the typing found in it.
        if self.annotate is not None:
            self.annotate(lifted)
        self._walk(body)


def _rewrite_captures(node, cap_names: set) -> None:
    """Replace `Name(cap)` reads with `MemberAccess(Name(env), cap)` in-place.

    A nested lambda is left alone: its own captures are rewritten against its own
    environment when it is lifted.
    """
    def rewrite_the_slots_of(owner: Node) -> bool:
        if isinstance(owner, Lambda):
            return False
        for name, value in node_fields(owner):
            rebuilt = _rewrite_slot(value, cap_names)
            if rebuilt is not value:
                setattr(owner, name, rebuilt)
        return True

    walk_nodes(node, rewrite_the_slots_of)


def _rewrite_slot(value, cap_names: set):
    """The new value of one field slot. A captured Name becomes a read off the env.

    A list is rewritten in place; a tuple cannot be, so it is rebuilt -- an `If.arms`
    element is a (cond, Block) tuple and the condition alone can BE a captured name
    (#400). The original is answered unchanged when nothing moved, so a slot that holds
    no capture keeps the object it had.
    """
    if isinstance(value, Name) and value.id in cap_names:
        return _env_access(value)
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _rewrite_slot(item, cap_names)
        return value
    if isinstance(value, tuple):
        rebuilt = tuple(_rewrite_slot(item, cap_names) for item in value)
        if any(new is not old for new, old in zip(rebuilt, value, strict=True)):
            return rebuilt
    return value


def _env_access(name_node: Name) -> MemberAccess:
    return MemberAccess(
        receiver=Name(id=ENV_PARAM_NAME, loc=name_node.loc),
        member=name_node.id,
        loc=name_node.loc,
    )
