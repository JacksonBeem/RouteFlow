# RouteFlow: Dependency-Aware Task Decomposition and Parallel Multi-LLM Execution on Serverless Workflows

This repository contains the code, the complete run records and the analysis behind the paper.
RouteFlow takes a complex LLM task and does three things with it:

1. splits it into subtasks connected by a dependency graph;
2. sends each subtask to a strong, medium or light LLM, depending on what the subtask computes;
3. runs the graph as an AWS Step Functions workflow in which independent subtasks run at the same
   time, each in its own AWS Lambda function.

The evaluation covers 100 LongCoT chemistry tasks (33 easy, 33 medium, 34 hard; 910 subtasks in
total).

> This package accompanies a paper under double-blind review. Author information will be added
> after review.

## Contents

1. [Configurations](#configurations)
2. [Results](#results)
3. [How a task is executed](#how-a-task-is-executed)
4. [Reproducing the results](#reproducing-the-results)
5. [Repository layout](#repository-layout)
6. [Licence](#licence)

## Configurations

The paper compares four configurations. All four run the same subtasks with the same prompts.

| Configuration | Schedule | Strong tier | Medium tier | Light tier |
|---|---|---|---|---|
| **B0** | sequential | GPT-5.2 | GPT-5.2 | GPT-5.2 |
| **A1** | parallel | GPT-5.2 | GPT-5.2 | GPT-5.2 |
| **P-DeepSeek** (RouteFlow) | parallel | GPT-5.2 | Gemini 3.1 Pro | DeepSeek V4.1 Flash |
| **P-Sonnet** (RouteFlow) | parallel | GPT-5.2 | Gemini 3.1 Pro | Claude Sonnet 4.5 |

- **B0 vs A1** isolates the effect of parallel execution: same model everywhere, different schedule.
- **A1 vs P-DeepSeek / P-Sonnet** isolates the effect of routing: same schedule, different models
  per subtask.

All models were accessed through OpenRouter with the provider pinned, low reasoning effort, at
most 16,384 output tokens, strict JSON-schema output and no fallbacks.

## Results

| Configuration | Correct | Median latency | Speedup vs. B0 [95% CI] | Total cost (vs. A1) |
|---|---|---|---|---|
| B0 | 16/100 | 155.8 s | 1.00× | $20.87 (−5.1%) |
| A1 | 15/100 | 124.6 s | 1.28× [1.23, 1.34] | $21.99 |
| P-DeepSeek | 15/100 | 125.2 s | 1.34× [1.28, 1.40] | $20.10 (−8.6%) |
| P-Sonnet | 16/100 | 112.1 s | 1.47× [1.41, 1.53] | $20.40 (−7.2%) |

- **Latency:** parallel execution alone cuts latency by 22%, and routing raises the speedup over
  B0 to 1.47×.
- **Cost:** routing lowers total cost by 7–9% relative to A1. Lambda and Step Functions together
  cost under 2% of the total.
- **Accuracy:** neither parallelism nor routing changes accuracy measurably.

[`replication/CLAIMS.md`](replication/CLAIMS.md) maps paper claims to analysis outputs,
historical reports and external sources.

## How a task is executed

### Planning (offline, before any run)

1. **Decomposition.** Each task is split into subtasks. Each subtask has an instruction, an
   output format and a list of the subtasks whose answers it needs. For the evaluation, the
   decomposition declared by the LongCoT problem is used, so that all configurations run
   identical subtasks. A decomposition call is still measured separately to report its latency.
2. **Dependency graph.** The declared inputs become the edges of a directed acyclic graph.
   - *Depth D:* the number of subtasks on the longest dependency chain.
   - *Ideal speedup N/D:* how much faster the task could be if every subtask took the same time
     (N subtasks in D rounds instead of N).
3. **Routing** (P-DeepSeek and P-Sonnet only). The router (`src/proposed/router.py`) reads each
   subtask's instruction and matches it against 41 ordered rules describing what the subtask
   computes. For example:
   - a reaction product → strong;
   - counting hydrogens → medium;
   - checking for a substructure → light.

   Two modifiers can then raise the tier. A subtask with no matching rule goes to the strong
   tier. Routing is a pure function of the text and makes no model call.

### Per-task lifecycle (the runner, on the local machine)

Tasks run **one at a time**. For each task, the runner does the following:

1. **Deploy.** Create one Lambda function per subtask (8 at a time). Each function contains the
   same worker code plus that subtask's fixed input. For RouteFlow configurations, it also holds
   the model, provider and token limit of the subtask's tier.
2. **Publish.** Publish the task's state machines: a chain for B0, one parallel state for
   A1 / RouteFlow.
3. **Run each configuration in turn.** B0 and A1 run back to back on the same functions, and
   which one goes first alternates from task to task. For each configuration:
   1. **Pre-warm** every function at once (16 at a time), so timed runs normally start warm.
      B0 still had 15 cold starts across 10 long tasks; their effect on the speedup is negligible.
   2. **Start** the Step Functions execution. The task's latency is the time from execution
      start to finish.
   3. **Collect**, after every invocation has reported: the execution history, each model call's
      record (tokens, cost, served model) and each function's Lambda billing report.
4. **Tear down.** Delete the functions and record the task's results as one row.

The two RouteFlow configurations each ran as a separate full pass over the 100 tasks, using
the same lifecycle with a single configuration per task.

### Inside one execution: sequential vs. parallel

Every subtask does the same work in every configuration:
1. read its predecessors' answers from S3;
2. build the prompt;
3. call its model once;
4. write its answer to S3.

Only answers stored in S3 pass between subtasks, never through the workflow's state. The
configurations differ only in **when** each subtask starts. For a task shaped like
S1 → (S2, S3) → S4 → S5:

```
B0 (sequential)      S1 ──► S2 ──► S3 ──► S4 ──► S5
                     latency ≈ sum of all subtask times

A1 / RouteFlow       S1 ──┬─► S2 ──┬─► S4 ──► S5
(parallel)                └─► S3 ──┘
                     latency ≈ longest dependency chain
```

- **B0: sequential.** The workflow is a chain of steps in dependency order. Each subtask is
  invoked only after the previous one has returned, even when the two are independent.
- **A1 and RouteFlow: parallel.** The workflow is a single parallel step with one branch per
  subtask, and all functions are invoked at the same moment.
  - Each function checks S3 every 0.2 s until all of its own predecessors' answers exist, and
    then calls its model. A subtask therefore starts as soon as its dependencies finish, with no
    barrier between levels. In the example, S2 and S3 run at the same time, and S4 starts the
    moment the slower of them finishes.
  - The cost is that waiting functions are billed while they wait. This is why the parallel
    configurations' Lambda cost is about three times B0's, though still under 2% of the total.
- **The final answer** is the answer of the last subtask (the graph's single sink). It is scored
  offline with the LongCoT benchmark's own checker.

### Retries (all configurations)

| Failure | Retries | Interval |
|---|---|---|
| Lambda service error | up to 3 | 2 s, doubling |
| Transient provider error (timeout, HTTP 429, 5xx) | up to 2 | 5 s, doubling |
| Malformed model output or other provider error | 1 | 1 s, same model (never escalated to another tier) |
| Rejected key or credit (HTTP 401, 402, 403) | none | — |
| Waited 600 s for predecessors | continued by a fresh invocation, up to 3 | 1 s |

## Reproducing the results

**Recompute the archived result summaries.** This needs Windows x64 and Python 3.14, and takes a
few seconds. No API key, AWS account or extra packages are required.

```powershell
python replication/verify_tier1.py
```

The script regenerates the two analysis outputs from the run records and compares them byte for
byte with the shipped files. Expected output is `MATCH` twice, with exit code 0. Historical
dataset-validation and development/reserve router-coverage reports are included separately;
this command does not regenerate those reports, figures or external literature claims.

Two further steps are optional and are described in
[`replication/README.md`](replication/README.md):
- **Re-grade the final answers** with the pinned LongCoT checker.
- **Re-run the experiment on AWS.** This makes paid model calls, about USD 84 for all four
  configurations. Model outputs vary from run to run, so a re-run reproduces the results
  statistically, not exactly.

[`replication/computational_requirements.md`](replication/computational_requirements.md) lists
software versions, seeds, costs and hardware.

## Repository layout

```
.
├── README.md
├── LICENSE
├── replication/
│   ├── README.md                      # full replication guide (grading, re-running on AWS)
│   ├── CLAIMS.md                      # paper claims -> analysis outputs, reports and external sources
│   ├── computational_requirements.md  # versions, seeds, costs, hardware
│   ├── verify_tier1.py                # recompute and check the archived result summaries
│   └── environment/                   # pinned dependencies
├── src/
│   ├── cohort100_ab/node_worker.py    # the subtask worker (one Lambda per subtask)
│   ├── cohort100_ab/asl.py            # the sequential (B0) and parallel (A1 / RouteFlow) workflows
│   ├── cohort100_ab/metrics.py        # latency and cost accounting
│   ├── cohort100_ab/nodes.py          # each subtask's fixed input
│   └── proposed/router.py             # RouteFlow's router: 41 rules, 2 modifiers, tier -> model presets
├── scripts/
│   ├── cohort100_ab_run.py            # runs B0 and A1
│   ├── proposed_run.py                # runs P-DeepSeek (--router v3) and P-Sonnet (--router v1)
│   ├── cohort100_ab_decompose.py      # measures decomposition time
│   ├── cohort100_ab_grade.py          # grades final answers with the LongCoT checker
│   ├── cohort100_ab_analyze.py        # B0 vs A1 analysis (pre-registered)
│   └── ...                            # dataset fetch/import for grading
├── data/
│   ├── cohort100_ab/nodes.jsonl       # the 100 tasks' subtasks
│   ├── cohort100_ab/dags.json         # their dependency graphs
│   ├── cohort100_ab/runs/             # run records (see below)
│   └── cohort100_ab/analysis/         # eval.json, paper_numbers.json and the script that builds them
├── docs/                              # B0 vs A1 pre-registration and results report
└── template.cohort100-ab.yaml         # AWS infrastructure (S3 bucket, IAM roles, two state machines)
```

Run records, one directory per run:

| Directory (`data/cohort100_ab/runs/`) | Configuration |
|---|---|
| `eval-b1`, `eval-b2` | B0 and A1 (two batches of 50 tasks) |
| `proposed-3` | P-DeepSeek |
| `proposed-4` | P-Sonnet |

Each run directory contains:
- `manifest.json`: configuration, routing and code hashes;
- `rows.jsonl`: one line per task, with latency, tokens, cost and status;
- `raw/`: every model call, the workflow history and the Lambda billing reports;
- `grades.json`: per-task correctness.

## Licence

- **This package:** code and run records are MIT-licensed (`LICENSE`).
- **LongCoT:** MIT-licensed by its authors. LongCoT examples carry a canary string for
  contamination detection, so please do not use the LongCoT-derived data in this package for
  model training.
- **Redaction:** AWS account identifiers in the run records are replaced by `000000000000`.
