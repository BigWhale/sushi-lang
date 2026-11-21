"""`LIB_MANIFEST_SET` edits one string field of a built library's manifest, through the real runner.

A fixture must be able to state a binary library that another host built (a foreign
`platform`). The runner builds the library on this host, then sets the field in the file
with the splice writer of the library-reader gate. The test reads the manifest of the
file that the consumer then reads, so a directive that the runner parses and ignores
cannot pass.
"""
from __future__ import annotations

from pathlib import Path

from _harness import run_single, detail
import enhanced_test_runner
from run_tests import _slib_manifest
from test_metadata import parse_test_metadata

USES_LIB = 'use <lib/dep>\n\nfn main() i32:\n    println("{N}")\n    return 0\n'
DEP = "public const i32 N = 1\n"
OUT_1 = '# EXPECT_STDOUT_EXACT: "1\\n"\n'


def _fixture(tmp_path: Path, header: str) -> Path:
    home = tmp_path / "fixture"
    home.mkdir()
    (home / "dep.sushi").write_text(DEP, encoding="utf-8")
    path = home / "test_manifest_set.sushi"
    path.write_text(header + "\n" + USES_LIB, encoding="utf-8")
    return path


def _record_manifests(monkeypatch) -> list[dict]:
    """The manifest of `dep.slib` after each manifest-edit step of the runner."""
    seen: list[dict] = []
    real = enhanced_test_runner.TestRunner._edit_manifests

    def _spy(metadata, workspace):
        failure = real(metadata, workspace)
        target = workspace.libs / "dep.slib"
        if failure is None and target.is_file():
            seen.append(_slib_manifest(target.read_bytes())[0])
        return failure

    monkeypatch.setattr(enhanced_test_runner.TestRunner, "_edit_manifests",
                        staticmethod(_spy))
    return seen


def test_the_directive_parses(tmp_path):
    meta = parse_test_metadata(_fixture(
        tmp_path, "# BUILD_LIB: dep.sushi\n# LIB_MANIFEST_SET: dep platform=linux_riscv64\n"))
    assert meta.lib_manifest_sets == [("dep", "platform", "linux_riscv64")]
    assert meta.libs_on_the_path


def test_the_field_is_set_in_the_file_the_consumer_reads(tmp_path, monkeypatch):
    seen = _record_manifests(monkeypatch)
    result = run_single(_fixture(
        tmp_path, "# BUILD_LIB: dep.sushi\n# LIB_MANIFEST_SET: dep platform=linux_riscv64\n"
        + OUT_1))
    assert result.total_success, detail(result)
    assert [m["platform"] for m in seen] == ["linux_riscv64"], seen
    assert seen[0]["library_name"] == "dep"


def test_control_without_the_directive_the_field_is_the_host_value(tmp_path, monkeypatch):
    seen = _record_manifests(monkeypatch)
    result = run_single(_fixture(tmp_path, "# BUILD_LIB: dep.sushi\n" + OUT_1))
    assert result.total_success, detail(result)
    assert [m["platform"] for m in seen] != ["linux_riscv64"], seen


def test_a_library_that_no_build_wrote_fails_the_fixture(tmp_path):
    result = run_single(_fixture(
        tmp_path, "# BUILD_LIB: dep.sushi\n# LIB_MANIFEST_SET: other platform=x\n" + OUT_1))
    assert not result.total_success
    assert "LIB_MANIFEST_SET names other" in result.compilation_message, detail(result)


def test_an_edit_with_no_library_build_fails_the_fixture(tmp_path):
    result = run_single(_fixture(tmp_path, "# LIB_MANIFEST_SET: dep platform=x\n" + OUT_1))
    assert not result.total_success
    assert "LIB_MANIFEST_SET names dep" in result.compilation_message, detail(result)


def test_a_field_that_is_not_a_string_fails_the_fixture(tmp_path):
    result = run_single(_fixture(
        tmp_path, "# BUILD_LIB: dep.sushi\n# LIB_MANIFEST_SET: dep templates=x\n" + OUT_1))
    assert not result.total_success
    assert "no string field 'templates'" in result.compilation_message, detail(result)


def test_a_value_without_an_assignment_fails_the_fixture(tmp_path):
    result = run_single(_fixture(
        tmp_path, "# BUILD_LIB: dep.sushi\n# LIB_MANIFEST_SET: dep platform\n" + OUT_1))
    assert not result.total_success
    assert "LIB_MANIFEST_SET takes" in detail(result), detail(result)
