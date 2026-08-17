"""python -m sushi_lang.packager -- same entry as the nori script."""
import os

from sushi_lang.packager import main

nori_cwd = os.environ.get("NORI_CWD")
if nori_cwd:
    os.chdir(nori_cwd)

raise SystemExit(main())
