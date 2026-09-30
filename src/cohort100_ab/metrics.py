"""Methodology metrics (Evaluation Methodology PDF, section 5) from collected run evidence. Pure functions."""
import re

# AWS Price List API, us-east-1, checked 2026-09-25 (list prices, first tier, free tier ignored).
PRICES = {
    "lambda_gb_second_arm64": 0.0000133334,
    "lambda_request": 0.0000002,
    "sfn_standard_transition": 0.000025,
}
REPORT = re.compile(r"REPORT RequestId: (?P<rid>\S+)\s+Duration: (?P<dur>[\d.]+) ms\s+"
                    r"Billed Duration: (?P<billed>\d+) ms\s+Memory Size: (?P<mem>\d+) MB"
                    r"(?:.*?Init Duration: (?P<init>[\d.]+) ms)?")


def parse_report(message):
    m = REPORT.search(message)
    if not m:
        return None
    return {"request_id": m["rid"], "duration_ms": float(m["dur"]), "billed_ms": int(m["billed"]),
            "memory_mb": int(m["mem"]), "init_ms": float(m["init"]) if m["init"] else None}


def lambda_cost(reports):
    gb_s = sum(r["billed_ms"] / 1000 * r["memory_mb"] / 1024 for r in reports)
    return gb_s * PRICES["lambda_gb_second_arm64"] + len(reports) * PRICES["lambda_request"]


def sfn_cost(transitions):
    return transitions * PRICES["sfn_standard_transition"]


def transitions(history_events):
    """Billed Standard transitions: states entered (Parallel branches included) plus one per retry.
    A retry re-schedules the Task without a new StateEntered event, so retries =
    TaskScheduled - TaskStateEntered."""
    types = [e["type"] for e in history_events]
    entered = sum(t.endswith("StateEntered") for t in types)
    retries = max(0, types.count("TaskScheduled") - types.count("TaskStateEntered"))
    return entered + retries


def invocations_started(history_events):
    """Lambda invocations the execution started (one TaskStarted per attempt, aborted ones included)."""
    return sum(e["type"] == "TaskStarted" for e in history_events)


def reports_expected(history_events):
    """Invocations that ran and so will write a REPORT line: every TaskStarted except those that
    failed with a throttle (Lambda rejected the invoke; the function never ran)."""
    throttled = sum(e["type"] == "TaskFailed" and (e.get("taskFailedEventDetails") or {}).get("error")
                    == "Lambda.TooManyRequestsException" for e in history_events)
    return max(0, invocations_started(history_events) - throttled)


# Execution errors that are outcomes of the system under test: the model's output was unusable
# after its retries (OutputInvalid, including truncation), the provider stayed unavailable after
# its retries (ProviderRetryable, e.g. 429s), or the workflow hit its timeout. Any other error
# (ProviderRejected = bad key / no credit, PredecessorMissing, Lambda.*, States.Runtime, an
# aborted execution) is a harness or account failure: the row is invalid and the run halts.
OUTCOME_ERRORS = {"OutputInvalid", "ProviderRetryable", "States.Timeout"}


def infrastructure_failure(summary):
    """None for a valid arm (succeeded, or failed with an outcome error); else a reason string."""
    if summary["status"] == "SUCCEEDED":
        return None
    if summary["status"] == "TIMED_OUT" or summary.get("error") in OUTCOME_ERRORS:
        return None
    return f"{summary['status']}:{summary.get('error')}"


def llm_cost(attempts):
    """Sum of OpenRouter-reported cost over every attempt, failed ones included.
    Returns (usd, unknown_attempts). Error replies without usage (cost_status unbilled_error_reply)
    and A1 waits that ended before any model call (no_call) count as 0; an attempt whose cost is
    unknown is counted, never guessed."""
    known, unknown = 0.0, 0
    for a in attempts:
        if isinstance(a.get("cost_usd"), (int, float)):
            known += a["cost_usd"]
        elif a.get("cost_status") not in ("unbilled_error_reply", "no_call"):
            unknown += 1
    return known, unknown


def arm_summary(execution, history_events, attempts, reports, final_node, call_bound_usd=0.0):
    """call_bound_usd: worst-case LLM cost of one call; each unknown-cost attempt and each
    unrecorded invocation adds it to C_LLM_upper_usd (what the budget guard uses)."""
    t_e2e = (execution["stopDate"] - execution["startDate"]).total_seconds()
    c_llm, unknown = llm_cost(attempts)
    c_lambda, n_trans = lambda_cost(reports), transitions(history_events)
    c_sfn = sfn_cost(n_trans)
    finals = [a for a in attempts if str(a["node_id"]) == str(final_node) and a.get("status") == "succeeded"]
    usage = [a.get("usage") or {} for a in attempts]
    model = [a for a in attempts if a.get("cost_status") != "no_call"]  # invocations that reached the model call
    statuses = {}
    for a in attempts:
        statuses[a.get("status")] = statuses.get(a.get("status"), 0) + 1
    return {
        "status": execution["status"],
        "error": execution.get("error"), "cause": (execution.get("cause") or "")[:500] or None,
        "attempt_statuses": statuses,
        "T_E2E_s": t_e2e,
        # Work time: inputs available -> done (excludes A1's dependency wait; B0's is an S3 read).
        "node_seconds": {str(a["node_id"]): a["finished_at"] - a["inputs_read_at"]
                         for a in attempts if a.get("status") == "succeeded"},
        # Time spent waiting for predecessors (A1), billed as Lambda duration; includes waits that
        # ended without a model call.
        "wait_seconds": sum((a["finished_at"] if a.get("inputs_read_at") is None else a["inputs_read_at"])
                            - a["started_at"] for a in attempts),
        "attempts": len(model),
        "failed_attempts": sum(a.get("status") != "succeeded" for a in model),
        "wait_expired": sum(a.get("status") == "wait_expired" for a in attempts),
        "aborted_waiting": sum(a.get("status") == "aborted_waiting" for a in attempts),
        "input_tokens": sum(u.get("prompt_tokens") or 0 for u in usage),
        "output_tokens": sum(u.get("completion_tokens") or 0 for u in usage),
        "reasoning_tokens": sum((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0 for u in usage),
        "max_completion_tokens": max((u.get("completion_tokens") or 0 for u in usage), default=0),
        "cached_tokens": sum((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for u in usage),
        "served_models": sorted({a["served_model"] for a in attempts if a.get("served_model")}),
        "served_providers": sorted({a["served_provider"] for a in attempts if a.get("served_provider")}),
        "C_LLM_usd": c_llm, "cost_unknown_attempts": unknown,
        "lambda_invocations": len(reports), "lambda_billed_ms": sum(r["billed_ms"] for r in reports),
        "cold_starts": sum(r["init_ms"] is not None for r in reports),
        # invocations that billed but left no attempt record (e.g. killed at the Lambda timeout)
        "unrecorded_invocations": max(0, len(reports) - len(attempts)),
        "invocations_started": invocations_started(history_events),
        # Model-call retries (provider/output); dependency-wait re-invocations are in wait_expired.
        "retries": max(0, len(model) - len({str(a["node_id"]) for a in model})),
        "rate_limited_attempts": sum(a.get("http_status") == 429 or (isinstance(a.get("error"), dict)
                                     and a["error"].get("code") == 429) for a in attempts),
        "truncated_attempts": sum(a.get("finish_reason") == "length" for a in attempts),
        "C_Lambda_usd": c_lambda, "transitions": n_trans, "C_StepFunctions_usd": c_sfn,
        "C_Total_usd": c_llm + c_lambda + c_sfn,
        "C_LLM_upper_usd": c_llm + call_bound_usd * (unknown + max(0, len(reports) - len(attempts))),
        # C_Total_usd counts unknown-cost calls as 0 (a lower bound); this one charges them at the bound.
        "C_Total_upper_usd": c_llm + call_bound_usd * (unknown + max(0, len(reports) - len(attempts)))
        + c_lambda + c_sfn,
        "final_answer": finals[-1]["answer"] if finals else None,
        "no_match": {str(a["node_id"]): a.get("no_match") for a in attempts
                     if a.get("status") == "succeeded" and a.get("no_match") is not None},
    }


def speedup(t_b0, t_cfg):
    return t_b0 / t_cfg, (t_b0 - t_cfg) / t_b0 * 100
