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


def _port(group: str) -> str:
    return (f"a port template: it calls TypeValidator.validate_expression, which is deleted, "
            f"and no pass reaches it; group {group} ports it to SHIR inputs before a SHIR "
            f"pass calls it")


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
    ("sushi_lang/semantics/ast.py", "attrs", "tests/docs_sweep.py reads the fence info string"),
    ("sushi_lang/semantics/shir/stop.py", "parse_stop", "tests/stop_census.py"),
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
    ("sushi_lang/semantics/shir/ownership.py", "record_capture_ownership",
     "kept for the closures group C2: the borrow pass on SHIR writes the ownership of each "
     "capture"),
    ("sushi_lang/semantics/shir/ownership.py", "record_lent*",
     "kept for the iterators group C3 and the peek return group B8: the borrow pass on "
     "SHIR writes the lent or owned state and the heap decision of an iterator"),
    ("sushi_lang/semantics/shir/ownership.py", "record_in_place",
     "kept for the iterators group C3: the borrow pass on SHIR writes the walk in place of "
     "a loop over an iterator value or a next() type"),
    ("sushi_lang/semantics/shir/lift.py", "record_*",
     "kept for the closures group of Phase 5: the lift pass on SHIR writes the symbol and "
     "the environment of each Lambda, where it stops today"),
    # -- the AST lift pass, which no stage runs from the switch -----------------------
    ("sushi_lang/semantics/passes/lift.py", "LambdaLifter",
     "kept for the closures group of Phase 5: the lift pass on SHIR takes the symbol and "
     "the environment layout from it"),
    ("sushi_lang/semantics/passes/lift.py", "lift_perk_impl",
     "kept for the closures group of Phase 5: the lift pass on SHIR takes the symbol and "
     "the environment layout from it"),
    # -- the AST helpers of typecheck that the switch left with no caller (P2.3) -----
    # Each one moves to SHIR inputs in the typecheck stage of its construct group
    # in the migration plan, and that stage deletes the entry.
    ("sushi_lang/semantics/ast.py", "in_cast_context", _TIER_A),
    (_TYPES + "expressions.py", "in_cast_context", _TIER_A),
    (_TYPES + "utils.py", "reject_named_args", _TIER_A),
    (_TYPES + "calls/user_defined.py", "validate_indirect_call", _port("C2")),
    (_TYPES + "calls/user_defined.py", "validate_errno_call", _TIER_D),
    (_TYPES + "calls/user_defined.py", "check_stdlib_function", _TIER_A),
    (_TYPES + "matching.py", "validate_match_statement", _port("A3 or A4")),
    (_TYPES + "constants.py", "assignment_span", _TIER_A),
    (_TYPES + "statements.py", "stamp_rebind_places", _TIER_A),
    (_TYPES + "statements.py", "validate_rebind_statement", _port("A5")),
    (_TYPES + "statements.py", "validate_let_destructure", _port("A5")),
    (_TYPES + "expressions.py", "refuse_range_value", _port("A7")),
    (_TYPES + "expressions.py", "reject_unknown_field", _TIER_A),
    (_TYPES + "expressions.py", "validate_array_literal", _port("A6")),
    (_TYPES + "expressions.py", "validate_cast_expression", _port("A1")),
    (_TYPES + "expressions.py", "validate_index_access", _port("A6")),
    (_TYPES + "calls/dotcall.py", "validate_variant_spelling", _port("A4")),
    (_TYPES + "calls/namespaced.py", "validate_namespaced_call", _port("A8")),
    (_TYPES + "calls/statics.py", "validate_static_call", _TIER_A),
    (_TYPES + "statements.py", "validate_let_reference", _port("B8")),
    (_TYPES + "arguments.py", "reject_type_pack_value", _TIER_C),
    (_TYPES + "visit/helpers.py", "validate_fn_field_call_args", _port("C2")),
    (_TYPES + "signatures.py", "check_template_statements", _TIER_C),
    (_TYPES + "statements.py", "validate_foreach_statement", _port("A7")),
    (_TYPES + "statements.py", "validate_expand_statement", _TIER_C),
)

# -- the AST helpers of borrow that the switch left with no caller (P2.6a) ------------
# Each one moves to SHIR inputs in the borrow stage of its construct group in the
# migration plan, and that stage deletes the entry.
_BORROW = "sushi_lang/semantics/passes/borrow/"


def _kept(tier: str, groups: str) -> str:
    return (f"kept for Tier {tier} ({groups}): the borrow stage of its construct group "
            "moves the helper to SHIR inputs")


def _ast_probe(group: str) -> str:
    return (f"a port template: it reads the AST, which no SHIR pass has, and no pass reaches "
            f"it; group {group} ports it to SHIR inputs before a SHIR caller is connected")


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
    (_BORROW + "reads.py", "_field_type", _ast_probe("A5")),
    (_BORROW + "reads.py", "constant_type", _ast_probe("A8")),
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
    (_BORROW + "reads.py", "unit_variables", _ast_probe("D1")),
    (_BORROW + "state.py", "is_unit_var", _kept("D", "D1: var")),
)

# -- the emitter families whose AST callers P2.13 deleted ----------------------------
# The old body emitter called each entry below. The families stay (ir.md Q7), and the
# group of each one gives it operands from SLIR. Its AST argument half calls the deleted
# expression layer until then.
_BACKEND = "sushi_lang/backend/"


def _family(group: str, what: str) -> str:
    return (f"kept for {group}: {what}; the SLIR emitter calls the family with operands "
            "there, and its AST argument half goes then")


WHITELIST += (
    (_BACKEND + "generics/list/__init__.py", "emit_list_method",
     _family("B2", "the List method family")),
    (_BACKEND + "generics/maybe.py", "emit_*maybe*_method",
     _family("A4 and B10", "the Maybe method family")),
    (_BACKEND + "generics/results.py", "emit_*result*_method",
     _family("A4 and B10", "the Result method family")),
    (_BACKEND + "generics/own.py", "emit_builtin_own_method",
     _family("B5", "the Own method family")),
    (_BACKEND + "runtime/closures.py", "materialize_function_ref",
     _family("C2", "a named function as a function value")),
    (_BACKEND + "runtime/closures.py", "emit_lambda",
     _family("C2", "the closure value and its environment")),
    (_BACKEND + "runtime/errors.py", "emit_runtime_error_with_message",
     _family("A11", "the trap of a failed assert, from the SLIR Trap terminator")),
    (_BACKEND + "runtime/iterator_adapters.py", "emit_adapter_call",
     _family("C3", "the iterator adapters")),
    (_BACKEND + "runtime/iterator_adapters.py", "owned_parts",
     _family("C3", "the parts of an owned iterator that a loop destroys")),
    (_BACKEND + "runtime/iterators.py", "emit_range_iterator",
     _family("A7", "a range as an Iterator@(i32) value")),
    (_BACKEND + "runtime/iterators.py", "emit_new_iterator",
     _family("C3", "Iterator.new over a next() type")),
    (_BACKEND + "runtime/strings.py", "emit_to_cstr",
     _family("D4", "the i8* string argument")),
    (_BACKEND + "runtime/strings.py", "emit_cstr_to_owned_fat_pointer",
     _family("D5", "the string that an extern returns")),
    (_BACKEND + "types/arrays/runs.py", "single_runs",
     _family("D3", "the T[] of a variadic tail, built by the array builders")),
    (_BACKEND + "types/display.py", "emit_value_to_str",
     _family("A12", "the string form of a value through Display")),
)

# -- AST facts whose last reader P2.13 deleted ----------------------------------------
# The old body emitter read each one. A port template writes it, and the group moves the
# fact to the tables of SHIR (TypeckResults, OwnershipResults) and deletes the field.
_SEMANTICS = "sushi_lang/semantics/"


def _fact(group: str, what: str) -> str:
    return (f"kept for {group}: {what}; an AST port template writes it, and the group "
            "moves the fact to a SHIR table and deletes it")


WHITELIST += (
    (_SEMANTICS + "*", "walks_in_place",
     _fact("C3", "the walk in place of a loop; tests/unit/test_walk_fields_have_one_writer.py "
                 "reads its writer")),
    (_SEMANTICS + "*", "resolved_scrutinee_type", _fact("A3 and A4", "the type of a scrutinee")),
    (_SEMANTICS + "*", "integer_match_type", _fact("A3", "the type of an integer match")),
    (_SEMANTICS + "*", "inferred_element_type", _fact("A6", "the type of an index access")),
    (_SEMANTICS + "*", "variadic_arg_types", _fact("D5", "the promoted types of a C vararg call")),
    (_SEMANTICS + "*", "callee_method_type_args",
     _fact("A10", "the method type arguments of an extension call")),
    (_SEMANTICS + "*", "callee_extension_unit",
     _fact("A10", "the declaring unit of an extension callee")),
    (_SEMANTICS + "*", "callee_is_static", _fact("A10", "a call of a static method")),
    (_SEMANTICS + "*", "callee_static_target", _fact("A10", "the type of a static callee")),
    (_SEMANTICS + "*", "iterator_next", _fact("C3", "the next() call of a protocol loop")),
    (_SEMANTICS + "*", "inferred_conversion",
     _fact("D2", "the declared conversion of a cast between two error types")),
    (_SEMANTICS + "passes/borrow/flow.py", "back_edge",
     "kept for the iterators group C3: a field of LoopFlow, which C3 ports"),
    (_SEMANTICS + "ast_walk.py", "ends_unrolled_run",
     "kept for the generic instances group C1: SHIR lowering of an instance asks it "
     "(tests/unit/test_unrolled_run_end_is_one.py)"),
    (_SEMANTICS + "typesys.py", "get_field_index",
     "kept for the struct and tuple group A5: the typecheck pass on SHIR reads the index "
     "of a field"),
)

# The members of ConsumingUse that no code names since the backend `consume` went: the
# closed set of consuming positions that docs/design/ir.md 7.9 and
# ownership-conventions.md 3.1 define.
WHITELIST += tuple(
    (_SEMANTICS + "ownership.py", member,
     "kept for the ownership groups of Tier B: a consuming position of the closed set "
     "ConsumingUse, which the design names")
    for member in ("CALL_ARG", "LET", "REBIND", "FIELD_ASSIGN", "STRUCT_FIELD",
                   "ENUM_PAYLOAD", "ELEMENT_ASSIGN", "RETURN", "MATCH_SCRUTINEE"))

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
