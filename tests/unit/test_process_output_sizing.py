"""Guard: the stdlib's ProcessOutput Result layout must match the compiler's sizing.

`run()` returns Result<ProcessOutput, ProcessError>. The stdlib IR-gen and the backend
call-site declaration derive the {i32, [N x i8]} data size from
type_definitions._process_output_size_bytes(), while the compiler sizes the same enum via
backend.types.core.sizing.TypeSizing. If these two ever disagree, the returned struct type
mismatches the caller's variable type (CE0017). This test pins them together so a future
change to ProcessOutput's fields (or the ABI sizing rules) can't silently drift.
"""
from sushi_lang.sushi_stdlib.src.type_definitions import (
    _process_output_size_bytes,
    get_process_output_result_type,
)
from sushi_lang.backend.types.core.sizing import TypeSizing
from sushi_lang.semantics.passes.collect.structs import StructTable
from sushi_lang.semantics.passes.collect.enums import EnumTable
from sushi_lang.semantics.typesys import StructType, EnumType, BuiltinType, EnumVariantInfo


def _process_output_struct() -> StructType:
    return StructType(
        name="ProcessOutput",
        fields=(
            ("exit_code", BuiltinType.I32),
            ("stdout_text", BuiltinType.STRING),
            ("stderr_text", BuiltinType.STRING),
        ),
    )


def _process_error_enum() -> EnumType:
    return EnumType(
        name="ProcessError",
        variants=(
            EnumVariantInfo(name="SpawnFailed", associated_types=()),
            EnumVariantInfo(name="ExitFailure", associated_types=()),
            EnumVariantInfo(name="SignalReceived", associated_types=()),
        ),
    )


def test_stdlib_process_output_size_matches_backend_sizing():
    struct_table = StructTable()
    struct_table.by_name["ProcessOutput"] = _process_output_struct()
    enum_table = EnumTable()
    enum_table.by_name["ProcessError"] = _process_error_enum()

    sizer = TypeSizing(struct_table, enum_table)
    compiler_size = sizer.get_type_size_bytes(struct_table.by_name["ProcessOutput"])

    assert _process_output_size_bytes() == compiler_size, (
        f"stdlib aligned size {_process_output_size_bytes()} != "
        f"compiler struct size {compiler_size}"
    )


def test_result_data_array_holds_process_output():
    # Result<ProcessOutput, ProcessError> = {i32 tag, [N x i8] data}; N must fit ProcessOutput.
    result_ty = get_process_output_result_type()
    data_array = result_ty.elements[1]
    assert data_array.count == _process_output_size_bytes()
    assert data_array.count >= 5  # at least large enough for the ProcessError variant too
