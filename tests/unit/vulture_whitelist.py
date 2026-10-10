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

WHITELIST: tuple[tuple[str, str, str], ...] = (
    # -- dispatch by a built name ----------------------------------------------------
    ("sushi_lang/semantics/*", "visit_*",
     "RecursiveVisitor dispatches on f'visit_{kind}' (semantics/visitors.py)"),
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
    ("sushi_lang/semantics/places.py", "NOT_A_STEP", "tests/unit/test_place_walk_is_total.py"),
    ("sushi_lang/semantics/stdlib_registry.py", "get_function", "tests/unit/test_stdlib_rows_are_one.py"),
    ("sushi_lang/semantics/type_walk.py", "TERMINAL_KINDS", "tests/unit/test_type_walk_is_total.py"),
    ("sushi_lang/semantics/visibility.py", "FOLLOWS_DECLARATION", "tests/unit/test_visibility_seam_is_total.py"),
    ("sushi_lang/semantics/visibility.py", "FOLLOWS_TARGET_TYPE", "tests/unit/test_visibility_seam_is_total.py"),
    ("sushi_lang/semantics/visibility.py", "NO_VISIBILITY", "tests/unit/test_visibility_seam_is_total.py"),
    ("sushi_lang/semantics/visitors.py", "WALKED_IN_PARENT", "tests/unit/test_visitor_dispatch_is_total.py"),
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
    # -- read by name ----------------------------------------------------------------
    ("tests/unit/test_bare_name_ladder_is_one.py", "is_*",
     "the test reads each Rungs method by name with getattr"),
)
