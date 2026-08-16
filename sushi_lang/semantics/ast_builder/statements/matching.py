"""Match statement and pattern parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Union
from lark import Tree, Token
from sushi_lang.semantics.ast import Match, MatchArm, Pattern, WildcardPattern, OwnPattern, Block, Expr
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree, ice, expect, unhandled
from sushi_lang.semantics.ast_builder.utils.expression_discovery import _EXPR_NODES, contains_expr_like
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_match_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Match:
    """Parse match_stmt: MATCH expr ":" _NEWLINE _INDENT match_arm+ _DEDENT"""
    # Find scrutinee expression - it should be a direct child (not inside match_arm)
    scrutinee_tree = None
    arms: List[MatchArm] = []

    for child in node.children:
        if isinstance(child, Tree):
            if child.data == "match_arm":
                arms.append(parse_matcharm(child, ast_builder))
            elif child.data in _EXPR_NODES and scrutinee_tree is None:
                # This is the scrutinee expression (first expression node that's a direct child)
                scrutinee_tree = child

    if scrutinee_tree is None:
        ice(node, "missing scrutinee expression")

    if not arms:
        ice(node, "must have at least one match arm")

    scrutinee = ast_builder._expr(scrutinee_tree)
    return Match(scrutinee=scrutinee, arms=arms, loc=span_of(node))


def parse_matcharm(t: Tree, ast_builder: 'ASTBuilder') -> MatchArm:
    """Parse match_arm: (pattern | wildcard_pattern) "->" (expr _NEWLINE | block)"""
    t = expect(t, "match_arm")

    # Extract pattern (can be pattern or wildcard_pattern)
    pattern: Union[Pattern, WildcardPattern]
    pattern_tree = first_tree(t.children, "pattern")
    wildcard_tree = first_tree(t.children, "wildcard_pattern")

    if pattern_tree is not None:
        pattern = parse_pattern(pattern_tree, ast_builder)
    elif wildcard_tree is not None:
        pattern = WildcardPattern(loc=span_of(wildcard_tree))
    else:
        ice(t, "missing pattern")

    # Extract body (either expression or block)
    body: Union[Expr, Block] = None  # type: ignore
    for child in t.children:
        if isinstance(child, Tree):
            if child.data == "block":
                body = ast_builder._block(child)
                break
            elif child.data == "match_arm_inline":
                # Single-line match arm with statement or expression
                # Convert to a single-statement block
                inline_child = child.children[0]
                if isinstance(inline_child, Tree):
                    if inline_child.data in ("return_stmt", "print_stmt", "println_stmt", "call_stmt", "break_stmt", "continue_stmt"):
                        # It's a statement - wrap in a block
                        stmt = ast_builder.stmt_parser.parse_stmt(inline_child)
                        body = Block(statements=[stmt], loc=span_of(child))
                    elif inline_child.data in _EXPR_NODES or contains_expr_like(inline_child):
                        # It's an expression - keep as expression
                        body = ast_builder._expr(inline_child)
                    else:
                        unhandled(inline_child)
                else:
                    # Token - shouldn't happen
                    unhandled(inline_child)
                break
            elif child.data in _EXPR_NODES or contains_expr_like(child):
                body = ast_builder._expr(child)
                break

    if body is None:
        ice(t, "missing body")

    return MatchArm(pattern=pattern, body=body, loc=span_of(t))


def parse_pattern(t: Tree, ast_builder: 'ASTBuilder', nested: bool = False) -> Pattern:
    """Parse pattern: NAME "." NAME ["(" pattern_list ")"]

    Supports nested patterns for matching nested enums:
    - FileResult.Ok(f) - simple binding
    - FileResult.Err(FileError.NotFound()) - nested pattern
    - FileResult.Err(_) - wildcard
    - Shape.Poly(poke p) - reference binding (#300 phase 3; TOP-LEVEL patterns only,
      because nested-pattern extraction walks through temporary copies and a pointer
      into one is a silently lost write -- `nested` carries that fact down)
    """
    t = expect(t, "pattern")

    # Extract enum and variant names (two NAME tokens)
    names = [ch for ch in t.children if isinstance(ch, Token) and ch.type == "NAME"]
    if len(names) < 2:
        ice(t, "expected EnumName.VariantName format")

    enum_name_tok = names[0]
    variant_name_tok = names[1]

    # Extract bindings (if any) - now supports nested patterns
    bindings: List[Union[str, Pattern]] = []
    pattern_list_tree = first_tree(t.children, "pattern_list")
    if pattern_list_tree is not None:
        for child in pattern_list_tree.children:
            if isinstance(child, Token):
                if child.type == "NAME":
                    bindings.append(str(child.value))
                elif child.type == "UNDERSCORE":
                    # Store underscore as "_" - scope analyzer will skip it
                    bindings.append("_")
            elif isinstance(child, Tree):
                # A reference binding (`Variant(poke x)`): legal in a TOP-LEVEL pattern
                # (#300 phase 3, on the aligned enum payload layout). In a NESTED pattern
                # it stays CE2424: nested extraction walks through temporary copies, so a
                # pointer into one writes to storage nobody reads.
                if child.data == "ref_binding":
                    if nested:
                        raise SyntaxDiagnostic("CE2424", span=span_of(child)) \
                            .help("bind the payload by value in the nested pattern, or "
                                  "restructure to match the inner enum at the top level")
                    mode_tok = next((c for c in child.children
                                     if isinstance(c, Token) and c.type == "BORROW_MODE"), None)
                    name_tok = next((c for c in child.children
                                     if isinstance(c, Token) and c.type == "NAME"), None)
                    if mode_tok is None or name_tok is None:
                        ice(t, "malformed ref_binding in pattern")
                    from sushi_lang.semantics.ast import RefBinding
                    bindings.append(RefBinding(
                        name=str(name_tok.value), mode=str(mode_tok.value),
                        loc=span_of(child)))
                    continue
                # Recurse for pattern_item
                if child.data == "pattern_item":
                    # pattern_item can be: pattern | NAME | wildcard_pattern | own_pattern_call
                    # Check what's inside
                    inner_pattern = first_tree(child.children, "pattern")
                    inner_wildcard = first_tree(child.children, "wildcard_pattern")
                    inner_own = first_tree(child.children, "own_pattern_call")
                    if inner_pattern is not None:
                        # Nested pattern - recurse
                        bindings.append(parse_pattern(inner_pattern, ast_builder, nested=True))
                    elif inner_wildcard is not None:
                        # Wildcard pattern
                        bindings.append("_")
                    elif inner_own is not None:
                        # Own pattern - parse it
                        bindings.append(parse_own_pattern(inner_own, ast_builder))
                    else:
                        # NAME token
                        token = next((c for c in child.children if isinstance(c, Token)), None)
                        if token and token.type == "NAME":
                            bindings.append(str(token.value))

    return Pattern(
        enum_name=str(enum_name_tok.value),
        variant_name=str(variant_name_tok.value),
        bindings=bindings,
        enum_name_span=span_of(enum_name_tok),
        variant_name_span=span_of(variant_name_tok),
        loc=span_of(t),
    )


def parse_own_pattern(t: Tree, ast_builder: 'ASTBuilder') -> 'OwnPattern':
    """Parse own_pattern: NAME "(" pattern_item ")"

    The NAME should be "Own", but we verify it at runtime.
    Returns an OwnPattern node with the inner pattern.
    """
    t = expect(t, "own_pattern_call")

    # Verify the NAME is "Own"
    name_token = next((c for c in t.children if isinstance(c, Token) and c.type == "NAME"), None)
    if name_token is None or str(name_token.value) != "Own":
        ice(t, f"expected 'Own' in pattern, got {name_token.value if name_token else 'nothing'}")

    # `Own(poke x)` / `Own(peek x)` (#300 phase 1): the alias renames the whole
    # pattern_item tree, so the reference form arrives as a `ref_binding` sibling.
    # Malloc'd storage is naturally aligned, so the enum-payload wall that defers
    # plain `poke` pattern bindings (CE2424) does not apply here.
    ref_tree = first_tree(t.children, "ref_binding")
    if ref_tree is not None:
        mode_tok = next((c for c in ref_tree.children
                         if isinstance(c, Token) and c.type == "BORROW_MODE"), None)
        name_tok = next((c for c in ref_tree.children
                         if isinstance(c, Token) and c.type == "NAME"), None)
        if mode_tok is None or name_tok is None:
            ice(t, "malformed ref_binding inside Own pattern")
        return OwnPattern(
            inner_pattern=str(name_tok.value),
            inner_borrow=str(mode_tok.value),
            inner_borrow_span=span_of(ref_tree),
            loc=span_of(t)
        )

    # Find the pattern_item inside
    pattern_item_tree = first_tree(t.children, "pattern_item")
    if pattern_item_tree is None:
        ice(t, "own_pattern must contain a pattern_item")

    # Parse the inner pattern_item
    # It can be: pattern | NAME | wildcard_pattern | own_pattern (nested)
    inner_pattern = first_tree(pattern_item_tree.children, "pattern")
    inner_wildcard = first_tree(pattern_item_tree.children, "wildcard_pattern")
    inner_own = first_tree(pattern_item_tree.children, "own_pattern_call")

    if inner_pattern is not None:
        # Nested pattern
        inner = parse_pattern(inner_pattern, ast_builder, nested=True)
    elif inner_wildcard is not None:
        # Wildcard - store as "_"
        inner = "_"
    elif inner_own is not None:
        # Nested Own pattern
        inner = parse_own_pattern(inner_own, ast_builder)
    else:
        # NAME token
        token = next((c for c in pattern_item_tree.children if isinstance(c, Token)), None)
        if token and token.type == "NAME":
            inner = str(token.value)
        else:
            ice(t, "invalid own_pattern inner item")

    return OwnPattern(
        inner_pattern=inner,
        loc=span_of(t)
    )
