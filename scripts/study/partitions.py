"""Outcome-blind, connected-component partitioning of normalized task inputs."""
from collections import Counter, defaultdict
import json
import re
import unicodedata

from .common import digest
from .longcot import parse_graph

SEED = "study-v1-2026-09-20-partitions-1"


def text_key(text):
    # Preserve punctuation, numbers and case (including chemical SMILES).
    return " ".join(unicodedata.normalize("NFKC", text).split())


def units(row):
    result = [row[k] for k in ("prompt", "origin_query", "task", "task_text")
              if isinstance(row.get(k), str) and row[k].strip()]
    if row["id"].startswith("longcot:"):
        graph, _ = parse_graph(row["domain"], row["template"], row["prompt"])
        if graph:
            for node in graph["nodes"]:
                # Node numbering is local to the composed task, not source identity.
                result.append(re.sub(r"\bnode_\d+\b", "node_REF", node["instruction"]))
    return sorted({text_key(t) for t in result})


def assign(tasks, exposures, legacy=()):
    """Group exact input text, query variants, and whole questions embedded in text.

    Whole-question containment uses >=12 words and >=80 characters. It does not
    join on generic shared substrings, answers, labels, or fuzzy similarity.
    """
    rows = {r["id"]: r for r in [*tasks, *legacy]}
    if len(rows) != len(tasks) + len(legacy):
        raise ValueError("duplicate task IDs")
    parent = {k: k for k in rows}
    links = []

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(a, b, reason):
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
            links.append({"a": a, "b": b, "reason": reason})

    owners, query_groups = {}, {}
    for qid, row in sorted(rows.items()):
        if row.get("query_group"):
            key = row["query_group"]
            if key in query_groups:
                join(qid, query_groups[key], "same_original_query_all_prompt_variants")
            query_groups[key] = qid
        for text in units(row):
            if text in owners:
                join(qid, owners[text], "identical_normalized_input_or_subproblem")
            else:
                owners[text] = qid
    # Anchor exact whole questions by 12 leading words; verify full containment.
    anchors = defaultdict(list)
    for text, qid in owners.items():
        words = text.split()
        if len(words) >= 12 and len(text) >= 80:
            anchors[tuple(words[:12])].append((text, qid))
    for text, qid in owners.items():
        words = text.split()
        for i in range(max(0, len(words) - 11)):
            for smaller, other in anchors.get(tuple(words[i:i + 12]), ()):
                if other != qid and len(smaller) < len(text) and smaller in text:
                    join(qid, other, "whole_question_contained_in_other_input")
    groups = defaultdict(list)
    for qid in rows:
        groups[root(qid)].append(qid)
    exposure_ids = set(exposures) | {r["id"] for r in legacy}
    unknown = set(exposures) - rows.keys()
    if unknown:
        raise ValueError(f"unknown exposure IDs: {sorted(unknown)}")
    assignments, components = [], []
    for members in sorted(groups.values(), key=lambda m: min(m)):
        members = sorted(members)
        group = digest(members)
        forced = sorted(set(members) & exposure_ids)
        bucket = int(digest([SEED, group])[:16], 16) % 10000
        partition = "development" if forced or bucket < 2000 else "pilot" if bucket < 4000 else "final_test"
        components.append({"group_id": group, "members": members,
                           "partition": partition, "forced_development_ids": forced})
        for qid in members:
            if qid.startswith("legacy:"):
                continue
            row = rows[qid]
            assignments.append({"id": qid, "group_id": group, "partition": partition,
                                "dataset": qid.split(":")[0],
                                "stratum": row.get("domain", row.get("source", row.get("dataset"))),
                                "adapter_eligible": row.get("fixed_graph_eligible", row.get("eligible", True)),
                                "forced_development": bool(forced)})
    return sorted(assignments, key=lambda r: r["id"]), components, links
