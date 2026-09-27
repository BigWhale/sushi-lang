"""Every nori.toml field has one TOML type, and a field of another type is NE1011 (#1025).

None of these tests compiles Sushi or touches the network.
"""
from __future__ import annotations

import re

import pytest

from sushi_lang.packager import cli
from sushi_lang.packager.manifest import ManifestError, load_manifest_from_string

PACKAGE = '[package]\nname = "towel"\nversion = "1.0.0"\n'


def _nori(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["nori", *argv])
    return cli.cli_main()


def _type_error(text: str) -> ManifestError:
    with pytest.raises(ManifestError) as info:
        load_manifest_from_string(text, "nori.toml")
    assert info.value.code == "NE1011"
    return info.value


BAD = {
    "integer name": ('[package]\nname = 5\nversion = "1.0.0"\n',
                     "[package].name", "string", "integer"),
    "integer version": ('[package]\nname = "towel"\nversion = 1\n',
                        "[package].version", "string", "integer"),
    "list description": (PACKAGE + 'description = ["a"]\n',
                         "[package].description", "string", "list"),
    "string libraries": (PACKAGE + '[files]\nlibraries = "towel.slib"\n',
                         "[files].libraries", "list of strings", "string"),
    "integer element": (PACKAGE + '[files]\nlibraries = ["a.slib", 3]\n',
                        "[files].libraries", "list of strings", "integer"),
    "table executables": (PACKAGE + '[files]\nexecutables = { a = "b" }\n',
                          "[files].executables", "list of strings", "table"),
    "boolean data": (PACKAGE + '[files]\ndata = true\n',
                     "[files].data", "list of strings", "boolean"),
    "integer source": (PACKAGE + '[install]\nsource = 7\n',
                       "[install].source", "string", "integer"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_a_field_of_the_wrong_type_is_ne1011(case):
    text, field, expected, found = BAD[case]
    message = str(_type_error(text))
    assert "nori.toml" in message
    assert field in message
    assert re.search(rf"must be an? {expected}, not (a list that holds )?an? {found}\b", message), message


def test_a_string_list_is_never_read_per_character():
    message = str(_type_error(PACKAGE + '[files]\nlibraries = "towel.slib"\n'))
    assert "'t'" not in message


def test_a_correct_manifest_reads():
    text = (PACKAGE + 'description = "d"\nauthor = "a"\nlicense = "MIT"\n'
            '[files]\nlibraries = ["towel.slib"]\nexecutables = []\ndata = ["share"]\n'
            '[install]\nsource = "here"\ndate = "2026-09-28"\n'
            '[dependencies]\nfish = "1.2.3"\n')
    manifest = load_manifest_from_string(text, "nori.toml")
    assert manifest.libraries == ["towel.slib"]
    assert manifest.data == ["share"]
    assert manifest.source == "here"


@pytest.mark.parametrize("case", ["integer name", "string libraries", "integer element"])
def test_nori_build_on_a_bad_manifest_exits_1_with_the_code(case, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "nori.toml").write_text(BAD[case][0])
    (tmp_path / "towel.slib").write_text("x\n")
    (tmp_path / "a.slib").write_text("x\n")

    assert _nori(monkeypatch, "build") == 1
    err = capsys.readouterr().err
    assert "error [NE1011]" in err and BAD[case][1] in err
    assert "NE0000" not in err and "Traceback" not in err


def test_a_listing_skips_a_wrongly_typed_manifest_with_the_code(tmp_path, monkeypatch, capsys):
    from sushi_lang.packager import installer

    bento = tmp_path / "bento"
    for name, text in (("broken", BAD["integer name"][0]), ("towel", PACKAGE)):
        (bento / name).mkdir(parents=True)
        (bento / name / "nori.toml").write_text(text)
    monkeypatch.setattr(installer, "BENTO_DIR", bento)

    packages = installer.PackageInstaller().get_installed_packages()
    assert [p.name for p in packages] == ["towel"]
    err = capsys.readouterr().err
    assert "skipping" in err and "NE1011" in err and "[package].name" in err


def test_a_listing_does_not_hide_a_fault_in_nori(tmp_path, monkeypatch):
    from sushi_lang.packager import installer

    bento = tmp_path / "bento"
    (bento / "towel").mkdir(parents=True)
    (bento / "towel" / "nori.toml").write_text(PACKAGE)
    monkeypatch.setattr(installer, "BENTO_DIR", bento)

    def broken(*args, **kwargs):
        raise AttributeError("a fault in nori")

    monkeypatch.setattr(installer, "load_manifest_from_string", broken)
    with pytest.raises(AttributeError):
        installer.PackageInstaller().get_installed_packages()
