"""Proposed-arm runner: A1's dependency-aware parallel schedule + per-node LLM routing.

Reuses the cohort100-ab harness unchanged (same node_worker.py bytes, A1-Parallel machine,
warm-up, settle barrier, S3 attempt records, teardown); only two things differ:
  * each subtask's Lambda gets its tier's model / provider / reasoning / max_tokens from
    proposed.router.route(instruction) (the worker reads them from its environment);
  * one arm, "Proposed", runs on the A1 state machine (payload arm = "Proposed", so its
    records live under runs/<key>/Proposed/...). B0/A1 are not re-run (user, 2026-09-28,
    option 2): results are compared against eval-b1/eval-b2.

Task sets: --task-set either (default) = the 21 cohort tasks B0 or A1 answered correctly;
--task-set both = the 10 both answered correctly (run proposed-1).
--task-set all = all 100 cohort tasks. --router picks the tier -> model preset (default v2).

  python scripts/proposed_run.py --mode stub --tasks easy1_6 --run-name proposed-stub-1
  python scripts/proposed_run.py --mode live --run-name proposed-2 --budget-usd N

Manifest, resume, budget guard and invalid-row halting follow cohort100_ab_run.py. The budget
check before each row uses the no-retry worst case of THAT row's routed nodes at their own prices.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from cohort100_ab import asl, metrics  # noqa: E402
import cohort100_ab_run as R  # noqa: E402
from proposed import router  # noqa: E402

ARM = "Proposed"
# The 10 tasks both B0 and A1 answered correctly in eval-b1 + eval-b2 (grades.json, 2026-09-27).
BOTH_CORRECT = ["easy1_0", "easy1_46", "easy1_47", "easy1_49", "easy1_6",
                "easy2_14", "easy2_47", "easy2_49", "med1_20", "med3_4"]
# The 21 tasks B0 OR A1 answered correctly (same grades); B0 solved 16, A1 15.
EITHER_CORRECT = ["easy1_0", "easy1_40", "easy1_46", "easy1_47", "easy1_49", "easy1_6", "easy1_9",
                  "easy2_14", "easy2_18", "easy2_4", "easy2_43", "easy2_47", "easy2_49",
                  "med1_20", "med1_26", "med1_3", "med1_32", "med1_6", "med3_19", "med3_4", "med3_43"]
TASK_SETS = {"both": BOTH_CORRECT, "either": EITHER_CORRECT,
             "all": []}  # empty = all 100 cohort tasks in load_tasks' seeded order (as eval-b1/b2)
LAMBDA = {k: R.CONFIG[k] for k in ("lambda_runtime", "lambda_arch", "lambda_memory_mb", "lambda_timeout_s",
                                   "stub_seconds")}
# Per tier: standard (not flex / priority) endpoints; prices = OpenRouter endpoint list, 2026-09-28.
# Per model: provider pin and prices. deepinfra/fp8 = JSON schema + reasoning_effort, 131k max out,
# the endpoint of the 2026-09-23 chemistry-v1 dev test (70/70 units). Sonnet/Gemini = router v1 (3dc73f1).
ENDPOINTS = {
    "openai/gpt-5.2": dict(provider_tag="openai", input_price_per_token=1.75e-6, output_price_per_token=14e-6),
    "deepseek/deepseek-v4.1-flash": dict(provider_tag="deepinfra/fp8", input_price_per_token=0.14e-6,
                                         output_price_per_token=0.42e-6),
    "google/gemini-3.1-pro-preview": dict(provider_tag="google-ai-studio", input_price_per_token=2e-6,
                                          output_price_per_token=12e-6),
    "anthropic/claude-sonnet-4.5": dict(provider_tag="anthropic", input_price_per_token=3e-6,
                                        output_price_per_token=15e-6),
}


def tier_config(preset):
    """{tier: Lambda env config} for one router.MODEL_SETS preset (effort low, 16384 max out everywhere)."""
    out = {}
    for tier, model in router.MODEL_SETS[preset].items():
        e = ENDPOINTS[model]
        out[tier] = dict(LAMBDA, model=model, provider_tag=e["provider_tag"], reasoning_effort="low",
                         max_tokens=16384, input_price_per_token=e["input_price_per_token"],
                         output_price_per_token=e["output_price_per_token"])
    return out


PRESET = "v2"
TIER_CONFIG = tier_config(PRESET)


def use_router(preset):
    """Select the tier -> model preset for this process (main() calls it once, before anything runs)."""
    global PRESET, TIER_CONFIG
    PRESET, TIER_CONFIG = preset, tier_config(preset)
    assert all(TIER_CONFIG["hard"][k] == R.CONFIG[k] for k in TIER_CONFIG["hard"])


# The hard tier must be exactly the baseline's settings, so a hard node is the B0/A1 call.
assert all(TIER_CONFIG["hard"][k] == R.CONFIG[k] for k in TIER_CONFIG["hard"])


def routing(task):
    """{node_id(str): route() decision} for every subtask of a task."""
    models = router.MODEL_SETS[PRESET]
    return {nid: router.route(n["scoped"]["instruction"], models) for nid, n in task["nodes"].items()}


def worst_case_usd(task):
    """One arm, one attempt per node, every call at its own tier's bound."""
    tiers = routing(task)
    return sum(R.call_bound_usd(n, TIER_CONFIG[tiers[nid]["tier"]]) for nid, n in task["nodes"].items())


def create_functions(aws, task, mode, suffix, worker_bytes):
    """One cohort100-ab create_functions call per tier over that tier's nodes (its env = its config)."""
    created, errors = {}, []
    by_tier = {}
    for nid, d in routing(task).items():
        by_tier.setdefault(d["tier"], {})[nid] = task["nodes"][nid]
    for tier, nodes in sorted(by_tier.items()):
        c, e = R.create_functions(aws, dict(task, nodes=nodes), TIER_CONFIG[tier], mode, suffix, worker_bytes)
        created.update(c)
        errors += e
    return created, errors


def definition(row, arns):
    """A1-Parallel, verbatim, with every Task's payload arm set to "Proposed"."""
    d = asl.a1(row, arns)
    d["Comment"] = f"Proposed (A1-Parallel + routing) {row['task_id']}"
    for branch in d["States"]["All_Nodes"]["Branches"]:
        for state in branch["States"].values():
            state["Parameters"]["Payload"]["arm"] = ARM
    return json.dumps(d)


def run_row(aws, task, position, mode, run_name, worker_bytes, run_dir):
    """cohort100_ab_run.run_row for the single Proposed arm. Returns (row record, exception or None)."""
    short, row = task["task_id"].split(":")[-1], task["row"]
    now = datetime.now(timezone.utc)
    run_key = f"{run_name}-{now:%Y%m%dT%H%M%S}"
    decisions = routing(task)
    bound = max(R.call_bound_usd(n, TIER_CONFIG[decisions[nid]["tier"]]) for nid, n in task["nodes"].items())
    fns, versions, running = {}, [], {}
    base = {"task_id": task["task_id"], "run_key": run_key, "position": position, "order": [ARM], "mode": mode,
            "difficulty": row["difficulty"], "template": row["template"], "strata": row["strata"],
            "N": row["N"], "D": row["D"], "W": row["W"], "CP_nodes": row["CP_nodes"],
            "nodes_sha256": R.nodes_sha(task),
            "routing": {nid: {k: d[k] for k in ("property", "tier", "bumps", "model")}
                        for nid, d in sorted(decisions.items(), key=lambda kv: int(kv[0]))}}
    raw = {"task_id": task["task_id"], "run_key": run_key, "warmups": {}, "arms": {}}
    try:
        fns, errors = create_functions(aws, task, mode, f"{now:%d%H%M%S}", worker_bytes)
        if errors:
            raise errors[0]
        base["function_code_sha256"] = {str(nid): sha for nid, (_, _, sha) in sorted(fns.items())}
        names = [name for name, _, _ in fns.values()]
        fetch = lambda: R.all_reports(aws, names)  # noqa: E731
        spec = definition(row, {nid: arn for nid, (_, arn, _) in fns.items()})
        warm_ids = set(R.warm(aws, [arn for _, arn, _ in fns.values()]))
        warm_reps, missing_warm = R.settle(fetch, set(), len(warm_ids), 60, only=warm_ids)
        warmup = {ARM: {"invocations": len(warm_ids), "C_Lambda_usd": metrics.lambda_cost(warm_reps.values()),
                        "init_ms": [r["init_ms"] for r in warm_reps.values()], "missing_reports": missing_warm}}
        raw["warmups"][ARM] = list(warm_reps.values())
        d, events = R.run_arm(aws, aws.out["A1StateMachineArn"], spec, short, ARM, run_key, versions, running)
        reps, shortfall = R.settle(fetch, warm_ids, metrics.reports_expected(events), LAMBDA["lambda_timeout_s"] + 90)
        att = R.attempts(aws, run_key, short, ARM)
        raw["arms"][ARM] = {"execution": d, "events": events, "attempts": att, "reports": list(reps.values())}
        arm = dict(metrics.arm_summary(d, events, att, list(reps.values()), row["final_node"], bound),
                   reports_shortfall=shortfall)
        why = metrics.infrastructure_failure(arm)
        out = dict(base, row_status="invalid" if why else "completed", arms={ARM: arm},
                   executions={ARM: d["executionArn"]}, warmup=warmup, raw=R.save_raw(run_dir, run_key, raw))
        if why:
            out["invalid_reason"] = {ARM: why}
        out["spend_upper_usd"] = (arm["C_LLM_upper_usd"] + arm["C_Lambda_usd"] + arm["C_StepFunctions_usd"]
                                  + warmup[ARM]["C_Lambda_usd"])
        return out, None
    except BaseException as exc:  # includes KeyboardInterrupt: stop, account, then re-raise in main
        R.stop_running(aws, running)
        recovered = {}
        if running:
            try:
                R.abort_waiters(aws, run_key, short, ARM)
            except Exception:
                pass
            try:
                att = R.attempts(aws, run_key, short, ARM)
                known, unknown = metrics.llm_cost(att)
                recovered[ARM] = {"attempts": len(att), "C_LLM_usd": known, "cost_unknown_attempts": unknown}
            except Exception:
                recovered[ARM] = {"attempts": None, "C_LLM_usd": 0.0, "cost_unknown_attempts": None}
        try:
            raw_path = R.save_raw(run_dir, run_key, raw)
        except Exception:
            raw_path = None
        return dict(base, row_status="crashed", error=type(exc).__name__, recovered=recovered,
                    executions=dict(running), raw=raw_path,
                    spend_upper_usd=R.crash_upper_bound(recovered, bound, row["N"]) if running else 0.0), exc
    finally:
        R.teardown(aws, [name for name, _, _ in fns.values()], versions)


def manifest_for(args, tasks, nodes_file_sha, worker_bytes):
    files = [ROOT / "src/cohort100_ab/asl.py", ROOT / "src/cohort100_ab/metrics.py", ROOT / "scripts/cohort100_ab_run.py",
             ROOT / "src/proposed/router.py", Path(__file__)]
    return {"run_name": args.run_name, "mode": args.mode, "source": "cohort", "arm": ARM,
            "router_version": router.VERSION, "router_preset": PRESET, "tier_config": TIER_CONFIG, "prices": metrics.PRICES,
            "nodes_sha256": nodes_file_sha, "tasks": [t["task_id"] for t in tasks],
            "routing": {t["task_id"]: {nid: d["model"] for nid, d in sorted(routing(t).items(), key=lambda kv: int(kv[0]))}
                        for t in tasks},
            "code_sha256": dict({p.name: R.sha_bytes(p.read_bytes()) for p in files},
                                **{R.WORKER.name: R.sha_bytes(worker_bytes)})}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["stub", "live"], required=True)
    ap.add_argument("--tasks", nargs="*", help="cohort task short ids (overrides --task-set)")
    ap.add_argument("--task-set", choices=sorted(TASK_SETS), default="either")
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--router", choices=sorted(router.MODEL_SETS), default="v2",
                    help="tier -> model preset: v1 Sonnet/Gemini/GPT-5.2, v2 DeepSeek/DeepSeek/GPT-5.2, "
                         "v3 DeepSeek/Gemini/GPT-5.2")
    ap.add_argument("--budget-usd", type=float)
    args = ap.parse_args()
    if args.mode == "live" and args.budget_usd is None:
        sys.exit("live mode needs --budget-usd")
    use_router(args.router)
    tasks, nodes_file_sha = R.load_tasks("cohort", args.tasks or TASK_SETS[args.task_set])
    worker_bytes = R.WORKER.read_bytes()  # the only read: every row deploys exactly these bytes
    run_dir = R.DATA / "runs" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_for(args, tasks, nodes_file_sha, worker_bytes)
    mpath = run_dir / "manifest.json"
    if mpath.exists():
        old = json.loads(mpath.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k != "created"} != manifest:
            sys.exit("run configuration differs from the frozen manifest; use a new --run-name")
    else:
        manifest["created"] = datetime.now(timezone.utc).isoformat()
        mpath.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    rows_path = run_dir / "rows.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()] if rows_path.exists() else []
    done_ids = {r["task_id"] for r in rows if r.get("row_status") == "completed"}
    spent = sum(r["spend_upper_usd"] for r in rows)

    aws = R.Aws()
    for position, task in enumerate(tasks):
        if task["task_id"] in done_ids:
            continue
        if args.mode == "live":
            worst = worst_case_usd(task)
            if spent + worst > args.budget_usd:
                print(f"STOP before {task['task_id']}: spent (upper) {spent:.4f} + worst case {worst:.4f} "
                      f"> budget {args.budget_usd}")
                break
        t0 = time.time()
        row, exc = run_row(aws, task, position, args.mode, args.run_name, worker_bytes, run_dir)
        with rows_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        spent += row["spend_upper_usd"]
        if exc is not None:
            print(f"[{position + 1}/{len(tasks)}] {task['task_id']} CRASHED ({type(exc).__name__}); recorded, "
                  f"spend upper ${row['spend_upper_usd']:.4f}; resume re-runs this row")
            raise exc
        if row["row_status"] == "invalid":
            sys.exit(f"[{position + 1}/{len(tasks)}] {task['task_id']} INVALID {row['invalid_reason']}; recorded, "
                     f"run halted; resume re-runs this row")
        a = row["arms"][ARM]
        print(f"[{position + 1}/{len(tasks)}] {task['task_id']} {a['status']} {a['T_E2E_s']:.1f}s | "
              f"LLM ${a['C_LLM_usd']:.4f} | spent (upper) ${spent:.4f} | {time.time() - t0:.0f}s wall")
        flags = {k: a[k] for k in ("retries", "rate_limited_attempts", "truncated_attempts", "cost_unknown_attempts",
                                   "unrecorded_invocations", "reports_shortfall")}
        if any(flags.values()):
            print("   " + ", ".join(f"{k}={v}" for k, v in flags.items() if v))


if __name__ == "__main__":
    main()
