# cohort100-ab results (2026-09-28)

Final results of the pre-registered B0-Sequential vs A1-Parallel comparison
(`docs/preregistration_B0_vs_A1.md`, approved `c636359`, deviations D1-D5 in its
section 14). Every number below comes from `data/cohort100_ab/analysis/eval.json`, produced by
`scripts/cohort100_ab_analyze.py` (unchanged since `3779fa8`) with
`--runs eval-b1 eval-b2 --decompose dec-1`, or from the run records it reads. Per-task and
per-arm detail, with every aggregate recomputed by formula, is in
`data/cohort100_ab/analysis/eval-tables.xlsx` (2,533 formulas, independently evaluated: 0 errors,
all checks match the script). Evidence commit `a8eea1a`, workbook commit `9a470c3`.

## Summary

- **H1 supported.** Running subtasks in parallel where dependencies allow (A1) finished faster
  than running them one at a time (B0): **S = 1.28, 95% CI [1.22, 1.34]**, n = 99 tasks over all
  10 templates, a **22.0% latency reduction**. Both batches agree (1.27 and 1.29).
- The ideal for these DAGs, replaying each task's measured node times, is 1.32 (dependency
  schedule). A1 reaches most of it; the gap is orchestration overhead.
- Adding decomposition time (mean 2.55 s) leaves the speedup at 1.28.
- A1 costs 4% more in total (C_Total ratio 1.04). Most of that is LLM cost (1.03x); A1's extra
  Lambda time spent waiting on predecessors adds about one more point.
- Accuracy (descriptive, no hypothesis): B0 16/100, A1 15/100; the arms disagree on 11 tasks.

## 1. What ran

- 100 LongCoT chemistry tasks (`cohort_100`, 910 subtasks, 10 templates), in two seeded batches
  of 50: `eval-b1` (started 2026-09-27 00:31 UTC) and `eval-b2` (completed 2026-09-27 17:25 EDT).
  Each task ran both arms back to back; which arm went first alternated by position.
- Model `openai/gpt-5.2` via OpenRouter, provider pinned to OpenAI, reasoning effort low,
  `max_tokens` 16384. Lambda python3.13 arm64 512 MB, us-east-1. All calls were served by
  gpt-5.2 via OpenAI (section 8 check).
- Code hashes for both batches match pilot-2 (`--require-code-of`): `asl.py 2c701786…`,
  `metrics.py 96371c47…`, `cohort100_ab_run.py 539a0457…`, `node_worker.py 3e963d4f…`.
- T_dec: `dec-1`, 100/100 tasks ok, USD 0.73 (D1: after batch 1; D5: paced resume after 20
  unbilled 429s).

**Completion.** B0 succeeded on 100/100 tasks, A1 on 99/100. The failed arm is `easy2_17` A1:
node 5 received three HTTP 429 replies from OpenRouter admission control ("could not verify
available credits for this request in time"). Under section 8 this is a valid row with a failed
arm, not re-run. It counts toward completion and accuracy but not toward speedup. No invalid
rows, no truncation left unresolved (one A1 call on `hard2_39` hit `max_tokens` and its retry
succeeded).

## 2. Primary estimate (H1)

| Estimate | Value |
|---|---|
| S, geometric-mean speedup, equal weight per template | **1.283** |
| 95% CI (stratified bootstrap, 10,000 replicates) | [1.225, 1.344] |
| Latency reduction 1 - 1/S | 22.0% |
| Row-pooled geometric mean (equal weight per task) | 1.265 |
| PDF latency reduction (T_B0 - T_A1)/T_B0, template-weighted | 18.6% |
| Tasks / templates in S | 99 / 10 |

The CI's lower bound is above 1, so **H1 is supported** by the pre-registered rule. For scale,
B0 took 185.1 s on average (median 155.8 s) and A1 147.4 s (median 124.9 s), over succeeded
arms.

## 3. Secondary estimates

**Replay (5.1) and wave barriers (5.2).**

| Schedule, from each arm's node times | From B0 | From A1 |
|---|---|---|
| Dependency replay (ideal parallel) | 1.316 [1.299, 1.332] | 1.325 [1.306, 1.344] |
| Wave-barrier replay | 1.223 | 1.230 |
| Wave-barrier loss | 6.7% | 6.9% |

The measured S (1.28) is about 0.03 below the dependency ideal. Waiting per dependency instead
of per wave gains about 7%: the earlier wave-loop design would have landed near 1.22.

**Orchestration overhead (5.3).** A1: 3.67 s per task beyond the ideal critical path. B0: 3.16 s
per task beyond the sum of its node times (template means).

**Decomposition time (5.4).** Mean T_dec 2.55 s. Speedup with T_dec added to both arms:
**1.277 [1.220, 1.338]**. The returned DAG matched the dataset DAG exactly on 69/100 tasks.
LongCoT prompts already list their subproblems, so T_dec is a lower bound for tasks that need
real decomposition (prereg 12.2).

**Cost (5.5).**

| USD, both batches | B0 | A1 |
|---|---|---|
| C_LLM (OpenRouter-reported) | 20.720 | 21.590 |
| C_Lambda | 0.123 | 0.372 |
| C_Total | 20.865 | 21.988 |
| C_Total_upper | 21.123 | 21.988 |

Template-weighted ratios A1/B0: C_LLM 1.030, C_Total 1.041. A1's Lambda cost is three times
B0's because each waiting subtask is a running Lambda: 36,522 billed Lambda-seconds of waiting.
In absolute terms A1 cost USD 1.12 more over 100 tasks: USD 0.87 LLM and USD 0.25 Lambda. The
LLM difference comes from token counts (the arms use identical prompts); its cause is not
analysed here.

Reconciliation: tokens x list price equals the reported C_LLM for every arm except
`protein_1CRN_query_10_mask_4` B0 (+USD 0.01714), which had one retryable error with unknown
cost; its tokens are counted but the provider reported no cost for that attempt. That arm's
C_Total_upper is USD 0.506 vs C_Total 0.249, which is the whole B0 upper-bound gap above.
Tasks with retried, 429 or unknown-cost calls: `hard2_39`, `protein_1CRN_query_10_mask_4`,
`easy2_17`.

## 4. Per template

| Template | Tasks | Both ok | N/D | S | Replay B0 | Replay A1 | C_Total A1/B0 |
|---|---|---|---|---|---|---|---|
| easy1 | 17 | 17 | 1.25 | 1.29 | 1.38 | 1.34 | 1.05 |
| easy2 | 16 | 15 | 1.25 | 1.04 | 1.07 | 1.07 | 1.04 |
| hard1 | 9 | 9 | 1.78 | 1.34 | 1.26 | 1.27 | 0.99 |
| hard2 | 9 | 9 | 1.60 | 1.09 | 1.26 | 1.26 | 1.11 |
| hard3 | 8 | 8 | 1.67 | 1.20 | 1.47 | 1.41 | 1.22 |
| hard4 | 8 | 8 | 1.20 | 1.05 | 1.08 | 1.09 | 1.02 |
| med1 | 9 | 9 | 2.25 | 1.55 | 1.58 | 1.63 | 1.06 |
| med2 | 8 | 8 | 2.25 | 1.59 | 1.52 | 1.61 | 0.97 |
| med3 | 8 | 8 | 1.83 | 1.73 | 1.63 | 1.71 | 1.00 |
| med4 | 8 | 8 | 1.20 | 1.15 | 1.07 | 1.07 | 0.99 |

easy2 is the weakest template (1.04 against N/D 1.25). Its replay ideal is also low (1.07): with
its measured node times, overlapping the parallel nodes saves only about 7% of the work time, so
the low speedup comes from the node durations, not from the scheduler.

**H2 (descriptive).** Spearman correlation of template S with replay (B0 durations) is 0.87,
with N/D 0.81. The measured speedup follows the duration-weighted ideal slightly more closely
than the node-count ideal, as H2 predicted. With 10 templates and only 7 distinct DAG shapes this
is not a test and the difference is small.

## 5. Sensitivity analyses

| Analysis (prereg section 6) | S | Tasks |
|---|---|---|
| Primary | 1.283 | 99 |
| 1. Cold-start adjusted | 1.282 | 99 |
| 2. Without exposed tasks | 1.315 | 66 (8 templates) |
| 2. Without flagged-impossible tasks | 1.292 | 83 |
| 2. Without easy2-policy tasks | 1.313 | 84 (9 templates) |
| 3. B0 ran first | 1.357 | 50 |
| 3. A1 ran first | 1.246 | 49 |
| 4. Batch 1 (`eval-b1`) | 1.274 | 50 |
| 4. Batch 2 (`eval-b2`) | 1.293 | 49 |
| 5. Failed arm = time at failure | 1.281 | 100 |
| 6. Without retried / 429 / unknown-cost tasks | 1.288 | 97 |
| 8. Without other served models | 1.283 | 99 (none served) |

Every sensitivity result stays between 1.24 and 1.36. Cold starts barely matter: B0 had 15 in
total and A1 none (both arms are pre-warmed; the prereg expected late B0 nodes to go cold on long
runs). Dropping exposed tasks removes the easy
templates and changes the template mix, so its higher value is not a cleaner estimate.

**Arm order.** The order effect is the largest spread here: 1.36 when B0 ran first vs 1.25 when
A1 ran first, and the same direction appears in each batch. Order alternated (50 / 49), so the
primary estimate averages over it, but it adds variance. Two post-hoc checks, not
pre-registered:

- *Prompt caching is ruled out.* Only one second-run arm (of 100) had any cached input tokens
  (10,880 in total), and no first-run arm had any.
- *Both arms are faster when they run second.* Each arm's time relative to its template mean,
  template-weighted: B0 x1.016 when first vs x0.989 when second (about 3% faster second); A1
  x1.019 vs x0.961 (about 6%). The two groups are different tasks, so this does not isolate a
  cause; it is consistent with a general advantage for whichever arm runs second. The mechanism
  is unknown.

## 6. Accuracy (descriptive only)

Graded with the upstream LongCoT verifier over all 100 tasks (a failed arm counts as
incorrect): **B0 16/100, A1 15/100**; the arms disagree on 11 tasks. No equivalence or
difference test was pre-registered. Both arms use the same prompts, model and settings; only
the schedule differs.

## 7. Deviations and run notes

Five deviations are logged in prereg section 14; none changed the code, configuration, model,
prompts or analysis script.

- **D1** T_dec ran after batch 1 instead of before (it is independent of both arms).
- **D2** Per-row results were visible during batch 1 (the frozen runner prints them); 9 rows'
  speedups were seen before batch 2.
- **D3** Interim batch-1 analysis before batch 2 (`analysis/eval-b1-interim.json`: S 1.27);
  superseded here.
- **D4** A second OpenRouter key was added for batch 2 after the interim look; it was never
  used (key 1 covered the whole batch).
- **D5** T_dec calls hit OpenRouter's new-account 20 requests/minute cap; resumed in paced
  chunks, 100/100 ok.

Run notes: the `hard2_39` truncation retry (batch 1), the `easy2_17` A1 failure and the
`protein_1CRN_query_10_mask_4` unknown-cost retry (batch 2) are handled by section 8 rules above.

## 8. Limitations

From prereg section 13, all still apply: one model, provider and region; chemistry only;
dataset-authored DAGs of width at most 5 (little rate-limit pressure); one sample per arm per
task; exposure confounded with the easy templates; 16 easy2 tasks under a local policy and 16
with impossible conditionals; error replies without usage assumed unbilled; C_Total excludes
S3, CloudWatch, Secrets Manager and warm-up; T_dec measured from the author's machine. Also:
the pre-registration was not registered externally and the repository has no remote, so its
timestamp is not independently verifiable (prereg section 11). Added by these results: an
unexplained arm-order effect (section 5) of about 9% between the two orders, balanced by design
but not understood.

Spend: USD 42.85 for the two batches (C_Total, both arms; upper bound USD 43.11) plus USD 0.73
for T_dec.
