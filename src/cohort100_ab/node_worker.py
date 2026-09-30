"""One subtask Lambda of the cohort_100 B0/A1 run.

Every subtask is deployed as its own function; the package is this file plus
node.json (the subtask's static scoped input, built offline from the chemistry
spec). B0-Sequential and A1-Parallel invoke the same functions, so the code,
prompt, model and settings are identical in both arms; only the schedule differs.

Event:  {"run_id", "arm": "B0"|"A1", "tag", "wait"?}  or  {"action": "warmup"}
Reads predecessor answers from S3, calls OpenRouter once, writes one attempt
record per invocation and (on success) the node record the successors read.
With "wait" (A1: every node starts at once), the node first polls S3 until each of
its own predecessors has succeeded, so it runs as soon as its dependencies are
done (per-node dependency scheduling, PDF section 2), not after a whole wave. It
stops waiting if the runner has written the arm's ABORT marker (the execution
ended, e.g. a sibling branch failed) or after WAIT_LIMIT_S (PredecessorWait,
retried). Raises ProviderRetryable / OutputInvalid (retried by Step Functions) or
ProviderRejected (not retried); every attempt's usage and cost is persisted before raising.
Zero dependencies beyond the Lambda runtime (boto3, urllib).
"""
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

import boto3
from botocore.config import Config

NODE = json.loads((Path(__file__).parent / "node.json").read_text(encoding="utf-8"))
# Module-level client: created during init; the warmup also fetches the key and opens S3.
S3 = boto3.client("s3", config=Config(connect_timeout=2, read_timeout=10,
                                      retries={"mode": "standard", "total_max_attempts": 4}))
URL = "https://openrouter.ai/api/v1/chat/completions"
FENCE = re.compile(r"\A\s*```(?:json)?[ \t]*\n(.*?)\n?```\s*\Z", re.S)
RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
REJECTED_HTTP = {401, 402, 403}  # bad/expired key, out of credit, forbidden: retrying cannot help
POLL_S = 0.2           # predecessor poll interval (one S3 GET per still-missing predecessor)
ABORT_CHECK_S = 1.0    # ABORT marker poll interval
WAIT_LIMIT_S = 600     # per invocation; leaves >= 280 s of the 900 s timeout for the model call
_KEY = None


class ProviderRetryable(Exception):
    pass


class OutputInvalid(Exception):
    pass


class ProviderRejected(Exception):
    """Not in any Retry list: the workflow fails at once."""


class PredecessorMissing(Exception):
    pass


class PredecessorWait(Exception):
    """Waited WAIT_LIMIT_S without all predecessors: retried (a fresh invocation keeps waiting)."""


class Aborted(Exception):
    """The execution already ended; this waiting branch must not call the model."""


def dumps(value):
    # Same serialization as the chemistry-v1 runtime (smoke.runtime.dumps).
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def key():
    global _KEY
    if _KEY is None:
        sm = boto3.client("secretsmanager", config=Config(connect_timeout=2, read_timeout=5))
        value = sm.get_secret_value(SecretId=os.environ["OPENROUTER_SECRET"])["SecretString"].strip()
        if value.startswith("{"):
            found = [v.strip() for v in json.loads(value).values()
                     if isinstance(v, str) and v.strip().startswith("sk-or-")]
            if len(found) != 1:
                raise ValueError("secret_must_contain_one_openrouter_key")
            value = found[0]
        _KEY = value
    return _KEY


def prefix(run_id, arm):
    return f"runs/{run_id}/{arm}/{NODE['task_short']}"


def put(k, value):
    S3.put_object(Bucket=os.environ["BUCKET"], Key=k, Body=dumps(value).encode(),
                  ContentType="application/json")


def read_record(k):
    try:
        return json.loads(S3.get_object(Bucket=os.environ["BUCKET"], Key=k)["Body"].read())
    except S3.exceptions.NoSuchKey:
        return None


def wait_for_predecessors(run_id, arm, sleep=time.sleep, clock=time.time):
    """Polls until every predecessor's node record exists. Node records are written only on
    success, so presence == succeeded. Returns their answers."""
    base, out = prefix(run_id, arm), {}
    t0 = last_abort_check = clock()
    while True:
        for nid in NODE["inputs"]:
            if nid not in out:
                rec = read_record(f"{base}/{nid}.json")
                if rec is not None:
                    out[nid] = rec["answer"]
        if len(out) == len(NODE["inputs"]):
            return out
        now = clock()
        if now - last_abort_check >= ABORT_CHECK_S:
            last_abort_check = now
            if read_record(f"{base}/ABORT") is not None:
                raise Aborted()
        if now - t0 >= WAIT_LIMIT_S:
            raise PredecessorWait()
        sleep(POLL_S)


def predecessor_outputs(run_id, arm):
    out = {}
    for nid in NODE["inputs"]:
        try:
            body = S3.get_object(Bucket=os.environ["BUCKET"], Key=f"{prefix(run_id, arm)}/{nid}.json")["Body"].read()
        except S3.exceptions.NoSuchKey:
            raise PredecessorMissing(nid)
        rec = json.loads(body)
        if rec.get("status") != "succeeded":
            raise PredecessorMissing(nid)
        out[nid] = rec["answer"]
    return out


def request_body(scoped, tag):
    env = os.environ
    return {
        "model": env["MODEL"],
        "max_tokens": int(env["MAX_TOKENS"]),
        "stream": False,
        "reasoning": {"effort": env["REASONING_EFFORT"]},
        "provider": {"only": [env["PROVIDER_TAG"]], "allow_fallbacks": False, "require_parameters": True},
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "subproblem_answer", "strict": True, "schema": NODE["scoped"]["output_schema"]}},
        "usage": {"include": True},
        # The tag opens the prompt so a node's B0 and A1 calls never share a provider prefix cache.
        "messages": [{"role": "system", "content": f"[unit {tag}]\n" + NODE["system"]},
                     {"role": "user", "content": dumps(scoped)}],
    }


def parse(content):
    """Strict: a JSON object matching the node's output schema with a nonempty answer."""
    if not isinstance(content, str):
        raise OutputInvalid("no_content")
    m = FENCE.match(content)
    try:
        value = json.loads(m.group(1) if m else content)
    except ValueError:
        raise OutputInvalid("not_json")
    schema = NODE["scoped"]["output_schema"]
    if not isinstance(value, dict) or set(value) != set(schema["required"]):
        raise OutputInvalid("schema_fields")
    if not isinstance(value["answer"], str) or not value["answer"].strip():
        raise OutputInvalid("empty_answer")
    if "no_match" in value and not isinstance(value["no_match"], bool):
        raise OutputInvalid("no_match_type")
    return value["answer"].strip(), value.get("no_match")


def call_provider(body, timeout):
    """Returns (http_status, payload). Any failure before a parsed reply is a transport error
    (status None): the request may already have been billed, so its cost is unknown."""
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + key(), "Content-Type": "application/json",
        "X-Title": "dag-orchestrator cohort100-ab"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except Exception:  # unreadable or non-JSON error body: the status alone classifies it
            payload = None
        return exc.code, payload
    except Exception as exc:  # URLError, timeouts, IncompleteRead/HTTPException, resets
        return None, {"transport_error": type(exc).__name__}
    try:
        return status, json.loads(raw)
    except ValueError:
        return None, {"transport_error": f"non_json_{status}"}


def stub_response(scoped):
    time.sleep(float(os.environ.get("STUB_SECONDS", "1.0")))
    fields = {"answer": f"stub-{NODE['node_id']}"}
    if "no_match" in scoped["output_schema"]["required"]:
        fields["no_match"] = False
    return 200, {"id": "stub", "model": "stub", "provider": "stub",
                 "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(fields)}}],
                 "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0}}


def warmup():
    """Pays every one-time cost of a real call except the model call itself: Lambda init (module
    load, S3 client), the Secrets Manager client + key fetch, and the first S3 connection."""
    t0 = time.time()
    if os.environ.get("PROVIDER_MODE") != "stub":
        key()
    S3.list_objects_v2(Bucket=os.environ["BUCKET"], Prefix="runs/", MaxKeys=1)
    return {"warm": True, "node": NODE["node_id"], "seconds": time.time() - t0}


def classify(status, payload):
    """-> (record status, exception class or None, cost_status).
    cost_status: reported (usage.cost present) | unbilled_error_reply (OpenRouter error reply
    without usage; assumed not charged) | unknown (transport failure or 200 without a cost)."""
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = err.get("code") if isinstance(err.get("code"), int) else status
    cost = "reported" if isinstance((payload.get("usage") or {}).get("cost"), (int, float)) else None
    if status is None:
        return "retryable_error", ProviderRetryable, cost or "unknown"
    if status == 200 and "choices" in payload:
        return None, None, cost or "unknown"
    if code in REJECTED_HTTP:
        return "rejected", ProviderRejected, cost or "unbilled_error_reply"
    if code in RETRYABLE_HTTP:
        return "retryable_error", ProviderRetryable, cost or "unbilled_error_reply"
    return "provider_error", OutputInvalid, cost or "unbilled_error_reply"


def handler(event, context):
    if event.get("action") == "warmup":
        return warmup()
    run_id, arm, tag = event["run_id"], event["arm"], event["tag"]
    request_id = getattr(context, "aws_request_id", "local")
    base = prefix(run_id, arm)
    record = {"task_id": NODE["task_id"], "node_id": NODE["node_id"], "arm": arm, "request_id": request_id,
              "started_at": time.time()}
    if event.get("wait"):
        try:
            inputs = wait_for_predecessors(run_id, arm)
        except (PredecessorWait, Aborted) as exc:
            # No model call was made: recorded so every invocation has a record, at no LLM cost.
            record.update(status="wait_expired" if isinstance(exc, PredecessorWait) else "aborted_waiting",
                          cost_status="no_call", cost_usd=None, usage={}, finished_at=time.time())
            put(f"{base}/attempts/{NODE['node_id']}/{request_id}.json", record)
            raise
    else:
        inputs = predecessor_outputs(run_id, arm)
    scoped = dict(NODE["scoped"], predecessor_outputs=inputs)
    record["inputs_read_at"] = time.time()
    body = request_body(scoped, tag)
    remaining = context.get_remaining_time_in_millis() / 1000 if context else 900
    t0 = time.time()
    if os.environ.get("PROVIDER_MODE") == "stub":
        status, payload = stub_response(scoped)
    else:
        status, payload = call_provider(body, timeout=max(10, remaining - 20))
    record.update(provider_started_at=t0, provider_finished_at=time.time(), http_status=status)
    payload = payload if isinstance(payload, dict) else {}
    usage = payload.get("usage") or {}
    choice = (payload.get("choices") or [{}])[0]
    failed_status, failure, cost_status = classify(status, payload)
    record.update(
        generation_id=payload.get("id"), served_model=payload.get("model"), served_provider=payload.get("provider"),
        finish_reason=choice.get("finish_reason"), usage=usage, cost_usd=usage.get("cost"), cost_status=cost_status,
        error=payload.get("error") or payload.get("transport_error"))
    try:
        if failure:
            record["status"] = failed_status
            raise failure(f"http_{status}")
        answer, no_match = parse((choice.get("message") or {}).get("content"))
        record.update(status="succeeded", answer=answer, no_match=no_match)
    except (ProviderRetryable, ProviderRejected, OutputInvalid) as exc:
        record.setdefault("status", "invalid_output")
        record["failure"] = str(exc)
        record["finished_at"] = time.time()
        put(f"{base}/attempts/{NODE['node_id']}/{request_id}.json", record)
        raise
    record["finished_at"] = time.time()
    put(f"{base}/attempts/{NODE['node_id']}/{request_id}.json", record)
    put(f"{base}/{NODE['node_id']}.json", record)
    return {"node": NODE["node_id"], "status": "succeeded"}
