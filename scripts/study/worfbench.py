"""Strict WorFBench task/reference separation with explicit parse exclusions."""
import ast
from collections import Counter
import re
from .common import STUDY, ROOT, digest, file_hash, graph_stats, jsonl, read, write


def parse_task(text):
    marker = re.search(r"(?:The action list you can take:|api_list\s*:|The tool list you can select from:|The api list you can use:|The database tables and corresponding fields you can operate are:)", text)
    if not marker:
        raise ValueError("allowed_action_marker_unrecognized")
    prefix = text[:marker.start()]
    task = re.sub(r"^.*?Task:\s*", "", prefix, count=1, flags=re.S).strip()
    actions_text = text[marker.end():].strip()
    if not actions_text.startswith(("[", "{")):
        return task, {"format": "verbatim_action_description", "content": actions_text}
    try:
        actions = ast.literal_eval(actions_text)
    except (SyntaxError, ValueError):
        raise ValueError("allowed_actions_parse_failure") from None
    if not isinstance(actions, (list, dict)):
        raise ValueError("allowed_actions_not_list_or_dict")
    return task, actions


def parse_reference(text):
    nodes, edges, issues = {}, [], []
    # Strict recognized layout: numbered node lines, optional continuation lines,
    # explicit edge tuples. Unknown nonblank lines are retained and flagged.
    active = None
    edge_mode = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"(?:\*\*)?(?:Nodes?|Graph|Edges?)\s*:(?:\*\*)?", line, re.I):
            edge_mode = line.lower().strip('*').startswith(('edge', 'graph'))
            active = None
            continue
        match = re.match(r"(\d+)\s*[:.]\s*(.+)", line)
        if match and not edge_mode and not match[2].startswith("("):
            if match[1] in nodes:
                issues.append("duplicate_node_id")
            nodes[match[1]] = match[2]
            active = match[1]
            continue
        tuples = re.findall(r"\(\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*\)", line)
        if tuples and (edge_mode or re.match(r"(?:Edges?\s*:|[-*]?\s*\()", line, re.I)):
            edge_mode = True
            active = None
            edges.extend(tuples)
            residue = re.sub(r"\([^()]*\)", "", re.sub(r"^Edges?\s*:", "", line, flags=re.I)).strip(" -*,;")
            if residue:
                issues.append("unparsed_edge_text")
            continue
        if active and not edge_mode:
            nodes[active] += "\n" + raw
        else:
            issues.append("unrecognized_reference_line")
    if not nodes or not edges:
        issues.append("missing_nodes_or_edges")
    if len(set(edges)) != len(edges):
        issues.append("duplicate_edge")
    ids = {"START", "END", *nodes}
    if any(a not in ids or b not in ids for a, b in edges):
        issues.append("unresolved_edge")
    if any(b == "START" or a == "END" for a, b in edges):
        issues.append("invalid_sentinel_direction")
    try:
        graph_stats(list(ids), edges)
        stats = graph_stats(list(nodes), [(a, b) for a, b in edges if a in nodes and b in nodes])
    except ValueError as e:
        issues.append(str(e))
        stats = None
    # Every node must be on a START-to-END path; do not insert missing edges.
    def reachable(start, reverse=False):
        seen = {start}
        while True:
            new = {a if reverse else b for a, b in edges if (b if reverse else a) in seen}
            if new <= seen:
                return seen
            seen |= new
    if not set(nodes) <= reachable("START") or not set(nodes) <= reachable("END", True):
        issues.append("node_outside_start_end_paths")
    return {"nodes": nodes, "edges": edges, "sentinels": ["START", "END"], "stats": stats}, sorted(set(issues))


def run():
    tasks, refs, inventory, examples = [], [], [], []
    development_ids = {r["id"] for r in read(ROOT / "data/worfbench_sample/index.json")["items"]}
    seen = set()
    source_matches = {}
    for path in sorted((STUDY / "raw/worfbench/hf/gold_traj").glob("*/graph_eval.json")):
        source = path.parent.name
        source_matches[source] = file_hash(path) == file_hash(STUDY / f"raw/worfbench/code/gold_traj/{source}/graph_eval.json")
        for r in read(path):
            qid = "worfbench:" + r["id"]
            if qid in seen:
                raise ValueError(f"duplicate ID: {qid}")
            seen.add(qid)
            messages = r["conversations"]
            users = [m["content"] for m in messages if m["role"] == "user"]
            assistants = [m["content"] for m in messages if m["role"] == "assistant"]
            issues = []
            if len(users) != 1 or len(assistants) != 1:
                issues.append("unexpected_conversation_roles")
            user = users[0] if users else ""
            try:
                task, actions = parse_task(user)
            except ValueError as e:
                task, actions = user, None
                issues.append(str(e))
            graph, graph_issues = parse_reference(assistants[0] if assistants else "")
            issues.extend(graph_issues)
            clean = {"id": qid, "source_id": r["id"], "source": source,
                     "source_split": "graph_eval", "study_partition": "development" if r["id"] in development_ids else "unassigned",
                     "task": task, "allowed_actions": actions,
                     "input_messages": [m for m in messages if m["role"] in {"system", "user"}],
                     "prompt_sha256": digest(user), "eligible": not issues, "exclusions": sorted(set(issues))}
            tasks.append(clean)
            refs.append({"id": qid, "raw_record": r, "reference_graph": graph, "parse_issues": sorted(set(issues))})
            inventory.append({"id": qid, "source": source, "eligible": not issues,
                              "parse_issues": sorted(set(issues)), "stats": graph["stats"]})
            if not any(e["source"] == source for e in examples):
                examples.append(clean)
    jsonl(STUDY / "normalized/worfbench/tasks.jsonl", tasks)
    jsonl(STUDY / "evaluator_only/worfbench/references.jsonl", refs)
    jsonl(STUDY / "audits/worfbench.inventory.jsonl", inventory)
    jsonl(STUDY / "audits/worfbench.examples.jsonl", examples)
    summary = {"rows": len(tasks), "by_source": dict(Counter(r["source"] for r in tasks)),
               "eligible": sum(r["eligible"] for r in tasks), "hf_github_bytes_match": source_matches,
               "issues": dict(Counter(i for r in tasks for i in r["exclusions"])),
               "eligible_shapes": dict(Counter("branch" if r["stats"]["max_wave_width"] > 1 else "chain" for r in inventory if r["eligible"])),
               "prior_development_examples": len(development_ids)}
    write(STUDY / "audits/worfbench.coverage.json", summary)
    print(summary)
