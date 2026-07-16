"""python -m sushi_lang.packager -- same entry as the nori script.

The nori wrapper script cds into the project root so this module is importable,
exporting the caller's directory as NORI_CWD (mirroring sushic's SUSHI_CWD);
restore it here so relative paths on the command line resolve where the user ran
nori from.
"""
import os

from sushi_lang.packager import main

nori_cwd = os.environ.get("NORI_CWD")
if nori_cwd:
    os.chdir(nori_cwd)

raise SystemExit(main())
