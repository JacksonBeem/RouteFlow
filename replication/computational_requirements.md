# Computational requirements

Captured 2026-09-30 for the RouteFlow replication package (B0, A1, P-DeepSeek, P-Sonnet).
The package has three tiers. Each needs a different environment.

**Supported platform: Windows x64 with CPython 3.14.** Linux and macOS are not supported. The
re-analysis tier is standard-library only and should run anywhere, but the grading lockfile only
installs on Windows.

## Tier 1: re-analysis (reproduces archived result summaries)

- **Software:** Python 3.14.5 with the standard library only. Neither
  `scripts/cohort100_ab_analyze.py` nor `data/cohort100_ab/analysis/paper_numbers.py` imports a
  third-party package.
- **Inputs:** committed run records under `data/cohort100_ab/`. No network, AWS account or API
  key is needed.
- **Runtime:** 1.4 s (`eval.json`) and 5.0 s (`paper_numbers.json`) on the author's machine.
- **Expected result:** both outputs byte-identical to the committed files (verified 2026-09-30).
  Commands and the claim-by-claim map are in `CLAIMS.md`.
- **Other Python versions:** byte-identity has been checked on 3.14.5 only. The seeding is
  deterministic across Python 3 versions, but float formatting and `random` internals have not
  been verified on other versions.

## Tier 2: grading (re-scores final answers, section 4.3)

- **Preparation:** a separate Python 3.14 environment with huggingface_hub 1.32.0 (the `hf` CLI)
  and pyarrow 25.0.1. Use its Python for fetch and import, as shown in `README.md`.
- **Software:** `scripts/cohort100_ab_grade.py` with the upstream LongCoT verifier, run with
  fallbacks off and network disabled.
- **Lockfile:** `data/study_v1/manifests/longcot.grader.requirements.lock`, hash-pinned: rdkit
  2026.3.1, numpy 2.4.4, pillow 12.2.0, sympy 1.14.0, mpmath 1.3.0, chess 1.11.2. The author's
  grading venv matches it exactly (`environment/grade-venv.freeze.txt`).
- **Platform:** the lock's hashes are for the `cp314-win_amd64` wheels (plus the `chess` sdist),
  matching the supported platform.
- **Upstream pins:** LongCoT code at GitHub `LongHorizonReasoning/longcot` @ `fb9649423f15f5b0091f8e988b100596cac592ca`;
  data at Hugging Face `LongHorizonReasoning/longcot` @ `7f9948570f5239f0f3697c6c9793b4866860c681`.
  Both are recorded in `data/study_v1/manifests/sources.lock.json`. GitHub files and HF data
  files have hashes; the three HF metadata files have byte sizes only.

## Tier 3: stub and paid re-runs (re-executes the experiment)

- **Local runner:** Python 3.14.5 with `environment/requirements-runner.txt` (boto3 1.43.83,
  botocore 1.43.97, aws-sam-cli 1.166.2). AWS CLI 2.34.59.
- **Decomposition input:** after importing LongCoT, copy `tasks.jsonl` to the legacy
  `tasks.v3.jsonl` filename as shown in `README.md`. The runner uses only each selected task's
  ID, prompt and prompt hash; all 100 cohort inputs match the original decomposition input.
- **Cloud:** AWS us-east-1.
  - Step Functions Standard workflows, 3 h timeout.
  - One Lambda function per subtask: python3.13 on arm64, 512 MB, 900 s timeout. boto3 comes
    from the Lambda runtime, whose version AWS manages and the run records do not capture.
- **Models:** all served through OpenRouter with the provider pinned (paper Table 2). An
  OpenRouter key with enough credit is required.
- **Cost of the original runs** (C_total):
  - B0 + A1: USD 42.85, plus T_dec USD 0.73.
  - P-DeepSeek: USD 20.10.
  - P-Sonnet: USD 20.40.
- **Budget behavior:** `--budget-usd` checks an estimated one-attempt reserve before each task;
  retries can exceed it. It is not a hard spending cap. A budget stop requires resuming under
  the same run name if the full cohort is wanted; see `README.md`. Reported `C_total` excludes
  S3, CloudWatch, Secrets Manager and warm-up, and one B0 call has unknown cost.
- **Duration:** tasks run one at a time. Summed per-task latency was about 9.2 h for B0 + A1,
  4.0 h for P-DeepSeek and 3.6 h for P-Sonnet. Wall-clock time is longer, because of warm-up,
  settling and teardown for each task.
- **Expected result:** statistically comparable, not identical. Model outputs vary from run to
  run, and the original configurations each ran once.
- **Stub mode:** `--mode stub` runs the same workflows with a fixed-delay worker and no model
  calls. It checks the deployment without spending on models.

## Seeds and randomness

Every random draw in the package uses a named string seed. There are no unseeded draws.

| Use | Seed | Where |
|---|---|---|
| Task order within a batch | `cohort100-ab-order-v1` | `scripts/cohort100_ab_run.py` `ORDER_SEED` |
| Batch split per template | `cohort100-ab-batch-v1` | `cohort100_ab_run.py` `BATCH_SEED` |
| Speedup bootstrap CIs (10,000 resamples) | `cohort100-ab-boot-v1` | `scripts/cohort100_ab_analyze.py` |
| Task-mean bootstrap CIs (accuracy, cost) | `paper-todo-v1` | `data/cohort100_ab/analysis/paper_numbers.py` |
| Cohort sampling (100 tasks, fixed quota) | `20260922` | `cohort_100/validation_report.json` `seed` |

The LLM calls themselves are not seedable. The provider's default temperature applies (paper
Table 2).

## Author's machine

Intel Core Ultra 5 225F, 31.5 GB RAM, Windows 11 Home (build 26200). The re-analysis tier needs
none of this: any machine with Python 3 works.

## Paste-ready block

> **Computational requirements.** The archived performance, cost and accuracy summaries can be
> reproduced from the committed run records with Python 3.14.5 and its standard library alone.
> Two scripts regenerate these summaries in under 10 s on a desktop CPU, byte-identical to the
> committed outputs, with all bootstrap CIs
> seeded. Historical dataset-validation and development/reserve coverage reports are included
> separately and are not regenerated by this check. Preparing the grading inputs requires
> huggingface_hub 1.32.0 and PyArrow 25.0.1. Re-grading answers requires RDKit 2026.3.1 and the
> LongCoT verifier (pinned commit and hash-locked dependencies included). Re-executing the experiment requires an AWS account (Step
> Functions, Lambda python3.13/arm64, us-east-1), an OpenRouter key, and about USD 84 for all four
> configurations (the original spend). Because LLM outputs vary between runs, a re-execution reproduces the results
> statistically rather than exactly.
