"""Source-disjoint partitions with quarantine, rather than exposure propagation.

Keep all representations of a query together. Remove unexposed query bundles
that directly share a source with exposed bundles. Quarantined tasks cannot be
used for tuning or evaluation. Only then group and split the retained remainder.
"""
from collections import defaultdict
from .common import digest
from .partitions import text_key, units

SEED = "study-v1-2026-09-20-purged-partitions-2"


class Union:
    def __init__(self, ids):
        self.parent = {qid: qid for qid in ids}

    def root(self, qid):
        parent = self.parent
        while parent[qid] != qid:
            parent[qid] = parent[parent[qid]]
            qid = parent[qid]
        return qid

    def join(self, members):
        roots = {self.root(qid) for qid in members}
        if roots:
            smallest = min(roots)
            for qid in roots:
                self.parent[qid] = smallest

    def groups(self):
        groups = defaultdict(set)
        for qid in self.parent:
            groups[self.root(qid)].add(qid)
        return list(groups.values())


def source_index(rows):
    """Full incidence sets, not v1's spanning-tree edge log.

    Expanding containment uses original owners, so no task bridges manufacture
    source identity. Output stores source hashes, not prompt/question text.
    """
    owners = defaultdict(set)
    clone_keys = defaultdict(set)
    for row in sorted(rows, key=lambda r: r["id"]):
        qid = row["id"]
        for text in units(row):
            owners[text].add(qid)
        if row.get("query_group"):
            clone_keys["query:" + row["query_group"]].add(qid)
        for field in ("prompt", "origin_query", "task", "task_text"):
            if isinstance(row.get(field), str) and row[field].strip():
                clone_keys["full:" + text_key(row[field])].add(qid)
    anchors = defaultdict(list)
    for text in owners:
        words = text.split()
        if len(words) >= 12 and len(text) >= 80:
            anchors[tuple(words[:12])].append(text)
    members = {text: set(qids) for text, qids in owners.items()}
    for text in owners:
        words = text.split()
        for i in range(max(0, len(words) - 11)):
            for smaller in anchors.get(tuple(words[i:i + 12]), ()):
                if len(smaller) < len(text) and smaller in text:
                    members[smaller].update(owners[text])
    sources = [{"source_hash": digest(text), "members": sorted(qids)} for text, qids in members.items() if len(qids) > 1]
    sources.sort(key=lambda s: s["source_hash"])
    clones = [sorted(qids) for qids in clone_keys.values() if len(qids) > 1]
    return sources, clones


def assign(rows, exposures, sources, clones):
    by_id = {r["id"]: r for r in rows}
    if len(by_id) != len(rows) or not set(exposures) <= by_id.keys():
        raise ValueError("duplicate IDs or unknown exposures")
    bundles = Union(by_id)
    for members in clones:
        bundles.join(members)
    exposed_roots = {bundles.root(qid) for qid in exposures}
    exposed = {qid for qid in by_id if bundles.root(qid) in exposed_roots}
    contaminated_roots = set()
    witnesses = defaultdict(set)
    for source in sources:
        if exposed.intersection(source["members"]):
            for qid in source["members"]:
                root = bundles.root(qid)
                if root not in exposed_roots:
                    contaminated_roots.add(root)
                    witnesses[root].add(source["source_hash"])
    quarantine = {qid for qid in by_id if bundles.root(qid) in contaminated_roots}
    retained = set(by_id) - quarantine - exposed
    components = Union(retained)
    for members in clones:
        components.join(set(members) & retained)
    for source in sources:
        components.join(set(source["members"]) & retained)
    assigned = {qid: "development" for qid in exposed}
    assigned.update({qid: "quarantine" for qid in quarantine})
    groups = []
    for members in sorted(components.groups(), key=min):
        group_id = digest(sorted(members))
        bucket = int(digest([SEED, group_id])[:16], 16) % 10000
        partition = "development" if bucket < 2000 else "pilot" if bucket < 4000 else "final_test"
        groups.append({"group_id": group_id, "partition": partition, "members": sorted(members)})
        assigned.update({qid: partition for qid in members})
    assignments = []
    group_ids = {qid: g["group_id"] for g in groups for qid in g["members"]}
    for members in bundles.groups():
        bundle_id = digest(sorted(members))
        for qid in members:
            if qid in exposed or qid in quarantine:
                group_ids[qid] = bundle_id
    for qid in sorted(by_id):
        assignments.append({"id": qid, "partition": assigned[qid],
                            "group_id": group_ids[qid],
                            "actual_exposure": qid in exposures, "exposed_query_bundle": qid in exposed,
                            "quarantine_source_hashes": sorted(witnesses.get(bundles.root(qid), ())) if qid in quarantine else []})
    verify(assignments, exposures, sources, clones)
    return assignments, groups


def verify(assignments, exposures, sources, clones):
    by_id = {r["id"]: r for r in assignments}
    for qid in exposures:
        if by_id[qid]["partition"] != "development":
            raise ValueError("exposure left development")
    for source in sources:
        partitions = {by_id[q]["partition"] for q in source["members"]} - {"quarantine"}
        if len(partitions) > 1:
            raise ValueError("retained source overlap across partitions")
    for clone in clones:
        if len({by_id[q]["partition"] for q in clone}) > 1:
            raise ValueError("query variants split")
