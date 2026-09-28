"""`TEST_TYPE` is not a directive (#1043).

The runner took the category of a fixture from its file name prefix and from
`should_run_runtime_test`, and nothing read the parsed `test_type`. A directive that
changes nothing does not exist, so a `TEST_TYPE` line is now an unknown directive and
fails its fixture, and no fixture carries one.
"""
from __future__ import annotations

import dataclasses

import test_metadata
from test_metadata import parse_test_metadata


def test_metadata_has_no_test_type_field():
    names = {field.name for field in dataclasses.fields(test_metadata.TestMetadata)}
    assert "test_type" not in names


def test_a_test_type_line_is_an_unknown_directive(tmp_path):
    path = tmp_path / "test_probe.sushi"
    path.write_text("# TEST_TYPE: runtime\n\nfn main() i32:\n    return Result.Ok(0)\n")

    errors = parse_test_metadata(path).directive_errors

    assert errors and "TEST_TYPE is not a directive the runner knows" in errors[0], errors
