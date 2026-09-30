"""Import the pinned LongCoT artifacts (fetched by fetch_study.py) into the normalized task and
reference files used for grading and decomposition. Offline; verifies every source hash first.

  python scripts/import_study.py longcot
"""
import argparse
from study import longcot
from study.common import STUDY, file_hash, read, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=["longcot"])
    args = parser.parse_args()
    for name in [args.dataset]:
        sources = read(STUDY / f"manifests/{name}.files.json")
        for entry in sources["files"]:
            if file_hash(STUDY / entry["path"]) != entry["sha256"]:
                raise ValueError(f"Source hash mismatch: {entry['path']}")
        longcot.run()
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
