"""Compare a graded Proposed run against B0/A1 (eval-b1 + eval-b2) on the same tasks.

  python scripts/proposed_compare.py proposed-1
Per task: correctness, LLM cost, T_E2E for Proposed / B0 / A1. Per tier: summed LLM cost of the
Proposed calls vs A1's calls on the SAME nodes (A1 = same schedule, gpt-5.2 everywhere), so the
cost of routing is separated from the cost of scheduling. Writes <run dir>/comparison.json.
"""
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data/cohort100_ab/runs"
BASELINE = ("eval-b1", "eval-b2")


def jl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def grades(run):
    return {(g["task_id"], g["arm"]): g["correct"]
            for g in json.loads((RUNS / run / "grades.json").read_text(encoding="utf-8"))["grades"]}


def node_costs(run, row, arm):
    """{node_id: summed LLM cost over that node's attempts (None if any attempt's cost is unknown)}."""
    raw = json.loads((RUNS / run / row["raw"]).read_text(encoding="utf-8"))
    out = defaultdict(float)
    for a in raw["arms"][arm]["attempts"]:
        c = a.get("cost_usd")
        if c is None and a.get("cost_status") not in ("no_call", "unbilled_error_reply"):
            out[a["node_id"]] = None
        elif out[a["node_id"]] is not None:
            out[a["node_id"]] += c or 0.0
    return dict(out)


def main():
    run = sys.argv[1]
    prop_rows = {r["task_id"]: r for r in jl(RUNS / run / "rows.jsonl") if r.get("row_status") == "completed"}
    base_rows, base_run, base_grades = {}, {}, {}
    for b in BASELINE:
        base_grades.update(grades(b))
        for r in jl(RUNS / b / "rows.jsonl"):
            if r.get("row_status") == "completed" and r["task_id"] in prop_rows:
                base_rows[r["task_id"]], base_run[r["task_id"]] = r, b
    pg = grades(run)
    tasks, tier_cost = [], defaultdict(lambda: {"nodes": 0, "proposed_usd": 0.0, "a1_usd": 0.0})
    for tid, pr in prop_rows.items():
        br, p = base_rows[tid], pr["arms"]["Proposed"]
        pc, ac = node_costs(run, pr, "Proposed"), node_costs(base_run[tid], br, "A1")
        for nid, route in pr["routing"].items():
            t = tier_cost[route["tier"]]
            t["nodes"] += 1
            t["proposed_usd"] += pc.get(nid) or 0.0
            t["a1_usd"] += ac.get(nid) or 0.0
        tasks.append(dict(task=tid.split(":")[-1], correct={"Proposed": pg.get((tid, "Proposed")),
                                                             "B0": base_grades.get((tid, "B0")),
                                                             "A1": base_grades.get((tid, "A1"))},
                          C_LLM_usd={"Proposed": p["C_LLM_usd"], "B0": br["arms"]["B0"]["C_LLM_usd"],
                                     "A1": br["arms"]["A1"]["C_LLM_usd"]},
                          T_E2E_s={"Proposed": p["T_E2E_s"], "B0": br["arms"]["B0"]["T_E2E_s"],
                                   "A1": br["arms"]["A1"]["T_E2E_s"]},
                          status=p["status"], retries=p["retries"], truncated=p["truncated_attempts"],
                          cost_unknown=p["cost_unknown_attempts"]))
    totals = {arm: {"correct": sum(bool(t["correct"][arm]) for t in tasks),
                    "C_LLM_usd": round(sum(t["C_LLM_usd"][arm] for t in tasks), 4),
                    "T_E2E_s": round(sum(t["T_E2E_s"][arm] for t in tasks), 1)} for arm in ("Proposed", "B0", "A1")}
    out = {"run": run, "baseline": BASELINE, "tasks": tasks, "totals": totals, "n": len(tasks),
           "tier_cost_same_nodes": {k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}
                                    for k, v in tier_cost.items()}}
    (RUNS / run / "comparison.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"{'task':9s} {'correct P/B0/A1':16s} {'LLM $ P / B0 / A1':26s} T_E2E s P / B0 / A1")
    for t in tasks:
        c, m, s = t["correct"], t["C_LLM_usd"], t["T_E2E_s"]
        print(f"{t['task']:9s} {'/'.join('Y' if c[a] else 'n' for a in ('Proposed', 'B0', 'A1')):16s} "
              f"{m['Proposed']:.4f} / {m['B0']:.4f} / {m['A1']:.4f}   "
              f"{s['Proposed']:.1f} / {s['B0']:.1f} / {s['A1']:.1f}"
              + (f"  [{t['status']}, retries {t['retries']}, trunc {t['truncated']}]" if t['status'] != 'SUCCEEDED'
                 or t['retries'] or t['truncated'] else ""))
    print("totals", json.dumps(totals))
    print("per tier, same nodes (Proposed vs A1 cost):", json.dumps(out["tier_cost_same_nodes"]))
    if len(tasks) < len(json.loads((RUNS / run / "manifest.json").read_text(encoding="utf-8"))["tasks"]):
        print("note: some manifest tasks have no completed row; they are not in this comparison")


if __name__ == "__main__":
    main()
