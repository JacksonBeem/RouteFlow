"""T_dec for cohort100-ab: one decomposition call per task, same model and settings as the arms.

The PDF's end-to-end latency starts at task submission, which includes decomposing the task
into subtasks. In cohort100-ab the DAGs come from the dataset, so no decomposition runs inside
the timed executions (T_exec). This script measures what that step would cost: the original
task prompt goes to the arms' model (same provider pin, reasoning effort and max_tokens) with
an instruction to list the subtasks and their dependencies, and the call's wall-clock latency
is T_dec. Decomposition is identical for B0 and A1, so it adds the same T_dec to both:
speedup_E2E = (T_B0 + T_dec) / (T_A1 + T_dec).

The returned DAG is scored against the dataset DAG (exact edge-set match, edge precision and
recall) only to show whether the call did the job it is timed for; it is never used by a run.
Calls run one at a time from this machine (not a Lambda): latency includes this machine's
network round trip, a few tens of ms against calls of seconds.

  python scripts/cohort100_ab_decompose.py --source dev --tasks hard1_10 med3_21 --run-name dec-dev-1 --budget-usd 1
  python scripts/cohort100_ab_decompose.py --source cohort --run-name dec-1 --budget-usd 5

Resumable: rows with status ok are skipped; failed rows are retried. Prints counts, latency and
cost only, never prompt text.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts")]
import cohort100_ab_run as R  # noqa: E402

TASKS = ROOT / "data/study_v1/normalized/longcot/tasks.v3.jsonl"
OUT = ROOT / "data/cohort100_ab/decompose"
URL = "https://openrouter.ai/api/v1/chat/completions"
SYSTEM = ("You are the decomposition step of a multi-LLM workflow. Split the task below into the "
          "subtasks it consists of, numbered from 1, and for each subtask list the ids of the subtasks "
          "whose outputs it needs. Do not solve anything. Return only the JSON object.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["subtasks"], "properties": {
    "subtasks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "depends_on"],
        "properties": {"id": {"type": "integer"}, "depends_on": {"type": "array", "items": {"type": "integer"}}}}}}}


def load_prompts(ids):
    out = {}
    for line in TASKS.read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        if t["id"] in ids:
            # Study convention: prompt_sha256 = sha256 of the JSON-encoded string (ensure_ascii=False).
            if hashlib.sha256(json.dumps(t["prompt"], ensure_ascii=False).encode("utf-8")).hexdigest() != t["prompt_sha256"]:
                sys.exit(f"prompt hash mismatch: {t['id']}")
            out[t["id"]] = t["prompt"]
    if set(out) != set(ids):
        sys.exit(f"missing prompts: {sorted(set(ids) - set(out))}")
    return out


def request_body(prompt, cfg):
    return {"model": cfg["model"], "max_tokens": cfg["max_tokens"], "stream": False,
            "reasoning": {"effort": cfg["reasoning_effort"]},
            "provider": {"only": [cfg["provider_tag"]], "allow_fallbacks": False, "require_parameters": True},
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "decomposition", "strict": True, "schema": SCHEMA}},
            "usage": {"include": True},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}


def call(body, key, timeout=600):
    """-> (http status or None, payload dict, latency seconds). Latency covers request to full body."""
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
        "X-Title": "dag-orchestrator cohort100-ab decompose"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        latency = time.perf_counter() - t0
        try:
            return exc.code, json.loads(exc.read()), latency
        except Exception:
            return exc.code, {}, latency
    except Exception as exc:
        return None, {"transport_error": type(exc).__name__}, time.perf_counter() - t0
    latency = time.perf_counter() - t0
    try:
        return status, json.loads(raw), latency
    except ValueError:
        return None, {"transport_error": f"non_json_{status}"}, latency


def parse_edges(content):
    """-> sorted [(pred, node)] or None if the reply is not a valid decomposition."""
    try:
        value = json.loads(content)
        subtasks = value["subtasks"]
        ids = {int(s["id"]) for s in subtasks}
        edges = sorted({(int(p), int(s["id"])) for s in subtasks for p in s["depends_on"]})
    except (TypeError, ValueError, KeyError):
        return None
    if not subtasks or any(p not in ids for p, _ in edges):
        return None
    return edges


def score(predicted, reference):
    ref, pred = set(map(tuple, reference)), set(predicted)
    hit = len(ref & pred)
    return {"exact": pred == ref, "edge_precision": hit / len(pred) if pred else (1.0 if not ref else 0.0),
            "edge_recall": hit / len(ref) if ref else 1.0}


def call_bound_usd(prompt, cfg):
    return (len(prompt) + len(SYSTEM)) / 2 * cfg["input_price_per_token"] + cfg["max_tokens"] * cfg["output_price_per_token"]


def openrouter_key():
    import boto3
    s = boto3.Session(region_name=R.REGION)
    out = {o["OutputKey"]: o["OutputValue"] for o in
           s.client("cloudformation").describe_stacks(StackName=R.STACK)["Stacks"][0]["Outputs"]}
    value = s.client("secretsmanager").get_secret_value(SecretId=out["OpenRouterSecret"])["SecretString"].strip()
    if value.startswith("{"):
        found = [v.strip() for v in json.loads(value).values() if isinstance(v, str) and v.strip().startswith("sk-or-")]
        if len(found) != 1:
            sys.exit("secret must contain exactly one OpenRouter key")
        value = found[0]
    return value


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["cohort", "dev"], required=True)
    ap.add_argument("--tasks", nargs="*")
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--budget-usd", type=float, required=True)
    args = ap.parse_args()
    cfg = R.CONFIG
    tasks, _ = R.load_tasks(args.source, args.tasks)
    prompts = load_prompts({t["task_id"] for t in tasks})
    run_dir = OUT / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"run_name": args.run_name, "source": args.source, "tasks": [t["task_id"] for t in tasks],
                "config": {k: cfg[k] for k in ("model", "provider_tag", "reasoning_effort", "max_tokens",
                                               "input_price_per_token", "output_price_per_token")},
                "system_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
                "schema_sha256": hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode()).hexdigest(),
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    mpath = run_dir / "manifest.json"
    if mpath.exists():
        old = json.loads(mpath.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k != "created"} != manifest:
            sys.exit("configuration differs from the frozen manifest; use a new --run-name")
    else:
        manifest["created"] = datetime.now(timezone.utc).isoformat()
        mpath.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    rows_path = run_dir / "rows.jsonl"
    rows = [json.loads(x) for x in rows_path.read_text(encoding="utf-8").splitlines()] if rows_path.exists() else []
    done = {r["task_id"] for r in rows if r["status"] == "ok"}
    spent = sum(r["cost_usd"] if isinstance(r.get("cost_usd"), (int, float)) else r["call_bound_usd"] for r in rows)
    key = openrouter_key()
    for i, task in enumerate(tasks):
        tid = task["task_id"]
        if tid in done:
            continue
        bound = call_bound_usd(prompts[tid], cfg)
        if spent + bound > args.budget_usd:
            print(f"STOP before {tid}: spent {spent:.4f} + call bound {bound:.4f} > budget {args.budget_usd}")
            break
        status, payload, latency = call(request_body(prompts[tid], cfg), key)
        usage = payload.get("usage") or {}
        choice = (payload.get("choices") or [{}])[0]
        edges = parse_edges((choice.get("message") or {}).get("content")) if status == 200 else None
        row = {"task_id": tid, "template": task["row"]["template"], "N": task["row"]["N"],
               "status": "ok" if edges is not None else "failed", "T_dec_s": latency, "http_status": status,
               "finish_reason": choice.get("finish_reason"), "served_model": payload.get("model"),
               "served_provider": payload.get("provider"), "usage": usage, "cost_usd": usage.get("cost"),
               "call_bound_usd": bound, "error": payload.get("error") or payload.get("transport_error"),
               "at": datetime.now(timezone.utc).isoformat()}
        if edges is not None:
            row.update(predicted_edges=edges, **score(edges, task["row"]["edges"]))
        with rows_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        spent += row["cost_usd"] if isinstance(row["cost_usd"], (int, float)) else bound
        print(f"[{i + 1}/{len(tasks)}] {tid.split(':')[-1]} {row['status']} T_dec {latency:.1f}s "
              f"out {usage.get('completion_tokens')} exact {row.get('exact')} | spent {spent:.4f}")


if __name__ == "__main__":
    main()
