"""Development-only completed-output boundary; no model, grader, or S3 access.

This local adapter is not wired to Lambda or selected by the active protocol.
It wraps the frozen v3 reference-expression adapter without editing that code.
"""
from .longcot import aggregate
from .longcot_v3 import scoped_input

VERSION = "development-scoping-v1"


def _graph(task, partition):
    if partition != "development":
        raise ValueError("development_partition_required")
    if not task.get("fixed_graph_eligible") or task.get("domain") not in {"math", "chemistry"}:
        raise ValueError("unsupported_development_task")
    graph = task["fixed_graph"]
    if task.get("exclusions") or graph.get("reference_audit_issues"):
        raise ValueError("unresolved_task_references")
    return graph


def _completed_outputs(node_ids, records):
    outputs, pointers = {}, {}
    for node in node_ids:
        record = records.get(node)
        if not record or record.get("status") != "succeeded":
            raise ValueError("unfinished_or_failed_predecessor:" + node)
        if record.get("node_id") != node or not isinstance(record.get("output_id"), str) or not record["output_id"]:
            raise ValueError("invalid_predecessor_identity:" + node)
        output = record.get("output")
        if not isinstance(output, dict) or set(output) != {"answer"} or not isinstance(output["answer"], str) or not output["answer"].strip():
            raise ValueError("invalid_predecessor_output:" + node)
        outputs[node] = dict(output)
        pointers[node] = record["output_id"]
    if len(set(pointers.values())) != len(pointers):
        raise ValueError("duplicate_predecessor_output_id")
    return outputs, pointers


def scope(task, partition, node_id, records):
    graph = _graph(task, partition)
    node = next((node for node in graph["nodes"] if node["id"] == node_id), None)
    if node is None:
        raise ValueError("unknown_node")
    outputs, pointers = _completed_outputs(node["inputs"], records)
    result = scoped_input(graph, node_id, outputs)
    result["predecessor_output_ids"] = pointers
    result["scoping_version"] = VERSION
    return result


def finish(task, partition, records):
    graph = _graph(task, partition)
    outputs, _ = _completed_outputs([node["id"] for node in graph["nodes"]], records)
    return aggregate(graph, outputs)
