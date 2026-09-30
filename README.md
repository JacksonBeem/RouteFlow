# 🔀 RouteFlow: Dependency-Aware Task Decomposition and Parallel Multi-LLM Execution on Serverless Workflows

This repository contains the code, complete per-task run records, and analysis for **RouteFlow**,
a serverless orchestration approach for complex LLM tasks. RouteFlow decomposes a task into a
**dependency graph of subtasks**, routes each subtask to a **strong, medium or light LLM tier** by
analysing what it computes, and executes the graph as an **AWS Step Functions workflow** in which
independent subtasks run in parallel, each in its own AWS Lambda function.

We compare two strong-model baselines with two RouteFlow variants on **100 LongCoT chemistry
tasks** (33 easy, 33 medium, 34 hard; 910 subtasks). **B0** is sequential and **A1** is parallel;
both use GPT-5.2 for every subtask. The RouteFlow variants are **P-DeepSeek** and **P-Sonnet**.

> This package accompanies a paper under double-blind review. Author information will be added
> after review.

## 📦 Features

- **Complete run records** for all four configurations: every model attempt (tokens, cost,
  served model and provider), the Step Functions execution history and the Lambda billing report,
  for every one of the 100 tasks.
- **One-command reproduction of the paper's numbers**: `replication/verify_tier1.py` regenerates
  every table, figure value and in-text number from the shipped records. It is offline,
  standard-library-only and takes seconds, and the output must be byte-identical to the shipped
  files.
- **Claim-by-claim traceability**: [`replication/CLAIMS.md`](replication/CLAIMS.md) maps each
  number in the paper to the output field that produces it. It also lists the places where the
  paper's wording and the evidence disagree.
- **Pre-registered baseline comparison**: the B0 vs A1 analysis follows a pre-registration frozen
  before any evaluation data existed, with its deviation log
  ([`docs/`](docs/cohort100_ab_preregistration_2026-09-26.md)).
- **Frozen code**: every run manifest records the SHA-256 of the code that executed. The runner
  refuses to start a new run unless the code matches (`--require-code-of`).
- **Seeded statistics**: all bootstrap CIs (10,000 stratified resamples) and task orderings use
  named string seeds.

## 🔐 Prerequisites

- **Windows x64 with Python 3.14.** Reproducing the paper's numbers needs nothing else: no API
  key, no AWS account, no third-party packages.
- **Re-grading answers:** the pinned LongCoT verifier and RDKit (hash-locked in
  `data/study_v1/manifests/longcot.grader.requirements.lock`).
- **Re-executing the experiment:** an AWS account (us-east-1) and an
  [OpenRouter](https://openrouter.ai/) API key stored in AWS Secrets Manager. Runner dependencies
  are in `replication/environment/requirements-runner.txt`.

  ⚠️ Re-execution makes paid model calls: about USD 84 for all four configurations at the
  original prices.

## 🧭 Approach Overview

```
   complex task (LongCoT chemistry problem)
                  │
        ┌─────────▼─────────┐
        │ 1-2 Decomposition │  subtasks s1..sN, each with an instruction,
        └─────────┬─────────┘  an output format and its declared inputs
                  │
        ┌─────────▼─────────┐
        │ 3  Dependency DAG │  G = (S, E); depth D, width W; ideal speedup S* = N/D
        └─────────┬─────────┘
                  │
        ┌─────────▼─────────┐  41 ordered rules on WHAT the subtask computes
        │ 4  Tier routing   │  -> strong / medium / light, plus two modifiers
        └─────────┬─────────┘  (multi-candidate: up one tier; tie-break: light -> medium)
                  │
        ┌─────────▼─────────┐  one Step Functions Parallel state, one Lambda per subtask;
        │ 5  Serverless run │  each subtask starts as soon as its own predecessors finish
        └─────────┬─────────┘  (no barriers between levels)
                  │
        ┌─────────▼─────────┐
        │ 6  Final answer   │  sink subtask aggregates; scored by the LongCoT checker
        └───────────────────┘
```

| Configuration | Schedule | Strong tier | Medium tier | Light tier |
|---|---|---|---|---|
| **B0** | sequential (topological order) | GPT-5.2 | GPT-5.2 | GPT-5.2 |
| **A1** | parallel serverless workflow | GPT-5.2 | GPT-5.2 | GPT-5.2 |
| **P-DeepSeek** | parallel serverless workflow | GPT-5.2 | Gemini 3.1 Pro | DeepSeek V4.1 Flash |
| **P-Sonnet** | parallel serverless workflow | GPT-5.2 | Gemini 3.1 Pro | Claude Sonnet 4.5 |

All models were accessed through OpenRouter with the provider pinned, low reasoning effort, at
most 16,384 output tokens, strict JSON-schema output and no fallbacks. Lambda: Python 3.13,
arm64, 512 MB.

## 📊 Headline Results

| Config | Correct | Median latency | Speedup vs. B0 [95% CI] | Cost (vs. A1) |
|---|---|---|---|---|
| B0 | 16/100 | 155.8 s | 1.00× | $20.87 (−5.1%) |
| A1 | 15/100 | 124.6 s | 1.28× [1.23, 1.34] | $21.99 |
| P-DeepSeek | 15/100 | 125.2 s | 1.34× [1.28, 1.40] | $20.10 (−8.6%) |
| P-Sonnet | 16/100 | 112.1 s | 1.47× [1.41, 1.53] | $20.40 (−7.2%) |

- **Latency:** serverless parallelism alone cuts latency by 22%, and routing raises the speedup
  to 1.47×.
- **Cost:** routing lowers cost by 7–9% relative to the parallel baseline. The serverless layer
  (Lambda + Step Functions) costs under 2% of the total.
- **Accuracy:** no configuration changes accuracy measurably.

Speedup is a template-weighted geometric mean of per-task ratios, with a stratified bootstrap CI.
[`replication/CLAIMS.md`](replication/CLAIMS.md) gives the source of each value.

## 🔬 Results by Question

| Question (paper section) | Evidence | Output keys |
|---|---|---|
| How much of the ideal speedup N/D does serverless parallelism achieve, and does routing shorten latency further? (4.1) | `runs/eval-b1`, `eval-b2` (B0, A1), `proposed-3`, `proposed-4` | `paper_todo_data.json`: `*.speedup_vs_B0_template_geomean`, `fig_R2`; `eval.json` (pre-registered) |
| Where does the cost go, and how much does routing save? (4.2) | same runs, per-attempt token and cost records in `raw/` | `paper_todo_data.json`: `cost_detail`, `fig_R4_routing` |
| Does parallelism or routing change accuracy? (4.3) | `grades.json` in each run | `paper_todo_data.json`: `*.correct`, `accuracy_overlap` |

Run directory to configuration:

| Run (`data/cohort100_ab/runs/…`) | Configuration | Router preset |
|---|---|---|
| `eval-b1`, `eval-b2` | B0 and A1 (arms of the same run, order alternated per task) | — |
| `proposed-3` | P-DeepSeek | `v3` |
| `proposed-4` | P-Sonnet | `v1` |
| `decompose/dec-1` | decomposition time T_dec | — |

## 📂 Step-by-Step Workflow

### A. Reproduce the paper's numbers (no key, no AWS, seconds)

```powershell
python replication/verify_tier1.py
```

Expected output: `MATCH` for `eval.json` and `paper_todo_data.json`, exit code 0. The two scripts
it runs can also be called directly:

```powershell
python scripts/cohort100_ab_analyze.py --runs eval-b1 eval-b2 --decompose dec-1 --out eval
python data/cohort100_ab/analysis/paper_todo_data.py
```

### B. Re-grade the final answers (optional)

This step builds the grading venv from the hash-locked requirements, fetches the pinned LongCoT
verifier, and scores each run's final answers offline. The commands are in
[`replication/README.md`](replication/README.md#tier-2-grading).

### C. Re-execute the experiment (optional, paid)

The steps are: deploy `template.cohort100-ab.yaml`, check the deployment with a stub run (no model
calls), then run the four configurations with a budget cap. The full commands and original costs
are in [`replication/README.md`](replication/README.md#tier-3-re-execution).

A re-execution reproduces the results statistically, not exactly, because model outputs vary
between runs.

## 📁 Repository Structure

```
.
├── README.md
├── LICENSE                                  # MIT
├── replication/
│   ├── README.md                            # replication guide: all three tiers in detail
│   ├── CLAIMS.md                            # paper number -> output key, discrepancies
│   ├── computational_requirements.md        # versions, seeds, costs, hardware
│   ├── verify_tier1.py                      # one-command reproduction check
│   └── environment/                         # runner pins and venv freezes
│
├── src/
│   ├── cohort100_ab/
│   │   ├── node_worker.py                   # the per-subtask Lambda worker
│   │   ├── asl.py                           # B0 (chain) and A1/RouteFlow (parallel) state machines
│   │   ├── metrics.py                       # cost model and per-arm summaries (tokens, Lambda, SFN)
│   │   └── nodes.py                         # per-subtask input (instruction, context, output schema)
│   └── proposed/router.py                   # RouteFlow's tier router: 41 rules, 2 modifiers, presets
│
├── scripts/
│   ├── cohort100_ab_run.py                  # B0/A1 runner (deploy, warm, run, collect, tear down)
│   ├── proposed_run.py                      # RouteFlow runner (--router v1 | v3)
│   ├── cohort100_ab_decompose.py            # decomposition-time measurement
│   ├── cohort100_ab_grade.py                # offline grading with the LongCoT verifier
│   ├── cohort100_ab_analyze.py              # pre-registered analysis -> eval.json
│   ├── proposed_compare.py                  # per-task RouteFlow vs B0/A1 comparison
│   └── fetch_study.py, import_study.py, study/   # pinned LongCoT fetch + import (grading)
│
├── data/
│   ├── cohort100_ab/
│   │   ├── nodes.jsonl, dags.json           # the 100 tasks: subtasks and dependency graphs
│   │   ├── runs/<run>/                      # frozen run records, never modified
│   │   │   ├── manifest.json                #   configuration, routing, code hashes
│   │   │   ├── rows.jsonl                   #   one row per task: latency, tokens, cost, status
│   │   │   ├── raw/                         #   every model attempt, SFN history, Lambda reports
│   │   │   └── grades.json                  #   per-task correctness
│   │   ├── decompose/dec-1/                 # T_dec measurement
│   │   └── analysis/                        # eval.json, paper_todo_data.json + generator
│   ├── proposed_router/summary.json         # router coverage on 3,990 dev/reserve subtasks
│   └── study_v1/                            # dataset validation report, source and grader locks
│
├── docs/                                    # B0 vs A1 pre-registration and results report
└── template.cohort100-ab.yaml               # CloudFormation: bucket, roles, two state machines
```

## 🚀 Extending

- **Bind a tier to a different model:** add a preset to `MODEL_SETS` in `src/proposed/router.py`
  and the model's provider and prices to `ENDPOINTS` in `scripts/proposed_run.py`. Then run with
  `--router <preset>`. The routing rules don't change.
- **Change the routing rules:** edit the ordered `PROPERTIES` list and the two modifiers in
  `src/proposed/router.py`. Specific patterns must stay ahead of the generic ones they contain.
- **Run other tasks:** a task needs a subtask file in the `nodes.jsonl` format (instruction,
  output format, declared inputs). The runners take `--tasks` to select a subset.

## 👨‍🔬 Citation

Citation information will be added after the review period.

## 📜 Licence

Code and run records: MIT (`LICENSE`). LongCoT (code and data) is MIT-licensed by its authors.
LongCoT examples carry a canary GUID for contamination detection, so please do not use the
LongCoT-derived data in this package for model training. AWS account identifiers in the run
records are redacted as `000000000000`.
