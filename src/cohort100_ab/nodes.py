"""Per-subtask node.json content for the cohort_100 B0/A1 run (offline).

A node's static input is exactly chemistry binding v2's scoped input (verbatim
instruction, task_context, shared_definitions, alias_bindings, binding rule,
output schema); only predecessor_outputs is filled at run time by the worker.

easy2_no_match rule (user-approved 2026-09-25): a subtask flagged `backtrack`
runs once like any other. Its verbatim instruction gets APPENDIX, its output
schema gains a required boolean `no_match`, and its system text names both
fields. Successors receive only the answer.
"""
from copy import deepcopy

POLICY = "easy2_no_match"
APPENDIX = ("Backtracking is unavailable: Subproblem #3 already covers every candidate combination. "
            "If no Mol-3 satisfies the constraint, set no_match to true and select the Mol-3 closest "
            "to the constraint.")
ONE_FIELD = ('Return only a JSON object with exactly one field, "answer", whose value is a nonempty string '
             'containing the answer.')
TWO_FIELDS = ('Return only a JSON object with exactly two fields: "answer", a nonempty string containing '
              'the answer, and "no_match", a boolean.')
NO_MATCH_SCHEMA = {"type": "object",
                   "properties": {"answer": {"type": "string"}, "no_match": {"type": "boolean"}},
                   "required": ["answer", "no_match"], "additionalProperties": False}


def short(task_id):
    return task_id.split(":")[-1]


def function_name(task_id, node_id):
    return f"c100ab-{short(task_id)}-n{node_id}"


def policy_nodes(spec):
    return sorted(k for k, flags in spec["control_flags"].items() if any(f.startswith("backtrack:") for f in flags))


def check_against_dag(spec, row):
    """The spec (from the pinned extractor) and the xlsx-derived DAG must agree exactly."""
    edges = sorted((int(p), int(n["id"])) for n in spec["graph"]["nodes"] for p in n["inputs"])
    ids = sorted(int(n["id"]) for n in spec["graph"]["nodes"])
    if spec["id"] != row["task_id"] or ids != list(range(1, row["N"] + 1)) \
            or edges != sorted(map(tuple, row["edges"])) \
            or spec["graph"]["final_node_ids"] != [str(row["final_node"])]:
        raise ValueError("spec_graph_differs_from_dag:" + spec["id"])


def build(spec, row, scope, system):
    """scope = chemistry_v1.binding.scope; system = chemistry-v1 SYSTEM text. Returns {node_id: node.json}."""
    check_against_dag(spec, row)
    special = policy_nodes(spec)
    if spec["requires_policy"] not in ([], [POLICY]) or bool(special) != bool(spec["requires_policy"]):
        raise ValueError("unexpected_policy:" + spec["id"])
    # APPENDIX names Subproblem #3 / Mol-3: only valid for the easy2 shape the rule was drafted from.
    if special and (spec["template"] != "easy2" or special != ["4"] or "backtrack to Subproblems #1 and #2"
                    not in next(n for n in spec["graph"]["nodes"] if n["id"] == "4")["instruction"]):
        raise ValueError("policy_shape_differs_from_easy2:" + spec["id"])
    if ONE_FIELD not in system:
        raise ValueError("system_text_changed")
    cleared = dict(deepcopy(spec), requires_policy=[])
    fake = {n["id"]: {"status": "succeeded", "output": {"answer": "x"}} for n in spec["graph"]["nodes"]}
    out = {}
    for n in spec["graph"]["nodes"]:
        nid = n["id"]
        scoped = scope(cleared, nid, fake)
        del scoped["predecessor_outputs"]
        node_system = system
        if nid in special:
            scoped["instruction"] = scoped["instruction"] + " " + APPENDIX
            scoped["output_schema"] = deepcopy(NO_MATCH_SCHEMA)
            node_system = system.replace(ONE_FIELD, TWO_FIELDS)
        out[nid] = {
            "task_id": spec["id"], "task_short": short(spec["id"]), "node_id": nid,
            "function_name": function_name(spec["id"], nid),
            "inputs": list(n["inputs"]), "final": nid in spec["graph"]["final_node_ids"],
            "policy": POLICY if nid in special else None,
            "system": node_system, "scoped": scoped,
        }
    return out
