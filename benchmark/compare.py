#!/usr/bin/env python3
"""Run the benchmarks with two or more Sushi compilers, at O2 and at the default, beside Go and Python.

Each compiler is a `sushic` of its own, for example from a release wheel in its own venv:

    uv venv -p 3.13 /tmp/sushi-0.16.0
    VIRTUAL_ENV=/tmp/sushi-0.16.0 uv pip install sushi_lang-0.16.0-py3-none-any.whl

    python3 benchmark/compare.py --sushic 0.16.0=/tmp/sushi-0.16.0/bin/sushic \\
                                 --sushic 0.17.0=/tmp/sushi-0.17.0/bin/sushic --json results.json

Method: 3 untimed runs of each binary (1 for Python), then 7 timed runs, wall clock around the
whole process, median and standard deviation. The Sushi builds run in turn inside each round.
Peak RSS comes from one `/usr/bin/time -l` run (macOS). The JSON also records the compile time
of each build and whether two versions give the same machine code (`otool -tX`).
"""
import argparse
import hashlib
import json
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPTS = {"O2": ["--opt", "O2"], "default": []}


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sushic", action="append", required=True, metavar="VERSION=PATH",
                    help="a Sushi compiler to compare; give it two or more times")
    ap.add_argument("--python", default="python3", help="Python interpreter (default python3 on PATH)")
    ap.add_argument("--runs", type=int, default=7, help="timed runs per program (default 7)")
    ap.add_argument("--warmup", type=int, default=3, help="untimed runs before timing (default 3)")
    ap.add_argument("--filter", default="", help="run only the benchmarks whose name contains this")
    ap.add_argument("--json", metavar="PATH", default="-", help="write the results to PATH ('-' for stdout)")
    return ap.parse_args()


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def timed(cmd):
    start = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    if r.returncode != 0:
        sys.exit(f"{' '.join(cmd)} exited {r.returncode}:\n{r.stderr}")
    return elapsed, r.stdout.strip()


def peak_rss_mb(cmd):
    m = re.search(r"(\d+)\s+maximum resident set size", sh(["/usr/bin/time", "-l", *cmd]).stderr)
    return int(m.group(1)) / (1024 * 1024) if m else None


def text_hash(exe):
    return hashlib.sha256(sh(["otool", "-tX", str(exe)]).stdout.encode()).hexdigest()


def stats(times):
    return {"median": statistics.median(times),
            "stdev": statistics.stdev(times) if len(times) > 1 else 0.0, "times": times}


def build(names, compilers, out, python):
    exe, compile_s = {}, {}
    for name in names:
        for v, sushic in compilers.items():
            for o, flags in OPTS.items():
                path = out / f"{name}.{v}.{o}"
                cmd = [sushic, *flags, "--no-incremental", "--cache-dir", str(out / f"cache-{v}-{o}"),
                       str(HERE / "sushi" / f"{name}.sushi"), "-o", str(path)]
                start = time.perf_counter()
                r = sh(cmd)
                compile_s[f"{v}-{o}"] = compile_s.get(f"{v}-{o}", {})
                compile_s[f"{v}-{o}"][name] = time.perf_counter() - start
                if not path.exists():
                    sys.exit(f"sushic {v} {o} {name}:\n{r.stdout}{r.stderr}")
                exe[(name, v, o)] = [str(path)]
        gexe = out / f"{name}.go"
        r = sh(["go", "build", "-o", str(gexe), str(HERE / "go" / f"{name}.go")])
        if r.returncode:
            sys.exit(f"go build {name}:\n{r.stderr}")
        exe[(name, "go")] = [str(gexe)]
        exe[(name, "python")] = [python, str(HERE / "python" / f"{name}.py")]
        print(f"built {name}", file=sys.stderr, flush=True)
    return exe, compile_s


def measure(name, exe, compilers, out, runs, warmup):
    sushi = [(name, v, o) for v in compilers for o in OPTS]
    for key in [*sushi, (name, "go")]:
        for _ in range(warmup):
            timed(exe[key])
    timed(exe[(name, "python")])
    outputs, times = {}, {k: [] for k in sushi}
    for _ in range(runs):
        for k in sushi:
            elapsed, outputs[f"sushi-{k[1]}-{k[2]}"] = timed(exe[k])
            times[k].append(elapsed)
    row = {f"sushi-{k[1]}-{k[2]}": stats(times[k]) for k in sushi}
    for lang in ("go", "python"):
        ts = []
        for _ in range(runs):
            elapsed, outputs[lang] = timed(exe[(name, lang)])
            ts.append(elapsed)
        row[lang] = stats(ts)
    row["outputs_match"] = len(set(outputs.values())) == 1
    row["rss_mb"] = {**{f"sushi-{v}": peak_rss_mb(exe[(name, v, "O2")]) for v in compilers},
                     "go": peak_rss_mb(exe[(name, "go")]), "python": peak_rss_mb(exe[(name, "python")])}
    for o in OPTS:
        row[f"same_code_{o}"] = len({text_hash(out / f"{name}.{v}.{o}") for v in compilers}) == 1
    return row


def main():
    args = parse_args()
    compilers = dict(s.split("=", 1) for s in args.sushic)
    if len(compilers) < 2:
        sys.exit("give --sushic two or more times")
    names = sorted(p.stem for p in (HERE / "sushi").glob("*.sushi") if args.filter in p.stem)
    if not names:
        sys.exit(f"no benchmark matches '{args.filter}'")
    with tempfile.TemporaryDirectory(prefix="sushi-compare-") as tmp:
        out = Path(tmp)
        exe, compile_s = build(names, compilers, out, args.python)
        results = {}
        for name in names:
            row = measure(name, exe, compilers, out, args.runs, args.warmup)
            row["compile_s"] = {k: v[name] for k, v in compile_s.items()}
            results[name] = row
            print(f"{name:22s} " + "  ".join(f"{v} {row[f'sushi-{v}-O2']['median'] * 1000:7.1f}" for v in compilers)
                  + f"  go {row['go']['median'] * 1000:7.1f}  py {row['python']['median'] * 1000:8.1f}"
                  + ("" if row["outputs_match"] else "  OUTPUT MISMATCH"), file=sys.stderr, flush=True)
    meta = {"date": time.strftime("%Y-%m-%d"),
            "cpu": sh(["sysctl", "-n", "machdep.cpu.brand_string"]).stdout.strip() or platform.processor(),
            "cores": sh(["sysctl", "-n", "hw.ncpu"]).stdout.strip(),
            "os": f"{platform.system()} {platform.mac_ver()[0] or platform.release()} {platform.machine()}",
            "sushi": list(compilers), "go": sh(["go", "version"]).stdout.split()[2],
            "python": sh([args.python, "--version"]).stdout.strip(),
            "warmup": args.warmup, "runs": args.runs}
    text = json.dumps({"meta": meta, "results": results}, indent=1)
    if args.json == "-":
        print(text)
    else:
        Path(args.json).write_text(text + "\n")
    if not all(r["outputs_match"] for r in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
