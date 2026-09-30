# cohort100-ab pre-registration (2026-09-26)

Status: **approved by the author 2026-09-26; binding from the commit that adds this file.** Any
change after that goes into section 14 (deviations log), never into the text above it.

## 1. Question

On the same LLM, does running a decomposed task's subtasks in parallel where their dependencies
allow (A1) finish faster than running them one at a time (B0), on AWS Lambda + Step Functions?

- **Workload:** the 100 LongCoT chemistry tasks of `cohort_100` (evaluation partition), each
  already decomposed into a DAG of subtasks by the dataset (910 subtasks).
- **B0-Sequential:** one Step Functions Task per subtask, chained in a fixed topological order.
- **A1-Parallel:** one Parallel state that starts every subtask at once; each subtask's function
  waits until its own predecessors have succeeded, then calls the model (per-node dependency
  scheduling, Evaluation Methodology PDF section 2).
- Both arms invoke the **same deployed functions** with the same code, prompts, model, settings
  and Retry policy. Only the schedule differs.

## 2. Hypotheses

- **H1 (primary):** A1 is faster than B0. Supported if the lower bound of the 95% CI of the
  primary estimate (section 4) is above 1.
- **H2 (secondary, structural):** across templates, the measured speedup follows the
  duration-weighted ideal (the replay estimate, section 5.1) more closely than the node-count
  ideal N/D. With 10 templates this is reported descriptively (Spearman correlation of template
  mean measured speedup with template mean replay and with N/D), not tested for significance.
- Accuracy has **no hypothesis**: it is reported descriptively (section 7).

## 3. Units and data

- **Unit of analysis:** one task (row). Each row runs both arms back to back.
- **Templates (strata):** 10 templates; the DAG shape is fixed within a template.

| Template | Rows | N | D | W | N/D | Batch 1 / 2 |
|---|---|---|---|---|---|---|
| easy1 | 17 | 5 | 4 | 2 | 1.25 | 9 / 8 |
| easy2 | 16 | 5 | 4 | 2 | 1.25 | 8 / 8 |
| hard1 | 9 | 16 | 9 | 5 | 1.78 | 4 / 5 |
| hard2 | 9 | 16 | 10 | 5 | 1.60 | 5 / 4 |
| hard3 | 8 | 15 | 9 | 4 | 1.67 | 4 / 4 |
| hard4 | 8 | 6 | 5 | 2 | 1.20 | 4 / 4 |
| med1 | 9 | 9 | 4 | 5 | 2.25 | 4 / 5 |
| med2 | 8 | 9 | 4 | 5 | 2.25 | 4 / 4 |
| med3 | 8 | 11 | 6 | 2 | 1.83 | 4 / 4 |
| med4 | 8 | 6 | 5 | 2 | 1.20 | 4 / 4 |

- N, D, W and CP are constant within a template (CP = D), so structural claims rest on 7
  distinct (N, D, W) shapes, not 100 independent points.
- **Batches:** rows are split 50/50 in advance by `batch_assignment` (seed `cohort100-ab-batch-v1`),
  splitting each template in half; the 34 exposed rows fall 17/17. Recorded in
  `data/cohort100_ab/batches.json`.
- **Run order:** within a batch, rows run in the seeded order (seed `cohort100-ab-order-v1`);
  which arm runs first alternates by position in the batch.
- **Strata carried per row:** `exposed` (34 rows, seen during development: all 33 easy rows +
  hard2_0), `flagged_impossible` (16 rows), `easy2_policy` (16 rows).

## 4. Primary estimate

For each row i where **both arms succeeded**: l_i = ln(T_B0,i / T_A1,i), where T is the Step
Functions execution time (stopDate - startDate), called T_exec below.

- Template mean: m_t = mean of l_i over template t's rows.
- **Primary estimate:** S = exp(mean of m_t over the 10 templates), i.e. the geometric-mean
  speedup with **equal weight per template**.
- **95% CI:** stratified bootstrap, resampling rows with replacement within each template,
  10,000 replicates, seed `cohort100-ab-boot-v1`, percentile interval.
- Also reported: latency reduction 1 - 1/S; the row-pooled geometric mean (equal weight per row);
  and the PDF's per-row latency reduction (T_B0 - T_A1) / T_B0, averaged with template weights.
- A template with no row where both arms succeeded is dropped from S and reported.

## 5. Secondary estimates

1. **Replay (scheduling effect without latency noise).** From one arm's measured node work times
   d_j (`node_seconds`: inputs available to done): replay speedup = sum(d_j) / (longest
   duration-weighted path through the DAG). Computed from B0's durations and from A1's, per row;
   aggregated like S. This is what H2 uses (B0 durations).
2. **Wave-barrier loss.** Replay under wave barriers (sum over waves of the slowest node) vs the
   dependency schedule, from the same durations: how much slower the earlier wave design would
   have been.
3. **Orchestration overhead.** A1: T_exec - replay critical path from A1's durations. B0:
   T_exec - sum of B0's durations.
4. **Decomposition time (T_dec).** One decomposition call per task with the same model and
   settings (`scripts/cohort100_ab_decompose.py`). Speedup including decomposition:
   (T_B0 + T_dec) / (T_A1 + T_dec), aggregated like S. Also reported: whether the returned DAG
   matches the dataset DAG exactly.
5. **Cost.** Per row and arm: C_LLM (OpenRouter-reported cost), C_Lambda (billed duration,
   including A1's dependency waits), C_StepFunctions (transitions incl. retries), C_Total. Ratios
   A1/B0 aggregated like S. C_LLM is also recomputed as tokens x list price and reconciled with
   the reported cost. Rows with unknown-cost calls are listed; C_Total_upper is reported beside
   C_Total for them.
6. **Per-template table** of everything above.

## 6. Sensitivity analyses

1. **Cold starts:** T_exec minus the summed Lambda init time of that arm's invocations, then S
   recomputed. (Both arms are re-warmed before they start, but late B0 nodes can go cold during
   long B0 runs; pilots showed 0-4 per B0 arm, about 0.5 s each.)
2. **Exposure and flags:** S without exposed rows (note: this drops almost all easy rows, so it
   also changes the template mix), without flagged rows, and without easy2-policy rows.
3. **Arm order:** mean l_i for B0-first vs A1-first rows (template-weighted).
4. **Batch:** S per batch.
5. **Failed arms:** lower bound treating a failed arm's time-at-failure as its time.
6. **Retries:** S without rows that had any retry, 429 or unknown-cost call.

## 7. Accuracy (descriptive only)

Per arm: correct final answers / all 100 tasks (tasks without a completed row count as incorrect),
graded with the upstream LongCoT verifier. Also: rows where the arms disagree. No equivalence or
difference test.

## 8. Row outcome rules

- **Speedup** uses only rows where both arms SUCCEEDED; completion rates are reported per arm and
  template.
- **Model outcomes** (unusable output after retries, provider unavailable after retries, workflow
  timeout) are valid rows: the arm counts as failed.
- **Invalid rows** (harness or account failure: rejected key, missing predecessor, Lambda error,
  dependency wait exhausted, aborted execution) halt the run and are re-run on resume, at most 2
  times; after that the row is excluded and listed.
- **Served model:** if any call is served by a model other than `openai/gpt-5.2`, rows from that
  point are flagged and S is reported with and without them.

## 9. Frozen setup

- Code: commit `4eabb80`. Both batches must reproduce pilot-2's hashes (`--require-code-of pilot-2`):
  `asl.py 2c701786…b58b`, `metrics.py 96371c47…4762`, `cohort100_ab_run.py 539a0457…ebce`,
  `node_worker.py 3e963d4f…f95b`.
- Model `openai/gpt-5.2`, provider pinned to OpenAI (no fallbacks), reasoning effort low,
  `max_tokens` 16384, strict JSON schema output, no temperature. Lambda python3.13 arm64 512 MB,
  timeout 900 s. A1 dependency poll 0.2 s, wait limit 600 s.
- Inputs: `nodes.jsonl fba02156…84ca`, `dags.json a18c2d1b…da0`, `batches.json c02ff17c…ccc`.
- T_dec: `cohort100_ab_decompose.py` as used in `dec-dev-1` (`c8190a1e…e3c0`).
- Region us-east-1. Prices: Lambda arm64 USD 0.0000133334 / GB-s + 0.0000002 / request, Step
  Functions USD 0.000025 / transition, gpt-5.2 USD 1.75 / 14 per M input / output tokens.
- Pilots (development rows only, not analysed): pilot-1 (wave-barrier A1, superseded) and
  pilot-2 (3 rows, largest completion 6,017 of 16,384 tokens, no truncation).

## 10. Procedure and budget

The OpenRouter key has USD 45.57 left (2026-09-26) and will not be raised. Estimated cost from
the pilots: about USD 19 per batch (range 11-28) plus under USD 1 for T_dec, so the full plan
(about USD 39) may not fit with the budget guard, which reserves each row's worst case (up to
USD 7.53) before starting it.

1. **Analysis code first:** `scripts/cohort100_ab_analyze.py` implements sections 4-7, is
   validated on the pilot rows, and is committed before batch 1 starts.
2. **T_dec:** run on all 100 tasks.
3. **Batch 1** with `--budget-usd` = USD 44.50 minus spend so far (keeps about USD 1 on the key).
4. **Between batches, failure checks only:** invalid rows, truncation, retries/429s, unknown
   cost, spend. No speedup or accuracy summaries are looked at until both batches end.
5. **Batch 2** with `--require-code-of <batch 1 run>` and the remaining budget.
6. **If the budget stops batch 2 early:** because rows run in seeded random order, the completed
   rows are a random subset of batch 2. The primary analysis uses all completed rows; S on batch 1
   alone (which is complete and balanced) is reported beside it. No rows are reweighted or added.
7. Grade, then run the analysis script unchanged.

## 11. Registration

Not registered externally (author's decision, 2026-09-26). The approved text is fixed by the git
commit that adds this file and by the file's SHA-256, recorded in the run notes. Limitation: the
repository has no remote, so the commit timestamp is not independently verifiable.

## 12. Deviations from the Evaluation Methodology PDF

1. **No Proposed configuration** (capability table, router, A1 vs Proposed): out of scope here.
2. **Latency is execution time plus a separately measured decomposition time.** The DAGs come
   from the dataset, so no decomposition runs inside the timed execution; T_dec is measured
   separately. LongCoT prompts already list their subproblems, so T_dec is a lower bound for
   tasks that need real decomposition.
3. **Warm start.** Functions are pre-warmed before each arm; cold starts are handled in
   sensitivity 1, not in the primary estimate.
4. **C_LLM is the provider-reported cost,** reconciled against tokens x price (section 5.5).
5. **CP is counted in nodes** (CP = D); the duration-weighted critical path appears in section 5.1.

## 13. Limitations

- One model (gpt-5.2, low reasoning effort), one provider, one region; chemistry only.
- Dataset-authored DAGs with width at most 5, so little rate-limit pressure.
- Single sample per arm per row: per-row speedups carry model-latency noise (the pilots ranged
  0.98 to 3.12), which is why the estimate aggregates per template and replay is reported.
- Exposure is confounded with the easy templates; the evaluation partition was not a pristine
  holdout.
- 16 easy2 rows use a local policy on node 4; 16 rows contain impossible conditionals.
- Error replies without usage are assumed unbilled (unverified; symmetric between arms).
- C_Total excludes S3, CloudWatch, Secrets Manager and warm-up (warm-up reported separately).
- T_dec calls run from the author's machine, not a Lambda (adds tens of ms of network time).

## 14. Deviations log

1. **D1 (2026-09-27): T_dec runs after batch 1, not before.** Section 10 step 2 placed T_dec on
   all 100 tasks before batch 1; batch 1 (`eval-b1`) ran first and `dec-1` has not run yet. T_dec
   calls are independent of both arms and of batch order. It will run before batch 2, unchanged
   (`cohort100_ab_decompose.py c8190a1e…e3c0`); its cost now comes from the budget left after
   batch 1.
2. **D2 (2026-09-27): per-row results were visible during batch 1.** The frozen runner
   (`cohort100_ab_run.py 539a0457…ebce`) prints each row's arm times and speedup to stdout and to
   `runs/eval-b1.log`, so the blind in section 10 step 4 was not achievable as written. During the
   between-batch failure check, 9 rows' speedups (positions 1-5 and 47-50) and the per-arm
   `no_match` flags on easy2 node 4 were seen; no aggregate was computed at that point. The runner
   is not changed for batch 2 (section 9 requires its hash). No decision is left open that this
   information could influence: batch 2's rows, order, code, budget rule and analysis are all
   fixed above.
3. **D3 (2026-09-27): interim batch-1 analysis for drafting tables.** Before batch 2, the author
   chose to grade `eval-b1` and run `cohort100_ab_analyze.py` unchanged on it alone (without
   T_dec, output `analysis/eval-b1-interim.json`) to draft the results tables. This replaces the
   blind in section 10 step 4. Nothing in batch 2, the procedure, the budget rule or the analysis
   script changes as a result; the final analysis is still the one in section 10 step 7 on both
   batches.
4. **D4 (2026-09-27): second OpenRouter key for batch 2.** Section 10 said the key would not be
   raised. After D3, a simulation of the budget guard on batch 2's seeded order showed that the
   remaining key (USD 23.61) would stop batch 2 at about 33 of 50 tasks, because the guard reserves
   each task's worst case (up to USD 7.53). The author added a second key (secret
   `dag-orchestrator3.0`, separate OpenRouter account, about USD 9.75 usable). Key 1
   (`dag-orchestrator2.0`) is used first; when it runs out, the resulting 402 makes that row
   invalid (section 8), the run halts, the secret's value is replaced with key 2, and the run
   resumes with the same run name, re-running that row. `--budget-usd` is set to the two keys'
   combined balance minus about USD 1 and the T_dec spend. This changes only how many batch-2
   tasks can run, not the code, configuration, model, provider pin, prompts or analysis; the
   decision was made after the interim look (D3), which is stated here for that reason.
5. **D5 (2026-09-27): T_dec calls hit OpenRouter's new-account rate limit.** `dec-1` sends calls
   back to back (about 24 per minute); key 1's account is capped at 20 requests per minute for
   this model, so 20 calls returned HTTP 429 (unbilled; the script counts them at their worst-case
   bound). The frozen script was resumed under the same run name in paced chunks (at most 14
   calls, then a 65 s pause), which re-ran only the failed tasks. Final: 100/100 tasks ok, USD
   0.73, all served by openai/gpt-5.2 via OpenAI. Each task's T_dec comes from its successful
   call; a 429 reply is immediate and does not affect it.

Run notes (events handled by the rules above, not deviations):
- Batch 1: one A1 call on `hard2_39` stopped at max_tokens and was retried successfully.
- Batch 2 (`eval-b2`, completed 2026-09-27, key 1 only; key 2 was not needed): the A1 arm of
  `easy2_17` failed at node 5 after three HTTP 429 replies from OpenRouter admission control
  ("could not verify available credits for this request in time"). Per section 8 this is a
  valid row with a failed A1 arm; it was not re-run. One B0 call on
  `protein_1CRN_query_10_mask_4` had a retryable error with unknown cost and was retried
  successfully. These notes were written before batch 2's speedups or grades were computed.
