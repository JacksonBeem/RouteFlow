# Run records

One directory per run. Records are archived as produced and are never edited, except that AWS
account identifiers are redacted as `000000000000`.

| Directory | Configuration | Tasks | Dates (UTC) |
|---|---|---|---|
| `eval-b1` | B0 and A1, batch 1 | 50 | 2026-09-27 |
| `eval-b2` | B0 and A1, batch 2 | 50 | 2026-09-27 |
| `proposed-3` | P-DeepSeek (router preset `v3`) | 100 | 2026-09-29 |
| `proposed-4` | P-Sonnet (router preset `v1`) | 100 | 2026-09-29/30 |

- **Baseline runs.** In `eval-b1` and `eval-b2`, every task ran both B0 and A1 back to back on the
  same functions, alternating which went first. The 100 tasks were split into the two batches by
  a seeded shuffle within each template (`../batches.json`).
- **RouteFlow runs.** `proposed-3` and `proposed-4` each ran all 100 tasks as a separate pass.

## Files in each run directory

| File | Contents |
|---|---|
| `manifest.json` | Frozen run configuration: model and provider settings, prices, task list, per-subtask routing (RouteFlow runs), and SHA-256 hashes of the code that ran |
| `rows.jsonl` | One line per task. Per configuration: status, end-to-end latency, per-subtask times, tokens, retries, cold starts, LLM / Lambda / Step Functions cost, final answer |
| `raw/<run key>.json` | Everything collected for one task: every model attempt (request ID, served model and provider, token usage, reported cost, answer), the Step Functions execution history, and each Lambda invocation's billing report |
| `grades.json` | Per-task correctness from the LongCoT checker (a failed configuration counts as incorrect) |
| `comparison.json` | RouteFlow runs only: per-task comparison with B0 and A1, and per-tier cost on the same subtasks |
| `../<run>.log` | The runner's console output, one line per task |

The decomposition-time measurement is in `../decompose/dec-1/`. It holds one decomposition call
per task, with its latency and how well the returned graph matches the dataset's graph.
