"""nori keeps the sushic error contract, and extract finds the package directory (#987).

A user error names its input and exits 1; a fault in nori itself says it is a bug and
exits 2; `--traceback` appends the Python traceback. None of these tests compiles Sushi.
"""
from __future__ import annotations

import io
import json
import re
import tarfile
from pathlib import Path

import pytest

from sushi_lang.packager import cli
from sushi_lang.packager.archive import ArchiveError, PackageArchive

MANIFEST = '[package]\nname = "towel"\nversion = "1.0.0"\n'


def _nori(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["nori", *argv])
    return cli.cli_main()


def _force_a_fault(monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise AttributeError("'NoneType' object has no attribute 'name'")

    monkeypatch.setattr("sushi_lang.packager.commands.build.load_manifest", broken)


def test_a_bad_manifest_names_the_file_and_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "nori.toml").write_text("garbage = \nname = \"towel\"\n")

    assert _nori(monkeypatch, "build") == 1
    err = capsys.readouterr().err
    assert "nori.toml" in err and "line" in err
    assert "Traceback" not in err


def test_a_manifest_of_the_wrong_shape_is_a_user_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "nori.toml").write_text('package = "towel"\n')

    assert _nori(monkeypatch, "build") == 1
    assert "nori.toml" in capsys.readouterr().err


def test_a_fault_in_nori_is_a_bug_and_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _force_a_fault(monkeypatch)

    assert _nori(monkeypatch, "build") == 2
    err = capsys.readouterr().err
    assert "bug" in err and "AttributeError" in err and "--traceback" in err
    assert "Traceback (most recent call last)" not in err


def test_traceback_appends_the_python_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _force_a_fault(monkeypatch)

    assert _nori(monkeypatch, "--traceback", "build") == 2
    assert "Traceback (most recent call last)" in capsys.readouterr().err


def test_an_archive_that_is_not_gzip_is_named(tmp_path):
    bad = tmp_path / "bad.nori"
    bad.write_text("hello\n")

    with pytest.raises(ArchiveError, match="bad.nori"):
        PackageArchive.read_manifest(bad)
    with pytest.raises(ArchiveError, match="bad.nori"):
        PackageArchive.extract(bad, tmp_path / "ex")


def test_a_bad_manifest_in_an_archive_names_the_archive(tmp_path):
    archive = _archive(tmp_path, [("towel-1.0.0/nori.toml", b"garbage = [\n")])

    with pytest.raises(ArchiveError, match="towel.nori"):
        PackageArchive.read_manifest(archive)


def _archive(tmp_path: Path, members: list[tuple[str, bytes]]) -> Path:
    path = tmp_path / "towel.nori"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_extract_returns_the_package_directory_not_the_first_member(tmp_path):
    archive = _archive(tmp_path, [
        ("README", b"Mostly Harmless\n"),
        ("towel-1.0.0/nori.toml", MANIFEST.encode()),
    ])

    extracted = PackageArchive.extract(archive, tmp_path / "ex")
    assert extracted == tmp_path / "ex" / "towel-1.0.0"
    assert extracted.is_dir()


def test_extract_refuses_a_manifest_outside_the_package_directory(tmp_path):
    archive = _archive(tmp_path, [("other/nori.toml", MANIFEST.encode())])

    with pytest.raises(ArchiveError, match="towel-1.0.0"):
        PackageArchive.extract(archive, tmp_path / "ex")


def test_the_command_dispatch_is_a_table():
    """Every subcommand but `help` has one row, and `run` holds no `if` chain."""
    import inspect

    parser_commands = set(cli.build_parser()._subparsers._group_actions[0].choices)
    assert set(cli.COMMANDS) == parser_commands - {"help"}
    assert "args.command ==" not in inspect.getsource(cli.run)


# #1000: an error from the user's environment is a user error, and every nori failure is
# a coded diagnostic on stderr. HOME and every ~/.sushi path point to tmp_path.

MANIFEST_WITH_FILE = MANIFEST + '\n[files]\nlibraries = ["towel.slib"]\n'
CODED = re.compile(r"error \[NE\d{4}\]")


@pytest.fixture
def sushi_home(tmp_path, monkeypatch):
    from sushi_lang.packager import constants, credentials, installer, paths

    home = tmp_path / "home"
    root = home / ".sushi"
    monkeypatch.setenv("HOME", str(home))
    for module in (constants, paths, installer):
        for name, sub in (("SUSHI_HOME", ""), ("BIN_DIR", "bin"), ("CACHE_DIR", "cache"),
                          ("BENTO_DIR", "bento"), ("STORE_DIR", "store")):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, root / sub if sub else root)
    monkeypatch.setattr(credentials, "CREDENTIALS_FILE", root / "credentials.toml")
    root.mkdir(parents=True)
    return root


def _project(tmp_path: Path, monkeypatch) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "nori.toml").write_text(MANIFEST_WITH_FILE)
    (project / "towel.slib").write_text("x\n")
    monkeypatch.chdir(project)
    return project


def test_an_output_directory_that_cannot_be_made_is_a_user_error(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path, monkeypatch)
    (project / "dist").write_text("a file where the directory goes\n")

    assert _nori(monkeypatch, "build") == 1
    err = capsys.readouterr().err
    assert str(project / "dist") in err
    assert "File exists" in err
    assert "bug" not in err and "FileExistsError" not in err
    assert "error [NE4001]" in err


def test_an_archive_that_cannot_be_written_names_the_path(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path, monkeypatch)
    target = project / "dist" / "towel-1.0.0.nori"

    def refused(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(target))

    monkeypatch.setattr("sushi_lang.packager.archive.tarfile.open", refused)
    assert _nori(monkeypatch, "build") == 1
    err = capsys.readouterr().err
    assert str(target) in err and "Permission denied" in err
    assert "bug" not in err and "PermissionError" not in err


def test_a_manifest_that_is_a_directory_is_a_user_error(tmp_path, monkeypatch, capsys):
    (tmp_path / "nori.toml").mkdir()
    monkeypatch.chdir(tmp_path)

    assert _nori(monkeypatch, "build") == 1
    err = capsys.readouterr().err
    assert str(tmp_path / "nori.toml") in err
    assert "bug" not in err and "IsADirectoryError" not in err


def test_a_malformed_credentials_file_names_the_file(sushi_home, monkeypatch, capsys):
    (sushi_home / "credentials.toml").write_text("not = = toml\n")

    assert _nori(monkeypatch, "status", "--repository", "omakase.example.net") == 1
    err = capsys.readouterr().err
    assert str(sushi_home / "credentials.toml") in err and "line 1" in err
    assert "bug" not in err and "TOMLDecodeError" not in err
    assert "error [NE1002]" in err


def test_a_malformed_credentials_file_stops_login(sushi_home, monkeypatch, capsys):
    from sushi_lang.packager.commands import login

    (sushi_home / "credentials.toml").write_text("not = = toml\n")
    monkeypatch.setattr("sys.stdin", io.StringIO("nori_key\n"))
    monkeypatch.setattr(login, "api_request", lambda *a, **k: {"username": "arthur"})

    assert _nori(monkeypatch, "login", "--repository", "omakase.example.net") == 1
    err = capsys.readouterr().err
    assert str(sushi_home / "credentials.toml") in err and "bug" not in err


def _install(root: Path, name: str, text: str) -> None:
    (root / "bento" / name).mkdir(parents=True)
    (root / "bento" / name / "nori.toml").write_text(text)


def test_one_malformed_installed_manifest_is_skipped(sushi_home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _install(sushi_home, "broken", "[package\n")
    _install(sushi_home, "towel", MANIFEST)

    assert _nori(monkeypatch, "list", "--global") == 0
    captured = capsys.readouterr()
    assert "skipping" in captured.err and str(sushi_home / "bento" / "broken") in captured.err
    assert "towel" in captured.out


def test_one_malformed_project_manifest_is_skipped(sushi_home, tmp_path, monkeypatch, capsys):
    project = _project(tmp_path, monkeypatch)
    deps = project / ".sushi_bento"
    for name, text in (("broken", "[package\n"), ("towel", MANIFEST)):
        (deps / name).mkdir(parents=True)
        (deps / name / "nori.toml").write_text(text)

    assert _nori(monkeypatch, "list") == 0
    captured = capsys.readouterr()
    assert "skipping" in captured.err and "towel" in captured.out


def test_a_bad_manifest_is_a_coded_diagnostic(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "nori.toml").write_text("garbage = \n")

    assert _nori(monkeypatch, "build") == 1
    assert "error [NE1002]" in capsys.readouterr().err


def test_a_fault_in_nori_is_a_coded_diagnostic(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _force_a_fault(monkeypatch)

    assert _nori(monkeypatch, "build") == 2
    assert "error [NE0000]" in capsys.readouterr().err


def _http_error(code: int, detail: str = ""):
    import urllib.error

    body = io.BytesIO(json.dumps({"detail": detail}).encode())
    return urllib.error.HTTPError("https://omakase.example.net", code, "refused", {}, body)


def _raise(exc):
    def raiser(*args, **kwargs):
        raise exc
    return raiser


def _stub_publish(project: Path, monkeypatch, exc) -> None:
    from sushi_lang.packager.commands import publish

    (project / "dist").mkdir()
    (project / "dist" / "towel-1.0.0.nori").write_bytes(b"archive")
    monkeypatch.setattr(publish, "load_token", lambda repo: "nori_token")
    monkeypatch.setattr(publish, "api_upload_multipart", _raise(exc))


def _failures(sushi_home: Path, tmp_path: Path, monkeypatch) -> dict:
    """Every per-command failure: the argv, the code, and how to set it up."""
    import urllib.error

    from sushi_lang.packager import api_client
    from sushi_lang.packager.api_client import ApiError
    from sushi_lang.packager.commands import login, search, status

    def in_empty_dir():
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)

    def in_project():
        return _project(tmp_path, monkeypatch)

    def publish_refused(status_code):
        def setup():
            _stub_publish(in_project(), monkeypatch, ApiError(status_code, "no"))
        return setup

    def token(module, exc):
        def setup():
            in_empty_dir()
            monkeypatch.setattr(module, "load_token", lambda repo: "nori_token")
            monkeypatch.setattr(module, "api_request", _raise(exc))
        return setup

    def urlopen(exc):
        def setup():
            in_empty_dir()
            monkeypatch.setattr(search.urllib.request, "urlopen", _raise(exc))
        return setup

    def piped_key(key, exc=None):
        def setup():
            in_empty_dir()
            monkeypatch.setattr("sys.stdin", io.StringIO(key + "\n"))
            if isinstance(exc, urllib.error.URLError):
                monkeypatch.setattr(api_client.urllib.request, "urlopen", _raise(exc))
            elif exc is not None:
                monkeypatch.setattr(login, "api_request", _raise(exc))
        return setup

    def existing_manifest():
        in_project()

    def installed_but_broken():
        in_empty_dir()
        _install(sushi_home, "towel", "[package\n")

    def a_plain_file():
        in_empty_dir()
        Path("notes.txt").write_text("Mostly Harmless\n")

    def an_empty_source():
        in_empty_dir()
        Path("source").mkdir()

    def a_missing_dependency():
        project = in_project()
        (project / "nori.toml").write_text(MANIFEST + '\n[dependencies]\nbabel = "4.2.0"\n')

    unreachable = urllib.error.URLError("no route to host")
    return {
        "info-missing": (["info", "towel"], "NE3003", in_empty_dir),
        "info-corrupt": (["info", "towel"], "NE3010", installed_but_broken),
        "init-exists": (["init"], "NE1009", existing_manifest),
        "install-usage": (["install", "towel", "to", "./x"], "NE3011", in_empty_dir),
        "install-no-path": (["install", "./nowhere"], "NE3005", in_empty_dir),
        "install-no-source": (["install", "towel", "from", "./nowhere"], "NE3005", in_empty_dir),
        "install-no-project": (["install"], "NE3008", in_empty_dir),
        "install-plain-file": (["install", "./notes.txt"], "NE3006", a_plain_file),
        "install-empty-source": (["install", "towel", "from", "source"], "NE3007",
                                 an_empty_source),
        "install-missing-dep": (["install"], "NE3002", a_missing_dependency),
        "install-remote": (["install", "towel"], "NE3009", in_empty_dir),
        "login-format": (["login"], "NE5005", piped_key("bad_key")),
        "login-refused": (["login"], "NE5004", piped_key("nori_key", ApiError(401, "no"))),
        "login-server": (["login"], "NE5002", piped_key("nori_key", ApiError(500, "down"))),
        "login-unreachable": (["login"], "NE5001", piped_key("nori_key", unreachable)),
        "publish-no-manifest": (["publish"], "NE1001", in_empty_dir),
        "publish-no-archive": (["publish"], "NE2007", in_project),
        "publish-no-token": (["publish"], "NE5003", lambda: _stub_publish(
            in_project(), monkeypatch, AssertionError("not reached"))
            or monkeypatch.setattr("sushi_lang.packager.commands.publish.load_token",
                                   lambda repo: None)),
        "publish-401": (["publish"], "NE5004", publish_refused(401)),
        "publish-403": (["publish"], "NE5006", publish_refused(403)),
        "publish-409": (["publish"], "NE5007", publish_refused(409)),
        "publish-413": (["publish"], "NE5008", publish_refused(413)),
        "publish-422": (["publish"], "NE5009", publish_refused(422)),
        "publish-500": (["publish"], "NE5002", publish_refused(500)),
        "remove-project": (["remove", "towel"], "NE3004", in_project),
        "remove-global": (["remove", "--global", "towel"], "NE3003", in_empty_dir),
        "search-http": (["search", "towel"], "NE5002", urlopen(_http_error(503))),
        "search-unreachable": (["search", "towel"], "NE5001", urlopen(unreachable)),
        "status-401": (["status"], "NE5004", token(status, ApiError(401, "no"))),
        "status-500": (["status"], "NE5002", token(status, ApiError(500, "down"))),
    }


FAILURES = [
    "info-missing", "info-corrupt", "init-exists", "install-usage", "install-no-path",
    "install-no-source", "install-no-project", "install-plain-file", "install-empty-source",
    "install-missing-dep", "install-remote", "login-format",
    "login-refused", "login-server", "login-unreachable", "publish-no-manifest",
    "publish-no-archive", "publish-no-token", "publish-401", "publish-403", "publish-409",
    "publish-413", "publish-422", "publish-500", "remove-project", "remove-global",
    "search-http", "search-unreachable", "status-401", "status-500",
]


@pytest.mark.parametrize("case", FAILURES)
def test_a_command_failure_is_a_coded_diagnostic_on_stderr(case, sushi_home, tmp_path,
                                                           monkeypatch, capsys):
    argv, code, setup = _failures(sushi_home, tmp_path, monkeypatch)[case]
    monkeypatch.setenv("SUSHI_REPOSITORY", "omakase.example.net")
    setup()
    capsys.readouterr()

    assert _nori(monkeypatch, *argv) == 1
    captured = capsys.readouterr()
    assert f"error [{code}]" in captured.err, captured.err
    assert any(line.startswith(f"error [{code}]") for line in captured.err.splitlines()), captured.err
    assert len(CODED.findall(captured.err)) == 1, captured.err
    assert "bug" not in captured.err
    assert not CODED.search(captured.out)


def test_every_failure_case_is_listed(sushi_home, tmp_path, monkeypatch):
    assert sorted(_failures(sushi_home, tmp_path, monkeypatch)) == sorted(FAILURES)


COMMANDS_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang" / "packager" / "commands"


def test_no_command_answers_a_failure_itself():
    """A command raises a NoriError for a failure; `cli_main` alone renders it and exits 1."""
    import ast

    hits = []
    for path in sorted(COMMANDS_ROOT.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            value = node.value.value if (isinstance(node, ast.Return)
                                         and isinstance(node.value, ast.Constant)) else 0
            if type(value) is int and value != 0:
                hits.append(f"{path.name}:{node.lineno}")
    assert hits == []
