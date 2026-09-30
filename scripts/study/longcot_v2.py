"""Corrected header-aware LongCoT graph adapter; v1 remains reproducible."""
import re
from .common import graph_stats
from .longcot import parse_graph as parse_v1, scoped_input as scope_v1


def parse_graph(domain, template, prompt):
    graph, issues = parse_v1(domain, template, prompt)
    if domain != "math" or graph is None:
        return graph, issues
    header = prompt.split("Problem node_", 1)[0]
    declarations = list(re.finditer(r"(?m)^(node_\d+):\s*([^\r\n]+)", header))
    if not declarations:
        if any(re.search(r"\[var\d+\]", n["instruction"]) for n in graph["nodes"]):
            issues.append("unbound_variable_placeholder")
        return graph, sorted(set(issues))
    ids = {n["id"] for n in graph["nodes"]}
    by_id = {m[1]: m[2] for m in declarations}
    if len(by_id) != len(declarations):
        issues.append("duplicate_dependency_declaration")
    if set(by_id) != ids:
        issues.append("incomplete_dependency_declarations")
    edges = set()
    for node in graph["nodes"]:
        declaration = by_id.get(node["id"], "")
        dep_text, _, bindings = declaration.partition("Variables:")
        if not re.fullmatch(r"(?:no dependencies\.|depends on node_\d+(?:,\s*node_\d+)*\.)\s*", dep_text):
            issues.append("unsupported_dependency_declaration")
        declared = set(re.findall(r"\bnode_\d+\b", dep_text))
        body_refs = set(node["inputs"])
        binding_refs = set(re.findall(r"\bnode_\d+\b", bindings))
        if (body_refs | binding_refs) - declared:
            issues.append("reference_not_in_declared_dependencies")
        used = set(re.findall(r"\[(var\d+)\]", node["instruction"]))
        assigned = re.findall(r"\b(var\d+)\s*=", bindings)
        if used - set(assigned):
            issues.append("unbound_variable_placeholder")
        if len(assigned) != len(set(assigned)):
            issues.append("duplicate_variable_binding")
        if re.search(r"\b(?:first|second|third|last)\s+(?:pair|triple)\b", bindings, re.I):
            issues.append("unordered_solution_reference")
        # Carry the source expressions verbatim. No gold substitution or eval().
        node["dependency_declaration"] = declaration
        node["variable_bindings"] = bindings.strip()
        node["inputs"] = sorted(declared | body_refs | binding_refs)
        edges.update((p, node["id"]) for p in node["inputs"])
    graph["edges"] = sorted(edges)
    try:
        graph.update(graph_stats([n["id"] for n in graph["nodes"]], graph["edges"]))
    except ValueError as error:
        issues.append(str(error))
        graph.update(depth=None, max_wave_width=None, waves=None)
    return graph, sorted(set(issues))


def scoped_input(graph, node_id, completed):
    result = scope_v1(graph, node_id, completed)
    node = next(n for n in graph["nodes"] if n["id"] == node_id)
    if "variable_bindings" in node:
        result["variable_bindings"] = node["variable_bindings"]
        result["dependency_declaration"] = node["dependency_declaration"]
    return result
