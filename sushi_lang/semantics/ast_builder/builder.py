"""Main ASTBuilder orchestrator for Sushi language compiler."""
from __future__ import annotations
from typing import List, Optional

from lark import Tree, Token

from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.unit_symbols import UnitKeyedSymbols

from sushi_lang.semantics.ast import Block, DocBlock, Expr, Program
from sushi_lang.internals.report import span_of

from sushi_lang.semantics.ast_builder.utils.tree_navigation import expect


class ASTBuilder:
    def __init__(self):
        """Initialize ASTBuilder with lazy-loaded parsers."""
        self._type_parser = None
        self._expr_parser = None
        self._stmt_parser = None
        # This unit's constants, by name. A fixed array's size may name one, and a
        # type is read while the AST is built -- long before the collect pass has a
        # constant table -- so the builder keeps its own.
        # This unit's own constants, which is all a size may name (Known Limitation
        # 14). One unit, so no unit key: `declare` with none and the flat view is
        # the whole answer.
        self.unit_constants: UnitKeyedSymbols = UnitKeyedSymbols()
        # Doc blocks the builder cannot report on: it takes no Reporter, and a block
        # that documents nothing is the `docs` pass's warning to raise. Every block
        # ends up attached, lifted, or here -- see documentation.md section 5.
        self.unit_doc: Optional[DocBlock] = None
        self.orphan_docs: List[DocBlock] = []
        # Body-first blocks parked on a Block, by id(block), until the enclosing
        # declaration lifts one. What still stands at the end of `build()` belongs to
        # a body that takes no docs.
        self.pending_body_docs: dict = {}

    def integer_constant(self, name: str) -> Optional[int]:
        """The value of an integer constant of this unit, None when there is none.

        The real evaluator does the reading, so a constant that is an expression
        (`HALF * 2`) or that names another constant counts exactly as a literal one
        does. Its reporter is silent: a name that is not a constant here is the
        caller's diagnostic to raise, with the array size in hand to name.
        """
        from sushi_lang.internals.report import Reporter
        from sushi_lang.semantics.passes.collect.constants import ConstantTable, ConstSig
        from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
        from sushi_lang.semantics.type_predicates import is_integer_type

        const_def = self.unit_constants.get(name)
        if const_def is None or const_def.ty is None or not is_integer_type(const_def.ty):
            return None

        table = ConstantTable()
        for known in self.unit_constants.by_name.values():
            table.declare(known.name, ConstSig(name=known.name, loc=known.loc,
                                               const_type=known.ty, decl=known))

        evaluated = ConstantEvaluator(Reporter(), table).evaluate(
            const_def.value, const_def.ty, const_def.loc)
        if evaluated is None or not isinstance(evaluated.value, int) or isinstance(evaluated.value, bool):
            return None
        return evaluated.value

    @property
    def type_parser(self):
        """Lazy-load TypeParser on first use."""
        if self._type_parser is None:
            from sushi_lang.semantics.ast_builder.types.parser import TypeParser
            self._type_parser = TypeParser(self)
        return self._type_parser

    @property
    def expr_parser(self):
        """Lazy-load ExpressionParser on first use."""
        if self._expr_parser is None:
            from sushi_lang.semantics.ast_builder.expressions.parser import ExpressionParser
            self._expr_parser = ExpressionParser(self)
        return self._expr_parser

    @property
    def stmt_parser(self):
        """Lazy-load StatementParser on first use."""
        if self._stmt_parser is None:
            from sushi_lang.semantics.ast_builder.statements.parser import StatementParser
            self._stmt_parser = StatementParser(self)
        return self._stmt_parser

    def build(self, tree: Tree) -> Program:
        """Build the Program of one unit from its parse tree."""
        from sushi_lang.semantics.ast_builder.declarations.docs import attach_docs
        from sushi_lang.semantics.ast_builder.declarations.toplevel import (
            TopLevelDeclarations, declarations_of)

        tree = expect(tree, "program")
        decls = TopLevelDeclarations()
        decls.read_constants(tree, self)
        for toplevel, declaration in declarations_of(tree):
            decls.add(toplevel, declaration, self)

        attach_docs(tree.children, decls.documented(), self, allow_unit_doc=True)
        self._orphan_unlifted_body_docs()

        return Program(uses=decls.uses, constants=decls.constants,
                       structs=decls.structs, enums=decls.enums, perks=decls.perks,
                       functions=decls.functions, extensions=decls.extensions,
                       generic_extensions=decls.generic_extensions,
                       perk_impls=decls.perk_impls, externals=decls.externals,
                       loc=span_of(tree), doc=self.unit_doc,
                       orphan_docs=self.orphan_docs,
                       first_declaration_span=decls.first_declaration_span)

    def _orphan_unlifted_body_docs(self) -> None:
        """A body block nothing lifted documents nothing: a lambda body, an `if` arm."""
        for _body, doc in self.pending_body_docs.values():
            doc.orphan_reason = "detached"
            self.orphan_docs.append(doc)
        self.orphan_docs.sort(key=lambda d: (d.loc.line, d.loc.col) if d.loc else (0, 0))

    def _parse_type(self, type_node: Tree) -> Optional[Type]:
        """Parse a type node into a Type object."""
        return self.type_parser.parse_type(type_node)

    def _block(self, t: Tree) -> Block:
        """Parse block with dispatch to statement handlers."""
        from sushi_lang.semantics.ast_builder.statements.blocks import parse_block
        return parse_block(t, self)

    def _expr(self, t: Tree | Token) -> Expr:
        """Parse an expression node into an Expr object."""
        return self.expr_parser.parse_expr(t)

