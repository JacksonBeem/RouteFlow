# Data for the TODO sections of agentic_serverless.pdf

Generated 2026-09-30 from `paper_todo_data.py` (numbers in `paper_todo_data.json`). Offline, from run records only.

Sources:
- **B0, A1:** eval-b1 + eval-b2 (run 2026-09-27).
- **proposed-3:** router v3 (light DeepSeek V4.1 Flash / medium Gemini 3.1 Pro / strong GPT-5.2), run 2026-09-29.
- **proposed-4:** router v1 (light Sonnet 4.5 / medium Gemini 3.1 Pro / strong GPT-5.2), run 2026-09-29/30.

All four ran the same 100 cohort tasks. The speedup estimator is the pre-registered one: a template-weighted geometric mean of per-task ratios, with a stratified bootstrap 95% CI (10,000 resamples). It reproduces the pre-registered A1 result exactly (S = 1.283 [1.225, 1.344], n = 99).

> **Decision needed first:** §3.3 says the medium and light tiers both use DeepSeek V4.1 Flash (router v2). That binding only ran on 21 tasks, never on all 100. The two full-100 runs are proposed-3 and proposed-4, so pick one as "Proposed". If you pick proposed-4, the §3.3 sentence "the light-tier model cost 3.4× more … so it was dropped" describes Sonnet 4.5, the very model proposed-4 uses as its light tier.

---

## Abstract (line 127), Introduction (line 331), Conclusion (line 974): headline results

Configuration columns are left to right: B0, A1, proposed-3, proposed-4.

| Metric (100 tasks) | B0 | A1 | proposed-3 | proposed-4 |
|---|---|---|---|---|
| Accuracy (correct/100) [95% CI] | 16 [0.10, 0.22] | 15 [0.09, 0.21] | 15 [0.09, 0.21] | 16 [0.10, 0.22] |
| Speedup vs B0, template geomean [95% CI] | 1 | 1.283 [1.225, 1.344] | 1.340 [1.280, 1.404] | 1.467 [1.406, 1.531] |
| Latency reduction vs B0 (1 − 1/S) | — | 22.0% | 25.4% | 31.8% |
| Median per-task speedup vs B0 | — | 1.272 | 1.236 | 1.384 |
| Speedup vs A1 [95% CI] | — | 1 | 1.045 [0.99, 1.10] | 1.144 [1.082, 1.207] |
| Median T_E2E per task | 155.8 s | 124.6 s | 125.2 s | 112.1 s |
| Summed T_E2E | 18,506 s | 14,668 s | 14,256 s | 12,869 s |
| C_Total, 100 tasks (USD) | 20.87 | 21.99 | 20.10 | 20.40 |
| C_LLM, 100 tasks (USD) | 20.72 | 21.59 | 19.71 | 20.04 |
| C_Total relative to A1 | 0.949 | 1 | 0.914 (−8.6%) | 0.928 (−7.2%) |
| C_Total relative to B0 | 1 | 1.054 | 0.963 (−3.7%) | 0.978 (−2.2%) |

Accuracy overlap (task-level agreement):
- **A1 vs proposed-3:** both correct on 8 tasks; 7 correct only under proposed-3, 7 only under A1.
- **A1 vs proposed-4:** both correct on 9 tasks; 7 correct only under proposed-4, 6 only under A1.
- **Reference points:** B0 and A1 (same model) disagree on 11 tasks. proposed-3 and proposed-4 disagree on 9.

Caveats for the headline claims:
- **Separate days:** Proposed ran on different days from B0/A1, so the speedup vs A1 is not a controlled comparison.
- **Accuracy:** the differences between configurations are within run-to-run noise.

## §3.3, line 783: Step Functions workflow type

- **Workflow type:** Standard, with a 3 h execution timeout.
- **Parallel structure:** each configuration's workflow is one Parallel state with a branch per subtask.

## §3.3, line 785: Lambda runtime, memory, timeout, region

- **Runtime:** Python 3.13 on arm64.
- **Memory:** 512 MB.
- **Timeout:** 900 s.
- **Region:** us-east-1.
- **Lifecycle:** one function per subtask, pre-warmed before timing and torn down after each task. Timed runs therefore have 0 cold starts.

## §3.3, line 798: reasoning effort and decoding settings

- **Settings shared by every tier:**
  - Reasoning effort "low".
  - max_tokens 16,384.
  - Strict JSON-schema structured output.
  - Provider pinned, with fallbacks disabled and required parameters enforced.
  - Temperature left at the provider default.
- **Providers and prices** (OpenRouter endpoint list, USD per million tokens, input/output):

| Model | Provider | Input | Output |
|---|---|---|---|
| GPT-5.2 (strong; every node of B0 and A1) | OpenAI | 1.75 | 14 |
| Gemini 3.1 Pro (medium, proposed-3 and proposed-4) | Google AI Studio | 2 | 12 |
| Sonnet 4.5 (light, proposed-4) | Anthropic | 3 | 15 |
| DeepSeek V4.1 Flash (light, proposed-3) | DeepInfra (fp8) | 0.14 | 0.42 |

- **Retry policy (Step Functions):**
  - Transient provider errors: up to 2 retries, 5 s initial interval, 2× backoff.
  - Malformed output: 1 retry after 1 s, on the same model, never escalated to a stronger tier.

## §3.4, line 937: repetitions r

- **r = 1** for every configuration.
- **B0 and A1:** ran in the same row, with their order alternated from task to task.
- **Proposed:** not interleaved with the baselines; each Proposed run was a separate full pass on its own day.

---

## §4 Results (line 942): data per planned figure/table

### Table R1 / Fig. R1: accuracy, latency and speedup by difficulty

| Difficulty (n) | Config | Correct [95% CI] | Median T_E2E | Speedup vs B0 [95% CI] | Median per-task speedup |
|---|---|---|---|---|---|
| Easy (33) | B0 | 10 [0.15, 0.45] | 82.8 s | — | — |
| | A1 | 11 [0.18, 0.48] | 72.5 s | 1.159 [1.072, 1.249] | 1.126 |
| | proposed-3 | 13 [0.24, 0.55] | 58.8 s | 1.267 [1.127, 1.403] | 1.203 |
| | proposed-4 | 12 [0.21, 0.52] | 64.2 s | 1.338 [1.238, 1.437] | 1.333 |
| Medium (33) | B0 | 6 [0.06, 0.30] | 189.5 s | — | — |
| | A1 | 4 [0.03, 0.21] | 122.1 s | 1.486 [1.398, 1.579] | 1.507 |
| | proposed-3 | 2 [0.00, 0.15] | 128.7 s | 1.590 [1.485, 1.699] | 1.487 |
| | proposed-4 | 4 [0.03, 0.24] | 112.4 s | 1.729 [1.606, 1.865] | 1.748 |
| Hard (34) | B0 | 0 [0, 0] | 293.4 s | — | — |
| | A1 | 0 [0, 0] | 234.3 s | 1.165 [1.063, 1.279] | 1.165 |
| | proposed-3 | 0 [0, 0] | 222.7 s | 1.162 [1.075, 1.255] | 1.163 |
| | proposed-4 | 0 [0, 0] | 200.8 s | 1.304 [1.224, 1.397] | 1.238 |

Speedup vs A1, by difficulty:
- **proposed-3:** easy 1.093 [0.983, 1.211]; medium 1.070 [0.996, 1.148]; hard 0.997 [0.902, 1.105].
- **proposed-4:** easy 1.155 [1.055, 1.262]; medium 1.164 [1.065, 1.268]; hard 1.119 [1.018, 1.236].

Notes:
- **Easy speedup n:** easy speedups use 32 of the 33 tasks, because one easy task had a failed baseline arm.
- **Hard accuracy:** every configuration gets 0/34 on hard, so those CIs are degenerate.

### Fig. R2: measured speedup (B0 → config) vs ideal N/D, per task

| Config | n | Tasks below ideal | Tasks above ideal | Mean measured/ideal | Spearman ρ (per task) |
|---|---|---|---|---|---|
| A1 | 99 | 76 | 23 | 0.86 | 0.47 |
| proposed-3 | 99 | 65 | 34 | 0.92 | 0.48 |
| proposed-4 | 99 | 62 | 37 | 0.98 | 0.57 |

Mean DAG structure by difficulty:

| Difficulty | N | D | N/D |
|---|---|---|---|
| Easy | 5.0 | 4.0 | 1.25 |
| Medium | 8.8 | 4.7 | 1.89 |
| Hard | 13.4 | 8.3 | 1.57 |
| All | 9.1 | 5.7 | 1.57 |

- **Template-level Spearman:** the pre-registered 10-template correlation (A1 measured speedup vs N/D) is 0.813.
- **Wave-barrier loss (pre-registered, A1):** 6.9%. This is how much slower a wave-barrier schedule would be than the per-node dependency schedule actually used.

### Fig. R3: mean cost per task (USD), split into LLM and Lambda + Step Functions

| Difficulty | Config | C_LLM | C_Lambda | C_StepFunctions | C_Total [95% CI] |
|---|---|---|---|---|---|
| Easy | B0 | 0.0853 | 0.00057 | 0.00012 | 0.0860 [0.0793, 0.0929] |
| | A1 | 0.0884 | 0.00134 | 0.00015 | 0.0899 [0.0825, 0.0978] |
| | proposed-3 | 0.0783 | 0.00123 | 0.00015 | 0.0797 [0.0726, 0.0884] |
| | proposed-4 | 0.0804 | 0.00115 | 0.00015 | 0.0817 [0.0771, 0.0869] |
| Medium | B0 | 0.2329 | 0.00133 | 0.00022 | 0.2345 [0.2214, 0.2481] |
| | A1 | 0.2331 | 0.00313 | 0.00024 | 0.2364 [0.2207, 0.2536] |
| | proposed-3 | 0.2238 | 0.00297 | 0.00024 | 0.2270 [0.2139, 0.2409] |
| | proposed-4 | 0.2205 | 0.00279 | 0.00024 | 0.2236 [0.2118, 0.2360] |
| Hard | B0 | 0.3006 | 0.00177 | 0.00034 | 0.3027 [0.2863, 0.3191] |
| | A1 | 0.3230 | 0.00661 | 0.00036 | 0.3299 [0.3071, 0.3523] |
| | proposed-3 | 0.2864 | 0.00662 | 0.00036 | 0.2934 [0.2749, 0.3136] |
| | proposed-4 | 0.2972 | 0.00613 | 0.00036 | 0.3037 [0.2870, 0.3215] |
| All | B0 | 0.2072 | 0.00123 | 0.00023 | 0.2087 [0.2013, 0.2163] |
| | A1 | 0.2159 | 0.00372 | 0.00025 | 0.2199 [0.2100, 0.2296] |
| | proposed-3 | 0.1971 | 0.00363 | 0.00025 | 0.2010 [0.1927, 0.2093] |
| | proposed-4 | 0.2004 | 0.00338 | 0.00025 | 0.2040 [0.1967, 0.2114] |

- **Serverless share:** Lambda plus Step Functions is under 2% of C_Total everywhere.
- **Why A1 and Proposed Lambda cost more than B0's:** in the parallel configurations every subtask's function starts at once and waits (billed) for its predecessors.

Same-node LLM cost by router tier: what each Proposed run paid on a tier's nodes vs what A1 paid on those same nodes.

| Tier (nodes) | proposed-3 | proposed-4 | A1 on the same nodes |
|---|---|---|---|
| Light (78) | 0.039 | 0.934 | 0.265 |
| Medium (338) | 4.750 | 4.554 | 6.064 |
| Strong (494) | 14.921 | 14.549 | 15.262 |

The strong tier is GPT-5.2 in all three runs, so its gap reflects run-to-run variation, not routing.

### Fig. R4: routing profile

The same routing for proposed-3 and proposed-4, since the rules don't depend on which model a tier binds to.

| Difficulty | Subtasks | Light | Medium | Strong |
|---|---|---|---|---|
| Easy | 165 | 17 (10.3%) | 67 (40.6%) | 81 (49.1%) |
| Medium | 289 | 8 (2.8%) | 116 (40.1%) | 165 (57.1%) |
| Hard | 456 | 53 (11.6%) | 155 (34.0%) | 248 (54.4%) |
| All | 910 | 78 (8.6%) | 338 (37.1%) | 494 (54.3%) |

Modifiers:
- **Multi-candidate:** fired 32 times, all in easy tasks.
- **Tie-break:** fired 0 times.
- **Unmatched instructions:** 0 (every subtask matched one of the 41 rules).

### Fig. R5 (optional): where time goes

| Config | Mean overhead per task | Share of T_E2E | Cold starts | Step Functions transitions (100 tasks) |
|---|---|---|---|---|
| B0 | 2.88 s | 1.6% | 0 | — |
| A1 | 3.39 s | 2.3% | 0 | — |
| proposed-3 | 3.43 s | 2.4% | 0 | 1,011 |
| proposed-4 | 1.41 s | 1.1% | 0 | 1,010 |

- **Overhead definition:** T_E2E minus the critical path, with each subtask weighted by its measured work time. For B0, overhead is T_E2E minus the summed work.
- **Proposed reliability:**
  - Both runs completed 100/100 tasks.
  - proposed-3 had 1 retry, caused by 1 truncated output.
  - proposed-4 had 0 retries.
  - Neither run hit a rate-limit error.

---

## Paper statements that don't match the implementation

- **§2.5, waves and barriers:** the paper says each wave is a Parallel state and its fan-in is a barrier. The A1/Proposed state machines instead use per-node dependency scheduling: one Parallel state, where each subtask starts as soon as its own predecessors succeed. There are no wave barriers.
- **§3.3, malformed output:** the paper says it is "recorded as a failure". It is actually retried once on the same model, and never escalated.
- **§3.3, model binding:** the text describes router v2 (DeepSeek on both lower tiers), which never ran on 100 tasks. See the decision note at the top.
- **§3.4, repetitions and interleaving:** the paper says tasks run r times in interleaved order. In fact r = 1, and Proposed was not interleaved with B0/A1. With r = 1, every CI above reflects variation across tasks, not variation across repeated runs.
