from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile

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


def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(
        part in {".", ".."} or ":" in part or "\\" in part or part.endswith((".", " "))
        or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
        for part in path.parts
    ):
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def extract_router_archive():
    archive = STUDY / "raw/llmrouterbench/hf/bench-release.tar.gz"
    locked = read(STUDY / "manifests/sources.lock.json")["llmrouterbench"]
    expected = next(f["lfs_sha256"] for f in locked["hf_files"] if f["path"] == archive.name)
    if file_hash(archive) != expected:
        raise ValueError("Archive SHA-256 mismatch")
    destination = STUDY / "raw/llmrouterbench/extracted"
    # Complete inspection precedes any extraction. No links, special files,
    # path traversal, Windows drive paths, or case-insensitive duplicates.
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        seen = set()
        inventory = []
        for m in members:
            path = safe_member(m.name)
            key = path.as_posix().casefold()
            if key in seen or not (m.isfile() or m.isdir()):
                raise ValueError(f"Duplicate or unsupported archive member: {m.name}")
            seen.add(key)
            inventory.append({"path": m.name, "bytes": m.size, "kind": "file" if m.isfile() else "directory"})
        write(STUDY / "audits/llmrouterbench.archive.json", {"archive_sha256": expected, "members": inventory})
        needed = sum(m.size for m in members if m.isfile() and not (destination / m.name).exists())
        if needed + 1024**3 > shutil.disk_usage(archive.parent).free:
            raise RuntimeError("Insufficient disk space for archive plus 1 GiB reserve")
        file_manifest = []
        for m in members:
            target = destination / m.name
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Extraction escaped raw directory")
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            with tar.extractfile(m) as source:
                if target.exists():
                    for block in iter(lambda: source.read(1024**2), b""):
                        h.update(block)
                    if target.stat().st_size != m.size or file_hash(target) != h.hexdigest():
                        raise ValueError(f"Existing extracted file differs: {target}")
                else:
                    with target.open("xb") as out:
                        for block in iter(lambda: source.read(1024**2), b""):
                            h.update(block)
                            out.write(block)
            file_manifest.append({"path": target.relative_to(STUDY).as_posix(), "bytes": m.size, "sha256": h.hexdigest()})
    write(STUDY / "manifests/llmrouterbench.extracted.json", {"archive_sha256": expected, "files": file_manifest})
    print(f"Inspected and extracted {len(file_manifest)} result files", flush=True)


if __name__ == "__main__":
    extract_router_archive()
