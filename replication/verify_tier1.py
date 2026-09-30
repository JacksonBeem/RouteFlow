"""Tier-1 check: regenerate the paper's numbers from the run records and compare with the shipped outputs.

  python replication/verify_tier1.py

Runs scripts/cohort100_ab_analyze.py (-> eval.json) and data/cohort100_ab/analysis/paper_todo_data.py
(-> paper_todo_data.json), compares each regenerated file byte for byte with the shipped one, then
restores the shipped files. Prints MATCH or DIFF per file; exit code 0 only if both match.
Offline, standard library only, a few seconds.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "data/cohort100_ab/analysis"
CHECKS = [
    (["scripts/cohort100_ab_analyze.py", "--runs", "eval-b1", "eval-b2", "--decompose", "dec-1", "--out", "eval"],
     ANALYSIS / "eval.json"),
    (["data/cohort100_ab/analysis/paper_todo_data.py"], ANALYSIS / "paper_todo_data.json"),
]


def main():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    ok = True
    for cmd, out in CHECKS:
        shipped = out.read_bytes()
        try:
            subprocess.run([sys.executable, *cmd], cwd=ROOT, env=env, check=True, capture_output=True)
            same = out.read_bytes() == shipped
        finally:
            out.write_bytes(shipped)
        print(f"{'MATCH' if same else 'DIFF '}  {out.relative_to(ROOT).as_posix()}")
        ok &= same
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
