#!/usr/bin/env python3
"""
Every test, one command:  py run_tests.py

Python covers the engine, node covers the front-end. Both matter - the two
worst bugs this project has had were a JS syntax error that silently killed
every button on the page, and a server crash that only showed up as an
upload that quietly never finished. Neither suite would have caught the
other one.

Exits non-zero if anything fails, so it can gate a commit.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "docs"


def run(label: str, cmd: list[str]) -> bool:
    print(f"\n=== {label} " + "=" * max(0, 56 - len(label)))
    return subprocess.run(cmd, cwd=str(ROOT)).returncode == 0


def main() -> int:
    ok = []
    node = shutil.which("node")

    # Syntax first: a broken file makes every other failure a red herring.
    if node:
        for js in ("app.js",):
            ok.append((f"syntax {js}",
                       run(f"syntax {js}", [node, "--check", str(FRONTEND / js)])))
    else:
        print("! node not found - skipping the front-end tests entirely.")
        print("  The JS is NOT being checked. Install node to cover it.")

    ok.append(("engine", run("engine (pytest)",
                             [sys.executable, "-m", "pytest", "tests/", "-q"])))

    if node:
        for js in sorted((ROOT / "tests").glob("test_*.js")):
            ok.append((js.stem, run(f"front-end {js.name}", [node, str(js)])))

    print("\n" + "=" * 60)
    failed = [name for name, good in ok if not good]
    for name, good in ok:
        print(f"  {'PASS' if good else 'FAIL'}  {name}")
    if failed:
        print(f"\n{len(failed)} failed: {', '.join(failed)}")
        return 1
    print(f"\nall {len(ok)} suites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
