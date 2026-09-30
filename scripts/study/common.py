from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "data/study_v1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(canonical(row) + "\n")


def graph_stats(nodes, edges):
    ids = set(nodes)
    if len(ids) != len(nodes):
        raise ValueError("duplicate_node_id")
    if any(a not in ids or b not in ids for a, b in edges):
        raise ValueError("unresolved_edge")
    pending, done, waves = set(ids), set(), []
    predecessors = {node: {a for a, b in edges if b == node} for node in ids}
    while pending:
        ready = sorted(n for n in pending if predecessors[n] <= done)
        if not ready:
            raise ValueError("cycle")
        waves.append(ready)
        done.update(ready)
        pending.difference_update(ready)
    return {"node_count": len(ids), "depth": len(waves),
            "max_wave_width": max(map(len, waves), default=0), "waves": waves}
