"""cohort100-ab analysis, exactly as pre-registered (docs/cohort100_ab_preregistration_2026-09-26.md,
sections 4-8). Offline: reads run records only.

  python scripts/cohort100_ab_analyze.py --runs eval-b1 eval-b2 --decompose dec-1 --out eval
  python scripts/cohort100_ab_analyze.py --runs pilot-2 --decompose dec-dev-1 --out pilot-2   (validation)

Inputs per run: manifest.json (task list = denominator), rows.jsonl (the completed row of each
task), raw/<run key>.json (Lambda REPORT lines, for the cold-start sensitivity), grades.json if
present (accuracy). DAG structure comes from nodes.jsonl / nodes_dev.jsonl (by the run's source).
Writes data/cohort100_ab/analysis/<out>.json and prints a summary.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/cohort100_ab"
BOOT_SEED = "cohort100-ab-boot-v1"
BOOT_REPS = 10000
MODEL = "openai/gpt-5.2"


# ---- estimators --------------------------------------------------------------

def template_geomean(items, value):
    """Equal weight per template: exp(mean over templates of the mean of log value). items: rows
    with 'template'. Rows whose value is None are skipped; templates left empty are dropped."""
    by = defaultdict(list)
    for it in items:
        v = value(it)
        if v is not None:
            by[it["template"]].append(math.log(v))
    if not by:
        return None
    return math.exp(sum(sum(v) / len(v) for v in by.values()) / len(by))


def template_mean(items, value):
    """Equal weight per template, arithmetic."""
    by = defaultdict(list)
    for it in items:
        v = value(it)
        if v is not None:
            by[it["template"]].append(v)
    return sum(sum(v) / len(v) for v in by.values()) / len(by) if by else None


def pooled_geomean(items, value):
    vals = [math.log(v) for v in (value(it) for it in items) if v is not None]
    return math.exp(sum(vals) / len(vals)) if vals else None


def bootstrap_ci(items, stat, reps=BOOT_REPS, seed=BOOT_SEED, level=0.95):
    """Stratified bootstrap: resample rows with replacement within each template; percentile CI."""
    by = defaultdict(list)
    for it in items:
        by[it["template"]].append(it)
    rng = random.Random(seed)
    groups = [by[t] for t in sorted(by)]
    out = []
    for _ in range(reps):
        sample = [g[rng.randrange(len(g))] for g in groups for _ in g]
        v = stat(sample)
        if v is not None:
            out.append(v)
    if not out:
        return None
    out.sort()
    lo = out[int(math.floor((1 - level) / 2 * (len(out) - 1)))]
    hi = out[int(math.ceil((1 + level) / 2 * (len(out) - 1)))]
    return [lo, hi]


def estimate(items, value, ci=True):
    s = template_geomean(items, value)
    res = {"S": s, "n_rows": sum(value(it) is not None for it in items),
           "n_templates": len({it["template"] for it in items if value(it) is not None})}
    if ci and s is not None:
        res["ci95"] = bootstrap_ci(items, lambda sample: template_geomean(sample, value))
    return res


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (sx * sy) if sx and sy else None


# ---- schedules (replay) --------------------------------------------------------

def critical_path(durations, order, edges):
    """Longest duration-weighted path: the finish time of per-node dependency scheduling."""
    preds = defaultdict(list)
    for a, b in edges:
        preds[b].append(a)
    finish = {}
    for n in order:
        finish[n] = durations[n] + max((finish[p] for p in preds[n]), default=0.0)
    return max(finish.values())


def barrier_time(durations, waves):
    return sum(max(durations[n] for n in w) for w in waves)


def replay(durations, dag):
    """-> (sequential, dependency schedule, wave-barrier schedule) seconds from one arm's node work times."""
    d = {int(k): v for k, v in durations.items()}
    if set(d) != set(dag["b0_order"]):
        return None
    return sum(d.values()), critical_path(d, dag["b0_order"], dag["edges"]), barrier_time(d, dag["a1_waves"])


# ---- loading -------------------------------------------------------------------

def load_run(name):
    run_dir = DATA / "runs" / name
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    records = [json.loads(x) for x in (run_dir / "rows.jsonl").read_text(encoding="utf-8").splitlines()]
    completed = {r["task_id"]: r for r in records if r.get("row_status", "completed") == "completed"}
    last = {}
    for r in records:
        last[r["task_id"]] = r.get("row_status", "completed")
    grades = None
    if (run_dir / "grades.json").exists():
        grades = json.loads((run_dir / "grades.json").read_text(encoding="utf-8"))["grades"]
    return run_dir, manifest, completed, last, grades


def load_dags(source):
    path = DATA / ("nodes.jsonl" if source == "cohort" else "nodes_dev.jsonl")
    return {t["task_id"]: t["row"] for t in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


def cold_init_seconds(run_dir, row):
    """-> {arm: summed Lambda init seconds of that arm's invocations} from the saved raw file."""
    if not row.get("raw"):
        return None
    raw = json.loads((run_dir / row["raw"]).read_text(encoding="utf-8"))
    return {arm: sum((r.get("init_ms") or 0) for r in a["reports"]) / 1000 for arm, a in raw["arms"].items()}


def check_disjoint(runs):
    """The analysed runs must not share tasks (each task is one unit, counted once)."""
    seen = {}
    for name in runs:
        for tid in json.loads((DATA / "runs" / name / "manifest.json").read_text(encoding="utf-8"))["tasks"]:
            if tid in seen:
                raise ValueError(f"{tid} is in both {seen[tid]} and {name}")
            seen[tid] = name


def build_items(runs, t_dec):
    """One item per manifest task: its completed row's measurements, or its outcome if none."""
    items, missing = [], []
    for name in runs:
        run_dir, manifest, completed, last, _ = load_run(name)
        dags = load_dags(manifest["source"])
        for tid in manifest["tasks"]:
            dag = dags[tid]
            row = completed.get(tid)
            if row is None:
                missing.append({"task_id": tid, "run": name, "template": dag["template"],
                                "status": last.get(tid, "not_run")})
                continue
            a, b = row["arms"]["A1"], row["arms"]["B0"]
            both = a["status"] == b["status"] == "SUCCEEDED"
            it = {"task_id": tid, "run": name, "template": dag["template"], "N": dag["N"], "D": dag["D"],
                  "strata": row.get("strata") or dag.get("strata") or {}, "order": row["order"],
                  "both_succeeded": both, "status": {"B0": b["status"], "A1": a["status"]},
                  "T": {"B0": b["T_E2E_s"], "A1": a["T_E2E_s"]},
                  "C_LLM": {"B0": b["C_LLM_usd"], "A1": a["C_LLM_usd"]},
                  "C_Total": {"B0": b["C_Total_usd"], "A1": a["C_Total_usd"]},
                  "C_Total_upper": {"B0": b.get("C_Total_upper_usd"), "A1": a.get("C_Total_upper_usd")},
                  "tokens": {arm: (x["input_tokens"], x["output_tokens"]) for arm, x in (("B0", b), ("A1", a))},
                  "C_Lambda": {"B0": b["C_Lambda_usd"], "A1": a["C_Lambda_usd"]},
                  "wait_seconds": {"B0": b.get("wait_seconds"), "A1": a.get("wait_seconds")},
                  "flagged_call": sum(x.get("retries", 0) + x.get("rate_limited_attempts", 0)
                                      + x.get("cost_unknown_attempts", 0) for x in (a, b)) > 0,
                  "other_model": any(m != MODEL for x in (a, b) for m in x.get("served_models", [])),
                  "t_dec": t_dec.get(tid)}
            for arm, x in (("B0", b), ("A1", a)):
                r = replay(x["node_seconds"], dag) if x["status"] == "SUCCEEDED" else None
                it[f"replay_{arm}"] = r
            it["init_s"] = cold_init_seconds(run_dir, row)
            items.append(it)
    return items, missing


def load_t_dec(name):
    if not name:
        return {}, None
    rows = [json.loads(x) for x in (DATA / "decompose" / name / "rows.jsonl").read_text(encoding="utf-8").splitlines()]
    ok = {r["task_id"]: r for r in rows if r["status"] == "ok"}
    return {t: r["T_dec_s"] for t, r in ok.items()}, ok


# ---- analysis ------------------------------------------------------------------

def speed(it):
    return it["T"]["B0"] / it["T"]["A1"] if it["both_succeeded"] else None


def analyze(runs, decompose, manifest_prices, allow_overlap=False):
    if not allow_overlap:
        check_disjoint(runs)
    t_dec, dec_rows = load_t_dec(decompose)
    items, missing = build_items(runs, t_dec)
    ok = [it for it in items if it["both_succeeded"]]
    out = {"runs": runs, "decompose": decompose, "n_tasks": len(items) + len(missing),
           "n_completed_rows": len(items), "n_both_succeeded": len(ok), "without_completed_row": missing}

    # section 4
    primary = estimate(ok, speed)
    primary["latency_reduction"] = 1 - 1 / primary["S"] if primary["S"] else None
    primary["H1_supported"] = bool(primary.get("ci95") and primary["ci95"][0] > 1)
    primary["row_pooled_geomean"] = pooled_geomean(ok, speed)
    primary["pdf_latency_reduction_template_mean"] = template_mean(
        ok, lambda it: (it["T"]["B0"] - it["T"]["A1"]) / it["T"]["B0"])
    out["primary"] = primary

    # section 5
    def rep_speed(arm, which):
        def f(it):
            r = it[f"replay_{arm}"]
            return r[0] / r[which] if r else None
        return f
    out["replay"] = {f"from_{arm}": {"dependency": estimate(ok, rep_speed(arm, 1)),
                                     "wave_barrier": estimate(ok, rep_speed(arm, 2), ci=False)} for arm in ("B0", "A1")}
    out["barrier_loss"] = {arm: template_mean(ok, lambda it, arm=arm: (it[f"replay_{arm}"][2] - it[f"replay_{arm}"][1])
                                              / it[f"replay_{arm}"][2] if it[f"replay_{arm}"] else None)
                           for arm in ("B0", "A1")}
    out["overhead_s"] = {
        "A1": template_mean(ok, lambda it: it["T"]["A1"] - it["replay_A1"][1] if it["replay_A1"] else None),
        "B0": template_mean(ok, lambda it: it["T"]["B0"] - it["replay_B0"][0] if it["replay_B0"] else None)}
    e2e = [it for it in ok if it["t_dec"] is not None]
    out["t_dec"] = {"n": len(e2e), "rows_ok": len(dec_rows or {}),
                    "mean_s": sum(it["t_dec"] for it in e2e) / len(e2e) if e2e else None,
                    "exact_dag_rate": (sum(r["exact"] for r in dec_rows.values()) / len(dec_rows)) if dec_rows else None,
                    "speedup_with_decomposition": estimate(
                        e2e, lambda it: (it["T"]["B0"] + it["t_dec"]) / (it["T"]["A1"] + it["t_dec"]))}
    pin, pout = manifest_prices
    formula = {arm: sum(it["tokens"][arm][0] * pin + it["tokens"][arm][1] * pout for it in items) for arm in ("B0", "A1")}
    out["cost"] = {
        "ratio_A1_over_B0": {"C_LLM": estimate(ok, lambda it: it["C_LLM"]["A1"] / it["C_LLM"]["B0"] if it["C_LLM"]["B0"] else None, ci=False),
                             "C_Total": estimate(ok, lambda it: it["C_Total"]["A1"] / it["C_Total"]["B0"], ci=False)},
        "totals_usd": {arm: {k: sum(it[k][arm] for it in items) for k in ("C_LLM", "C_Lambda", "C_Total")}
                       for arm in ("B0", "A1")},
        "C_LLM_formula_usd": formula,
        "A1_wait_lambda_seconds": sum(it["wait_seconds"]["A1"] or 0 for it in items),
        "rows_with_unknown_or_retried_calls": [it["task_id"] for it in items if it["flagged_call"]]}

    per = {}
    for t in sorted({it["template"] for it in items} | {m["template"] for m in missing}):
        rows_t = [it for it in items if it["template"] == t]
        ok_t = [it for it in rows_t if it["both_succeeded"]]
        per[t] = {"rows": len(rows_t), "without_completed_row": sum(m["template"] == t for m in missing),
                  "both_succeeded": len(ok_t),
                  "completion": {arm: sum(it["status"][arm] == "SUCCEEDED" for it in rows_t) for arm in ("B0", "A1")},
                  "N_over_D": rows_t[0]["N"] / rows_t[0]["D"] if rows_t else None,
                  "S": template_geomean(ok_t, speed),
                  "replay_B0": template_geomean(ok_t, rep_speed("B0", 1)),
                  "replay_A1": template_geomean(ok_t, rep_speed("A1", 1)),
                  "C_Total_ratio": template_geomean(ok_t, lambda it: it["C_Total"]["A1"] / it["C_Total"]["B0"])}
    out["per_template"] = per
    h2 = [(v["S"], v["replay_B0"], v["N_over_D"]) for v in per.values() if v["S"] and v["replay_B0"]]
    out["H2"] = {"n_templates": len(h2),
                 "spearman_measured_vs_replay": spearman([x[0] for x in h2], [x[1] for x in h2]),
                 "spearman_measured_vs_N_over_D": spearman([x[0] for x in h2], [x[2] for x in h2])}

    # section 6
    def cold_adj(it):
        if not it["both_succeeded"] or not it["init_s"]:
            return None
        return (it["T"]["B0"] - it["init_s"].get("B0", 0)) / (it["T"]["A1"] - it["init_s"].get("A1", 0))
    with_init = [it for it in ok if it["init_s"]]
    s = out["sensitivity"] = {"cold_start_adjusted": dict(estimate(with_init, cold_adj, ci=False),
                                                          S_unadjusted_same_rows=template_geomean(with_init, speed))}
    for key in ("exposed", "flagged_impossible", "easy2_policy"):
        s[f"without_{key}"] = estimate([it for it in ok if not it["strata"].get(key)], speed, ci=False)
    order_logs = {}
    for first in ("B0", "A1"):
        sub = [it for it in ok if it["order"][0] == first]
        order_logs[f"{first}_first"] = estimate(sub, speed, ci=False)
    s["arm_order"] = order_logs
    s["per_run"] = {name: estimate([it for it in ok if it["run"] == name], speed, ci=False) for name in runs}
    s["failed_arm_time_at_failure"] = estimate(items, lambda it: it["T"]["B0"] / it["T"]["A1"], ci=False)
    s["without_retried_or_unknown_cost_rows"] = estimate([it for it in ok if not it["flagged_call"]], speed, ci=False)
    s["served_model"] = {"rows_with_other_model": [it["task_id"] for it in items if it["other_model"]],
                         "without_them": estimate([it for it in ok if not it["other_model"]], speed, ci=False)}

    # section 7
    graded, disagree, ungraded = {}, 0, []
    tasks = set()
    for name in runs:
        _, manifest, _, _, grades = load_run(name)
        tasks |= set(manifest["tasks"])
        if grades is None:
            ungraded.append(name)
        for g in grades or []:
            graded[(g["task_id"], g["arm"])] = bool(g["correct"])
    if ungraded:
        out["accuracy"] = {"not_reported": f"runs not graded: {ungraded}"}
    elif graded:
        acc = {arm: sum(graded.get((t, arm), False) for t in tasks) for arm in ("B0", "A1")}
        disagree = sum(graded.get((t, "B0"), False) != graded.get((t, "A1"), False) for t in tasks)
        out["accuracy"] = {"denominator": len(tasks), "correct": acc,
                           "rate": {a: c / len(tasks) for a, c in acc.items()}, "arms_disagree": disagree}
    else:
        out["accuracy"] = None
    return out


def fmt(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--decompose")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-overlap", action="store_true", help="validation only: runs may share tasks")
    args = ap.parse_args()
    cfg = json.loads((DATA / "runs" / args.runs[0] / "manifest.json").read_text(encoding="utf-8"))["config"]
    res = analyze(args.runs, args.decompose, (cfg["input_price_per_token"], cfg["output_price_per_token"]),
                  args.allow_overlap)
    path = DATA / "analysis" / f"{args.out}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    p = res["primary"]
    print(f"tasks {res['n_tasks']} | completed rows {res['n_completed_rows']} | both succeeded {res['n_both_succeeded']}")
    print(f"PRIMARY S = {fmt(p['S'])}  95% CI {[fmt(x) for x in p.get('ci95') or []]}  "
          f"latency reduction {fmt(p['latency_reduction'] and 100 * p['latency_reduction'], 1)}%  H1 {p['H1_supported']}")
    print(f"replay (B0 durations) {fmt(res['replay']['from_B0']['dependency']['S'])} | with T_dec "
          f"{fmt(res['t_dec']['speedup_with_decomposition']['S'])} | cold-adjusted "
          f"{fmt(res['sensitivity']['cold_start_adjusted']['S'])}")
    print("template  rows ok  N/D   S     replayB0 C_Total A1/B0")
    for t, v in res["per_template"].items():
        print(f"{t:8s}  {v['rows']:4d} {v['both_succeeded']:2d}  {fmt(v['N_over_D'])}  {fmt(v['S'])}  "
              f"{fmt(v['replay_B0'])}     {fmt(v['C_Total_ratio'])}")
    print(f"written {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
