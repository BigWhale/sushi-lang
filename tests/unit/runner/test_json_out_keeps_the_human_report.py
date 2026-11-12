"""`--json-out PATH` writes the report to a file and keeps the human output.

`--json` puts the report on stdout and turns the human output off: no progress, no
failure blocks, no summary. The CI job that feeds the badges used `--json`, so its log
showed no test. `--json-out` gives the badge job its file and the log its report.
"""
from __future__ import annotations

import json

from _harness import ONE_FIXTURE, run_tests


def test_json_out_writes_the_report_to_the_file(tmp_path):
    out = tmp_path / "report.json"
    done = run_tests("--skip-build", "--filter", ONE_FIXTURE, "--json-out", str(out))

    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["total_tests"] == 1 and report["passed"] == 1, report


def test_json_out_keeps_the_human_report_on_stdout(tmp_path):
    out = tmp_path / "report.json"
    done = run_tests("--skip-build", "--filter", ONE_FIXTURE, "--json-out", str(out))

    assert "Test Results" in done.stdout, done.stdout[-2000:]
    assert "Running 1 tests" in done.stdout, done.stdout[-2000:]


def test_json_out_writes_the_report_of_an_empty_selection(tmp_path):
    """A consumer reads a report, so the empty selection writes one too."""
    out = tmp_path / "report.json"
    done = run_tests("--skip-build", "--filter", "zzz_no_such_area",
                     "--json-out", str(out))

    assert done.returncode != 0, done.stdout[-2000:]
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["selected_nothing"] is True, report
