"""Main ASTBuilder orchestrator for Sushi language compiler."""
from __future__ import annotations
from typing import List, Optional

from lark import Tree, Token

from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.unit_symbols import UnitKeyedSymbols
from sushi_lang.semantics.generics.types import GenericTypeRef

from sushi_lang.semantics.ast import (
    Program, UseStatement, FuncDef, ConstDef, DocBlock, ExtendDef, Block,
    StructDef, EnumDef, PerkDef, ExtendWithDef, Expr, ExternalBlock,
)
from sushi_lang.internals.report import span_of


from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_tree as _first_tree,
    find_tree_recursive as _find_tree_recursive,
    expect,
)


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
                                               const_type=known.ty))

        evaluated = ConstantEvaluator(Reporter(), table, self.unit_constants).evaluate(
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
        """Build Program AST from parse tree."""
        from sushi_lang.semantics.ast_builder.declarations import imports, functions, constants, structs, enums, perks, extensions, externals

        tree = expect(tree, "program")
        uses: List[UseStatement] = []
        constants_list: List[ConstDef] = []
        structs_list: List[StructDef] = []
        enums_list: List[EnumDef] = []
        perks_list: List[PerkDef] = []
        funcs: List[FuncDef] = []
        extensions_list: List[ExtendDef] = []
        generic_extensions: List[ExtendDef] = []
        perk_impls: List[ExtendWithDef] = []
        externals_list: List[ExternalBlock] = []
        first_declaration_span = None

        # Constants come first, whatever order they were written in: a fixed array's
        # size may name one, and every other declaration can hold such a type.
        for ch in tree.children:
            if not isinstance(ch, Tree):
                continue
            const = (_first_tree(ch.children, "const_def") or _find_tree_recursive(ch, "const_def")
                     if ch.data == "toplevel" else
                     ch if ch.data == "const_def" else None)
            if const is not None:
                const_def = constants.parse_constdef(const, self)
                constants_list.append(const_def)
                self.unit_constants.declare(const_def.name, const_def)

        for ch in tree.children:
            if not isinstance(ch, Tree):
                continue
            node = ch
            if node.data == "toplevel":
                use = _first_tree(node.children, "use_stmt") or _find_tree_recursive(node, "use_stmt")
                if use is not None:
                    uses.append(imports.parse_usestatement(use, self))
                    continue

                # The first thing that is not an import. Source order lives in the tree
                # and nowhere else, so the rule that reads it (CE3014) is served here.
                if first_declaration_span is None:
                    first_declaration_span = span_of(node)

                const = _first_tree(node.children, "const_def") or _find_tree_recursive(node, "const_def")
                if const is not None:
                    continue

                struct = _first_tree(node.children, "struct_def") or _find_tree_recursive(node, "struct_def")
                if struct is not None:
                    structs_list.append(structs.parse_structdef(struct, self))
                    continue

                enum = _first_tree(node.children, "enum_def") or _find_tree_recursive(node, "enum_def")
                if enum is not None:
                    enums_list.append(enums.parse_enumdef(enum, self))
                    continue

                perk = _first_tree(node.children, "perk_def") or _find_tree_recursive(node, "perk_def")
                if perk is not None:
                    perks_list.append(perks.parse_perkdef(perk, self))
                    continue

                external = _first_tree(node.children, "external_block") or _find_tree_recursive(node, "external_block")
                if external is not None:
                    externals_list.append(externals.parse_external_block(external, self))
                    continue

                extend_stmt = _first_tree(node.children, "extend_stmt") or _find_tree_recursive(node, "extend_stmt")
                if extend_stmt is not None:
                    for child in extend_stmt.children:
                        if isinstance(child, Tree):
                            if child.data == "extend_with_def":
                                perk_impls.append(perks.parse_handle_extend_stmt_with(extend_stmt, self))
                                break
                            elif child.data == "extend_def":
                                ext_def = extensions.parse_handle_extend_stmt_def(extend_stmt, self)
                                if ext_def.target_type is not None and isinstance(ext_def.target_type, GenericTypeRef):
                                    generic_extensions.append(ext_def)
                                else:
                                    extensions_list.append(ext_def)
                                break
                    continue

                fn = _first_tree(node.children, "function_def") or _find_tree_recursive(node, "function_def")
                if fn is not None:
                    funcs.append(functions.parse_funcdef(fn, self))
                    continue

                ext_with = _first_tree(node.children, "extend_with_def") or _find_tree_recursive(node, "extend_with_def")
                if ext_with is not None:
                    perk_impls.append(perks.parse_extendwithdef(ext_with, self))
                    continue

            elif node.data == "use_stmt":
                uses.append(imports.parse_usestatement(node, self))
            elif node.data == "const_def":
                continue
            elif node.data == "struct_def":
                structs_list.append(structs.parse_structdef(node, self))
            elif node.data == "enum_def":
                enums_list.append(enums.parse_enumdef(node, self))
            elif node.data == "perk_def":
                perks_list.append(perks.parse_perkdef(node, self))
            elif node.data == "external_block":
                externals_list.append(externals.parse_external_block(node, self))
            elif node.data == "function_def":
                funcs.append(functions.parse_funcdef(node, self))
            elif node.data == "extend_stmt":
                for child in node.children:
                    if isinstance(child, Tree):
                        if child.data == "extend_with_def":
                            perk_impls.append(perks.parse_handle_extend_stmt_with(node, self))
                            break  # Only process one suffix per extend_stmt
                        elif child.data == "extend_def":
                            ext_def = extensions.parse_handle_extend_stmt_def(node, self)
                            if ext_def.target_type is not None and isinstance(ext_def.target_type, GenericTypeRef):
                                generic_extensions.append(ext_def)
                            else:
                                extensions_list.append(ext_def)
                            break  # Only process one suffix per extend_stmt
            elif node.data == "extend_with_def":
                perk_impls.append(perks.parse_extendwithdef(node, self))

        from sushi_lang.semantics.ast_builder.declarations.docs import attach_docs
        attach_docs(tree.children,
                    [*uses, *constants_list, *structs_list, *enums_list, *perks_list,
                     *funcs, *extensions_list, *generic_extensions, *perk_impls,
                     *externals_list],
                    self, allow_unit_doc=True)

        # A body block nothing lifted documents nothing: a lambda body, an `if` arm.
        for _body, doc in self.pending_body_docs.values():
            doc.orphan_reason = "detached"
            self.orphan_docs.append(doc)
        self.orphan_docs.sort(key=lambda d: (d.loc.line, d.loc.col) if d.loc else (0, 0))

        return Program(uses=uses, constants=constants_list, structs=structs_list, enums=enums_list, perks=perks_list, functions=funcs, extensions=extensions_list, generic_extensions=generic_extensions, perk_impls=perk_impls, externals=externals_list, loc=span_of(tree), doc=self.unit_doc, orphan_docs=self.orphan_docs,
                       first_declaration_span=first_declaration_span)

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

