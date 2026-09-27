"""nori keeps the sushic error contract, and extract finds the package directory (#987).

A user error names its input and exits 1; a fault in nori itself says it is a bug and
exits 2; `--traceback` appends the Python traceback. None of these tests compiles Sushi.
"""
from __future__ import annotations

import io
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
