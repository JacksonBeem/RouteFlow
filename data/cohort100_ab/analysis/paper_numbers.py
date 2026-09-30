"""Compute the paper's numbers (accuracy, latency, speedup, cost, routing profile) from the run records.

  python data/cohort100_ab/analysis/paper_numbers.py      (any cwd; writes paper_numbers.json next to this file)

Offline, standard library only. Configurations: B0 and A1 = runs eval-b1 + eval-b2;
P-DeepSeek = run proposed-3 (router preset v3); P-Sonnet = run proposed-4 (router preset v1).
Speedup estimator: template-weighted geometric mean of per-task ratios with a stratified bootstrap
95% CI (the same estimator as scripts/cohort100_ab_analyze.py). Output keys use the paper's names;
replication/CLAIMS.md maps each paper number to its key."""
import json, math, random, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "paper_numbers.json"
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
import cohort100_ab_analyze as A  # noqa: E402

RUNS = ROOT / "data/cohort100_ab/runs"
items, missing = A.build_items(["eval-b1", "eval-b2"], {})
assert not missing and len(items) == 100
byid = {it["task_id"]: it for it in items}


def grades(run, arm=None):
    g = json.loads((RUNS / run / "grades.json").read_text(encoding="utf-8"))["grades"]
    return {x["task_id"]: x["correct"] for x in g if arm is None or x["arm"] == arm}


G = {"B0": {**grades("eval-b1", "B0"), **grades("eval-b2", "B0")},
     "A1": {**grades("eval-b1", "A1"), **grades("eval-b2", "A1")},
     "P3": grades("proposed-3"), "P4": grades("proposed-4")}

dags = {}
for line in (ROOT / "data/cohort100_ab/nodes.jsonl").read_text(encoding="utf-8").splitlines():
    t = json.loads(line)
    dags[t["task_id"]] = t

DAGROW = A.load_dags("cohort")
rec = {}  # (cfg, tid) -> dict(T, C_LLM, C_Lambda, C_SFN, cold, trans, crit_s, status)
for it in items:
    tid = it["task_id"]
    for arm in ("B0", "A1"):
        rec[(arm, tid)] = dict(T=it["T"][arm], C_LLM=it["C_LLM"][arm], C_Lambda=it["C_Lambda"][arm],
                               C_SFN=it["C_Total"][arm] - it["C_LLM"][arm] - it["C_Lambda"][arm],
                               crit=(it[f"replay_{arm}"][1] if it[f"replay_{arm}"] else None),
                               sumwork=(it[f"replay_{arm}"][0] if it[f"replay_{arm}"] else None))
routing = {}
for cfg, run in (("P3", "proposed-3"), ("P4", "proposed-4")):
    for line in (RUNS / run / "rows.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["row_status"] != "completed":
            continue
        a = r["arms"]["Proposed"]
        rp = A.replay(a["node_seconds"], DAGROW[r["task_id"]]) if a["status"] == "SUCCEEDED" else None
        rec[(cfg, r["task_id"])] = dict(T=a["T_E2E_s"], C_LLM=a["C_LLM_usd"], C_Lambda=a["C_Lambda_usd"],
                                        C_SFN=a["C_StepFunctions_usd"], node_seconds=a["node_seconds"],
                                        status=a["status"], cold=a["cold_starts"], trans=a["transitions"],
                                        retries=a["retries"], trunc=a["truncated_attempts"],
                                        rl=a["rate_limited_attempts"], crit=rp[1] if rp else None,
                                        sumwork=rp[0] if rp else None)
        routing[(cfg, r["task_id"])] = r["routing"]


CFGS = ("B0", "A1", "P3", "P4")
DIFF = {tid: tid.split(":")[2] for tid in byid}
TPL = {tid: byid[tid]["template"] for tid in byid}
GROUPS = {"easy": [t for t in byid if DIFF[t] == "easy"], "medium": [t for t in byid if DIFF[t] == "medium"],
          "hard": [t for t in byid if DIFF[t] == "hard"], "all": list(byid)}
rng_seed = "paper-todo-v1"


def boot_mean(tids, f, reps=10000):
    """Stratified (by template) bootstrap percentile 95% CI of the plain mean over tasks."""
    by = defaultdict(list)
    for t in tids:
        by[TPL[t]].append(f(t))
    rng = random.Random(rng_seed)
    est = sum(sum(v) for v in by.values()) / len(tids)
    sims = []
    for _ in range(reps):
        tot = n = 0
        for v in by.values():
            smp = [rng.choice(v) for _ in v]
            tot += sum(smp); n += len(smp)
        sims.append(tot / n)
    sims.sort()
    return est, sims[int(0.025 * reps)], sims[int(0.975 * reps) - 1]


def items_for(tids, c):
    return [dict(task_id=t, template=TPL[t], ratio=rec[("B0", t)]["T"] / rec[(c, t)]["T"]) for t in tids]


out = {}
for g, tids in GROUPS.items():
    o = out[g] = {"n": len(tids)}
    for c in CFGS:
        acc = boot_mean(tids, lambda t: 1.0 if G[c][t] else 0.0)
        ct = boot_mean(tids, lambda t: rec[(c, t)]["C_LLM"] + rec[(c, t)]["C_Lambda"] + rec[(c, t)]["C_SFN"])
        d = {"correct": sum(bool(G[c][t]) for t in tids), "acc": acc,
             "T_median_s": statistics.median(rec[(c, t)]["T"] for t in tids),
             "T_sum_s": sum(rec[(c, t)]["T"] for t in tids),
             "C_LLM_mean": sum(rec[(c, t)]["C_LLM"] for t in tids) / len(tids),
             "C_Lambda_mean": sum(rec[(c, t)]["C_Lambda"] for t in tids) / len(tids),
             "C_SFN_mean": sum(rec[(c, t)]["C_SFN"] for t in tids) / len(tids),
             "C_Total_mean_ci": ct, "C_Total_sum": ct[0] * len(tids)}
        if c != "B0":
            ok = [t for t in tids if rec[("B0", t)]["T"] and rec[(c, t)]["T"] and byid[t]["both_succeeded"]
                  and rec[(c, t)].get("status", "SUCCEEDED") == "SUCCEEDED"]
            d["speedup_vs_B0_template_geomean"] = A.estimate(items_for(ok, c), lambda it: it["ratio"])
            d["speedup_vs_B0_median_task"] = statistics.median(rec[("B0", t)]["T"] / rec[(c, t)]["T"] for t in ok)
        if c in ("P3", "P4"):
            ok = [t for t in tids if byid[t]["both_succeeded"]]
            d["speedup_vs_A1_template_geomean"] = A.estimate(
                [dict(task_id=t, template=TPL[t], r=rec[("A1", t)]["T"] / rec[(c, t)]["T"]) for t in ok],
                lambda it: it["r"])
            d["cost_ratio_vs_A1_Ctotal"] = sum(rec[(c, t)]["C_LLM"] + rec[(c, t)]["C_Lambda"] + rec[(c, t)]["C_SFN"] for t in tids) /                 sum(rec[("A1", t)]["C_LLM"] + rec[("A1", t)]["C_Lambda"] + rec[("A1", t)]["C_SFN"] for t in tids)
            d["cost_ratio_vs_B0_Ctotal"] = sum(rec[(c, t)]["C_LLM"] + rec[(c, t)]["C_Lambda"] + rec[(c, t)]["C_SFN"] for t in tids) /                 sum(rec[("B0", t)]["C_LLM"] + rec[("B0", t)]["C_Lambda"] + rec[("B0", t)]["C_SFN"] for t in tids)
        # overhead = T_E2E - duration-weighted critical path (A1-like) or sum of work (B0)
        key = "sumwork" if c == "B0" else "crit"
        oh = [rec[(c, t)]["T"] - rec[(c, t)][key] for t in tids if rec[(c, t)].get(key) is not None]
        d["overhead_mean_s"] = sum(oh) / len(oh) if oh else None
        d["overhead_share_of_T"] = sum(oh) / sum(rec[(c, t)]["T"] for t in tids if rec[(c, t)].get(key) is not None) if oh else None
        o[c] = d
    # structure (Fig R2)
    o["N_over_D_mean"] = sum(byid[t]["N"] / byid[t]["D"] for t in tids) / len(tids)
    o["N_mean"] = sum(byid[t]["N"] for t in tids) / len(tids)
    o["D_mean"] = sum(byid[t]["D"] for t in tids) / len(tids)

# Fig R2 per-task: measured vs ideal
fig2 = {}
for c in ("A1", "P3", "P4"):
    xs = [byid[t]["N"] / byid[t]["D"] for t in byid if byid[t]["both_succeeded"]]
    ys = [rec[("B0", t)]["T"] / rec[(c, t)]["T"] for t in byid if byid[t]["both_succeeded"]]
    fig2[c] = {"spearman_measured_vs_N_over_D": A.spearman(xs, ys),
               "tasks_below_ideal": sum(y < x for x, y in zip(xs, ys)), "tasks_above_ideal": sum(y > x for x, y in zip(xs, ys)),
               "n": len(xs), "mean_measured_over_ideal": sum(y / x for x, y in zip(xs, ys)) / len(xs)}
out["fig_R2"] = fig2

# Fig R4 routing profile
prof = {}
for c in ("P3", "P4"):
    for g, tids in GROUPS.items():
        tiers, mods, props = Counter(), Counter(), Counter()
        unmatched = 0
        for t in tids:
            for nid, d in routing[(c, t)].items():
                tiers[d["tier"]] += 1
                for b in d["bumps"]:
                    mods[b] += 1
                unmatched += d["property"] is None
        n = sum(tiers.values())
        prof.setdefault(g, {"nodes": n, "tiers": dict(tiers), "share": {k: v / n for k, v in tiers.items()},
                            "modifiers": dict(mods), "unmatched": unmatched})
out["fig_R4_routing"] = prof

# reliability
rel = {}
for c in ("P3", "P4"):
    rel[c] = {k: sum(rec[(c, t)][k] for t in byid) for k in ("retries", "trunc", "rl", "cold", "trans")}
    rel[c]["succeeded"] = sum(rec[(c, t)]["status"] == "SUCCEEDED" for t in byid)
out["reliability"] = rel

# accuracy overlap
ov = {}
for c in ("P3", "P4"):
    ov[c] = {"both_with_A1": sum(G[c][t] and G["A1"][t] for t in byid),
             "only_proposed": sum(G[c][t] and not G["A1"][t] for t in byid),
             "only_A1": sum(G["A1"][t] and not G[c][t] for t in byid)}
ov["B0_vs_A1_disagree"] = sum(G["B0"][t] != G["A1"][t] for t in byid)
ov["P3_vs_P4_disagree"] = sum(G["P3"][t] != G["P4"][t] for t in byid)
out["accuracy_overlap"] = ov

# Cost detail (paper Table 4, Fig. 3, section 4.2) from every attempt record, retries included.
# Tiers use the paper's names: light = router tier "easy", medium = "medium", strong = "hard".
# B0/A1 nodes get the tier the router assigns the same node, so each tier compares like with like.
PAPER_TIER = {"easy": "light", "medium": "medium", "hard": "strong"}
RUNS_OF = {"B0": (("eval-b1", "eval-b2"), "B0"), "A1": (("eval-b1", "eval-b2"), "A1"),
           "P3": (("proposed-3",), "Proposed"), "P4": (("proposed-4",), "Proposed")}
node_tier = {(t, str(nid)): PAPER_TIER[d["tier"]] for (c, t), rt in routing.items() if c == "P3" for nid, d in rt.items()}


def out_price(run):
    """{model: output USD/token} from the run's manifest."""
    m = json.loads((RUNS / run / "manifest.json").read_text(encoding="utf-8"))
    if "tier_config" in m:
        return {v["model"]: v["output_price_per_token"] for v in m["tier_config"].values()}
    return {m["config"]["model"]: m["config"]["output_price_per_token"]}


cost = {}
for c, (runs, arm) in RUNS_OF.items():
    tot = Counter()
    tiers = defaultdict(lambda: {"nodes": set(), "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    for run in runs:
        price = out_price(run)
        for line in (RUNS / run / "rows.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            a = r["arms"][arm]
            for k, f in (("C_LLM", "C_LLM_usd"), ("C_Lambda", "C_Lambda_usd"), ("C_SFN", "C_StepFunctions_usd"),
                         ("lambda_invocations", "lambda_invocations"), ("lambda_billed_ms", "lambda_billed_ms"),
                         ("wait_s", "wait_seconds"), ("transitions", "transitions"), ("cold_starts", "cold_starts")):
                tot[k] += a.get(f) or 0
            raw = json.loads((RUNS / run / r["raw"]).read_text(encoding="utf-8"))["arms"][arm]
            for at in raw["attempts"]:
                u = at.get("usage") or {}
                key = (r["task_id"], str(at["node_id"]))
                ti = tiers[node_tier[key]]
                ti["nodes"].add(key)
                ti["input_tokens"] += u.get("prompt_tokens", 0)
                ti["output_tokens"] += u.get("completion_tokens", 0)
                ti["cost_usd"] += at.get("cost_usd") or 0
                tot["output_tokens"] += u.get("completion_tokens", 0)
                tot["reasoning_tokens"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
                tot["output_list_usd"] += u.get("completion_tokens", 0) * price.get(at.get("served_model"), 0)
    c_total = tot["C_LLM"] + tot["C_Lambda"] + tot["C_SFN"]
    billed_s = tot["lambda_billed_ms"] / 1000
    wait_share = tot["wait_s"] / billed_s
    cost[c] = {"C_LLM": tot["C_LLM"], "C_Lambda": tot["C_Lambda"], "C_SFN": tot["C_SFN"], "C_Total": c_total,
               "LLM_share": tot["C_LLM"] / c_total,
               "serverless_usd": tot["C_Lambda"] + tot["C_SFN"],
               "serverless_share": (tot["C_Lambda"] + tot["C_SFN"]) / c_total,
               "output_token_share_of_C_LLM": tot["output_list_usd"] / tot["C_LLM"],
               "reasoning_share_of_output_tokens": tot["reasoning_tokens"] / tot["output_tokens"],
               "lambda_invocations": tot["lambda_invocations"], "lambda_billed_s": billed_s,
               "lambda_wait_s": tot["wait_s"], "lambda_wait_share_of_billed": wait_share,
               # upper bound on what starting workers only when ready could save (all waiting billed at C_Lambda's rate)
               "lambda_wait_cost_share_of_C_Total": wait_share * tot["C_Lambda"] / c_total,
               "sfn_transitions": tot["transitions"], "cold_starts": tot["cold_starts"],
               "tiers": {t: {"subtasks": len(v["nodes"]), "input_tokens": v["input_tokens"],
                             "output_tokens": v["output_tokens"], "cost_usd": v["cost_usd"],
                             "usd_per_subtask": v["cost_usd"] / len(v["nodes"]),
                             "output_tokens_per_subtask": v["output_tokens"] / len(v["nodes"])}
                         for t, v in sorted(tiers.items())}}
out["cost_detail"] = cost

# Output keys in the paper's notation (internal short codes above are P3 / P4 and router tier
# names easy / hard; the paper says P-DeepSeek / P-Sonnet and light / strong).
KEYS = {"P3": "P-DeepSeek", "P4": "P-Sonnet", "fig_R2": "speedup_vs_ideal", "fig_R4_routing": "routing_profile",
        "only_proposed": "only_routeflow", "P3_vs_P4_disagree": "P-DeepSeek_vs_P-Sonnet_disagree"}
ROUTER_TIER = {"easy": "light", "medium": "medium", "hard": "strong"}


def paper_keys(x):
    return {KEYS.get(k, k): paper_keys(v) for k, v in x.items()} if isinstance(x, dict) else x


out = paper_keys(out)
for g in out["routing_profile"].values():
    for k in ("tiers", "share"):
        g[k] = {ROUTER_TIER[t]: v for t, v in g[k].items()}
OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
print("ok")
