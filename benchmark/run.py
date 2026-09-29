#!/usr/bin/env python3
"""Build and run the Sushi, Go and Python benchmarks, then print the results.

Each benchmark is one file in each of sushi/, go/ and python/, with the same stem.
Sushi is built with the development compiler (../sushic), Go and Python are the ones
on PATH. The executables go in a temporary directory that is deleted at the end.

    python3 benchmark/run.py                   # ASCII table
    python3 benchmark/run.py --json out.json   # JSON file ('-' for stdout)
    python3 benchmark/run.py --filter hashmap --runs 3
"""

import argparse
import json
import math
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUSHIC = HERE.parent / "sushic"
LANGS = ("sushi", "go", "python")


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=5, help="timed runs per program (default 5)")
    ap.add_argument("--warmup", type=int, default=3, help="untimed runs before timing (default 3)")
    ap.add_argument("--opt", default="O2", help="sushic --opt level (default O2)")
    ap.add_argument("--filter", default="", help="run only the benchmarks whose name contains this")
    ap.add_argument("--json", metavar="PATH", help="write JSON to PATH ('-' for stdout) instead of the table")
    ap.add_argument("--python", default="python3", help="Python interpreter (default python3 on PATH)")
    return ap.parse_args()


def benchmark_names(pattern):
    names = sorted(p.stem for p in (HERE / "sushi").glob("*.sushi") if pattern in p.stem)
    for name in names:
        for lang, ext in (("go", "go"), ("python", "py")):
            if not (HERE / lang / f"{name}.{ext}").exists():
                sys.exit(f"missing {lang}/{name}.{ext}")
    return names


def check(cmd, what):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        sys.exit(f"{what} failed:\n{r.stdout}{r.stderr}")
    return r


def build(names, tmp, opt, python):
    (tmp / "sushi").mkdir()
    (tmp / "go").mkdir()
    commands = {}
    for name in names:
        exe = tmp / "sushi" / name
        check([str(SUSHIC), "--opt", opt, "--cache-dir", str(tmp / "cache"),
               str(HERE / "sushi" / f"{name}.sushi"), "-o", str(exe)], f"sushic {name}")
        if not exe.exists():
            sys.exit(f"sushic {name} wrote no executable")
        gexe = tmp / "go" / name
        check(["go", "build", "-o", str(gexe), str(HERE / "go" / f"{name}.go")], f"go build {name}")
        commands[name] = {"sushi": [str(exe)], "go": [str(gexe)],
                          "python": [python, str(HERE / "python" / f"{name}.py")]}
    return commands


def run_once(cmd):
    start = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    if r.returncode != 0:
        sys.exit(f"{' '.join(cmd)} exited {r.returncode}:\n{r.stderr}")
    return elapsed, r.stdout


def measure(commands, runs, warmup):
    results = {}
    for name, per_lang in commands.items():
        row = {}
        for lang in LANGS:
            cmd = per_lang[lang]
            _, output = run_once(cmd)
            for _ in range(warmup - 1):
                run_once(cmd)
            times = [run_once(cmd)[0] for _ in range(runs)]
            row[lang] = {"median": statistics.median(times), "min": min(times),
                         "stdev": statistics.stdev(times) if runs > 1 else 0.0,
                         "times": times, "output": output.strip()}
        row["outputs_match"] = len({row[lang]["output"] for lang in LANGS}) == 1
        results[name] = row
        print(f"  {name:22s} " + "  ".join(f"{lang} {row[lang]['median'] * 1000:8.1f} ms" for lang in LANGS)
              + ("" if row["outputs_match"] else "  OUTPUT MISMATCH"), file=sys.stderr, flush=True)
    return results


def version(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout or r.stderr).strip().splitlines()[0]


def geomean(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def table(results):
    head = ("benchmark", "sushi ms", "go ms", "python ms", "go/sushi", "py/sushi", "output")
    rows = [head]
    for name, r in results.items():
        s, g, p = (r[lang]["median"] for lang in LANGS)
        rows.append((name, f"{s * 1000:.1f}", f"{g * 1000:.1f}", f"{p * 1000:.1f}",
                     f"{g / s:.2f}x", f"{p / s:.2f}x", "same" if r["outputs_match"] else "DIFFERS"))
    rows.append(("geometric mean", "", "", "",
                 f"{geomean([r['go']['median'] / r['sushi']['median'] for r in results.values()]):.2f}x",
                 f"{geomean([r['python']['median'] / r['sushi']['median'] for r in results.values()]):.2f}x", ""))
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def fmt(row):
        return "| " + " | ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths, strict=True))) + " |"

    body = [line, fmt(rows[0]), line, *map(fmt, rows[1:-1]), line, fmt(rows[-1]), line]
    return "\n".join(body)


def main():
    args = parse_args()
    for tool in ("go", args.python):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on PATH")
    names = benchmark_names(args.filter)
    if not names:
        sys.exit(f"no benchmark matches '{args.filter}'")
    with tempfile.TemporaryDirectory(prefix="sushi-bench-") as tmp:
        print(f"building {len(names)} benchmarks ...", file=sys.stderr, flush=True)
        commands = build(names, Path(tmp), args.opt, args.python)
        print(f"running ({args.warmup} warm-up + {args.runs} timed runs each) ...", file=sys.stderr, flush=True)
        results = measure(commands, args.runs, args.warmup)
    meta = {"sushi": "sushic " + version([str(SUSHIC), "--version"]).split()[-1], "go": version(["go", "version"]),
            "python": version([args.python, "--version"]), "opt": args.opt,
            "runs": args.runs, "warmup": args.warmup,
            "machine": f"{platform.system()} {platform.release()} {platform.machine()}"}
    if args.json:
        text = json.dumps({"meta": meta, "results": results}, indent=1)
        if args.json == "-":
            print(text)
        else:
            Path(args.json).write_text(text + "\n")
    else:
        print(f"sushi: {meta['sushi']} (--opt {args.opt}) | go: {meta['go']} | python: {meta['python']}")
        print(table(results))
    if not all(r["outputs_match"] for r in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
