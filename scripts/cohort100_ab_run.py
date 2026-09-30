"""cohort_100 B0/A1 runner: one row at a time, per-subtask Lambdas shared by both workflows.

Per row: create one Lambda per subtask (node_worker.py + that subtask's node.json)
-> publish a B0-Sequential and an A1-Parallel version over those functions -> for each
arm (alternating which goes first by row position): pre-warm every function (init, key
fetch, first S3 connection), run the arm, wait until every invocation it started has
finished -> collect execution times, S3 attempt records, Step Functions history and
Lambda REPORT lines, saved raw to <run dir>/raw/<run key>.json -> tear the functions
down -> append the row to rows.jsonl.

  python scripts/cohort100_ab_run.py --mode stub --source cohort --tasks easy1_6 --run-name stub-1
  python scripts/cohort100_ab_run.py --mode live --source dev --tasks med1_x --run-name dev-1 --budget-usd 2
  python scripts/cohort100_ab_run.py --mode live --source cohort --run-name eval-1 --budget-usd N
  python scripts/cohort100_ab_run.py --mode live --source cohort --batch 1 --run-name eval-b1 --budget-usd N
  python scripts/cohort100_ab_run.py --mode live --source cohort --batch 2 --run-name eval-b2 --budget-usd N \
      --require-code-of eval-b1

The cohort is split in advance into two batches of 50 (batch_assignment): each template's rows
are split in half by a seeded shuffle, so either batch alone covers every template in proportion.
--require-code-of refuses to start unless the code hashes and config equal that run's manifest.

The run configuration is frozen in <run dir>/manifest.json at first launch; a resumed run
must match it exactly and skips rows recorded as completed (a crashed row is recorded with its
recovered spend and re-run). An arm that fails for a harness or account reason (see
metrics.infrastructure_failure: rejected key, missing predecessor, Lambda error, aborted) makes
the row 'invalid': it is recorded, the run halts, and resume re-runs it. Live mode needs
--budget-usd: before each row the runner stops if the spend upper bound so far + that row's
no-retry worst case exceeds it (retries are not in the pre-row bound, so one row can overshoot
it; its actual upper-bound spend is then counted before the next check). Calls whose cost is
unknown (transport failures, invocations without a record) count at their worst case, so the
run never has to halt for them; they are reported per row.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]
from cohort100_ab import asl, metrics  # noqa: E402

DATA = ROOT / "data/cohort100_ab"
WORKER = ROOT / "src/cohort100_ab/node_worker.py"
REGION = "us-east-1"
STACK = "dag-orchestrator-c100ab"
ORDER_SEED = "cohort100-ab-order-v1"
BATCH_SEED = "cohort100-ab-batch-v1"
CONFIG = {
    "model": "openai/gpt-5.2",
    "provider_tag": "openai",          # OpenAI standard tier only: not flex (queueing), not fast (2x price)
    "reasoning_effort": "low",         # user, 2026-09-25
    "max_tokens": 16384,               # provisional until the development pilot; then frozen
    "input_price_per_token": 1.75e-6,  # OpenRouter endpoint list, 2026-09-25
    "output_price_per_token": 14e-6,
    "lambda_runtime": "python3.13", "lambda_arch": "arm64", "lambda_memory_mb": 512,
    "lambda_timeout_s": 900, "stub_seconds": 1.0,
}


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def load_tasks(source, wanted):
    path = DATA / ("nodes.jsonl" if source == "cohort" else "nodes_dev.jsonl")
    tasks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if wanted:
        keys = {t["task_id"].split(":")[-1]: t for t in tasks}
        missing = [w for w in wanted if w not in keys]
        if missing:
            sys.exit(f"unknown tasks for source {source}: {missing}")
        tasks = [keys[w] for w in wanted]
    else:
        random.Random(ORDER_SEED).shuffle(tasks)  # difficulty not confounded with time of day
    return tasks, sha_bytes(path.read_bytes())


def batch_assignment(task_ids, template_of):
    """-> {task_id: 1|2}. Per template (in name order): seeded shuffle of the sorted ids, first half to
    batch 1. Odd-sized templates alternate which batch gets the extra row, so the totals differ by at
    most one. Depends only on the ids and BATCH_SEED, never on run order or results."""
    by_template = {}
    for tid in sorted(task_ids):
        by_template.setdefault(template_of[tid], []).append(tid)
    out, odd = {}, 0
    for template in sorted(by_template):
        ids = by_template[template]
        random.Random(f"{BATCH_SEED}|{template}").shuffle(ids)
        k = len(ids) // 2
        if len(ids) % 2:
            k += odd % 2 == 0
            odd += 1
        out.update({tid: 1 for tid in ids[:k]})
        out.update({tid: 2 for tid in ids[k:]})
    return out


def package(node, worker_bytes):
    """worker_bytes: read once per run and hashed into the manifest, so an edit to the file
    during a run can never reach a later row."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("node_worker.py", (2026, 1, 1, 0, 0, 0)), worker_bytes)
        z.writestr(zipfile.ZipInfo("node.json", (2026, 1, 1, 0, 0, 0)),
                   json.dumps(node, ensure_ascii=False, sort_keys=True).encode())
    return buf.getvalue()


def call_bound_usd(node, cfg):
    """Worst-case LLM cost of one call: input ~ chars/2 tokens (conservative), output at max_tokens."""
    chars = len(json.dumps(node["scoped"], ensure_ascii=False)) + len(node["system"]) + 4096 * len(node["inputs"])
    return chars / 2 * cfg["input_price_per_token"] + cfg["max_tokens"] * cfg["output_price_per_token"]


def worst_case_usd(task, cfg):
    """Both arms, one attempt per node, every call at the bound."""
    return 2 * sum(call_bound_usd(n, cfg) for n in task["nodes"].values())


class Aws:
    def __init__(self):
        import boto3
        from botocore.config import Config
        s = boto3.Session(region_name=REGION)
        cfg = Config(retries={"mode": "standard", "max_attempts": 8})
        self.lam, self.sfn = s.client("lambda", config=cfg), s.client("stepfunctions", config=cfg)
        self.logs, self.s3 = s.client("logs", config=cfg), s.client("s3", config=cfg)
        out = s.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]
        self.out = {o["OutputKey"]: o["OutputValue"] for o in out}


def create_functions(aws, task, cfg, mode, suffix, worker_bytes):
    """One function per subtask. Names carry a per-row-attempt suffix, so a function (or a straggling
    invocation) from an interrupted attempt can never be confused with this attempt's."""
    env = {"BUCKET": aws.out["Bucket"], "OPENROUTER_SECRET": aws.out["OpenRouterSecret"],
           "MODEL": cfg["model"], "PROVIDER_TAG": cfg["provider_tag"], "REASONING_EFFORT": cfg["reasoning_effort"],
           "MAX_TOKENS": str(cfg["max_tokens"]), "PROVIDER_MODE": mode, "STUB_SECONDS": str(cfg["stub_seconds"])}

    def one(node):
        node = dict(node, function_name=f"{node['function_name']}-{suffix}")
        name = node["function_name"]
        for attempt in range(6):  # a just-created role can take a few seconds to be assumable
            try:
                r = aws.lam.create_function(
                    FunctionName=name, Runtime=cfg["lambda_runtime"], Architectures=[cfg["lambda_arch"]],
                    Role=aws.out["NodeRoleArn"], Handler="node_worker.handler", Code={"ZipFile": package(node, worker_bytes)},
                    MemorySize=cfg["lambda_memory_mb"], Timeout=cfg["lambda_timeout_s"],
                    Environment={"Variables": env}, Tags={"project": "dag-orchestrator", "run": "cohort100-ab"})
                break
            except aws.lam.exceptions.InvalidParameterValueException:
                if attempt == 5:
                    raise
                time.sleep(5)
        aws.lam.get_waiter("function_active_v2").wait(FunctionName=name)
        return int(node["node_id"]), (name, r["FunctionArn"], r["CodeSha256"])

    created = {}
    with ThreadPoolExecutor(8) as pool:
        futures = [pool.submit(one, n) for n in task["nodes"].values()]
        errors = []
        for f in futures:
            try:
                nid, pair = f.result()
                created[nid] = pair
            except Exception as exc:
                errors.append(exc)
    return created, errors


def warm(aws, arns):
    """Invokes each function once (Lambda init + key fetch + first S3 connection). Returns request ids."""
    def one(arn):
        r = aws.lam.invoke(FunctionName=arn, Payload=json.dumps({"action": "warmup"}).encode())
        if r.get("FunctionError"):
            raise RuntimeError(f"warmup failed: {arn}")
        return r["ResponseMetadata"]["RequestId"]
    with ThreadPoolExecutor(16) as pool:
        return list(pool.map(one, arns))


def history(aws, arn):
    events = []
    for page in aws.sfn.get_paginator("get_execution_history").paginate(executionArn=arn):
        events += page["events"]
    return events


def run_arm(aws, machine_arn, definition, short, arm, run_key, versions, running):
    version = aws.sfn.update_state_machine(stateMachineArn=machine_arn, definition=definition, publish=True,
                                           versionDescription=f"{run_key} {short}"[:256])["stateMachineVersionArn"]
    versions.append(version)
    tag = hashlib.sha256(f"{run_key}|{short}|{arm}".encode()).hexdigest()[:16]
    ex = aws.sfn.start_execution(stateMachineArn=version, name=f"{short}-{arm}-{run_key}"[:80],
                                 input=json.dumps({"run_id": run_key, "tag": tag}))
    running[arm] = ex["executionArn"]
    while True:
        d = aws.sfn.describe_execution(executionArn=ex["executionArn"])
        if d["status"] != "RUNNING":
            break
        time.sleep(2)
    abort_waiters(aws, run_key, short, arm)
    return d, history(aws, ex["executionArn"])


def abort_waiters(aws, run_key, short, arm):
    """The execution has ended: A1 branches aborted by a failed sibling may still be waiting for
    predecessors; the marker makes them stop (no model call) instead of waiting out WAIT_LIMIT_S."""
    aws.s3.put_object(Bucket=aws.out["Bucket"], Key=f"runs/{run_key}/{arm}/{short}/ABORT", Body=b"{}",
                      ContentType="application/json")


def attempts(aws, run_key, short, arm):
    prefix = f"runs/{run_key}/{arm}/{short}/attempts/"
    out = []
    for page in aws.s3.get_paginator("list_objects_v2").paginate(Bucket=aws.out["Bucket"], Prefix=prefix):
        for obj in page.get("Contents", []):
            out.append(json.loads(aws.s3.get_object(Bucket=aws.out["Bucket"], Key=obj["Key"])["Body"].read()))
    return sorted(out, key=lambda a: a["started_at"])


def all_reports(aws, names):
    found = {}
    for name in names:
        try:
            for page in aws.logs.get_paginator("filter_log_events").paginate(
                    logGroupName=f"/aws/lambda/{name}", filterPattern="REPORT"):
                for e in page["events"]:
                    r = metrics.parse_report(e["message"])
                    if r:
                        found[r["request_id"]] = dict(r, function=name, timestamp=e["timestamp"])
        except aws.logs.exceptions.ResourceNotFoundException:
            pass
    return found


def settle(fetch, claimed, expected, deadline, sleep=time.sleep, clock=time.time, only=None):
    """Waits until `expected` invocations not yet claimed (by a warmup or an earlier arm) have
    finished, one REPORT each. Invocations aborted by a failed Parallel sibling keep running, so
    this barrier runs before the next arm starts and before teardown; attribution is by request
    id, never by time window. only: wait for exactly these request ids (a warmup). fetch() ->
    {request_id: report}. Returns (new reports, shortfall)."""
    t0 = clock()
    while True:
        new = {rid: r for rid, r in fetch().items()
               if rid not in claimed and (only is None or rid in only)}
        if len(new) >= expected or clock() - t0 > deadline:
            return new, max(0, expected - len(new))
        sleep(5)


def stop_running(aws, running):
    for arm, arn in running.items():
        try:
            if aws.sfn.describe_execution(executionArn=arn)["status"] == "RUNNING":
                aws.sfn.stop_execution(executionArn=arn, error="RunnerCrashed", cause="runner exception")
        except Exception as exc:
            print(f"  could not stop {arm} execution: {type(exc).__name__}")


def teardown(aws, names, versions):
    for name in names:
        for call in (lambda: aws.lam.delete_function(FunctionName=name),
                     lambda: aws.logs.delete_log_group(logGroupName=f"/aws/lambda/{name}")):
            try:
                call()
            except Exception as exc:  # already gone is fine; anything else is reported, not fatal
                if "NotFound" not in type(exc).__name__:
                    print(f"  teardown warning {name}: {type(exc).__name__}")
    for v in versions:
        try:
            aws.sfn.delete_state_machine_version(stateMachineVersionArn=v)
        except Exception as exc:
            print(f"  teardown warning {v}: {type(exc).__name__}")


def crash_upper_bound(recovered, bound, n_nodes):
    """Known cost + the call bound for every call that may not have reported (all 2N if S3 was
    unreadable for an arm) + one more call per node for invocations the crash may have left running."""
    unknown = sum(r["cost_unknown_attempts"] if r["cost_unknown_attempts"] is not None else 2 * n_nodes
                  for r in recovered.values())
    return sum(r["C_LLM_usd"] for r in recovered.values()) + bound * (unknown + n_nodes)


def save_raw(run_dir, run_key, raw):
    """Everything the row's metrics were computed from, kept before teardown deletes the log
    groups (Step Functions history also expires): executions, events, attempts, REPORT lines."""
    path = run_dir / "raw" / f"{run_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path.relative_to(run_dir).as_posix()


def nodes_sha(task):
    return sha_bytes(json.dumps(task["nodes"], ensure_ascii=False, sort_keys=True).encode())


def run_row(aws, task, position, cfg, mode, run_name, worker_bytes, run_dir):
    """Returns (row record, exception or None). A crashed row is still recorded (row_status
    'crashed', with the spend recovered from S3) so the budget sees it; it is re-run on resume.
    A row with an infrastructure failure is recorded as 'invalid' (see module docstring)."""
    short, row = task["task_id"].split(":")[-1], task["row"]
    order = ["B0", "A1"] if position % 2 == 0 else ["A1", "B0"]
    now = datetime.now(timezone.utc)
    # A fresh key per row attempt: a rerun of an interrupted row never reads the old attempt's records.
    run_key = f"{run_name}-{now:%Y%m%dT%H%M%S}"
    bound = max(call_bound_usd(n, cfg) for n in task["nodes"].values())
    fns, versions, running, results = {}, [], {}, {}
    base = {"task_id": task["task_id"], "run_key": run_key, "position": position, "order": order, "mode": mode,
            "difficulty": row["difficulty"], "template": row["template"], "strata": row["strata"],
            "N": row["N"], "D": row["D"], "W": row["W"], "CP_nodes": row["CP_nodes"],
            "nodes_sha256": nodes_sha(task)}
    raw = {"task_id": task["task_id"], "run_key": run_key, "warmups": {}, "arms": {}}
    try:
        fns, errors = create_functions(aws, task, cfg, mode, f"{now:%d%H%M%S}", worker_bytes)
        if errors:
            raise errors[0]
        base["function_code_sha256"] = {str(nid): sha for nid, (_, _, sha) in sorted(fns.items())}
        names = [name for name, _, _ in fns.values()]
        fetch = lambda: all_reports(aws, names)  # noqa: E731
        defs = asl.definitions(row, {nid: arn for nid, (_, arn, _) in fns.items()})
        machines = {"B0": aws.out["B0StateMachineArn"], "A1": aws.out["A1StateMachineArn"]}
        claimed, warmups = set(), {}
        for arm in order:
            # Re-warm before each arm: the second arm's functions would otherwise sit idle for the
            # whole first arm, so only the second arm could meet reclaimed (cold) containers.
            warm_ids = set(warm(aws, [arn for _, arn, _ in fns.values()]))
            warm_reps, missing_warm = settle(fetch, claimed, len(warm_ids), 60, only=warm_ids)
            claimed |= warm_ids
            warmups[arm] = {"invocations": len(warm_ids), "C_Lambda_usd": metrics.lambda_cost(warm_reps.values()),
                            "init_ms": [r["init_ms"] for r in warm_reps.values()], "missing_reports": missing_warm}
            raw["warmups"][arm] = list(warm_reps.values())
            d, events = run_arm(aws, machines[arm], defs[arm], short, arm, run_key, versions, running)
            reps, shortfall = settle(fetch, claimed, metrics.reports_expected(events), cfg["lambda_timeout_s"] + 90)
            claimed |= set(reps)
            results[arm] = (d, events, attempts(aws, run_key, short, arm), list(reps.values()), shortfall)
            raw["arms"][arm] = {"execution": d, "events": events, "attempts": results[arm][2],
                                "reports": results[arm][3]}
        arms = {arm: dict(metrics.arm_summary(d, ev, att, reps, row["final_node"], bound), reports_shortfall=shortfall)
                for arm, (d, ev, att, reps, shortfall) in results.items()}
        invalid = {arm: why for arm, a in arms.items() if (why := metrics.infrastructure_failure(a))}
        out = dict(base, row_status="invalid" if invalid else "completed", arms=arms,
                   executions={arm: r[0]["executionArn"] for arm, r in results.items()}, warmup=warmups,
                   raw=save_raw(run_dir, run_key, raw))
        if invalid:
            out["invalid_reason"] = invalid
        out["spend_upper_usd"] = sum(a["C_LLM_upper_usd"] + a["C_Lambda_usd"] + a["C_StepFunctions_usd"]
                                     for a in arms.values()) + sum(w["C_Lambda_usd"] for w in warmups.values())
        if arms["B0"]["status"] == arms["A1"]["status"] == "SUCCEEDED":
            out["speedup"], out["latency_reduction_pct"] = metrics.speedup(arms["B0"]["T_E2E_s"], arms["A1"]["T_E2E_s"])
        return out, None
    except BaseException as exc:  # includes KeyboardInterrupt: stop, account, then re-raise in main
        stop_running(aws, running)
        for arm in running:
            try:
                abort_waiters(aws, run_key, short, arm)
            except Exception:
                pass
        recovered = {}
        for arm in running:
            try:
                att = attempts(aws, run_key, short, arm)
                known, unknown = metrics.llm_cost(att)
                recovered[arm] = {"attempts": len(att), "C_LLM_usd": known, "cost_unknown_attempts": unknown}
            except Exception:
                recovered[arm] = {"attempts": None, "C_LLM_usd": 0.0, "cost_unknown_attempts": None}
        try:
            raw_path = save_raw(run_dir, run_key, raw)
        except Exception:
            raw_path = None
        return dict(base, row_status="crashed", error=type(exc).__name__, recovered=recovered,
                    executions=dict(running), raw=raw_path,
                    spend_upper_usd=crash_upper_bound(recovered, bound, row["N"]) if running else 0.0), exc
    finally:
        teardown(aws, [name for name, _, _ in fns.values()], versions)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["stub", "live"], required=True)
    ap.add_argument("--source", choices=["cohort", "dev"], required=True)
    ap.add_argument("--tasks", nargs="*", help="task short ids (e.g. easy1_6); default = all, seeded order")
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--budget-usd", type=float)
    ap.add_argument("--max-tokens", type=int, help="override CONFIG max_tokens (frozen in the manifest)")
    ap.add_argument("--batch", type=int, choices=[1, 2], help="cohort only: run one pre-assigned batch of 50")
    ap.add_argument("--require-code-of", help="refuse unless code hashes and config equal this run's manifest")
    args = ap.parse_args()
    if args.mode == "live" and args.budget_usd is None:
        sys.exit("live mode needs --budget-usd")
    if args.batch and (args.source != "cohort" or args.tasks):
        sys.exit("--batch needs --source cohort and no --tasks")
    cfg = dict(CONFIG, **({"max_tokens": args.max_tokens} if args.max_tokens else {}))
    tasks, nodes_file_sha = load_tasks(args.source, args.tasks)
    if args.batch:
        batches = batch_assignment([t["task_id"] for t in tasks], {t["task_id"]: t["row"]["template"] for t in tasks})
        tasks = [t for t in tasks if batches[t["task_id"]] == args.batch]  # keeps the seeded run order
    worker_bytes = WORKER.read_bytes()  # the only read: every row deploys exactly these bytes
    run_dir = DATA / "runs" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"run_name": args.run_name, "mode": args.mode, "source": args.source, "config": cfg,
                "prices": metrics.PRICES, "nodes_sha256": nodes_file_sha, "order_seed": ORDER_SEED,
                "batch": args.batch, "batch_seed": BATCH_SEED if args.batch else None,
                "tasks": [t["task_id"] for t in tasks],
                "code_sha256": dict({p.name: sha_bytes(p.read_bytes()) for p in
                                     (ROOT / "src/cohort100_ab/asl.py", ROOT / "src/cohort100_ab/metrics.py",
                                      Path(__file__))}, **{WORKER.name: sha_bytes(worker_bytes)})}
    if args.require_code_of:
        ref = json.loads((DATA / "runs" / args.require_code_of / "manifest.json").read_text(encoding="utf-8"))
        if ref["code_sha256"] != manifest["code_sha256"] or ref["config"] != cfg or ref["prices"] != metrics.PRICES:
            sys.exit(f"code, config or prices differ from {args.require_code_of}; refusing to run")
    mpath = run_dir / "manifest.json"
    if mpath.exists():
        old = json.loads(mpath.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k != "created"} != manifest:
            sys.exit("run configuration differs from the frozen manifest; use a new --run-name")
        manifest = old
    else:
        manifest["created"] = datetime.now(timezone.utc).isoformat()
        mpath.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    rows_path = run_dir / "rows.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()] if rows_path.exists() else []
    done_ids = {r["task_id"] for r in rows if r.get("row_status", "completed") == "completed"}
    # Budget basis: every recorded row, crashed ones included, at its spend upper bound.
    spent = sum(r.get("spend_upper_usd", sum(a["C_Total_usd"] for a in r.get("arms", {}).values())) for r in rows)

    aws = Aws()
    for position, task in enumerate(tasks):
        if task["task_id"] in done_ids:
            continue
        if args.mode == "live":
            worst = worst_case_usd(task, cfg)
            if spent + worst > args.budget_usd:
                print(f"STOP before {task['task_id']}: spent (upper) {spent:.4f} + worst case {worst:.4f} "
                      f"> budget {args.budget_usd}")
                break
        t0 = time.time()
        row, exc = run_row(aws, task, position, cfg, args.mode, args.run_name, worker_bytes, run_dir)
        with rows_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        spent += row["spend_upper_usd"]
        if exc is not None:
            print(f"[{position + 1}/{len(tasks)}] {task['task_id']} CRASHED ({type(exc).__name__}); recorded, "
                  f"spend upper ${row['spend_upper_usd']:.4f}; resume re-runs this row")
            raise exc
        if row["row_status"] == "invalid":
            sys.exit(f"[{position + 1}/{len(tasks)}] {task['task_id']} INVALID {row['invalid_reason']}; recorded, "
                     f"run halted (a harness/account failure would repeat on every row); resume re-runs this row")
        b0, a1 = row["arms"]["B0"], row["arms"]["A1"]
        print(f"[{position + 1}/{len(tasks)}] {task['task_id']} order={'/'.join(row['order'])} "
              f"B0 {b0['status']} {b0['T_E2E_s']:.1f}s | A1 {a1['status']} {a1['T_E2E_s']:.1f}s | "
              f"speedup {row.get('speedup', float('nan')):.2f} | row ${b0['C_Total_usd'] + a1['C_Total_usd']:.4f} "
              f"| spent (upper) ${spent:.4f} | {time.time() - t0:.0f}s wall")
        flags = {k: sum(a[k] for a in (b0, a1)) for k in ("retries", "rate_limited_attempts", "truncated_attempts",
                                                          "cost_unknown_attempts", "unrecorded_invocations",
                                                          "reports_shortfall")}
        if any(flags.values()):
            print("   " + ", ".join(f"{k}={v}" for k, v in flags.items() if v))


if __name__ == "__main__":
    main()
