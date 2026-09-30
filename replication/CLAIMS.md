# Claims map: paper → evidence

Claims in *Dependency-Aware Task Decomposition and Parallel Multi-LLM Execution on Serverless
Workflows* (RouteFlow, WoAIS2 '26 submission) traced to analysis outputs, historical reports or external sources.
Line numbers refer to the margin numbers of the submitted PDF. Figures are not regenerated; the
numbers behind them are.

## Reproducing the numbers

Both commands are offline, use only the Python standard library, and read only committed run
records. Neither makes a model or AWS call. Tested on Python 3.14.5.

```
python scripts/cohort100_ab_analyze.py --runs eval-b1 eval-b2 --decompose dec-1 --out eval
python data/cohort100_ab/analysis/paper_numbers.py
```

1. Writes `data/cohort100_ab/analysis/eval.json`, the pre-registered B0 vs A1 analysis
   (`docs/cohort100_ab_preregistration_2026-09-26.md`).
2. Writes `data/cohort100_ab/analysis/paper_numbers.json`, the four configurations' performance,
   cost, accuracy and routing summaries.

Both outputs are shipped and regenerate byte-identical. `python replication/verify_tier1.py`
runs both commands and checks this. Bootstrap CIs are seeded: `cohort100-ab-boot-v1` for the analysis script and
`paper-todo-v1` for the task-mean CIs in `paper_numbers.py`.

This check reproduces the archived result summaries. It does not rerun the nine dataset checks
or the development/reserve coverage checks in the historical reports cited below, nor external
literature results. Accuracy here uses saved grades; tier 2 re-scores the answers. Both commands
above select the original archived runs.

## Notation

| Paper | Run records / code |
|---|---|
| B0, A1 | arms `B0`, `A1` of runs `eval-b1` + `eval-b2` |
| P-DeepSeek | run `proposed-3`, router preset `v3`; key `P-DeepSeek` in `paper_numbers.json` |
| P-Sonnet | run `proposed-4`, router preset `v1`; key `P-Sonnet` |
| Strong / Medium / Light tier | router tier `hard` / `medium` / `easy` (`cost_detail.*.tiers` already uses the paper's names) |
| Easy / medium / hard *difficulty* | `easy` / `medium` / `hard` groups in `paper_numbers.json` |
| T_c(t) | `T_E2E_s` per arm in `rows.jsonl` |
| S_c (Eq. 3) | `speedup_vs_B0_template_geomean.S` (same estimator as `eval.json` `primary`) |
| N, D, S* = N/D | `N`, `D` in `data/cohort100_ab/dags.json`; `N_over_D_mean` |
| C_LLM, C_Lambda, C_SFN, C_total | `C_LLM`, `C_Lambda`, `C_SFN`, `C_Total` |

Both manifests of P-DeepSeek and P-Sonnet record `router_version: proposed-router-v2`. The
preset (`router_preset`) is what distinguishes them.

In the paths below, `J` is `data/cohort100_ab/analysis/paper_numbers.json`.

## Section 3.1: dataset

| Claim (line) | Source |
|---|---|
| 100 tasks: 33 easy, 33 medium, 34 hard; ≥ 8 per template; fixed quota and seed (364–368) | `data/study_v1/audits/chemistry_deps_v1/cohort_100/validation_report.json`: `quota`, `seed` 20260922, `status_counts` |
| Nine automated checks, all 100 pass (369–378) | same file, `tasks[*].checks` (9 keys), `check_failures` = {} |
| 910 subtasks, 1,011 edges, 185 transitively redundant in 25 medium + all 34 hard tasks (378–380) | `dags.json`: sum of `N`, `edge_count`, `redundant_edges` |
| Easy N = 5, D = 4 (N/D 1.25); medium N/D 1.89; hard N 13.4, D 8.3 (N/D 1.57) (381–386) | `J` `{easy,medium,hard}.N_mean`, `D_mean`, `N_over_D_mean` |
| med4 and hard4 share a DAG (386) | `dags.json`: identical `N` + `edges` |

## Sections 2.4 and 3.2–3.3: router, configurations, deployment

| Claim (line) | Source |
|---|---|
| 41 ordered rules; 3,990 development + reserve subtasks all match (296, 425–427) | `src/proposed/router.py` `PROPERTIES`; `data/proposed_router/summary.json` `splits` (410 + 3,580, `unmatched` 0) |
| Table 2 models, providers, prices, shared settings | `runs/proposed-{3,4}/manifest.json` `tier_config`; `scripts/cohort100_ab_run.py` `CONFIG` |
| Standard workflow, 3 h timeout; Python 3.13, arm64, 512 MB, 900 s (407–412) | `src/cohort100_ab/asl.py`; `CONFIG` |
| Transient errors: 2 retries, 5 s, backoff; malformed output: 1 retry, same model (417–420) | `asl.py` `RETRY` (`ProviderRetryable`, `OutputInvalid`) |

## Table 3, Section 4.1, Figure 2: latency and speedup

| Claim (line) | Source |
|---|---|
| Correct 16 / 15 / 15 / 16 | `J` `all.{B0,A1,P-DeepSeek,P-Sonnet}.correct` |
| Median latency 155.8 / 124.6 / 125.2 / 112.1 s | `all.*.T_median_s` (all 100 tasks; a failed arm counts its time to failure) |
| Speedup 1.28× [1.23, 1.34], 1.34× [1.28, 1.40], 1.47× [1.41, 1.53] | `all.*.speedup_vs_B0_template_geomean` (`S`, `ci95`); A1 also equals `eval.json` `primary` |
| Cost $20.87 (−5.1%), $21.99, $20.10 (−8.6%), $20.40 (−7.2%) | `all.*.C_Total_sum`, `cost_ratio_vs_A1_Ctotal` |
| Figure 2 bars and error bars | `{easy,medium,hard,all}.*.speedup_vs_B0_template_geomean`; dashed lines `N_over_D_mean` |
| 22% latency reduction; medium 1.49×, easy 1.16×, hard 1.17× (518–524) | `1 − 1/S`; per-difficulty `S` |
| Spearman ρ = 0.81 across templates (525) | `eval.json` `H2.spearman_measured_vs_N_over_D` (template level) |
| 86% of ideal; 76 of 99 tasks below it (527–528) | `speedup_vs_ideal.A1.mean_measured_over_ideal`, `tasks_below_ideal` |
| Overhead 1.4–3.4 s per task, 1.1–2.4% of latency (529–530) | `all.*.overhead_mean_s`, `overhead_share_of_T` |
| P-Sonnet 1.14× [1.08, 1.21] over A1, 98% of ideal, above it on 37 tasks (533–536) | `all.P-Sonnet.speedup_vs_A1_template_geomean`; `speedup_vs_ideal.P-Sonnet` |
| P-DeepSeek 1.05× [0.99, 1.10] over A1 (537–539) | `all.P-DeepSeek.speedup_vs_A1_template_geomean` |

## Section 4.2, Figure 3, Table 4: cost

| Claim (line) | Source |
|---|---|
| LLM ≥ 98% of cost (582–583) | `J` `cost_detail.*.LLM_share` |
| Output tokens 97% reasoning (583–585) | `cost_detail.*.reasoning_share_of_output_tokens` |
| Serverless $0.15 (0.7%) for B0; $0.36–0.40 (1.8–1.9%) parallel (585–587) | `cost_detail.*.serverless_usd`, `serverless_share` |
| 1,010 transitions, $0.025 (589) | `cost_detail.*.sfn_transitions`, `C_SFN` |
| At most 1.2% of total saved by starting workers only when ready (593–594) | `cost_detail.*.lambda_wait_cost_share_of_C_Total` |
| A1 +5% vs B0; routing −8.6% / −7.2% vs A1, −3.7% / −2.2% vs B0 (595–599) | `all.*.cost_ratio_vs_{A1,B0}_Ctotal`; `C_Total_sum` |
| Routing 54% / 37% / 9%; light 3–12% by difficulty; multi-candidate 32 (all easy); tie-break 0 (601–606) | `routing_profile` |
| Strong ~1,800 output tokens per subtask (607) | `cost_detail.{P-DeepSeek,P-Sonnet}.tiers.strong.output_tokens_per_subtask` |
| GPT-5.2 73–76% of LLM cost (608) | `tiers.strong.cost_usd / C_LLM` |
| Gemini $0.013–0.014 vs $0.018 per subtask, 22–25% lower (609–611) | `tiers.medium.usd_per_subtask` (P-DeepSeek, P-Sonnet vs A1) |
| DeepSeek $0.0005 (85% below GPT-5.2); Sonnet $0.012 (3.5×) (613–616) | `tiers.light.usd_per_subtask` (P-DeepSeek, P-Sonnet vs A1) |
| Table 4 tokens and $/subtask | `cost_detail.{A1,P-DeepSeek,P-Sonnet}.tiers.*` |
| Figure 3 segments (e.g. A1 15.26 strong + 6.06 medium) | `cost_detail.*.tiers.*.cost_usd`; B0 bar = `cost_detail.B0.C_LLM` |

## Section 4.3: accuracy

| Claim (line) | Source |
|---|---|
| 15 or 16 of 100; no configuration solves a hard task (629–632) | `J` `*.correct` per difficulty |
| B0 solves 6 of 67 medium + hard tasks (632–633) | `medium.B0.correct` + `hard.B0.correct` |
| B0 and A1 disagree on 11 tasks (636) | `accuracy_overlap.B0_vs_A1_disagree` |
| LongCoT reports 10.1% for GPT-5.2 (433, 634) | external citation [20], not reproduced here |

## Claims the evidence does not support as written

| Line | Paper says | Evidence |
|---|---|---|
| 416–417, 529 | no timed run incurs a cold start | B0 had **15** cold starts across 10 tasks; A1, P-DeepSeek and P-Sonnet had 0 (`cost_detail.*.cold_starts`). Effect on S is negligible: cold-adjusted 1.2823 vs 1.2827 (`eval.json` `sensitivity.cold_start_adjusted`). |
| 584 | output tokens make up 83% of the cost | 82.4–86.1% (`output_token_share_of_C_LLM`): P-Sonnet 82.4, P-DeepSeek 82.8, B0 85.6, A1 86.1 |
| 587–588 | 910 invocations billed 51–54K s, $0.34–0.37 | Matches P-Sonnet and P-DeepSeek (910/911 invocations, 50.7K/54.5K s). A1: 913 invocations, 55.8K s, $0.372. |
| 591 | 67% of billed Lambda time is waiting | 65.4% (A1) – 67.1% (P-Sonnet) (`lambda_wait_share_of_billed`) |
| 607–608 | ~760 output tokens per medium subtask | 764 (P-Sonnet), 812 (P-DeepSeek) |

## Denominators

- **Latency medians and costs:** all 100 tasks per configuration. The one failed arm (A1 on
  `easy2_17`) contributes its time to failure and its cost.
- **Speedups:** the 99 tasks where both B0 and A1 succeeded (the pre-registered primary set).
  `docs/cohort100_ab_results_2026-09-28.md` reports medians over succeeded arms only, so its A1
  median (124.9 s) differs from Table 3's (124.6 s).
- **Accuracy:** a failed arm counts as incorrect.
