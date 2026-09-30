"""Reproduce offline study imports from pinned, hash-verified raw artifacts."""
import argparse
from study import longcot, routerbench, worfbench
from study.common import STUDY, extract_router_archive, file_hash, read, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=["all", "longcot", "worfbench", "llmrouterbench"])
    parser.add_argument("--extract", action="store_true", help="Inspect/verify/extract router archive first.")
    args = parser.parse_args()
    names = [args.dataset] if args.dataset != "all" else ["longcot", "worfbench", "llmrouterbench"]
    for name in names:
        sources = read(STUDY / f"manifests/{name}.files.json")
        for entry in sources["files"]:
            if file_hash(STUDY / entry["path"]) != entry["sha256"]:
                raise ValueError(f"Source hash mismatch: {entry['path']}")
        if name == "llmrouterbench":
            if args.extract:
                extract_router_archive()
            for entry in read(STUDY / "manifests/llmrouterbench.extracted.json")["files"]:
                if file_hash(STUDY / entry["path"]) != entry["sha256"]:
                    raise ValueError(f"Extracted file hash mismatch: {entry['path']}")
        {"longcot": longcot, "worfbench": worfbench, "llmrouterbench": routerbench}[name].run()
        write(STUDY / f"manifests/{name}.import.json", {
            "dataset": name, "import_version": 1,
            "input_manifest_sha256": file_hash(STUDY / f"manifests/{name}.files.json"),
            "source_files_verified": len(sources["files"]),
            "scripts": {p.name: file_hash(p) for p in sorted((STUDY.parents[1] / "scripts/study").glob("*.py"))},
            "normalized": {p.name: file_hash(p) for p in sorted((STUDY / f"normalized/{name}").glob("*")) if p.is_file()},
            "evaluator_only": {p.name: file_hash(p) for p in sorted((STUDY / f"evaluator_only/{name}").glob("*")) if p.is_file() and not p.name.startswith("replay-")},
            "coverage": read(STUDY / f"audits/{name}.coverage.json"),
        })


if __name__ == "__main__":
    main()
