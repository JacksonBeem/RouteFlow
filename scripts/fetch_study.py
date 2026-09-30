"""Fetch public artifacts at revisions in study_v1/manifests/sources.lock.json.

Hugging Face downloads use hf; GitHub files are verified against pinned Git blob
IDs. Raw files are never silently overwritten when content differs.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "data/study_v1"


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def code_selected(name, path):
    if "__pycache__" in path or path.endswith(".pyc"):
        return False
    if name != "llmrouterbench":
        return not path.startswith("assets/")
    return (not path.startswith(("baselines/", "data/", "assets/", "."))
            and Path(path).suffix in {".py", ".txt", ".toml", ".lock", ".md", ".yaml", ".json"}) or path.upper().startswith("LICENSE")


def fetch(name, spec):
    hf = Path(sys.executable).parent / ("hf.exe" if os.name == "nt" else "hf")
    if not hf.exists():
        hf = shutil.which("hf")
    if not hf:
        raise RuntimeError("Install huggingface_hub CLI in the preparation environment")
    dest = STUDY / "raw" / name / "hf"
    env = os.environ.copy()
    env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    subprocess.run([str(hf), "download", spec["hf_repo"], "--type", "dataset",
                    "--revision", spec["hf_revision"], "--local-dir", str(dest),
                    "--max-workers", "4", "--quiet"], env=env, check=True)
    files = []
    for info in spec["hf_files"]:
        path = dest / info["path"]
        digest = sha(path)
        if info["lfs_sha256"] and digest != info["lfs_sha256"]:
            raise ValueError(f"HF hash mismatch: {path}")
        if path.stat().st_size != info["bytes"]:
            raise ValueError(f"HF size mismatch: {path}")
        files.append({"path": path.relative_to(STUDY).as_posix(), "sha256": digest,
                      "bytes": path.stat().st_size, "source_revision": spec["hf_revision"],
                      "url": f"https://huggingface.co/datasets/{spec['hf_repo']}/resolve/{spec['hf_revision']}/{info['path']}"})

    def code(info):
        path = STUDY / "raw" / name / "code" / info["path"]
        url = f"https://raw.githubusercontent.com/{spec['github_repo']}/{spec['github_revision']}/{info['path']}"
        if path.exists():
            content = path.read_bytes()
        else:
            with urllib.request.urlopen(url, timeout=120) as response:
                content = response.read()
        git_hash = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if git_hash != info["sha"]:
            raise ValueError(f"Git blob hash mismatch: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return {"path": path.relative_to(STUDY).as_posix(), "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content), "source_revision": spec["github_revision"], "url": url}

    selected = [f for f in spec["github_files"] if f["type"] == "blob" and code_selected(name, f["path"])]
    with ThreadPoolExecutor(max_workers=8) as pool:
        files.extend(pool.map(code, selected))
    manifest = {"dataset": name, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                "hf_revision": spec["hf_revision"], "github_revision": spec["github_revision"],
                "files": sorted(files, key=lambda f: f["path"])}
    (STUDY / "manifests" / f"{name}.files.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{name}: {len(files)} pinned files verified", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=["all", "longcot", "worfbench", "llmrouterbench"])
    parser.add_argument("--embedding", action="store_true", help="Also fetch the pinned WorFEval embedding artifacts.")
    args = parser.parse_args()
    specs = json.loads((STUDY / "manifests/sources.lock.json").read_text())
    for name in ([args.dataset] if args.dataset != "all" else specs):
        fetch(name, specs[name])
    if args.embedding:
        manifest = json.loads((STUDY / "manifests/worfbench.embedding.json").read_text(encoding="utf-8"))
        dest = STUDY / "raw/worfbench/embedding"
        filenames = [str(Path(f["path"]).relative_to("raw/worfbench/embedding")).replace("\\", "/") for f in manifest["files"]]
        hf = Path(sys.executable).parent / ("hf.exe" if os.name == "nt" else "hf")
        env = os.environ.copy()
        env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        subprocess.run([str(hf), "download", manifest["repo"], *filenames,
                        "--revision", manifest["revision"], "--local-dir", str(dest), "--quiet"], env=env, check=True)
        for entry in manifest["files"]:
            if sha(STUDY / entry["path"]) != entry["sha256"]:
                raise ValueError(f"Embedding hash mismatch: {entry['path']}")


if __name__ == "__main__":
    main()
