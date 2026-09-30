"""LongCoT import and conservative explicit-graph audit. No answer substitution."""
from collections import Counter
from fractions import Fraction
import re

from .common import STUDY, digest, graph_stats, jsonl, read, write


def parse_graph(domain, template, prompt):
    issues = []
    if domain == "math":
        pattern = r"(?m)^Problem (node_\d+):\s*"
        end = re.search(r"(?m)^What are the answers|^Return the answers", prompt)
        body = prompt[:end.start()] if end else prompt
        finals = re.findall(r"answer for (node_\d+)", prompt[end.start():] if end else "")
        if template in {"conditional", "backtracking"}:
            issues.append("conditional_or_backtracking_not_fixed_dag")
    elif domain == "chemistry":
        pattern = r"(?m)^Subproblem (\d+):\s*"
        end = re.search(r"(?m)^When you are done", prompt)
        body = prompt[:end.start()] if end else prompt
        finals = []
        if template != "easy1":
            issues.append("chemistry_template_requires_separate_adapter")
    else:
        return None, ["procedural_task_no_explicit_execution_dag"]
    starts = list(re.finditer(pattern, body))
    if not starts:
        return None, ["no_explicit_nodes"]
    nodes, edges = [], []
    for i, match in enumerate(starts):
        node_id = match[1]
        instruction = body[match.end():starts[i + 1].start() if i + 1 < len(starts) else len(body)].strip()
        if domain == "math":
            refs = re.findall(r"\bnode_\d+\b", instruction)
            if re.search(r"\b(?:first|second|third|last)\s+(?:pair|triple)\b", instruction, re.I):
                issues.append("unordered_solution_reference")
            if re.search(r"\bprevious\b", instruction, re.I) and not refs:
                issues.append("implicit_reference_requires_review")
        else:
            # Remove output-name declarations before dependency extraction.
            # easy1 is the supported composition; other templates remain quarantined.
            refs_text = re.split(r"Consider the selected mol", instruction, flags=re.I)[0]
            refs = re.findall(r"\bMol[- ](\d+)\b|\bSubproblem\s*#(\d+)\b", refs_text, re.I)
            refs = [a or b for a, b in refs]
        refs = sorted(set(refs))
        edges.extend((p, node_id) for p in refs)
        nodes.append({"id": node_id, "instruction": instruction, "inputs": refs,
                      "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                                        "required": ["answer"], "additionalProperties": False}})
    if domain == "chemistry":
        finals = [nodes[-1]["id"]]
    if not finals or any(n not in {r["id"] for r in nodes} for n in finals):
        issues.append("final_output_order_unresolved")
    try:
        stats = graph_stats([n["id"] for n in nodes], edges)
    except ValueError as e:
        issues.append(str(e))
        stats = {"node_count": len(nodes), "depth": None, "max_wave_width": None, "waves": None}
    return {"nodes": nodes, "edges": edges, "final_node_ids": finals,
            "aggregation": "ordered_list" if domain == "math" else "single_answer",
            **stats}, sorted(set(issues))


def normalize_answer(value):
    """Only exact integer/rational literals are normalized; expressions stay intact.

    Workers must supply ordered sequences as strings where requested. Unordered
    solution-set references are quarantined; we never invent a set ordering.
    """
    if not isinstance(value, str):
        raise ValueError("worker answer must be a string")
    value = value.strip()
    if re.fullmatch(r"[+-]?\d+(?:\s*/\s*[+-]?\d+)?", value):
        parts = [int(p) for p in value.replace(" ", "").split("/")]
        value = str(Fraction(*parts))
    return value


def scoped_input(graph, node_id, completed):
    node = next(n for n in graph["nodes"] if n["id"] == node_id)
    inputs = {}
    for predecessor in node["inputs"]:
        if predecessor not in completed:
            raise ValueError(f"unfinished predecessor: {predecessor}")
        inputs[predecessor] = normalize_answer(completed[predecessor]["answer"])
    return {"instruction": node["instruction"], "predecessor_outputs": inputs,
            "binding_rule": "Evaluate all reference expressions in the instruction using these actual predecessor outputs.",
            "output_schema": node["output_schema"]}


def aggregate(graph, completed):
    values = [normalize_answer(completed[n]["answer"]) for n in graph["final_node_ids"]]
    return values if graph["aggregation"] == "ordered_list" else values[0]


def render_solution(value):
    # The upstream math parser expects a mathematical comma-separated list,
    # not JSON strings (JSON would double-escape LaTeX backslashes).
    text = "[" + ", ".join(value) + "]" if isinstance(value, list) else value
    return "solution = " + text


def run():
    import json
    import pyarrow.parquet as pq
    tasks, refs, inventory, mismatches, examples = [], [], [], [], []
    for path in sorted((STUDY / "raw/longcot/code/src/data").glob("*/*.json")):
        domain, difficulty = path.parent.name, path.stem
        hf = pq.read_table(STUDY / f"raw/longcot/hf/data/{domain}/{difficulty}.parquet").to_pylist()
        by_id = {r["question_id"]: r for r in hf}
        if len(by_id) != len(hf):
            raise ValueError("duplicate HF question ID")
        rows = read(path)["questions"]
        if set(by_id) != {r["question_id"] for r in rows}:
            raise ValueError(f"HF/code ID mismatch: {domain}/{difficulty}")
        for r in rows:
            qid = f"longcot:{domain}:{difficulty}:{r['question_id']}"
            h = by_id[r["question_id"]]
            template = (r.get("problem") or {}).get("template")
            h_answer = json.loads(h["answer"]) if h.get("answer") is not None else None
            mismatch = [k for k, actual, expected in [
                ("prompt", h["prompt"], r["prompt"]), ("answer", h_answer, r["answer"]),
                ("template", h["template"], template)] if actual != expected]
            if mismatch:
                mismatches.append({"id": qid, "fields": mismatch})
            graph, issues = parse_graph(domain, template, r["prompt"])
            if mismatch:
                issues.append("hf_code_content_mismatch")
            if r["question_id"] == "linear_easy_19":
                issues.append("known_anecdotal_dependency_and_ordering_ambiguity")
            eligible = graph is not None and not issues and difficulty == "easy"
            task = {"id": qid, "source_id": r["question_id"], "source_split": difficulty,
                    "study_partition": "unassigned", "domain": domain, "template": template,
                    "prompt": r["prompt"], "prompt_sha256": digest(r["prompt"]),
                    "fixed_graph_eligible": eligible, "exclusions": sorted(set(issues))}
            # Only approved fixed graphs enter execution inputs; candidates remain evaluator-only.
            if eligible:
                task["fixed_graph"] = graph
            tasks.append(task)
            checkpoint = {}
            if graph and domain == "math" and isinstance(r["answer"], list) and len(graph["final_node_ids"]) == len(r["answer"]):
                checkpoint = dict(zip(graph["final_node_ids"], r["answer"]))
            refs.append({"id": qid, "raw_record": r, "hf_record": h, "candidate_graph": graph,
                         "checkpoint_answers": checkpoint, "upstream_verifier": template})
            inventory.append({k: task[k] for k in ("id", "domain", "source_split", "template", "fixed_graph_eligible", "exclusions")} | {
                "node_count": graph["node_count"] if graph else None,
                "depth": graph["depth"] if graph else None,
                "max_wave_width": graph["max_wave_width"] if graph else None,
                "reference_answer_present": r["answer"] is not None,
                "checkpoint_labels": len(checkpoint),
                "required_grader_runtime": {"chemistry": "rdkit", "math": "sympy", "chess": "chess", "cs": "stdlib", "logic": "stdlib"}[domain]})
            if difficulty == "easy" and not any(e["domain"] == domain for e in examples):
                examples.append(task)
    selected_shapes = set()
    for task in tasks:
        if task["fixed_graph_eligible"]:
            key = (task["domain"], task["fixed_graph"]["max_wave_width"] > 1)
            if key not in selected_shapes:
                selected_shapes.add(key)
                if not any(e["id"] == task["id"] for e in examples):
                    examples.append(task)
    jsonl(STUDY / "normalized/longcot/tasks.jsonl", tasks)
    jsonl(STUDY / "evaluator_only/longcot/references.jsonl", refs)
    jsonl(STUDY / "audits/longcot.inventory.jsonl", inventory)
    jsonl(STUDY / "audits/longcot.examples.jsonl", examples)
    # HF includes concatenated all-domain files too; check rather than double-count.
    combined_matches = {}
    for difficulty in ("easy", "medium", "hard"):
        combined = pq.read_table(STUDY / f"raw/longcot/hf/data/all/{difficulty}/longcot-{difficulty}.parquet").to_pylist()
        expected = {(r["hf_record"]["domain"], r["hf_record"]["question_id"]): r["hf_record"]
                    for r in refs if r["hf_record"]["difficulty"] == difficulty}
        observed = {(r["domain"], r["question_id"]): r for r in combined}
        combined_matches[difficulty] = len(combined) == len(expected) and observed == expected
    summary = {"rows": len(tasks), "hf_code_mismatches": mismatches,
               "hf_combined_files_match_domain_files": combined_matches,
               "by_domain_split": dict(Counter(f"{r['domain']}/{r['source_split']}" for r in inventory)),
               "eligible_easy_by_domain_shape": dict(Counter(
                   f"{r['domain']}/{'branch' if r['max_wave_width'] > 1 else 'chain'}"
                   for r in inventory if r["fixed_graph_eligible"])),
               "exclusions": dict(Counter(i for r in inventory for i in r["exclusions"])),
               "null_reference_answers": sum(not r["reference_answer_present"] for r in inventory),
               "graph_width_definition": "maximum ready wave size under wave-synchronous scheduling; not maximum antichain",
               "scope": "all releases audited; only easy supported explicit graphs eligible for initial fixed-graph execution"}
    write(STUDY / "audits/longcot.coverage.json", summary)
    print(summary)
