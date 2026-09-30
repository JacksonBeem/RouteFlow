"""Grade a cohort100-ab run's final answers offline (methodology 5.1 accuracy).

  .aws-sam/study-grade-venv/Scripts/python.exe scripts/cohort100_ab_grade.py <run name>

Same verifier as grade_chemistry_v1.py (upstream LongCoT, RDKit, fallbacks off,
network disabled), applied to `solution = <final answer>` per arm. References are
loaded only for the run's own task ids (cohort_100 evaluation rows are authorized
for this run, user 2026-09-25). Stub runs are refused. Writes grades.json in the
run dir; prints per-arm accuracy only.

The denominator is the manifest's task list: a task without a completed row (only crashed or
invalid attempts, or never reached before a budget stop) gets a grade entry with its row status
and counts as incorrect, so accuracy can never rise because rows went missing.
"""
import importlib.util
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "data/study_v1"


def load_upstream():
    path = STUDY / "raw/longcot/code/src"
    spec = importlib.util.spec_from_file_location("longcot", path / "__init__.py", submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["longcot"] = module
    spec.loader.exec_module(module)
    return module


def outcomes(manifest, records):
    """-> [(task_id, completed row or None, row status)] in manifest order. A task's graded row is
    its completed attempt; otherwise its status is that of its last attempt, or 'not_run'."""
    last, done = {}, {}
    for r in records:
        status = r.get("row_status", "completed")
        last[r["task_id"]] = status
        if status == "completed":
            done[r["task_id"]] = r
    return [(t, done.get(t), "completed" if t in done else last.get(t, "not_run")) for t in manifest["tasks"]]


def main():
    run_dir = ROOT / "data/cohort100_ab/runs" / sys.argv[1]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["mode"] != "live":
        sys.exit("stub runs have no gradable answers")
    records = [json.loads(line) for line in (run_dir / "rows.jsonl").read_text(encoding="utf-8").splitlines()]
    tasks = outcomes(manifest, records)
    rows = [row for _, row, _ in tasks if row]
    missing = [(t, status) for t, row, status in tasks if not row]
    if missing:
        print(f"note: {len(missing)} task(s) have no completed row and count as incorrect: {missing}")

    def deny_network(*args, **kwargs):
        raise RuntimeError("network disabled during grading")
    socket.socket.connect = deny_network
    upstream = load_upstream()
    from rdkit import RDLogger
    RDLogger.DisableLog("rdApp.*")
    options = upstream.VerifyOptions(math=upstream.MathVerifyOptions(enable_fallback=False),
                                     chemistry=upstream.ChemistryVerifyOptions(enable_fallback=False))
    wanted = {r["task_id"] for r in rows}
    refs = {}
    for line in (STUDY / "evaluator_only/longcot/references.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["id"] in wanted:
            refs[r["id"]] = r
    if set(refs) != wanted:
        sys.exit(f"missing references: {sorted(wanted - set(refs))}")

    grades, tally = [], {}
    # A single-arm run (proposed_run.py) names its arm in the manifest; B0/A1 manifests have none.
    run_arms = [manifest["arm"]] if manifest.get("arm") else ["B0", "A1"]
    for task_id, status in missing:
        for arm in run_arms:
            grades.append({"task_id": task_id, "arm": arm, "status": status.upper(), "correct": False})
            tally.setdefault(arm, [0, 0])[1] += 1
    for row in rows:
        q = upstream.Question(domain="chemistry", difficulty=row["task_id"].split(":")[2], **refs[row["task_id"]]["raw_record"])
        for arm, a in row["arms"].items():
            correct = False
            if a["status"] == "SUCCEEDED" and a["final_answer"]:
                correct = bool(upstream.verify(q, "solution = " + a["final_answer"].strip(), options))
            grades.append({"task_id": row["task_id"], "arm": arm, "status": a["status"], "correct": correct,
                           "difficulty": row["difficulty"], "template": row["template"], "strata": row.get("strata"),
                           "no_match": a["no_match"]})
            t = tally.setdefault(arm, [0, 0])
            t[0] += correct
            t[1] += 1
    (run_dir / "grades.json").write_text(json.dumps({"run_name": sys.argv[1], "grades": grades}, indent=1) + "\n",
                                         encoding="utf-8")
    for arm, (c, n) in sorted(tally.items()):
        print(f"{arm}: accuracy {c}/{n} = {c / n:.3f}  (denominator = manifest tasks; failed workflows and "
              f"tasks without a completed row count as incorrect)")


if __name__ == "__main__":
    main()
