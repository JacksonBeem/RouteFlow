"""Reject unresolved reference slots; apply only explicit, hash-bound repairs."""
import hashlib
import re
from .common import digest, graph_stats
from .longcot_v2 import parse_graph as parse_v2, scoped_input as scope_v2


def reference_slots(text):
    """Return each source reference clause, independently of neighboring clauses."""
    return re.findall(r"\[For this value[^\]]*\]", text, flags=re.I)


def parse_graph(domain, template, prompt, resolution=None):
    graph, issues = parse_v2(domain, template, prompt)
    if graph is None or domain != "math":
        return graph, issues
    if resolution and resolution["prompt_sha256"] != digest(prompt):
        raise ValueError("resolution_prompt_hash_mismatch")
    applied = []
    for node in graph["nodes"]:
        # Inspect EACH clause; another named clause must not hide an unnamed one.
        text = node["instruction"] + "\n" + node.get("variable_bindings", "")
        unnamed = [s for s in reference_slots(text) if not re.search(r"\bnode_\d+\b", s)]
        fixes = []
        for clause in unnamed:
            predecessor, evidence = None, None
            if "dependency_declaration" in node and len(node["inputs"]) == 1:
                predecessor = node["inputs"][0]
                evidence = "single_explicit_header_predecessor"
            elif resolution and node["id"] == resolution["node_id"] and clause == resolution["clause"]:
                predecessor = resolution["predecessor"]
                evidence = resolution["evidence_id"]
                applied.append(node["id"])
            if predecessor is None:
                issues.append("ambiguous_header_reference" if "dependency_declaration" in node and len(node["inputs"]) > 1 else "unnamed_reference_requires_review")
            else:
                fixes.append({"clause": clause, "predecessor": predecessor, "evidence": evidence})
                node["inputs"] = sorted(set(node["inputs"]) | {predecessor})
        if fixes:
            node["reference_resolutions"] = fixes
    if resolution and applied != [resolution["node_id"]]:
        raise ValueError("resolution_not_applied_exactly_once")
    graph["edges"] = sorted({(p, n["id"]) for n in graph["nodes"] for p in n["inputs"]})
    try:
        graph.update(graph_stats([n["id"] for n in graph["nodes"]], graph["edges"]))
    except ValueError as error:
        issues.append(str(error))
        graph.update(depth=None, max_wave_width=None, waves=None)
    # Callers cannot accidentally use a flagged graph as an execution graph.
    graph["reference_audit_issues"] = sorted(set(issues))
    return graph, sorted(set(issues))


def scoped_input(graph, node_id, completed):
    if graph.get("reference_audit_issues"):
        raise ValueError("graph_has_unresolved_reference_or_parse_issues")
    node = next(n for n in graph["nodes"] if n["id"] == node_id)
    result = scope_v2(graph, node_id, completed)
    if node.get("reference_resolutions"):
        result["reference_resolutions"] = node["reference_resolutions"]
    return result
