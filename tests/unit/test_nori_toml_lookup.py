"""The nori.toml is read from the working directory alone, and what it names stays below it (#1066).

None of these tests compiles Sushi: they call the packager and the version lookup of a
--lib build directly.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from sushi_lang.backend.library_errors import LibraryError
from sushi_lang.backend.library_manifest import resolve_library_version
from sushi_lang.packager.archive import ArchiveError, PackageArchive
from sushi_lang.packager.manifest import load_manifest_from_string
from sushi_lang.packager.paths import find_project_root

MANIFEST = '[package]\nname = "towel"\nversion = "1.0.0"\n'


def test_a_parent_manifest_is_not_the_project_root(tmp_path, monkeypatch):
    (tmp_path / "nori.toml").write_text(MANIFEST)
    sub = tmp_path / "app"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert find_project_root() is None


def test_the_working_directory_manifest_is_the_project_root(tmp_path, monkeypatch):
    (tmp_path / "nori.toml").write_text(MANIFEST)
    monkeypatch.chdir(tmp_path)
    assert find_project_root() == Path.cwd()


def test_a_parent_manifest_does_not_decide_the_version(tmp_path, monkeypatch):
    (tmp_path / "nori.toml").write_text('[package\n')
    sub = tmp_path / "deep"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert resolve_library_version("1.0.0", "towel") == "1.0.0"


def test_a_broken_link_is_an_unreadable_manifest(tmp_path, monkeypatch):
    os.symlink(tmp_path / "gone.toml", tmp_path / "nori.toml")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LibraryError) as caught:
        resolve_library_version("1.0.0", "towel")
    assert caught.value.code == "CE3518"
    assert "No such file or directory" in str(caught.value)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads a file with no read permission")
def test_a_manifest_with_no_read_permission_is_unreadable(tmp_path, monkeypatch):
    manifest = tmp_path / "nori.toml"
    manifest.write_text(MANIFEST)
    manifest.chmod(0)
    monkeypatch.chdir(tmp_path)
    try:
        with pytest.raises(LibraryError) as caught:
            resolve_library_version("1.0.0", "towel")
    finally:
        manifest.chmod(0o644)
    assert caught.value.code == "CE3518"
    assert "Permission denied" in str(caught.value)


def _package(tmp_path: Path, files: str) -> Path:
    (tmp_path / "nori.toml").write_text(MANIFEST + "\n[files]\n" + files)
    return tmp_path


@pytest.mark.parametrize("field", ["libraries", "executables", "data"])
@pytest.mark.parametrize("entry", ["../x.slib", "/etc/x.slib", "lib/../../x.slib"])
def test_a_files_entry_that_leaves_the_package_is_refused(tmp_path, field, entry):
    outside = tmp_path / "x.slib"
    outside.write_text("outside")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _package(pkg, f'{field} = ["{entry}"]\n')
    manifest = load_manifest_from_string((pkg / "nori.toml").read_bytes())
    with pytest.raises(ArchiveError) as caught:
        PackageArchive.create(manifest, pkg, tmp_path / "out")
    assert caught.value.code == "NE2008"
    assert entry in str(caught.value)


def test_a_files_entry_below_the_package_is_packed(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "lib").mkdir(parents=True)
    (pkg / "lib" / "towel.slib").write_text("towel")
    _package(pkg, 'libraries = ["lib/towel.slib"]\n')
    manifest = load_manifest_from_string((pkg / "nori.toml").read_bytes())
    assert PackageArchive.create(manifest, pkg, tmp_path / "out").is_file()
