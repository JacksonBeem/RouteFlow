# RouteFlow replication package

Replication package for *Dependency-Aware Task Decomposition and Parallel Multi-LLM Execution on
Serverless Workflows*. It covers the four configurations in the paper, all run on the same 100
LongCoT chemistry tasks:

| Configuration | What it is | Run records |
|---|---|---|
| B0 | sequential, GPT-5.2 on every subtask | `data/cohort100_ab/runs/eval-b1`, `eval-b2` (arm `B0`) |
| A1 | parallel serverless workflow, GPT-5.2 on every subtask | same runs (arm `A1`) |
| P-DeepSeek | RouteFlow: light DeepSeek V4.1 Flash / medium Gemini 3.1 Pro / strong GPT-5.2 | `data/cohort100_ab/runs/proposed-3` |
| P-Sonnet | RouteFlow: light Claude Sonnet 4.5 / medium Gemini 3.1 Pro / strong GPT-5.2 | `data/cohort100_ab/runs/proposed-4` |

- **`CLAIMS.md`** maps paper claims to analysis outputs, historical reports or external sources. It also
  lists the five places where the paper's text and the evidence disagree.
- **`computational_requirements.md`** gives the software versions, seeds and costs for each tier.
- **Supported platform:** Windows x64 with CPython 3.14. Commands below use PowerShell from the package root.

## Three ways to reproduce

| Tier | Reproduces | Needs | Cost | Result |
|---|---|---|---|---|
| 1. Re-analysis | archived performance, cost and accuracy summaries | Python 3.14, standard library only | free, < 10 s | byte-identical |
| 2. Grading | the accuracy column (section 4.3) | RDKit + pinned LongCoT verifier | free, network for one download | same grades |
| 3. Re-execution | the experiment itself | AWS account + OpenRouter key | about USD 84 for all four | statistically comparable |

### Tier 1: re-analysis

From the package root:

```powershell
python replication/verify_tier1.py
```

This runs both analysis scripts, compares each regenerated output byte for byte with the shipped
file, and prints `MATCH` or `DIFF` for each. It exits with code 0 only if both match. The shipped
files are restored afterwards either way.

- `scripts/cohort100_ab_analyze.py --runs eval-b1 eval-b2 --decompose dec-1 --out eval`
  recomputes the pre-registered B0 vs A1 analysis into `eval.json`. The pre-registration is
  `docs/cohort100_ab_preregistration_2026-09-26.md`.
- `data/cohort100_ab/analysis/paper_numbers.py` recomputes the four configurations' result summaries into
  `paper_numbers.json`.

Both scripts read only the shipped run records under `data/cohort100_ab/`.
The historical dataset-validation and development/reserve router-coverage reports are included
as evidence; these commands do not regenerate them. Figures and external literature claims are
also outside this check. Accuracy is aggregated from the saved grades; tier 2 re-scores answers.

### Tier 2: grading

Grading uses the benchmark's own verifier at a pinned commit, with fallbacks off and the network
disabled while scoring. The LongCoT code and data are not committed. They are fetched at the
revisions pinned in `data/study_v1/manifests/sources.lock.json`. GitHub files and HF data files
are hash-checked; the three HF metadata files (`.gitattributes`, `LICENSE`, `README.md`) are
size-checked. Preparation needs the `hf` CLI and PyArrow in addition to the grading dependencies.

```powershell
python -m venv .aws-sam/study-prep-venv
.aws-sam/study-prep-venv/Scripts/python.exe -m pip install huggingface_hub==1.32.0 pyarrow==25.0.1
.aws-sam/study-prep-venv/Scripts/python.exe scripts/fetch_study.py longcot
.aws-sam/study-prep-venv/Scripts/python.exe scripts/import_study.py longcot
# Supply the legacy filename used for decomposition prompt lookup.
Copy-Item data/study_v1/normalized/longcot/tasks.jsonl data/study_v1/normalized/longcot/tasks.v3.jsonl

python -m venv .aws-sam/study-grade-venv
.aws-sam/study-grade-venv/Scripts/python.exe -m pip install --require-hashes -r data/study_v1/manifests/longcot.grader.requirements.lock
.aws-sam/study-grade-venv/Scripts/python.exe scripts/cohort100_ab_grade.py eval-b1
```

Fetching needs network access; importing and grading are offline. The import produces
`data/study_v1/evaluator_only/longcot/references.jsonl`. The copied task file supplies only the
`id`, `prompt` and `prompt_sha256` fields used by the decomposition runner: all 100 cohort
prompts and hashes match its original input. The copy does not recreate historical v3 graph annotations.

Repeat the last command for `eval-b2`, `proposed-3` and `proposed-4`. Each writes `grades.json`
in its run directory, which should match the committed file. Then re-run tier 1.

### Tier 3: re-execution

This tier makes paid model calls. Complete tier 2's preparation first, including the task-file
copy needed for decomposition. Install the runner dependencies and AWS CLI before deployment:

```powershell
python -m venv .aws-sam/runner-venv
.aws-sam/runner-venv/Scripts/python.exe -m pip install -r replication/environment/requirements-runner.txt
$RunnerPython = ".aws-sam/runner-venv/Scripts/python.exe"
aws --version
```

`--budget-usd` is a **pre-task admission threshold, not a hard spending cap**. It checks spend
so far plus an estimated one-attempt-per-node reserve before starting a task. Retries can exceed
that threshold. S3, CloudWatch and Secrets Manager charges are not included. The examples below
use USD 30 per baseline batch and USD 25 per routed run as illustrative thresholds, not guarantees
of completion or maximum charges. With the original records, USD 25 stops the baseline batches
after 44/50 and 46/50 tasks; admitting the full saved traces needs about USD 29.13 and USD 26.43.
Choose thresholds and available credit deliberately before launching.

1. **Store an OpenRouter key.** Put it in AWS Secrets Manager (us-east-1), either as a bare
   `sk-or-…` string or as a JSON object holding exactly one such key.
2. **Deploy the shared infrastructure:** an S3 bucket, two IAM roles and two Step Functions
   state machines.
   ```powershell
   $OpenRouterSecret = "your-secret-name"
   aws cloudformation deploy --region us-east-1 --template-file template.cohort100-ab.yaml --stack-name dag-orchestrator-c100ab --capabilities CAPABILITY_IAM --parameter-overrides "OpenRouterSecret=$OpenRouterSecret"
   ```
   Replace `your-secret-name` with the name from step 1. The runners create and delete one Lambda
   function per subtask for each task; the shared infrastructure remains deployed.
3. **Check the deployment without model calls:**
   ```powershell
   & $RunnerPython scripts/cohort100_ab_run.py --mode stub --source cohort --tasks easy1_6 --run-name my-stub
   & $RunnerPython scripts/proposed_run.py --mode stub --tasks easy1_6 --router v3 --run-name my-proposed-stub
   ```
   Stub mode still incurs AWS charges.
4. **Run the four configurations.** Each command writes a new run directory. The `--require-code-of`
   flag refuses to start unless the code hashes and configuration equal those of the named
   original run.
   ```powershell
   # B0 and A1, two batches of 50 (original spend about USD 43 in total)
   & $RunnerPython scripts/cohort100_ab_run.py --mode live --source cohort --batch 1 --run-name my-b1 --budget-usd 30 --require-code-of eval-b1
   & $RunnerPython scripts/cohort100_ab_run.py --mode live --source cohort --batch 2 --run-name my-b2 --budget-usd 30 --require-code-of eval-b1
   # decomposition time T_dec (original USD 0.73)
   & $RunnerPython scripts/cohort100_ab_decompose.py --source cohort --run-name my-dec --budget-usd 2
   # P-DeepSeek (original USD 20.10) and P-Sonnet (original USD 20.40)
   & $RunnerPython scripts/proposed_run.py --mode live --task-set all --router v3 --run-name my-p-deepseek --budget-usd 25
   & $RunnerPython scripts/proposed_run.py --mode live --task-set all --router v1 --run-name my-p-sonnet --budget-usd 25
   ```
   A `STOP before ...` message means the run is incomplete even if its exit code is 0. To resume,
   repeat that run's command with the same run name and configuration; raise the threshold only
   if further spending is intended. The threshold is cumulative for that run, not an additional
   allowance. Completed tasks are skipped. Before reporting a full-cohort result, confirm that
   every manifest task has a completed row (50 per baseline batch, 100 per routed run); a failed
   arm can still belong to a completed row and counts as an experimental outcome. For decomposition,
   all 100 task IDs must have an `ok` row; failed decomposition calls are retried on resume.
5. **Grade the new answers** as in tier 2, substituting the new run names. The tier-1 verifier
   and `paper_numbers.py` are fixed to the archived runs; they do not analyse the newly named runs.
   The supplied all-configuration paper summaries reproduce the original experiment only.

A re-execution will not reproduce the numbers exactly. Model outputs and latencies vary between
runs, and each original configuration ran once. The paper's confidence intervals describe
variation across tasks, not the expected range across repeated experiments.

Runner dependencies are listed in `environment/requirements-runner.txt`.

## Repository layout

| Path | Contents |
|---|---|
| `src/cohort100_ab/` | per-subtask worker (`node_worker.py`), Step Functions definitions (`asl.py`), cost model (`metrics.py`) |
| `src/proposed/router.py` | RouteFlow's rule-based router: 41 properties, two modifiers, tier-to-model presets |
| `scripts/cohort100_ab_*.py`, `scripts/proposed_*.py` | runners, grader, analysis |
| `data/cohort100_ab/nodes.jsonl`, `dags.json` | the 100 tasks' subtask specifications and dependency graphs |
| `data/cohort100_ab/runs/<run>/` | `manifest.json` (frozen configuration and code hashes), `rows.jsonl` (one row per task), `raw/` (every model attempt, Step Functions history and Lambda report), `grades.json` |
| `data/cohort100_ab/analysis/` | `eval.json`, `paper_numbers.json`, the scripts that produce them |
| `data/study_v1/audits/chemistry_deps_v1/cohort_100/validation_report.json` | the nine per-task dataset checks (paper section 3.1) |
| `docs/cohort100_ab_*` | pre-registration (with deviation log) and results report for B0 vs A1 |

Earlier wave-loop and scheduling prototypes from the parent project are not part of this release.

## Code names vs. paper names

In code and records, router tiers are called `easy` / `medium` / `hard`. The paper calls them
light / medium / strong. Task difficulty levels are separate and also called easy / medium / hard.
P-DeepSeek and P-Sonnet both record `router_version: proposed-router-v2`; their `router_preset`
(`v3`, `v1`) distinguishes them. `CLAIMS.md` has the full notation table.

## Data and licences

- **This package:** code and run records are under the MIT License (`LICENSE`).
- **LongCoT:** code and data are MIT-licensed (GitHub and Hugging Face
  `LongHorizonReasoning/longcot`). The run records here contain model answers and subtask
  specifications derived from LongCoT.
- **Canary:** LongCoT marks every example with a canary GUID so that training contamination can
  be detected. Please do not use this package's data to train models.

## Limitations

- **One run per configuration.** Confidence intervals reflect variation across tasks, not across
  repeated runs.
- **Different days.** P-DeepSeek and P-Sonnet ran on different days from B0 and A1, so their
  comparison with A1 is not fully controlled.
- **Unrecorded runtime version.** The Lambda runtime's own boto3 version was not recorded.
- **Tiers 2 and 3 not yet re-run.** Their commands have not been re-run from a clean copy for
  this package. Tier 1 has been re-run on the packaged copy.
