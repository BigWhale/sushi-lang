"""The badge renderer must never report greener than the run it describes.

The job this replaces combined `|| true`, `continue-on-error` and a bare `json.load`, so
any crash left the previous green badge standing in the gist. Every case below is one way
that used to happen, asserted from the side that must now fail loudly instead.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / ".github" / "scripts" / "make_badges.py"


def _load_module():
    """`.github/scripts` is not a package, so import the file by path.

    Registered in sys.modules before exec: @dataclass resolves annotations through
    `sys.modules[cls.__module__]`, which is None for a module that never landed there.
    """
    spec = importlib.util.spec_from_file_location("make_badges", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mb = _load_module()


def _corpus(tmp_path, **overrides) -> Path:
    data = {"total_tests": 1699, "passed": 1699, "failed": 0, "leak_checks_skipped": 0}
    data.update(overrides)
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _junit(tmp_path, tests=1077, failures=0, errors=0, skipped=3) -> Path:
    path = tmp_path / "pytest.xml"
    path.write_text(
        f'<testsuites><testsuite name="pytest" tests="{tests}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped}" /></testsuites>',
        encoding="utf-8",
    )
    return path


# Parsing


def test_corpus_counts_are_read(tmp_path):
    suite = mb.load_corpus(_corpus(tmp_path))
    assert (suite.total, suite.passed, suite.failed) == (1699, 1699, 0)


def test_pytest_passed_is_derived_by_subtraction(tmp_path):
    """JUnit has no `passed` attribute; `tests` counts skips and errors too."""
    suite = mb.load_pytest(_junit(tmp_path, tests=1077, failures=2, errors=1, skipped=3))
    assert (suite.total, suite.failed, suite.skipped) == (1077, 3, 3)
    assert suite.passed == 1077 - 3 - 3


def test_a_bare_testsuite_root_is_accepted(tmp_path):
    """The legacy family emits <testsuite> as the root, not wrapped in <testsuites>."""
    path = tmp_path / "legacy.xml"
    path.write_text('<testsuite tests="10" failures="1" errors="0" skipped="0" />',
                    encoding="utf-8")
    assert mb.load_pytest(path).passed == 9


# The colours carry the meaning


def test_all_green_is_passing(tmp_path):
    suites = [mb.load_corpus(_corpus(tmp_path)), mb.load_pytest(_junit(tmp_path))]
    status = mb.status_badge(suites, {"test-linux": "success", "pytest": "success"})
    assert (status["message"], status["color"]) == ("passing", mb.GREEN)


def test_a_failing_suite_turns_the_status_red(tmp_path):
    suites = [mb.load_corpus(_corpus(tmp_path, passed=1697, failed=2)),
              mb.load_pytest(_junit(tmp_path))]
    status = mb.status_badge(suites, {"test-linux": "failure", "pytest": "success"})
    assert status["color"] == mb.RED
    assert mb.count_badge(suites[0])["color"] == mb.RED, "the failing suite's own badge too"
    assert mb.count_badge(suites[1])["color"] == mb.GREEN, "the passing suite stays green"


def test_skipped_leak_checks_are_not_a_clean_run(tmp_path):
    """#241: a skipped leak assertion is not a passing one, so green would be a lie."""
    suite = mb.load_corpus(_corpus(tmp_path, leak_checks_skipped=96))
    badge = mb.count_badge(suite)
    assert badge["color"] == mb.YELLOW
    assert "96" in badge["message"]
    status = mb.status_badge([suite], {"test-linux": "success"})
    assert status["color"] == mb.YELLOW


def test_a_job_that_died_without_reporting_is_not_green(tmp_path):
    """A green badge must be impossible when the job it describes did not succeed."""
    suites = [mb.load_corpus(_corpus(tmp_path)), mb.load_pytest(_junit(tmp_path))]
    status = mb.status_badge(suites, {"test-linux": "success", "pytest": "cancelled"})
    assert status["color"] == mb.RED


def test_a_macos_only_failure_still_turns_the_status_red(tmp_path):
    """test-macos uploads no artifact, so its job result is the ONLY signal it sends.

    Both suite artifacts come from Linux. Drop macOS from the roll-up and a regression that
    only reproduces there publishes a green badge -- the exact stale green being removed.
    """
    suites = [mb.load_corpus(_corpus(tmp_path)), mb.load_pytest(_junit(tmp_path))]
    status = mb.status_badge(
        suites, {"test-linux": "success", "pytest": "success", "test-macos": "failure"}
    )
    assert status["color"] == mb.RED


# The stale-green failure modes, each asserted from the failing side


def test_a_missing_artifact_from_a_failed_job_renders_red(tmp_path):
    """The producing job died. Report red -- crashing here would keep the old green."""
    suite = mb.load_suite(tmp_path / "absent.json", "sushi tests", mb.load_corpus, "failure")
    assert suite.unavailable
    assert mb.count_badge(suite)["color"] == mb.RED


def test_a_missing_artifact_from_a_successful_job_is_fatal(tmp_path):
    """That combination is a workflow bug, and publishing anything would paper over it."""
    with pytest.raises(SystemExit):
        mb.load_suite(tmp_path / "absent.json", "sushi tests", mb.load_corpus, "success")


def test_non_json_input_is_fatal_and_quotes_the_offending_bytes(tmp_path, capsys):
    """The runner prints build failures as prose and exits before emitting JSON."""
    path = tmp_path / "corpus.json"
    path.write_text("error: could not build the stdlib\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        mb.load_corpus(path)
    assert "could not build the stdlib" in capsys.readouterr().err


def test_a_truncated_junit_report_is_fatal(tmp_path):
    path = tmp_path / "pytest.xml"
    path.write_text("<testsuites>", encoding="utf-8")
    with pytest.raises(SystemExit):
        mb.load_pytest(path)


def test_a_junit_report_with_no_testsuite_is_fatal(tmp_path):
    """pytest died before writing counts; an empty report must not read as zero failures."""
    path = tmp_path / "pytest.xml"
    path.write_text("<testsuites></testsuites>", encoding="utf-8")
    with pytest.raises(SystemExit):
        mb.load_pytest(path)


# The gist payload


def test_the_payload_carries_every_badge(tmp_path):
    suites = [mb.load_corpus(_corpus(tmp_path)), mb.load_pytest(_junit(tmp_path))]
    badges = mb.build_badges(suites, {"test-linux": "success", "pytest": "success"})
    payload = mb.build_payload(badges)
    assert set(payload["files"]) == {"badge_status.json", "badge_sushi.json",
                                     "badge_python.json"}
    for entry in payload["files"].values():
        # shields.io requires schemaVersion 1; a malformed endpoint renders as an error.
        assert json.loads(entry["content"])["schemaVersion"] == 1
