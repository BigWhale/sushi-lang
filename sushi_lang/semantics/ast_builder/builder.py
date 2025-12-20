"""Main ASTBuilder orchestrator for Sushi language compiler.

This module contains the core ASTBuilder class that coordinates parsing of Lark
parse trees into typed AST nodes. The builder delegates to specialized parsers:

- Type parsing: semantics.ast_builder.types
- Expression parsing: semantics.ast_builder.expressions
- Statement parsing: semantics.ast_builder.statements
- Declaration parsing: semantics.ast_builder.declarations
- Utilities: semantics.ast_builder.utils

Architecture:
    - Strategy pattern for type/expression/statement parsing
    - Direct delegation for declaration parsing
    - Lazy initialization of parser instances
    - Zero runtime overhead through static dispatch
"""
from __future__ import annotations
from typing import List, Optional

from lark import Tree, Token

from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.generics.types import GenericTypeRef

from sushi_lang.semantics.ast import (
    Program, UseStatement, FuncDef, ConstDef, ExtendDef, Block,
    StructDef, EnumDef, PerkDef, ExtendWithDef, Expr,
)
from sushi_lang.internals.report import span_of


# ------------------------
# Import utilities from new locations
# ------------------------

# Tree navigation utilities
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first as _first,
    first_name as _first_name,
    first_method_name as _first_method_name,
    first_tree as _first_tree,
    find_tree_recursive as _find_tree_recursive,
    first_tree_child as _first_tree_child,
)

# Expression discovery utilities
from sushi_lang.semantics.ast_builder.utils.expression_discovery import (
    contains_expr_like as _contains_expr_like,
    contains_op as _contains_op,
    token_count as _token_count,
    find_outer_expr_structural as _find_outer_expr_structural,
    expr_and_block as _expr_and_block,
    _EXPR_NODES,
)

# String processing utilities
from sushi_lang.semantics.ast_builder.utils.string_processing import (
    process_string_escapes as _process_string_escapes,
    parse_interpolated_string as _parse_interpolated_string,
    get_interpolation_parser as _get_interpolation_parser,
    apply_location_offset as _apply_location_offset,
    parse_interpolation_expr as _parse_interpolation_expr,
    parse_string_token as _parse_string_token,
)




# ------------------------
# AST Builder
# ------------------------

class ASTBuilder:
    def __init__(self):
        """Initialize ASTBuilder with lazy-loaded parsers."""
        self._type_parser = None
        self._expr_parser = None
        self._stmt_parser = None

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
        """Build Program AST from parse tree.

        Orchestrates parsing of all top-level declarations using specialized parsers.
        """
        from sushi_lang.semantics.ast_builder.declarations import imports, functions, constants, structs, enums, perks, extensions

        assert isinstance(tree, Tree) and tree.data == "program"
        uses: List[UseStatement] = []
        constants_list: List[ConstDef] = []
        structs_list: List[StructDef] = []
        enums_list: List[EnumDef] = []
        perks_list: List[PerkDef] = []
        funcs: List[FuncDef] = []
        extensions_list: List[ExtendDef] = []
        generic_extensions: List[ExtendDef] = []
        perk_impls: List[ExtendWithDef] = []

        for ch in tree.children:
            if not isinstance(ch, Tree):
                continue
            node = ch
            if node.data == "toplevel":
                # Look for use statement
                use = _first_tree(node.children, "use_stmt") or _find_tree_recursive(node, "use_stmt")
                if use is not None:
                    uses.append(imports.parse_usestatement(use, self))
                    continue

                # Look for constant definition
                const = _first_tree(node.children, "const_def") or _find_tree_recursive(node, "const_def")
                if const is not None:
                    constants_list.append(constants.parse_constdef(const, self))
                    continue

                # Look for struct definition
                struct = _first_tree(node.children, "struct_def") or _find_tree_recursive(node, "struct_def")
                if struct is not None:
                    structs_list.append(structs.parse_structdef(struct, self))
                    continue

                # Look for enum definition
                enum = _first_tree(node.children, "enum_def") or _find_tree_recursive(node, "enum_def")
                if enum is not None:
                    enums_list.append(enums.parse_enumdef(enum, self))
                    continue

                # Look for perk definition
                perk = _first_tree(node.children, "perk_def") or _find_tree_recursive(node, "perk_def")
                if perk is not None:
                    perks_list.append(perks.parse_perkdef(perk, self))
                    continue

                # Look for extend_stmt BEFORE function_def (to avoid matching nested functions)
                extend_stmt = _first_tree(node.children, "extend_stmt") or _find_tree_recursive(node, "extend_stmt")
                if extend_stmt is not None:
                    # Check which type of extension it is
                    for child in extend_stmt.children:
                        if isinstance(child, Tree):
                            if child.data == "extend_with_def":
                                # Perk implementation
                                perk_impls.append(perks.parse_handle_extend_stmt_with(extend_stmt, self))
                                break
                            elif child.data == "extend_def":
                                # Regular extension method
                                ext_def = extensions.parse_handle_extend_stmt_def(extend_stmt, self)
                                if ext_def.target_type is not None and isinstance(ext_def.target_type, GenericTypeRef):
                                    generic_extensions.append(ext_def)
                                else:
                                    extensions_list.append(ext_def)
                                break
                    continue

                # Look for function definition
                fn = _first_tree(node.children, "function_def") or _find_tree_recursive(node, "function_def")
                if fn is not None:
                    funcs.append(functions.parse_funcdef(fn, self))
                    continue

                # Legacy handlers for extend_def and extend_with_def (if they appear standalone)
                ext = _first_tree(node.children, "extend_def") or _find_tree_recursive(node, "extend_def")
                if ext is not None:
                    ext_def = extensions.parse_extenddef(ext, self)
                    # Separate generic and non-generic extensions
                    if ext_def.target_type is not None and isinstance(ext_def.target_type, GenericTypeRef):
                        generic_extensions.append(ext_def)
                    else:
                        extensions_list.append(ext_def)
                    continue

                # Look for perk implementation (extend...with) - legacy
                ext_with = _first_tree(node.children, "extend_with_def") or _find_tree_recursive(node, "extend_with_def")
                if ext_with is not None:
                    perk_impls.append(perks.parse_extendwithdef(ext_with, self))
                    continue

            elif node.data == "use_stmt":
                uses.append(imports.parse_usestatement(node, self))
            elif node.data == "const_def":
                constants_list.append(constants.parse_constdef(node, self))
            elif node.data == "struct_def":
                structs_list.append(structs.parse_structdef(node, self))
            elif node.data == "enum_def":
                enums_list.append(enums.parse_enumdef(node, self))
            elif node.data == "perk_def":
                perks_list.append(perks.parse_perkdef(node, self))
            elif node.data == "function_def":
                funcs.append(functions.parse_funcdef(node, self))
            elif node.data == "extend_stmt":
                # Handle unified extend statement (not in toplevel section)
                # The extend_suffix child will be aliased to either "extend_def" or "extend_with_def"
                for child in node.children:
                    if isinstance(child, Tree):
                        if child.data == "extend_with_def":
                            # Perk implementation
                            perk_impls.append(perks.parse_handle_extend_stmt_with(node, self))
                            break  # Only process one suffix per extend_stmt
                        elif child.data == "extend_def":
                            # Regular extension method
                            ext_def = extensions.parse_handle_extend_stmt_def(node, self)
                            if ext_def.target_type is not None and isinstance(ext_def.target_type, GenericTypeRef):
                                generic_extensions.append(ext_def)
                            else:
                                extensions_list.append(ext_def)
                            break  # Only process one suffix per extend_stmt
            elif node.data == "extend_def":
                ext_def = extensions.parse_extenddef(node, self)
                # Separate generic and non-generic extensions
                if ext_def.target_type is not None and isinstance(ext_def.target_type, GenericTypeRef):
                    generic_extensions.append(ext_def)
                else:
                    extensions_list.append(ext_def)
            elif node.data == "extend_with_def":
                perk_impls.append(perks.parse_extendwithdef(node, self))

        return Program(uses=uses, constants=constants_list, structs=structs_list, enums=enums_list, perks=perks_list, functions=funcs, extensions=extensions_list, generic_extensions=generic_extensions, perk_impls=perk_impls, loc=span_of(tree))

    # --- type parsing ---

    def _parse_type(self, type_node: Tree) -> Optional[Type]:
        """Parse a type node into a Type object.

        Delegates to TypeParser for all type parsing logic.
        """
        return self.type_parser.parse_type(type_node)


    # --- blocks & statements ---

    def _block(self, t: Tree) -> Block:
        """Parse block with dispatch to statement handlers.

        Delegates to blocks.parse_block for all block parsing logic.
        """
        from sushi_lang.semantics.ast_builder.statements.blocks import parse_block
        return parse_block(t, self)


    # --- expressions ---

    def _expr(self, t: Tree | Token) -> Expr:
        """Parse an expression node into an Expr object.

        Delegates to ExpressionParser for all expression parsing logic.
        """
        return self.expr_parser.parse_expr(t)

