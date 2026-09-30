# Analysis outputs

| File | Produced by | Contents |
|---|---|---|
| `eval.json` | `scripts/cohort100_ab_analyze.py --runs eval-b1 eval-b2 --decompose dec-1 --out eval` | The pre-registered B0 vs A1 analysis (`docs/preregistration_B0_vs_A1.md`): primary speedup and CI, replay ideals, wave-barrier loss, overhead, decomposition time, cost, per-template results, sensitivity analyses |
| `paper_numbers.json` | `paper_numbers.py` | All four configurations: accuracy, latency, speedup vs. B0 and A1, cost breakdown (LLM / Lambda / Step Functions, per router tier), routing profile, reliability, by difficulty and overall |
| `router_coverage.json` | historical report, not regenerated here | The router applied to the 910 cohort subtasks and 3,990 development and reserve subtasks: tier counts, modifier counts, rules matched (all subtasks matched a rule). Its `models` field records the model binding at the time of the report; the four evaluated configurations' bindings are in each run's `manifest.json`. |
| `eval-tables.xlsx` | historical workbook | Per-task and per-configuration B0 / A1 results, with every aggregate recomputed by spreadsheet formula (referenced by `docs/results_B0_vs_A1.md`) |
| `eval-b1-interim.json` | historical output | Interim analysis of batch 1 only, run before batch 2 (deviation D3 in the pre-registration). Superseded by `eval.json` |

`python replication/verify.py` regenerates `eval.json` and `paper_numbers.json` and checks that
both are byte-identical to the shipped files. [`replication/CLAIMS.md`](../../../replication/CLAIMS.md)
maps each number in the paper to its field in these files.

Both files use the paper's configuration names, except in `eval.json`, which covers only B0 and A1.
Router tiers are called light / medium / strong in `paper_numbers.json`; the router code and run
manifests call them `easy` / `medium` / `hard`.
