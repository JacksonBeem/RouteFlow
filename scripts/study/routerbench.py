"""Audit recorded outcomes without array-position joins or synthesized scores."""
from collections import Counter
import json
import math
from pathlib import Path
import sqlite3
import tempfile
from .common import STUDY, canonical, digest, jsonl, read, write


def identity(dataset, row):
    origin = row.get("origin_query")
    prompt = row.get("prompt")
    has_origin = isinstance(origin, str) and bool(origin.strip())
    has_prompt = isinstance(prompt, str) and bool(prompt.strip())
    origin_hash = digest(origin) if has_origin else None
    prompt_hash = digest(prompt) if has_prompt else None
    # Original query content plus exact prompt. 'index' is retained for audit,
    # never used to align models; ordering and index bases differ by collector.
    group = digest([dataset, origin_hash]) if has_origin else None
    qid = digest([dataset, origin_hash, prompt_hash]) if has_origin and has_prompt else None
    return qid, group, origin_hash, prompt_hash


def number(value):
    return float(value) if isinstance(value, (float, int)) and math.isfinite(value) else None


def run():
    output = STUDY / "evaluator_only/llmrouterbench"
    output.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="replay-", suffix=".sqlite", dir=output)
    import os
    os.close(handle)
    db = sqlite3.connect(temporary)
    db.executescript('''
        CREATE TABLE queries (id TEXT PRIMARY KEY, query_group TEXT, dataset TEXT,
          origin_sha256 TEXT, prompt_sha256 TEXT, origin_query TEXT, prompt TEXT);
        CREATE TABLE records (file TEXT, position INTEGER, query_id TEXT, query_group TEXT,
          dataset TEXT, split TEXT, model TEXT, original_index TEXT,
          score REAL, cost REAL, prompt_tokens REAL, completion_tokens REAL,
          ground_truth_sha256 TEXT, prediction_sha256 TEXT, issues TEXT,
          PRIMARY KEY(file,position));
    ''')
    inventory, exclusions, examples, schema = [], [], {}, Counter()
    files = read(STUDY / "manifests/llmrouterbench.extracted.json")["files"]
    for count, f in enumerate(files, 1):
        path = STUDY / f["path"]
        payload = read(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            exclusions.append({"file": f["path"], "reason": "unrecognized_result_wrapper"})
            continue
        dataset, model, split = (payload.get(k) for k in ("dataset_name", "model_name", "split"))
        if not isinstance(dataset, str) or not isinstance(model, str):
            exclusions.append({"file": f["path"], "reason": "missing_dataset_or_model_identity"})
            continue
        rows = payload["records"]
        scores, costs, field_counts, issues_count, extra_fields = [], [], Counter(), Counter(), Counter()
        for position, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"Non-object record: {path}:{position}")
            field_counts.update(row.keys())
            schema.update(f"{k}:{type(v).__name__}" for k, v in row.items())
            if isinstance(row.get("extra_fields"), dict):
                extra_fields.update(row["extra_fields"].keys())
            qid, group, oh, ph = identity(dataset, row)
            issues = []
            if qid is None:
                issues.append("missing_original_query_or_prompt")
            else:
                db.execute("INSERT OR IGNORE INTO queries VALUES (?,?,?,?,?,?,?)",
                           (qid, group, dataset, oh, ph, row["origin_query"], row["prompt"]))
                if dataset not in examples:
                    examples[dataset] = {"id": "llmrouterbench:" + qid, "dataset": dataset,
                                         "origin_query": row["origin_query"], "prompt": row["prompt"],
                                         "source_file": f["path"], "record_position": position}
            score, cost = number(row.get("score")), number(row.get("cost"))
            if score is None:
                issues.append("missing_or_nonnumeric_score")
            else:
                scores.append(score)
            if cost is None:
                issues.append("missing_or_nonnumeric_cost")
            else:
                costs.append(cost)
                if cost < 0:
                    issues.append("negative_cost")
                if cost == 0:
                    issues.append("zero_cost_not_verified_free")
            if row.get("error") or row.get("success") is False or row.get("successful") is False:
                issues.append("explicit_failure")
            if row.get("raw_output") in (None, ""):
                issues.append("empty_or_absent_output")
            issues_count.update(issues)
            db.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                f["path"], position, qid, group, dataset, split, model, canonical(row.get("index")),
                score, cost, number(row.get("prompt_tokens")), number(row.get("completion_tokens")),
                digest(row.get("ground_truth")), digest(row.get("prediction")), canonical(issues)))
        inventory.append({"file": f["path"], "sha256": f["sha256"], "dataset": dataset,
                          "model": model, "split": split, "records": len(rows),
                          "wrapper": {k: v for k, v in payload.items() if k != "records"},
                          "record_fields": dict(field_counts), "extra_field_names": dict(extra_fields), "issues": dict(issues_count),
                          "score_min": min(scores, default=None), "score_max": max(scores, default=None),
                          "nonbinary_scores": sum(s not in (0, 1) for s in scores),
                          "cost_min": min(costs, default=None), "cost_max": max(costs, default=None),
                          "wrapper_count_matches": payload.get("counts") == len(rows)})
        if count % 50 == 0:
            db.commit()
            print(f"Router audit: {count}/{len(files)} files", flush=True)
    db.executescript('''
        CREATE INDEX records_query_model ON records(query_id,model);
        CREATE INDEX queries_group ON queries(query_group);
        CREATE INDEX records_group ON records(query_group);
    ''')
    db.commit()
    def query(sql):
        cur = db.execute(sql)
        return [dict(zip([c[0] for c in cur.description], row)) for row in cur]
    coverage = query('''SELECT dataset, count(*) AS records, count(DISTINCT model) AS models,
        count(DISTINCT query_id) AS query_prompt_variants, count(DISTINCT query_group) AS original_queries,
        sum(score IS NULL) AS null_scores, sum(cost IS NULL) AS null_costs, sum(cost=0) AS zero_costs
        FROM records GROUP BY dataset''')
    cohort = query('''SELECT dataset,model,count(*) AS records,count(DISTINCT query_id) AS queries,
        sum(cost>0) AS positive_cost_records FROM records GROUP BY dataset,model''')
    prompt_variants = query('''SELECT query_group, dataset, count(*) AS prompt_variants
        FROM queries GROUP BY query_group HAVING count(*) > 1''')
    repeats = query('''SELECT query_id,model,count(*) AS records,count(DISTINCT file) AS files
        FROM records WHERE query_id IS NOT NULL GROUP BY query_id,model HAVING count(*) > 1''')
    label_conflicts = query('''SELECT query_id,count(DISTINCT ground_truth_sha256) AS labels
        FROM records WHERE query_id IS NOT NULL GROUP BY query_id HAVING count(DISTINCT ground_truth_sha256)>1''')
    missing_labels = (digest(None), digest(""))
    meaningful_conflicts = query(f'''SELECT query_id,dataset,count(DISTINCT ground_truth_sha256) AS labels
        FROM records WHERE query_id IS NOT NULL AND ground_truth_sha256 NOT IN {missing_labels!r}
        GROUP BY query_id HAVING count(DISTINCT ground_truth_sha256)>1''')
    matrices = query('''SELECT dataset,available_models,count(*) AS queries FROM (
        SELECT dataset,query_id,count(DISTINCT model) AS available_models FROM records
        WHERE query_id IS NOT NULL AND score IS NOT NULL GROUP BY dataset,query_id)
        GROUP BY dataset,available_models''')
    jsonl(STUDY / "normalized/llmrouterbench/tasks.jsonl", (
        {"id": "llmrouterbench:" + r[0], "query_group": r[1], "dataset": r[2],
         "origin_sha256": r[3], "prompt_sha256": r[4], "origin_query": r[5], "prompt": r[6],
         "source_splits": json.loads(r[7]), "study_partition": "unassigned"}
        for r in db.execute('''SELECT q.*, json_group_array(DISTINCT r.split)
            FROM queries q JOIN records r ON q.id=r.query_id GROUP BY q.id ORDER BY q.id''')))
    summary = {"files": len(files), "recognized_files": len(inventory), "file_exclusions": exclusions,
               "records": db.execute("SELECT count(*) FROM records").fetchone()[0],
               "coverage": coverage, "distinct_model_ids": [r[0] for r in db.execute("SELECT DISTINCT model FROM records ORDER BY model")],
               "query_groups_with_prompt_variants": len(prompt_variants),
               "repeated_query_model_cells": len(repeats), "conflicting_label_cells": len(label_conflicts),
               "conflicting_nonempty_label_cells": meaningful_conflicts,
               "record_field_types": dict(schema),
               "cost_semantics": "recorded nominal model cost; upstream uses provider usage cost or per-million-token prices, defaults to zero when unavailable",
               "latency_semantics": "time_taken is a file-wrapper field; never normalized as per-query latency",
               "join_rule": "exact dataset + original query content hash + prompt hash; never array position or index",
               "missing_results": "absent cells and nulls remain missing; zero-cost rows are not assumed free"}
    jsonl(STUDY / "audits/llmrouterbench.files.jsonl", inventory)
    jsonl(STUDY / "audits/llmrouterbench.cohorts.jsonl", cohort)
    jsonl(STUDY / "audits/llmrouterbench.prompt_variants.jsonl", prompt_variants)
    jsonl(STUDY / "audits/llmrouterbench.repeats.jsonl", repeats)
    jsonl(STUDY / "audits/llmrouterbench.label_conflicts.jsonl", label_conflicts)
    jsonl(STUDY / "audits/llmrouterbench.matrix_coverage.jsonl", matrices)
    jsonl(STUDY / "audits/llmrouterbench.examples.jsonl", examples.values())
    write(STUDY / "audits/llmrouterbench.coverage.json", summary)
    db.close()
    Path(temporary).replace(output / "replay.sqlite")
    print({k: summary[k] for k in ("files", "recognized_files", "records", "query_groups_with_prompt_variants", "repeated_query_model_cells", "conflicting_label_cells")})
