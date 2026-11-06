<p align="center">
  <img src="assets/reposcanner-logo.svg" alt="reposcanner" width="760">
</p>

<p align="center">
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-3776AB">
  <img alt="Local only" src="https://img.shields.io/badge/code%20privacy-local%20only-10B981">
  <img alt="Generated code" src="https://img.shields.io/badge/generated%20code-tool%20%2F%20compiler-8B5CF6">
  <img alt="Tests" src="https://img.shields.io/badge/tests-pytest-0F172A">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-blue">
</p>

# reposcanner

`reposcanner` prepares local repositories for code dataset review. It reports repository size, languages, tool/compiler-generated code, anonymized samples, token estimates, and optional internal sale-fit scores.

Read [issues.md](issues.md) for the current audit, known failures, and the prioritized work needed before treating output as ready to share. Automated anonymization and sample QA do not establish that a delivery is complete, private, buildable, or representative.

## Sale Requirements

For a repository to be a strong sale candidate:

- It should be Tier 1 from the local sale-fit model.
- Review the tool/compiler-generated share and the evidence behind it. There is no generated-code sale threshold.
- It should contain high-quality, human-written, production-grade code.
- It must be private or internal. Public/open-source repositories are not accepted for this sale path.
- The prepared sample must be anonymized before sending.

Use the Tier 1 sale-fit score to prioritize review; it is not a calibrated sale probability. After reviewing the anonymized sample and metadata, email `hello@grably.us` at GRABLY Inc. with a short note that you would like to sell the repository and work out a deal. Attach both generated files from `reposcanner_out/`: the anonymized sample zip and `metadata.json`. Without the sample and metadata, GRABLY cannot evaluate it.

## No Source-Code APIs

`reposcanner` does not send repository source code to external APIs. Scanning, LOC counting, anonymization, sample creation, sale scoring, and tool/compiler-generated detection run locally.

Generated-file detection uses filename, directory, annotation, and comment-header rules. It does not load an authorship model or download weights. Scanning has no network step unless `--refresh-git` is explicitly requested; installation can fetch packages.

## One-Line Use

From inside the repository you want to scan:

```bash
uvx --from "reposcanner @ git+https://github.com/antonvice/reposcanner.git" reposcanner .
```

That runs the default full pipeline and writes:

```text
reposcanner_out/
  metadata.json
  {repo_id}_sample.zip
```

If `metadata.json` reports Tier 1, review the generated files, then email `hello@grably.us` and attach both: `metadata.json` and the anonymized sample zip.

You can also pass a path:

```bash
uvx --from "reposcanner @ git+https://github.com/antonvice/reposcanner.git" reposcanner /path/to/private-repo
```

Fast local checkout usage:

```bash
git clone https://github.com/antonvice/reposcanner.git
cd reposcanner
uv sync --group dev
uv run reposcanner .
```

## Default Pipeline

By default, `reposcanner .` runs:

- metadata scan
- fair LOC/language counting
- token estimates
- local sale-fit tier/probability model
- tool/compiler-generated LOC percentage and per-file reasons
- default anonymization
- customer-facing sample zip creation
- metadata and sample output into `reposcanner_out/`

Anonymization is applied to generated outputs, especially the sample zip. Use a separate output directory: custom output paths currently lack protection against overwriting input files (see RS-018 in [issues.md](issues.md)).

## Key Commands

Print metadata to stdout:

```bash
reposcanner . --output - --no-prep-sample
```

Write to a custom output folder:

```bash
reposcanner . --output-dir ./sale_scan
```

Create only core source metadata columns:

```bash
reposcanner . --schema core --output - --no-prep-sample
```

Refresh git refs before scanning metadata:

```bash
reposcanner . --refresh-git
```

`--refresh-git` uses `git fetch --all --tags --prune`. It does not run `git pull` because pull mutates local branches. LOC is counted from the current checked-out worktree only, so fetched branches and older commits are not double-counted as code lines. Git history is used for metadata like commit/contributor/branch counts.

## Output Schemas

Default schema is `extended`. It emits the 31 base metadata columns plus local extras:

- `token_stats.estimated_code_tokens`
- `token_stats.estimated_text_tokens`
- `token_stats.tokens_by_language`
- `token_stats.tokens_by_extension`
- `sale_prediction.tier`
- `sale_prediction.sale_probability`
- `sale_prediction.similarity_to_sold`
- `generated_code_detection` (scope, totals, language shares, matched files and reasons)
- `sample_quality`
- `anonymization`

Use `--schema core` when you need only the base source metadata columns:

```text
repo_id
raw_loc
logical_loc
autogen_loc
generated_code_percent
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

For `primary_language`, it uses files without a generated-code match and skips data/markup/style languages when a programming language exists. A large detected generated C# client cannot displace the application's other Python code. Generated-only repositories have no eligible primary language and cannot pass sample QA. Markup-only repositories can still pass the line-count check; this is an open issue (RS-013).

The language distribution still includes counted languages that pass the 1% threshold. Only the primary-language choice is adjusted.

### Generated files and honest totals

Generated files remain in `raw_loc`, `logical_loc`, and language distributions, but are reported separately in `autogen_loc` and excluded from samples. `non_generated_loc` is `logical_loc - autogen_loc`; it still includes markup and does not prove human authorship.

Detection uses known artifact names/directories and leading comment headers, including .NET Designer/`.g.cs` files, protobuf/gRPC outputs, Dart generators, Qt outputs, minified bundles, lockfiles, `<auto-generated>`, `@generated`, and common compiler/client-generator headers. Java/Kotlin `@Generated` annotations are also recognized. Markers inside ordinary string literals or function comments do not classify a file as generated. Migrations and TypeScript declaration files are not blanket-excluded because they may be authored or edited by hand.

This is evidence-based filtering, not a perfect authorship classifier. Unmarked generated code can be missed, and edited generated files may still retain their markers.

Version 0.2 metadata uses `null` for unavailable Git/review counts, unavailable bundle sizes, and failed size measurements. PR counts from Git are explicitly labeled as inferred; test-suite labels describe detected files/configuration, not measured coverage. `sample_loc` now counts the emitted sample (zero when sample preparation is disabled). Internal sale scores are labeled as heuristic and omitted from metadata inside the client sample ZIP.

## Fair LOC Counting

Dependency, virtual environment, package cache, and build output directories are skipped during traversal. This includes `.venv`, `.vwnv`, `venv`, `node_modules`, `vendor`, `dist`, `build`, `.next`, `.nuxt`, `.gradle`, `.m2`, `Pods`, `DerivedData`, `target`, `bin`, and `obj`.

Those files do not contribute to raw LOC, logical LOC, source file count, language distribution, token estimates, generated-code percentages, or sale-fit scoring.

## Tool/compiler-generated percentage

`generated_code_percent = 100 * autogen_loc / logical_loc`.

The denominator is **counted LOC after the scanner's dependency/build/unsupported-file exclusions**, including markup and configuration. It is not total disk content or just programming-language LOC. For example, 100 generated C# lines plus 300 other C# lines and 600 XML lines gives 10% overall and 25% within C#. An empty denominator produces `null`, with status `no_counted_code`.

Every counted file is inspected; there is no largest-files sample for this metric. Extended output includes `generated_code_detection` with:

- `method`: versioned tool/compiler rule set;
- `total_loc`, `generated_loc`, `percent`, `total_files`, and `generated_files`;
- `by_language`: each language's denominator, generated LOC, and percentage;
- `files`: matched paths, languages, counted LOC, and a rule category such as `generated_filename`, `generated_directory`, `generated_comment_header`, or `generated_annotation`.

These rules identify artifacts such as .NET Designer files, protobuf/gRPC stubs, generator output, minified bundles, and counted lockfiles. They do not classify whether a person or a language model wrote the remaining code. Unmarked generator output can be missed, and manually edited generated files are classified as whole files. Per-file paths receive the same anonymization pass as sample paths; review them before sharing.

Version 0.3 removes AI authorship inference completely: the `ai` package extra, DroidDetect/PyTorch/Transformers dependencies, `--ai-*` / `--no-ai-*` flags, old `ai_*` metadata, and the 10% appeal gate are gone. Remove those options from older commands. `--schema core` now has 31 fields, including `generated_code_percent`; detailed detection is available in extended output and ZIP metadata. Generated-only input still fails sample QA because it has no eligible source sample.

## Default Anonymization

Anonymization is enabled by default for generated outputs. It does not rewrite your repository.

Source anonymization uses language lexers to distinguish code from strings and comments. It replaces detected sensitive values with valid identifiers such as:

- emails with `ANON_EMAIL_001`
- phone numbers with `ANON_PHONE_001`
- usernames and local user paths with `ANON_USER_001`
- names and author/owner fields with `ANON_NAME_001`
- explicit company/project terms with `ANON_ORG_001` or `ANON_NAME_001`
- URLs, hosts, endpoints, and IP addresses with `ANON_URL_001` / `ANON_IP_001`
- detected secrets with `ANON_SECRET_001`

One in-memory map is shared across every file in a scan. Repeated exact values detected in the same category get the same numbered alias. Secret matching is case-sensitive. The reverse map is never exported. Explicit identity terms are also replaced in identifiers, source references, and archive paths. Free-text metadata uses bracketed versions of the same aliases.

Known gaps remain: some credential forms and undetected repetitions survive, and alias reservation can happen too late when a description is supplied. See RS-001 through RS-004 in [issues.md](issues.md). An alias map alone does not establish that all occurrences or identities were removed.

Entropy checks apply to literal/comment content, never ordinary code identifiers. Decorators, annotations, interpolation expressions, escaped strings, and version fields are preserved. Known public XML schema namespaces are preserved so project files remain valid. Notebook samples retain anonymized code/Markdown cells and remove execution outputs, attachments, and metadata; notebook LOC counts code cells only.

The sanitizer compares non-literal code tokens, except for explicit identity renames. Python is also parsed when the original parses under the running Python version. Transformations rejected by these checks stop sample creation before a ZIP is opened. These checks do not establish syntax validity for every language, a successful build, or equivalent runtime behavior when credentials, endpoints, or identities change.

It automatically loads identity terms from git remotes, recent git author names/emails, repository folder name, `package.json`, and `pyproject.toml`. You should still add company/project/customer terms explicitly:

```bash
reposcanner . --anonymize-term "Acme Corp" --anonymize-term "Internal Platform"
```

Or from a file:

```bash
reposcanner . --anonymize-terms-file private_terms.txt
```

The sample zip includes `anonymization_report.json` with replacement counts. These counts are not a privacy clearance. Review the full archive, including paths and metadata, before sending it.

## Secret-Removal Approach

The local sanitizer combines pattern matching, contextual key matching, and entropy checks. This follows the same broad families of techniques used by common local secret scanners:

- [GitHub secret scanning](https://docs.github.com/code-security/secret-scanning/about-secret-scanning) documents provider-specific and generic token patterns, including history-aware scanning across branches.
- [Gitleaks](https://github.com/gitleaks/gitleaks) focuses on detecting secrets in repos/files/stdin with regex rules and entropy-style matching.
- [Yelp detect-secrets](https://github.com/Yelp/detect-secrets) is plugin-based and supports regex-style detectors.
- [Microsoft Presidio](https://microsoft.github.io/presidio/) separates PII detection from anonymization operators such as replace/redact/mask.

`reposcanner` is intentionally conservative for sale samples: if something looks like a credential or private endpoint, it is safer to tag it than preserve it.

## Preparing A Sample

Default sample zip shape:

```text
data/{repo_id}/
  repo_summary.md
  metadata.json
  sample_quality.json
  sample_manifest.json
  anonymization_report.json
  samples/
    ...
```

Selection round-robins source directories, targets at least 5,000 primary-language code lines, and includes at least eight primary-language files when available. It also includes representative tests, one file from other programming languages, and README/build/dependency context. Whole files are retained; the 5,000-line target is not a size cap. Directory breadth is a heuristic and still needs a human check for representative business logic.

Generated and dependency files never enter the sample, including through the support-file path. Counts and QA are computed after anonymization. The manifest records the selection method, emitted paths/counts, and format version. Symlinked files and prior scanner output are excluded.

Line-count statuses are:

- `PASS`: at least 5,000 counted lines in the primary programming language.
- `PASS_UNDER_5K_ABOVE_1K`: 1,000-4,999 counted primary-language lines.
- `PASS_SMALL_WHOLE_PROJECT`: small repositories where the sample is close to the whole repo.
- Fail statuses explain whether the primary language was missing, absent from the sample, too small, or not close enough.

## Flag Reference

Output and shape:

- `path`: positional repo path, same as `--repo`.
- `--repo PATH`: repository root. Default `.`.
- `--output PATH`: metadata output path. Use `--output -` for stdout.
- `--output-dir DIR`: default output folder. Default `reposcanner_out`.
- `--format json|jsonl|yaml`: metadata format. Default `json`.
- `--pretty`: pretty-print JSON.
- `--repo-id ID`: stable repository id. Default UUID4.
- `--schema extended|core`: full metadata or the 31 base columns.
- `--bundle-path PATH`: optional bundle/zip path for `repo_bundle_mb`.
- `--sample-loc N`: explicit `sample_loc` override; otherwise emitted sample code LOC, or zero without a sample. Actual measured counts remain in `sample_quality`.

Pipeline toggles:

- `--no-prep-sample`: skip sample zip creation.
- `--no-token-stats`: skip token estimates.
- `--no-sale-prediction`: skip local sale-fit scoring.
- `--no-anonymize`: write generated outputs without anonymization.
- `--refresh-git`: fetch all git refs/tags before scan.

Anonymization controls:

- `--anonymize-term TERM`: extra term to replace. Repeatable.
- `--anonymize-terms-file PATH`: one term per line. Repeatable.

Description:

- `--description TEXT`: include a repo description in extended metadata.
- `--description-file PATH`: read repo description from a file.
- `reposcanner description-prompt`: print a Codex prompt for generating the description locally.

## Repository Description Prompt

The repository description is part of evaluation metadata. It helps reviewers quickly understand what the codebase does, what domain it serves, and which technical components matter. A missing or vague description makes the repository harder to evaluate.

To create a clean customer-facing repo description locally with Codex:

```bash
reposcanner description-prompt
```

Paste that prompt into Codex while Codex is opened inside the target repository. It asks Codex to inspect README files, manifests, entrypoints, and top-level directories, then produce a short paragraph without mentioning internal delivery process.

Use the prompt with an agent or harness that can actually inspect the repository, search files, and run read-only commands. Do not use a plain chat model that only sees the prompt and cannot access the repo; it will guess and produce weaker metadata.

You can also use a local model through tools such as Ollama, LM Studio, llama.cpp, or any other local inference setup, as long as it is connected to a repo-aware agent/harness. That keeps repository contents on your machine. Save the generated paragraph to a file, review/anonymize it, then include it in the scan:

```bash
reposcanner . --description-file repo_description.txt
```

The description is stored as `repo_description` in `metadata.json` and is also anonymized by the default output sanitizer.

## Development

Install with dev tools:

```bash
uv sync --group dev
```

Run tests:

```bash
uv run pytest
```

Install local pre-commit hooks:

```bash
uv run pre-commit install
```

Run the same checks manually:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Reproducible demo scan on this repo:

```bash
uv run reposcanner . --output-dir reposcanner_out_demo --no-hud
```

## Notes

- `sample_loc` counts emitted sample LOC, or is zero when sample creation is disabled; `--sample-loc` can override it.
- `repo_bundle_mb` and `reviewed_pr_count` are `null` when unavailable.
- Audit regression tests marked `xfail(strict=True)` reproduce open issues; they are not passing safety checks. See [issues.md](issues.md).
