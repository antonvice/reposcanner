# reposcanner

`reposcanner` is a small CLI for generating one repository metadata row for code dataset QA and repository sale preparation.

It runs locally inside a checked-out repository and emits JSON, JSONL, or YAML. The CLI uses `rich` for progress, logs, summary tables, and a sale-fit HUD.

If the analysis reports Tier 1 / high probability of sale, email `hello@grably.us` at GRABLY Inc. with a short note that you would like to sell the repository and work out a deal. Attach the prepared sample zip to that message; without the sample, GRABLY cannot evaluate it. The repository and sample must be anonymized before creating or sending the sample.

## Install

From GitHub:

```bash
uv tool install git+https://github.com/antonvice/reposcanner.git
```

From a local checkout:

```bash
git clone https://github.com/antonvice/reposcanner.git
cd reposcanner
uv tool install .
```

For development:

```bash
git clone https://github.com/antonvice/reposcanner.git
cd reposcanner
uv sync
uv run reposcanner --help
```

## Quick Start

Run inside the repository you want to scan:

```bash
reposcanner scan --repo . --pretty --output repo_metadata.json
```

By default, `reposcanner` shows a live terminal HUD with progress bars, scan logs, a summary panel, and a language distribution table. The HUD writes to stderr, so stdout remains safe for JSON/JSONL piping.

Core metadata columns only:

```bash
reposcanner scan --repo . --schema core --format json --pretty --output repo_metadata.json
```

JSONL:

```bash
reposcanner scan --repo . --schema core --format jsonl --output repo_metadata.jsonl
```

YAML:

```bash
reposcanner scan --repo . --format yaml --output repo_metadata.yaml
```

Set a stable repository id:

```bash
reposcanner scan --repo . --repo-id 61860335-aa82-4a5b-93b9-ac0a8bb35a9f --pretty
```

Print to stdout:

```bash
reposcanner scan --repo . --format jsonl
```

Disable the HUD for scripts or CI:

```bash
reposcanner scan --repo . --no-hud --format jsonl
```

Prepare a code sample zip after anonymizing the repository:

```bash
reposcanner scan --repo . --prep-sample --sample-output ./out --pretty --output repo_metadata.json
```

## Output Schemas

Default schema is `extended`. It emits the base metadata columns plus useful local extras:

- `token_stats.estimated_code_tokens`
- `token_stats.estimated_text_tokens`
- `token_stats.tokens_by_language`
- `token_stats.tokens_by_extension`
- `sale_prediction.tier`
- `sale_prediction.sale_probability`
- `sale_prediction.similarity_to_sold`

The bundled sale-fit model is trained on repositories that were previously sold. It runs locally with the package; there is no separate service call. Repositories with `similarity_to_sold >= 0.85` are reported as Tier 1 / high probability of sale, `>= 0.70` as Tier 2 / promising, and the rest as Tier 3 / standard.

Use `--schema core` when you want only the source metadata columns:

```text
repo_id
raw_loc
logical_loc
autogen_loc
symbols_count
source_files
primary_language
lang_distribution
commit_count
contributors_count
total_pr_count
reviewed_pr_count
ci_checks
deployment_infra
monitoring
test_suite
containerized
docstring_ratio
readme_quality
issue_tracker
avg_func_length
created_at
branch_count
repo_bundle_mb
repo_git_history_mb
repo_worktree_mb
extensions
documentation_cnt
comment_ratio
sample_loc
```

## Primary Language Rule

`reposcanner` counts language lines across the repository while excluding dependency/build directories for logical metrics.

For `primary_language`, it intentionally skips non-primary data/markup/style languages when a real programming language exists. For example, if YAML, JSON, or CSS is the largest bucket but Python or JavaScript is also present, the scanner picks the real programming language instead of reporting YAML/JSON/CSS as primary.

The language distribution still includes counted languages that pass the 1% threshold, including JSON/YAML/CSS. Only the primary-language choice is adjusted.

## Fair LOC Counting

Dependency, virtual environment, package cache, and build output directories are skipped during traversal. This includes common folders such as `.venv`, `.vwnv`, `venv`, `node_modules`, `vendor`, `dist`, `build`, `.next`, `.nuxt`, `.gradle`, `.m2`, `Pods`, `DerivedData`, `target`, `bin`, and `obj`.

Those files do not contribute to raw LOC, logical LOC, source file count, language distribution, token estimates, or sale-fit scoring.

## Preparing A Sample

`--prep-sample` creates a customer-facing sample zip shaped like:

```text
data/{repo_id}/
  repo_summary.md
  metadata.json
  sample_quality.json
  sample_manifest.json
  samples/
    ...
```

Important: anonymize the repository before running `--prep-sample`. Remove company names, customer names, secrets, proprietary hostnames, private URLs, credentials, personal data, and any other identifying information first. The generated sample is intended to be attached to the email to GRABLY only after that anonymization step.

The sample QA mirrors the repository sale pipeline:

- `PASS`: at least 5,000 counted lines in the primary programming language.
- `PASS_UNDER_5K_ABOVE_1K`: 1,000-4,999 counted primary-language lines.
- `PASS_SMALL_WHOLE_PROJECT`: small repositories where the sample is close to the whole repo (`logical_loc <= 6,500` and either at least 80% of logical LOC or within 500 LOC).
- Fail statuses explain whether the primary language was missing, absent from the sample, too small, or not close enough to the whole repo.

## Token Estimates

The extended schema includes a lightweight code-token estimate. It is not a model-specific tokenizer. It is a deterministic local heuristic that counts identifiers, literals, operators, and punctuation, which is useful for comparing repositories without installing tokenizer packages.

For rough LLM text tokens, it also reports a standard `chars / 4` estimate.

## Repository Description Prompt

To create a clean customer-facing repo description with Codex:

```bash
reposcanner description-prompt
```

Paste that prompt into Codex while Codex is opened inside the target repository. The prompt asks Codex to inspect README files, manifests, entrypoints, and top-level directories, then produce a short paragraph without mentioning suppliers or internal delivery process.

You can include a generated description in extended metadata:

```bash
reposcanner scan --repo . --description-file repo_description.txt --pretty
```

## Notes

- `sample_loc` defaults to `logical_loc`. If you are scanning a sampled subset and already know the sample LOC, pass `--sample-loc`.
- `repo_bundle_mb` is `0.0` unless you pass `--bundle-path`.
- `reviewed_pr_count` is `0` because local git history cannot reliably determine review status.
- The scanner is conservative and local-only; it does not call external APIs.
