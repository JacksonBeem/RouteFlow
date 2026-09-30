"""Step Functions definitions for one cohort row: B0-Sequential and A1-Parallel.

Both definitions invoke the same per-subtask functions with the same Retry
policy; they differ only in schedule:
  B0: one Task per subtask, chained in b0_order (waves flattened).
  A1: one Parallel state with a branch per subtask, all started at once; each
      subtask's function waits (payload "wait": true) until its own predecessors
      have succeeded, so a subtask runs as soon as its dependencies are done
      (per-node dependency scheduling, PDF section 2), not at a wave barrier.
Node answers travel through S3, never through the state payload (ResultPath null).
"""
import json

RETRY = [
    {"ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException",
                     "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
     "IntervalSeconds": 2, "MaxAttempts": 3, "BackoffRate": 2.0},
    {"ErrorEquals": ["ProviderRetryable"], "IntervalSeconds": 5, "MaxAttempts": 2, "BackoffRate": 2.0},
    {"ErrorEquals": ["OutputInvalid"], "IntervalSeconds": 1, "MaxAttempts": 1},
    # A1 only in practice (B0 never waits): a fresh invocation continues the wait.
    {"ErrorEquals": ["PredecessorWait"], "IntervalSeconds": 1, "MaxAttempts": 3, "BackoffRate": 1.0},
]
# 3 h, far above any expected B0 (16 nodes x ~1 min): a 1 h cap could only ever cut off B0,
# censoring exactly the rows with the largest speedups.
TIMEOUT_SECONDS = 10800


def task(function_arn, arm, next_state=None, wait=False):
    payload = {"run_id.$": "$.run_id", "arm": arm, "tag.$": "$.tag"}
    if wait:
        payload["wait"] = True
    state = {
        "Type": "Task",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {"FunctionName": function_arn, "Payload": payload},
        "ResultPath": None,
        "Retry": RETRY,
    }
    if next_state:
        state["Next"] = next_state
    else:
        state["End"] = True
    return state


def b0(row, arns):
    order = row["b0_order"]
    states = {f"Node_{n}": task(arns[n], "B0", f"Node_{order[i + 1]}" if i + 1 < len(order) else None)
              for i, n in enumerate(order)}
    return {"Comment": f"B0-Sequential {row['task_id']}", "StartAt": f"Node_{order[0]}",
            "TimeoutSeconds": TIMEOUT_SECONDS, "States": states}


def a1(row, arns):
    branches = [{"StartAt": f"Node_{n}", "States": {f"Node_{n}": task(arns[n], "A1", wait=True)}}
                for n in row["b0_order"]]
    return {"Comment": f"A1-Parallel {row['task_id']}", "StartAt": "All_Nodes", "TimeoutSeconds": TIMEOUT_SECONDS,
            "States": {"All_Nodes": {"Type": "Parallel", "Branches": branches, "ResultPath": None, "End": True}}}


def definitions(row, arns):
    """arns: {node_id(int): function ARN}. Returns {"B0": json, "A1": json}."""
    if set(arns) != set(row["b0_order"]):
        raise ValueError("function set != subtask set")
    return {"B0": json.dumps(b0(row, arns)), "A1": json.dumps(a1(row, arns))}
