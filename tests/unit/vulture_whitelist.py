"""The names that are live by a path vulture cannot see.

`tests/unit/test_no_dead_code.py` reads this module. It runs vulture twice: over
`sushi_lang/` (IGNORE_DECORATORS, WHITELIST, RATCHET) and over `tests/unit/`
(TEST_IGNORE_DECORATORS, TEST_WHITELIST). Each entry is
`(path pattern, name pattern, reason)`, matched with `fnmatch` against one vulture
candidate: the path is relative to the repository root. A pattern names a FAMILY;
a single name names one entry. An entry that matches no candidate fails the gate,
so this list cannot keep a name that is gone.

RATCHET holds the candidates that are dead or doubtful but that this gate does not
resolve. It may only get shorter: an entry that is no longer a candidate fails the
gate, and a new candidate cannot go in it without a reason and a review.
"""
from __future__ import annotations

# `@<decorator>` patterns passed to `vulture --ignore-decorators`.
IGNORE_DECORATORS: dict[str, str] = {
    "@METHOD_TYPE_REGISTRY.validator":
        "registers the validation hook of a built-in method family "
        "(semantics/passes/types/calls/methods.py)",
}

_PLATFORM_ABI = "platform ABI reference table"
_LLVMLITE = "llvmlite reads this attribute when it prints the module"
_TYPES = "sushi_lang/semantics/passes/types/"
_TIER_A = ("kept for Tier A (Phase 3): the typecheck stage of its construct group moves "
           "the helper to SHIR inputs")
_TIER_B = ("kept for Tier B (Phase 4): the typecheck stage of its construct group moves "
           "the helper to SHIR inputs")
_TIER_C = ("kept for Tier C (Phase 5): the typecheck stage of its construct group moves "
           "the helper to SHIR inputs")
_TIER_D = ("kept for Tier D (Phase 6): the typecheck stage of its construct group moves "
           "the helper to SHIR inputs")
WHITELIST: tuple[tuple[str, str, str], ...] = (
    # -- dispatch by a built name ----------------------------------------------------
    ("sushi_lang/semantics/*", "visit_*",
     "NodeVisitor dispatches on f'visit_{kind}' (semantics/visitors.py)"),
    ("sushi_lang/sushi_stdlib/src/*", "is_builtin_*_constant",
     "StdlibRegistry reads f'is_builtin_{module}_constant' by getattr"),
    ("sushi_lang/sushi_stdlib/src/*", "get_builtin_*_constant_value",
     "StdlibRegistry reads f'get_builtin_{module}_constant_value' by getattr"),
    ("sushi_lang/sushi_stdlib/src/_platform/*/files.py", "ST_*_OFFSET",
     "io/files/stat.py reads each offset by name with getattr"),
    ("sushi_lang/internals/styling.py", "italic",
     "compiler/lib_info.py reads a style by name from _MARKS with getattr"),
    ("sushi_lang/semantics/method_effects.py", "consumes_args",
     "methods_where() reads a MethodEffect flag by name with getattr"),
    # -- protocols of another library ------------------------------------------------
    ("sushi_lang/internals/indenter.py", "*_type",
     "the Lark Indenter protocol reads these class attributes"),
    ("sushi_lang/internals/indenter.py", "*_types",
     "the Lark Indenter protocol reads these class attributes"),
    ("sushi_lang/*", "global_constant", _LLVMLITE),
    ("sushi_lang/*", "unnamed_addr", _LLVMLITE),
    ("sushi_lang/compiler/cli.py", "__cause__",
     "Python reads the cause of an exception when it chains it"),
    # -- a string annotation reads it ------------------------------------------------
    ("sushi_lang/semantics/passes/collect/utils.py", "TypeNameTable",
     "the Protocol is named only in string annotations"),
    # -- a value of a closed set, compared by value ----------------------------------
    ("sushi_lang/semantics/places.py", "NONE", "the zero member of the Step flag"),
    ("sushi_lang/sushi_stdlib/src/io/files/positional.py", "INTENT_READ",
     "intent 0 of the fd_open protocol; the switch default handles it"),
    # -- gate data: a gate test reads it ---------------------------------------------
    ("sushi_lang/backend/lifecycle.py", "registered_halves", "tests/unit/test_lifecycle_handlers.py"),
    ("sushi_lang/internals/errors/registry.py", "category", "tests/unit/test_error_registry.py"),
    ("sushi_lang/semantics/ast.py", "attrs", "tests/docs_sweep.py reads the fence info string"),
    ("sushi_lang/semantics/ast_walk.py", "TERMINAL_NODES", "tests/unit/test_body_walk_is_total.py"),
    ("sushi_lang/semantics/ast_walk.py", "FIELD_KINDS", "tests/unit/test_node_walk_is_total.py"),
    ("sushi_lang/semantics/generics/contracts.py", "WALKED_KINDS",
     "tests/unit/test_contract_dispatch_is_total.py"),
    ("sushi_lang/semantics/generics/contracts.py", "ACCEPTED_KINDS",
     "tests/unit/test_contract_dispatch_is_total.py"),
    ("sushi_lang/semantics/generics/hashing.py", "WALKED_KINDS",
     "tests/unit/test_hashability_dispatch_is_total.py"),
    ("sushi_lang/semantics/generics/hashing.py", "HASHABLE_KINDS",
     "tests/unit/test_hashability_dispatch_is_total.py"),
    ("sushi_lang/semantics/name_ladder.py", "RUNGS", "tests/unit/test_bare_name_ladder_is_one.py"),
    ("sushi_lang/semantics/param_modes.py", "FFI_EXTERN", "tests/unit/test_callee_mode_matrix.py"),
    ("sushi_lang/semantics/passes/types/method_registry.py", "families",
     "tests/unit/test_method_family_dispatch_is_one.py"),
    ("sushi_lang/semantics/places.py", "*NOT_A_STEP", "tests/unit/test_place_walk_is_total.py"),
    (_TYPES + "fold.py", "UNREADABLE", "tests/unit/test_typeck_fold_is_total.py"),
    ("sushi_lang/semantics/stdlib_registry.py", "get_function", "tests/unit/test_stdlib_rows_are_one.py"),
    ("sushi_lang/semantics/type_walk.py", "TERMINAL_KINDS", "tests/unit/test_type_walk_is_total.py"),
    ("sushi_lang/semantics/visibility.py", "FOLLOWS_DECLARATION", "tests/unit/test_visibility_seam_is_total.py"),
    ("sushi_lang/semantics/visibility.py", "NO_VISIBILITY", "tests/unit/test_visibility_seam_is_total.py"),
    ("sushi_lang/sushi_stdlib/src/_platform/*/net.py", "AI_CANONNAME_OFFSET",
     "tests/unit/test_net_platform_constants.py"),
    ("sushi_lang/sushi_stdlib/src/collections/strings/__init__.py", "arg_count",
     "tests/unit/test_string_method_rows_are_one.py"),
    ("sushi_lang/sushi_stdlib/src/io/files_funcs.py", "FILE_UTILITY_FUNCTIONS",
     "tests/unit/test_stdlib_signature_tables.py"),
    ("sushi_lang/sushi_stdlib/src/io/files_funcs.py", "get_builtin_files_function_return_type",
     "tests/unit/test_stdlib_signature_tables.py"),
    ("sushi_lang/sushi_stdlib/src/net/socket_funcs.py", "SOCKET_FUNCTIONS",
     "tests/unit/test_stdlib_signature_tables.py"),
    ("sushi_lang/sushi_stdlib/src/net/socket_funcs.py", "get_builtin_socket_function_return_type",
     "tests/unit/test_stdlib_signature_tables.py"),
    # -- kept by a maintainer ruling, one entry each (no pattern) --------------------
    ("sushi_lang/sushi_stdlib/src/_platform/*/net.py", "SO_ERROR", _PLATFORM_ABI),
    ("sushi_lang/sushi_stdlib/src/_platform/*/net.py", "SOCKADDR_HAS_LEN", _PLATFORM_ABI),
    ("sushi_lang/backend/library_format.py", "FLAG_SOURCE_COMPRESSED",
     "a reserved bit of the .slib header format; the library design defines it and "
     "the writer keeps it zero"),
    # -- kept for a later chunk of the IR migration (docs/design/ir.md) --------------
    ("sushi_lang/semantics/shir/typeck.py", "record_capture",
     "kept for the closures group of Phase 5: the typecheck pass on SHIR writes the type "
     "of each capture"),
    ("sushi_lang/semantics/shir/typeck.py", "record_field_index",
     "kept for the struct and tuple group of Phase 3: the typecheck pass on SHIR writes "
     "the index of a field"),
    ("sushi_lang/semantics/shir/typeck.py", "record_range_value",
     "kept for the range group of Phase 3: the typecheck pass on SHIR writes whether a "
     "range is a value"),
    ("sushi_lang/semantics/shir/typeck.py", "record_match",
     "kept for the match groups of Phase 3: the typecheck pass on SHIR writes the facts "
     "of a match"),
    ("sushi_lang/semantics/shir/typeck.py", "record_element",
     "kept for the struct and tuple group of Phase 3: the typecheck pass on SHIR writes "
     "the type of each target of a destructure"),
    ("sushi_lang/semantics/shir/typeck.py", "capture_type",
     "kept for the closures group of Phase 5: lift and SLIR lowering read the type of each "
     "capture"),
    ("sushi_lang/semantics/shir/typeck.py", "integer",
     "kept for the match group of Phase 3: SHIR-to-SLIR lowering reads the integer type "
     "of a literal arm and of a range arm"),
    ("sushi_lang/semantics/shir/ownership.py", "record_*",
     "kept for P2.6: the borrow pass on SHIR is the one writer of OwnershipResults"),
    ("sushi_lang/semantics/shir/ownership.py", "is_conditionally_moved",
     "kept for P2.9: SHIR-to-SLIR lowering gives a conditionally moved local a drop flag"),
    ("sushi_lang/semantics/shir/lift.py", "record_*",
     "kept for P2.7: the lift pass on SHIR is the one writer of LiftResults"),
    ("sushi_lang/semantics/shir/lift_check.py", "check_lift",
     "kept for P2.7: the lift pass on SHIR checks its table when it ends a body"),
    # -- the stages after typecheck, which the driver does not run from the switch ----
    ("sushi_lang/semantics/passes/lift.py", "LambdaLifter",
     "kept for P2.7: the driver runs lift on SHIR after typecheck"),
    ("sushi_lang/semantics/passes/lift.py", "lift_perk_impl",
     "kept for P2.7: the driver runs lift on SHIR after typecheck"),
    ("sushi_lang/compiler/pipeline.py", "_resolve_out_path",
     "kept for P2.11: the emitter of a SlirFunction writes the output of the compile"),
    ("sushi_lang/compiler/pipeline.py", "_compile_monolithic",
     "kept for P2.11: the emitter of a SlirFunction builds one module for each unit"),
    ("sushi_lang/compiler/pipeline.py", "_compile_incremental",
     "kept for P2.11: the emitter of a SlirFunction keeps the per-unit cache"),
    # -- the AST helpers of typecheck that the switch left with no caller (P2.3) -----
    # Each one moves to SHIR inputs in the typecheck stage of its construct group
    # in the migration plan, and that stage deletes the entry.
    ("sushi_lang/semantics/ast.py", "in_cast_context", _TIER_A),
    (_TYPES + "expressions.py", "in_cast_context", _TIER_A),
    (_TYPES + "utils.py", "reject_named_args", _TIER_A),
    (_TYPES + "calls/user_defined.py", "validate_indirect_call", _TIER_C),
    (_TYPES + "calls/user_defined.py", "validate_errno_call", _TIER_D),
    (_TYPES + "calls/user_defined.py", "check_stdlib_function", _TIER_A),
    (_TYPES + "matching.py", "validate_match_statement", _TIER_A),
    (_TYPES + "constants.py", "assignment_span", _TIER_A),
    (_TYPES + "statements.py", "stamp_rebind_places", _TIER_A),
    (_TYPES + "statements.py", "validate_rebind_statement", _TIER_A),
    (_TYPES + "statements.py", "validate_let_destructure", _TIER_A),
    (_TYPES + "expressions.py", "refuse_range_value", _TIER_A),
    (_TYPES + "expressions.py", "reject_unknown_field", _TIER_A),
    (_TYPES + "expressions.py", "validate_array_literal", _TIER_A),
    (_TYPES + "expressions.py", "validate_cast_expression", _TIER_A),
    (_TYPES + "expressions.py", "validate_index_access", _TIER_A),
    (_TYPES + "calls/dotcall.py", "validate_variant_spelling", _TIER_A),
    (_TYPES + "calls/namespaced.py", "validate_namespaced_call", _TIER_A),
    (_TYPES + "calls/statics.py", "validate_static_call", _TIER_A),
    (_TYPES + "statements.py", "validate_let_reference", _TIER_B),
    (_TYPES + "arguments.py", "reject_type_pack_value", _TIER_C),
    (_TYPES + "visit/helpers.py", "validate_fn_field_call_args", _TIER_C),
    (_TYPES + "signatures.py", "check_template_statements", _TIER_C),
    (_TYPES + "statements.py", "validate_foreach_statement", _TIER_A),
    (_TYPES + "statements.py", "validate_expand_statement", _TIER_C),
)

# -- the AST helpers of borrow that the switch left with no caller (P2.6a) ------------
# Each one moves to SHIR inputs in the borrow stage of its construct group in the
# migration plan, and that stage deletes the entry.
_BORROW = "sushi_lang/semantics/passes/borrow/"


def _kept(tier: str, groups: str) -> str:
    return (f"kept for Tier {tier} ({groups}): the borrow stage of its construct group "
            "moves the helper to SHIR inputs")


WHITELIST += (
    (_BORROW + "flow.py", "without", _kept("C", "C3: iterators, the item of each round")),
    (_BORROW + "flow.py", "invalidation_of", _kept("C", "C3: iterators, a view of a loop")),
    (_BORROW + "flow.py", "LoopFlow", _kept("C", "C3: iterators, a view of a loop")),
    (_BORROW + "bindings.py", "register_pattern_bindings", _kept("A", "A3, A4: match")),
    (_BORROW + "bindings.py", "reject_partial_take", _kept("A", "A3, A4: match")),
    (_BORROW + "calls.py", "_borrowed_method_args", _kept("A", "A1, A10: method calls")),
    (_BORROW + "calls.py", "settle_receiver", _kept("A", "A1, A10: method calls")),
    (_BORROW + "calls.py", "settle_method_args", _kept("A", "A1, A10: method calls")),
    (_BORROW + "writes.py", "maybe_reject_mutation", _kept("A", "A1, A10: method calls")),
    (_BORROW + "reads.py", "_field_type", _kept("A", "A5: struct fields")),
    (_BORROW + "reads.py", "constant_type", _kept("A", "A8: constants")),
    (_BORROW + "calls.py", "reject_self_aliasing_copy", _kept("B", "B1: owning arrays")),
    (_BORROW + "consume.py", "reject_slice_of_temporary", _kept("B", "B1: owning arrays")),
    (_BORROW + "calls.py", "maybe_mark_container_insert", _kept("B", "B2, B4: containers")),
    (_BORROW + "calls.py", "maybe_mark_own_alloc_move", _kept("B", "B5: Own")),
    (_BORROW + "takes.py", "field_take", _kept("B", "B7: field take")),
    (_BORROW + "takes.py", "spend", _kept("B", "B7: field take")),
    (_BORROW + "bindings.py", "bind_let_reference", _kept("B", "B8: reference let")),
    (_BORROW + "borrowed_returns.py", "body_return", _kept("B", "B8: peek returns")),
    (_BORROW + "borrowed_returns.py", "settle_borrowed_call", _kept("B", "B8: peek returns")),
    (_BORROW + "borrowed_returns.py", "reject_view_of_temporary",
     _kept("B", "B8: peek returns")),
    (_BORROW + "borrowed_returns.py", "check_borrowed_return",
     _kept("B", "B8: peek returns")),
    (_BORROW + "calls.py", "consume_indirect_args", _kept("C", "C2: closures")),
    (_BORROW + "consume.py", "reconcile_closure_bind", _kept("C", "C2: closures")),
    (_BORROW + "bindings.py", "bind_item", _kept("C", "C3: iterators")),
    (_BORROW + "bindings.py", "bind_iterator", _kept("C", "C3: iterators")),
    (_BORROW + "bindings.py", "bind_lent_walk", _kept("C", "C3: iterators")),
    (_BORROW + "iterators.py", "settle_walk", _kept("C", "C3: iterators")),
    (_BORROW + "iterators.py", "settle_iterator_source", _kept("C", "C3: iterators")),
    (_BORROW + "diagnostics.py", "emit_change_under_iterator", _kept("C", "C3: iterators")),
    (_BORROW + "reads.py", "unit_variables", _kept("D", "D1: var")),
    (_BORROW + "state.py", "is_unit_var", _kept("D", "D1: var")),
)

RATCHET: tuple[tuple[str, str, str], ...] = ()

# -- the scan of `tests/unit/` ---------------------------------------------------------

TEST_IGNORE_DECORATORS: dict[str, str] = {
    "@pytest.fixture": "pytest calls a fixture that a test names as a parameter, or an autouse one",
}

_FIXTURE = "a test requests this fixture by its parameter name"
_STAND_IN = "a stand-in keeps the parameter name of the function it replaces"
_READ_BY_CODE = "code under test reads this attribute of the stand-in; this scan does not see sushi_lang/"

TEST_WHITELIST: tuple[tuple[str, str, str], ...] = (
    # -- the pytest protocol ---------------------------------------------------------
    ("tests/unit/*", "pytest_*", "pytest calls a hook by its name"),
    ("tests/unit/*", "pytestmark", "pytest reads the module-level mark"),
    ("tests/unit/compiler_stage_gate.py", "longrepr", "pytest prints the longrepr of a report"),
    ("tests/unit/compiler_stage_gate.py", "exitstatus", "pytest exits with the session exitstatus"),
    ("tests/unit/runner/test_a_skipped_leak_check_is_not_a_pass.py", "no_interposer", _FIXTURE),
    ("tests/unit/test_diagnostic_spelling_gate.py", "armed", _FIXTURE),
    ("tests/unit/test_diagnostic_spelling_gate.py", "disarmed", _FIXTURE),
    # -- a stand-in for a real object ------------------------------------------------
    ("tests/unit/runner/test_leak_gate_wiring.py", "binary_path", _STAND_IN),
    ("tests/unit/test_nori_colour.py", "repo", _STAND_IN),
    ("tests/unit/test_nori_colour.py", "mp", "every RENDERERS row takes the monkeypatch"),
    ("tests/unit/test_nori_errors.py", "repo", _STAND_IN),
    ("tests/unit/test_pack_substitution.py", "template_file", _STAND_IN),
    ("tests/unit/test_pack_substitution.py", "error_params", _STAND_IN),
    ("tests/unit/test_pack_substitution.py", "_validate_type_constraints", _READ_BY_CODE),
    ("tests/unit/test_pack_substitution.py", "ret_span", _READ_BY_CODE),
    ("tests/unit/test_pack_substitution.py", "err_span", _READ_BY_CODE),
    ("tests/unit/test_lambda_annotate_seam.py", "infer_expression_type", _READ_BY_CODE),
    ("tests/unit/test_borrow_dispatch_is_total.py", "decl_name", _READ_BY_CODE),
    ("tests/unit/test_library_registration_records.py", "loaded_libraries", _READ_BY_CODE),
    ("tests/unit/test_drop_invalidates_cache.py", "file_path", _READ_BY_CODE),
    ("tests/unit/test_drop_invalidates_cache.py", "public_symbols", _READ_BY_CODE),
    ("tests/unit/test_drop_invalidates_cache.py", "dependencies", _READ_BY_CODE),
    ("tests/unit/test_nori_colour.py", "isatty", "the colour decision asks the stream isatty()"),
    ("tests/unit/test_report_colour.py", "isatty", "the colour decision asks the stream isatty()"),
    ("tests/unit/test_nori_errors.py", "size", "tarfile reads TarInfo.size when it adds the member"),
    # -- a write to a real object that the code under test reads ---------------------
    ("tests/unit/test_cache.py", "_target_triple", "BuildCache.global_key reads the field"),
    ("tests/unit/test_generic_instance_reports_once.py", "collapse_repeats",
     "Reporter.error reads the flag"),
    ("tests/unit/test_stdlib_build_is_one_table.py", "stdlib_dir",
     "StdlibLinker._resolve_stdlib_unit reads the field"),
    ("tests/unit/test_ast_nodes_are_slotted.py", "some_analysis_result",
     "the test writes it to show that __slots__ refuses it"),
    ("tests/unit/test_shir_nodes_are_slotted.py", "inferred_type",
     "the test writes it to show that __slots__ refuses it"),
    # -- read by name ----------------------------------------------------------------
    ("tests/unit/test_bare_name_ladder_is_one.py", "is_*",
     "the test reads each Rungs method by name with getattr"),
)
