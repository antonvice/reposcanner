# Repository audit and implementation backlog

Reviewed 2026-11-06 against `702aa99` and the 0.3.0 generated-code changes. Scope: traversal, classification, metrics, sampling, anonymization, serialization, CLI, packaging, documentation, and tests. This is a review of this implementation, not proof that every possible defect has been found. No supplier source or actual credentials are included in the fixtures.

**Current assessment:** useful for local triage, but not an unattended clearance to send a repository to a client. A `PASS` sample status currently measures line-count rules. It does not establish privacy, syntax validity, buildability, representative business logic, ownership, or completeness. The highest priorities are preventing credential leaks, alias collisions, source overwrites, and misleading scan success.

## Implemented in 0.3.0

- Removed AI authorship inference, DroidDetect loading, heuristic AI scores, confidence/threshold/rejection fields, CLI switches, and the `ai` dependency extra. Removed their model/runtime dependencies from the lockfile. There is no replacement human-versus-AI classifier or generated-code sale gate.
- Added `generated_code_percent = 100 * autogen_loc / logical_loc`. The denominator is counted LOC after dependency/build/unsupported-file exclusions, including markup and configuration. Empty denominators produce `null`, not a claim of 0% generated code.
- Inspect every counted file. Extended metadata records the rule version, numerator, denominator, per-language percentages, and matched paths with rule categories. Matched paths go through the output anonymizer; its limitations below still apply.
- Keep detected generated files out of samples and record excluded file/LOC totals. Sample manifests use format version 3. Core output has 31 fields; scripts expecting 30 fields or the old switches must migrate.
- Count recognized lockfiles that previously had no recognized extension (`Cargo.lock`, `go.sum`, `poetry.lock`, `Pipfile.lock`, `composer.lock`, `uv.lock`, `yarn.lock`). They are generated artifacts and never source-sample candidates.
- Stop treating a bare “do not edit” warning as sufficient generation evidence. Extend named-tool headers. Tests cover false-positive warnings as well as generated files.
- Correct README statements about unavailable measurements, default sample LOC, anonymization guarantees, and removal of authorship detection.

`tests/test_generated_detection.py` verifies the new behavior. `tests/test_sample_regressions.py` retains the earlier generated-C#, sample breadth, syntax, notebook, and alias regressions.

Validation for this audit: 83 tests passed and 24 open-defect examples were expected failures; no tests were skipped. The Node syntax and .NET build checks executed. Ruff lint/format and lockfile consistency passed. A wheel and source distribution built successfully; the wheel's default CLI, ZIP readback, and packaged description prompt worked in a fresh environment containing only the five runtime packages, with no authorship-model dependencies.

## Evidence and priorities

- **Reproduced:** exercised with a synthetic fixture. Most are retained in `tests/test_audit_findings.py`; additional probes are described explicitly below.
- **Inspected:** a concrete implementation risk found by reading the execution path; no claim of a demonstrated failure on every affected platform/language.
- **P1:** source integrity, privacy, or materially misleading acceptance. Resolve before unattended client delivery.
- **P2:** measurement accuracy, coverage, robustness, or maintainability needed for dependable operation.
- **P3:** operational usability improvements.

All RS issues below are **OPEN**. Audit tests use `xfail(strict=True, raises=AssertionError)` to demonstrate desired behavior that currently fails. An expected failure is an unresolved defect, not a passing safety test. When fixing an issue, remove its expected-failure marker and expand the regression as needed. Run `uv run pytest tests/test_audit_findings.py -q -rxX` to see the open executable examples.

## Privacy and code preservation

### RS-001 — P1 — Credential syntax and context gaps

**Reproduced;** `RepoAnonymizer.sanitize_code`, secret regexes, text handling. Synthetic secrets survive C# verbatim literals (`password = @"plain-secret"`), Python triple quotes and `API_TOKEN`, YAML block scalars, namespaced XML password elements, and short README assignments (`password=short`). These are ordinary configuration/source formats, not exotic obfuscation.

**Fix:** recognize complete literal boundaries and language-specific keyed values, including secret-key aliases. Preserve quoting/escaping; reject a sample when a detected value cannot be safely replaced. Avoid broad entropy replacement of identifiers. Include connection strings, HTTP authorization, URL credentials, shell expansion, multiline/raw strings, and structured config in the fixture matrix.

**Acceptance:** every supported fixture emits the same valid syntax with no secret value, or fails closed with a useful reason. Test both short and long values and negative examples such as public keys, checksums, versions, and ordinary identifiers.

### RS-002 — P1 — The alias map is not a discovery-and-propagation pass

**Reproduced;** `RepoAnonymizer`. In `password = "plain-secret"; copy = "plain-secret"`, only the keyed occurrence is removed. The map is consulted when a value is independently detected; it does not remove every occurrence of an already known secret. The same value can also receive different aliases when classified under different tags.

**Fix:** discover sensitive values across selected files, paths, and metadata before rewriting. Define one canonical replacement per exact sensitive value, with explicit priority for secrets. Propagate only into safe literal/comment/path positions; short common words must not rename unrelated program identifiers. Keep originals and mappings in memory only.

**Acceptance:** repeat the value before and after discovery, in multiple files, descriptions, paths, and categories. All applicable occurrences share an alias; unrelated code stays intact; no reverse map is exported.

### RS-003 — P1 — Metadata can allocate a colliding alias before source reservation

**Reproduced;** `build_metadata` → description sanitization → `write_sample_zip`. With a description containing `AcmeCorp` and source declaring both `ANON_ORG_001` and `AcmeCorp`, the output contains two declarations named `ANON_ORG_001`. Python parsing succeeds, but the second declaration overwrites the first.

**Fix:** reserve all selected source identifiers and target path components before allocating any alias, including aliases in descriptions and detection reports. Validate collisions after rewriting, including case-insensitive archive extraction targets.

**Acceptance:** description-first and file-first scans preserve two distinct symbols; aliases and source/path references agree regardless of file traversal order. No ZIP is published on a collision.

### RS-004 — P1 — Identity collection and path redaction are incomplete

**Reproduced/inspected;** `manifest_identity_terms`, `load_anonymization_terms`, `sanitize_path`. `@acme/budget` loses the organization scope, object-shaped package authors are ignored, and missing terms files are silently skipped. Compound identifiers such as `AcmeCorpService`, IP-named folders, unlisted names, customer IDs, addresses, and Unicode variants are not reliably recognized. Generic names shorter than four characters are deliberately excluded.

**Fix:** parse manifests structurally, support common author/organization shapes, fail on explicitly requested unreadable terms files, and report identity-source coverage. Add explicit typed terms and reviewed rename rules for compound identifiers/paths. Do not blindly replace every word that resembles a name or number.

**Acceptance:** scoped packages, TOML author arrays, compound names, Unicode names, Windows paths, and IP/ID path components have positive and false-positive fixtures. Reports describe what identity sources were inspected without listing original identities.

### RS-005 — P1 — Lexical preservation does not guarantee valid or equivalent code

**Inspected;** `sanitize_code`, `code_signature`, `_sanitize_csharp_interpolation`. Most non-Python checks compare tokens while excluding literals/comments. Changes inside JSON strings, regexes, shell syntax, heredocs, templates, imports, namespaces, module paths, SQL, or compiler directives can break structure or references without changing that signature. Replacing an endpoint, reflection string, serialized type name, or external package name can change behavior even when the file compiles. `go.mod` and text documents use whole-text replacement without a grammar check.

**Fix:** separate privacy redaction from symbol renaming, validate formats with their parsers, and add syntax-only compiler checks for supported languages. Record `validated`, `unsupported`, `original_invalid`, and `failed` per file. Treat external references and public schema identifiers separately. Never claim behavior equivalence or execute arbitrary supplier build scripts as a default validation step.

**Acceptance:** nested interpolation/raw strings, escapes, regex literals, decorators, imports, package references, reflection, and build manifests survive syntax validation. Deliberate semantic changes are disclosed. Unsupported validation cannot silently receive a validated label.

### RS-006 — P1 — Notebook extras bypass redaction; notebook assumptions can abort scans

**Reproduced/inspected;** `count_file_code`, notebook branch of `sanitize_code`, `token_stats`. Extra notebook-level fields and cell IDs are retained verbatim despite explicit identity terms. Malformed JSON aborts the entire scan. All code cells are assumed to be Python, including R, Julia, and notebook magics. LOC counts code cells while token estimates consume the entire original JSON, including outputs and metadata.

**Fix:** build output notebooks from an allowlisted schema; regenerate safe cell IDs and remove or sanitize unknown fields. Use kernel metadata for language identification before removing it. Handle unsupported kernels/magics explicitly and give malformed files a reported failure status. Feed the same normalized code-cell content to LOC and token metrics.

**Acceptance:** custom fields, raw/Markdown cells, attachments, outputs, IDs, invalid JSON, missing fields, R/Julia, and magic-cell fixtures produce either a valid sanitized notebook or an explicit failure. No hidden retained fields or output-derived code tokens.

### RS-007 — P1 — No independent final-delivery privacy check

**Inspected;** `write_sample_zip`. The final known-secret check only covers a subset of patterns in selected source transformations. It does not independently inspect every final archive entry name, report, description, manifest field, or metadata value. Replacement counts cannot demonstrate absence of secrets. `--no-anonymize` can still produce a line-count PASS, with only ancillary flags recording the privacy state.

**Fix:** inspect the completed staged archive with an independent local rules pass and explicit configured identity terms. Include all paths and text entries. Block publication for unresolved findings or clearly mark review-required output; distinguish anonymization disabled, completed, and privacy review passed. Never emit matched original values into customer reports or ordinary logs.

**Acceptance:** seed synthetic secrets/identities into every output surface, including the new generated-file report. Verify failure before final publication, safe diagnostics, and that no original/reverse-map data is exported. Absence of findings must still be described as rule coverage, not universal privacy proof.

## Discovery and sampling

### RS-008 — P1 — Broad directory exclusions remove legitimate source

**Reproduced/inspected;** `DEPENDENCY_DIRS`, `iter_candidate_files`. First-party `packages/core/rules.py` disappears. Generic names such as `packages`, `gradle`, `env`, `bin`, `Debug`, and `Release` are treated as dependencies everywhere. Conversely, vendored copies in unrecognized locations remain. Rules are case-sensitive and do not use repository ownership or ignore metadata.

**Fix:** distinguish unambiguous caches from ambiguous directory names; use explicit include/exclude configuration and relevant manifest/ignore evidence. Give user includes defined precedence. Expose an exclusion ledger with counts, sizes, and reasons. Decide tracked/untracked/submodule scope explicitly.

**Acceptance:** JS and .NET monorepos retain first-party packages while real dependency caches stay excluded. Case variants and override precedence are tested. Excluded LOC is not silently represented as an empty repository.

### RS-009 — P2 — Encoding, read failures, and unbounded reads distort scans

**Reproduced/inspected;** `is_binary`, `read_text`, repeated content reads. UTF-16 C# is skipped because its BOM-encoded text contains NUL bytes. UTF-8 decoding uses `errors="ignore"`; invalid bytes disappear. Read errors return an empty string or binary verdict. `path.read_bytes()[:4096]` loads the entire file to inspect a 4 KB prefix, and later stages load it again. There are no file/aggregate size limits.

**Fix:** read a bounded binary prefix, recognize supported BOM encodings, and decode strictly with explicit supported fallbacks. Record unreadable/undecodable/oversized files and incomplete coverage. Reuse one bounded content representation where possible; allow explicit large-file limits.

**Acceptance:** UTF-8/BOM/UTF-16, permission failures, mid-read changes, multi-GB files, and adversarial long lines do not cause silent loss or unbounded memory use. Unsupported encodings are reported, not dropped.

### RS-010 — P1 — Output exclusion happens too late and has wrong ancestry semantics

**Reproduced/inspected;** `build_metadata`, `collect_file_stats`, `scan_command`. An output directory that is an ancestor of the repository removes every stat. Custom outputs are scanned before the post-collection filter, so an excluded malformed notebook can still abort. Custom `--output` metadata inside the source tree and direct `--sample-output something.zip` paths are not consistently excluded on reruns.

**Fix:** resolve all exact output paths and true output descendants before traversal. Reject incompatible/ambiguous layouts; do not treat an ancestor directory as wholly excluded. Use the same exclusions for every metric helper.

**Acceptance:** default/custom directories, sibling and ancestor folders, direct ZIP paths, existing outputs, malformed files in excluded output folders, and second runs produce consistent input counts without hiding the repository.

### RS-011 — P2 — Measurements and samples can come from different file versions

**Inspected;** scan → metrics → selection → reservation → anonymization. Files are reread repeatedly with no snapshot, stable hash, or identity check. A file can become generated, change size, be replaced, or become a symlink after discovery. Metadata can describe old content while the ZIP contains new content.

**Fix:** use a stable read/cache or verify hashes and file identities before publication. Fail or retry a changed scan with an explicit status; bind metadata and sample to the same content snapshot. Retain symlink/root-containment checks at each read boundary.

**Acceptance:** mutate and replace files between phases; no mismatched or out-of-root content is delivered. A clean unchanged rerun preserves counts and selected identities.

### RS-012 — P2 — Directory breadth is not representative sampling; secondary tests are unbounded

**Reproduced/inspected;** `select_sample_files`. Ten secondary-language Python tests are all selected with a C# primary even though the explicit test selection takes only two. The later secondary loop exempts test files from its per-language cap. Large files, duplicated scaffolds, and many project manifests can produce a huge archive; 5,000 lines is a minimum, not a cap. Round-robin directory size does not identify business logic, entrypoints, modules, or functional coverage.

**Fix:** enforce separate source/test/context budgets, bounded file and byte limits, deterministic module/language quotas, and optional explicit sample inclusions. Detect exact duplicate content before counting/selecting; keep near-duplicate grouping reviewable. Report why each file was selected and which modules/languages are absent. Avoid silently truncating a source file mid-function.

**Acceptance:** mixed-language tests cannot consume the entire budget; multi-module and duplicate-heavy fixtures show measurable coverage. Very large files trigger an explicit budget/review result. Reversing discovery order preserves selection.

### RS-013 — P1 — Sample PASS and process success overstate quality

**Reproduced;** `choose_primary_language`, `sample_quality`, `sanitize_code`, `scan_command`. A 6,000-line Markdown-only repository gets `PASS`. An invalid Python file (`def broken(:`) is preserved because original parse failures are ignored; enough valid neighboring lines still yield a PASS. Empty/generated-only sample failure still exits the CLI with code 0. These are line-count decisions, not code acceptance.

**Fix:** require a supported programming primary for programming-sample QA, separate size/coverage/syntax/privacy statuses, and define a machine-readable overall outcome. Preserve an explicit original-invalid status instead of silently bypassing validation. Add a strict CLI mode that returns nonzero for failed delivery checks while retaining diagnostic artifacts.

**Acceptance:** markup-only, generated-only, empty, legacy/invalid Python, missing-module, and disabled-anonymization fixtures cannot be mistaken for a validated delivery. Document exit codes and strict-mode behavior.

## Measurement and classification

### RS-014 — P2 — Logical LOC is an approximate line filter, not language-correct LOC

**Reproduced/inspected;** `count_code_and_comments`. Multiline XML comments contribute code lines; `/* doc */ int value;` contributes zero code lines. Block comments, inline comments, SQL strings, Python docstrings, nested comments, and language-specific forms are inconsistent. Comment-only files are omitted from the comment sum in `build_metadata`. `comment_ratio` divides comments by code, so it is not a proportion of all nonblank lines.

**Fix:** derive line classification from language-aware tokens, define treatment of mixed code/comment lines and docstrings, and name ratios by their denominator. Distinguish physical, code, comment, generated, markup/config, and unsupported counts.

**Acceptance:** golden fixtures across the supported language matrix have reviewed expected counts. Generated percentages, primary language, and sample QA all use the same corrected count definition.

### RS-015 — P2 — Function, engineering, and token metrics can mislead

**Reproduced/inspected;** `function_metrics`, `monitoring`, `test_suite`, `token_stats`. Non-Python function length takes up to 80 subsequent lines rather than locating the end; a one-line function followed by 80 blank lines can report length 80. Python also counts classes as functions. Mere text mentions can imply `Full SRE`; ten test filenames imply `Comprehensive`; C# `Tests.cs` matching differs between sampling and test-suite reporting. Generated code can influence these metrics. Token counts are regex/chars-per-four estimates, not a specified model tokenizer.

**Fix:** use parsers for supported function metrics or report unavailable instead of fabricated lengths. Rename inferred engineering fields as detected signals and expose evidence. Keep test existence separate from measured execution/coverage. Split estimated tokens by source/generated/docs/config and normalize notebooks.

**Acceptance:** one-line/nested functions and classes have correct definitions; comments/examples cannot independently establish production monitoring. Tests for reported signals must not claim actual operational coverage. Estimates carry method, scope, and uncertainty.

### RS-016 — P2 — Command failures become zeros or successful refreshes

**Reproduced/inspected;** `run`, Git helpers, `refresh_git_refs`. `run` swallows every exception and returns an empty string. A failed fetch is reported as `ok`; a Git history read failure can look like zero commits when repository discovery succeeds. There is no timeout, including on opt-in remote fetch.

**Fix:** return structured command results with status, safe diagnostics, timeout, and output. Distinguish no data from command failure and propagate unknown/incomplete measurements. Treat expected empty output from a successful fetch separately from a failed fetch.

**Acceptance:** missing executables, permission errors, corrupted/shallow Git data, network failures, and timeouts cannot produce successful refresh status or silently measured zero counts.

### RS-017 — P2 — Git and size metadata lack consistent provenance

**Inspected;** Git helpers and `du_mb`. Commit counting drops any subject containing `revert`, branch count can include symbolic remote HEAD and local/remote duplicates, contributor names are not stable identities, and PR regexes cannot establish real/reviewed PR counts. Shallow history is not labeled. A worktree's `.git` pointer file is measured instead of its actual Git directory. `du` measures allocated disk blocks, includes dependencies/output in worktree size, and is unavailable on some platforms. A supplied nonexistent bundle can appear as size zero.

**Fix:** define each metric's Git refs/history/identity scope, detect shallow/worktree repositories, resolve the actual Git directory, and separate allocated/apparent/bundle bytes. Report unavailable inputs as errors or unknowns. Keep PR inference explicitly distinct from hosted PR/review measurements.

**Acceptance:** shallow clones, linked worktrees, merge/revert messages, duplicate refs/authors, sparse files, missing bundles, and Windows/no-`du` fixtures produce documented values and provenance.

### RS-021 — P2 — Sale-fit features are stale and the score is not calibrated

**Reproduced/inspected;** `build_metadata`, `sale_model_vector`, `predict_sale`, bundled `sale_model.json`. Prediction occurs before measured `sample_loc` is written, even though it is a model feature. The bundled model predates changed generated/primary-language/unknown-value semantics and uses language labels different from the scanner. Reference/vector length mismatches are silently truncated. The label `high probability of sale` and field `sale_probability` overstate an uncalibrated similarity score despite its interpretation note.

**Fix:** compute features from finalized measurements; validate model dimensions, labels, feature schema, and missing-data policy. Until reevaluated on held-out labeled data, make this an explicitly internal similarity feature, remove probability wording, or disable it by default. Removing the AI authorship detector does not by itself repair this separate local sale-fit calculation.

**Acceptance:** the recorded scoring feature vector matches emitted metadata; bad/stale model schemas fail explicitly. Any probability claim requires documented calibration and evaluation, separate from generated-code percentages.

### RS-022 — P2 — Tool-generated rules have false positives, blind spots, and whole-file granularity

**Reproduced/inspected;** `generated_file_reason`. A generated header after `using System;` is missed because only the leading comment run is inspected; headers beyond 8 KB are missed. Generic directories such as `out` can contain authored source. One `@Generated` annotation can classify an entire mixed Java/Kotlin file. Unmarked generator output, hand-edited scaffolds, and generated regions inside authored files cannot be accurately separated by current rules.

**Fix:** support explicit include/exclude/generated overrides with documented precedence, optionally consume `linguist-generated` attributes, and retain rule evidence. Expand tool-specific fixtures before broadening rules. Use region-level counts only where reliable start/end markers exist; otherwise disclose whole-file classification. Do not infer “human-written” from a negative match or reintroduce AI authorship inference.

**Acceptance:** each new rule has at least a positive generated fixture and a negative authored/example-string fixture. Human-edited migrations/declarations and mixed regions have explicit treatment. Report what is detected, not an unsupported true-generation estimate.

Fixture families to extend (names alone are candidates for testing, not permission to blanket-exclude):

| Ecosystem | Tool artifacts / evidence to test | Important counterexamples |
| --- | --- | --- |
| .NET / Unity | Designer, `.g.cs`, `.g.i.cs`, assembly info, Razor/XAML output, source-generator headers, `.resx` wrappers | Handwritten partial classes, edited designers, generated regions only |
| JVM / Android | annotation processors, MapStruct, JOOQ, JAXB, Dagger/Hilt, Room, KSP, `R.java` / `BuildConfig.java` | Method-level annotations, examples, classes with similar names |
| Go / protobuf | `Code generated ... DO NOT EDIT.`, protobuf/gRPC, mocks, stringer, sqlc, ent | Generated marker in a string/comment example, package docs before marker |
| Python | protobuf/gRPC, OpenAPI, ORM/model generators, compiled Cython wrappers | Authored migrations, maintained schema/model definitions |
| JS / TS / web | bundles, minifiers, transpiled JS, source maps, Prisma, GraphQL, API clients, framework output | Authored `.d.ts`, source-map references, unminified authored `bundle.js` |
| C / C++ | Bison/Flex, ANTLR, Qt moc/uic/rcc, SWIG, protocol compilers, CMake output | Checked-in authored headers and build inputs |
| Rust / Swift / Dart | bindgen/prost, Sourcery, SwiftGen, Flutter serializers, `.freezed.dart`, route generators | Authored FFI wrappers, extensions, editable templates |
| Config / assets | lockfiles, generated schemas, localization catalogs, compiled CSS, generated SVG/XML, infrastructure plans | Authored schemas, config, styles, translations; build artifacts excluded from scan scope |

### RS-023 — P2 — Unsupported languages and extensions disappear without coverage accounting

**Inspected;** `LANG_BY_EXTENSION`, `SPECIAL_FILENAMES`, `choose_primary_language`, `rounded_distribution`. Unlisted source/config types such as HCL/Terraform, Razor, and build-system files may disappear or be mislabeled even while other helpers infer their existence. Some special filenames are case-sensitive. `.h` is always C; notebooks are always Python. Language percentages below 1% are omitted without a remainder. SQL is excluded from programming-primary preference despite some SQL-heavy applications.

**Fix:** expose supported/unknown extension inventories, allow language overrides, expand only with tested counting/anonymization support, and make primary-language policy configurable where requirements differ. Include an `other` remainder or full distribution.

**Acceptance:** an unrecognized codebase reports unsupported coverage, not a confidently empty scan. Ambiguous headers, SQL-only repos, templates, notebook kernels, and small secondary languages have explicit results.

### RS-025 — P1 — Metadata helpers do not share the source traversal boundary

**Inspected;** `ci_files`, `documentation_cnt`, `readme_quality`, `deployment_infra`, `containerized`, identity collection. Some helpers use unrestricted `rglob` or follow file symlinks while source traversal excludes them. Vendored CI/Docker files can influence first-party metrics; a symlinked README can be read outside the repository. Identity/issue helpers can consult a parent Git repository even when `git_metadata_available` is false for the requested directory.

**Fix:** use a shared checked inventory for every local read, with explicit extra-file categories for CI/manifests. Enforce root containment and Git-root identity consistently. Report exceptions to scan scope rather than silently expanding it.

**Acceptance:** external symlinked documents, dependency-only infrastructure, nested non-Git directories, and nested repositories cannot affect unrelated metadata or export out-of-root content.

## Output, operations, and contracts

### RS-018 — P1 — User-selected output can overwrite source or another output

**Reproduced/inspected;** `scan_command`, `sample_zip_path`, `write_sample_zip`. `--output app.py --no-prep-sample` overwrites the scanned source with metadata. There is no protection against a ZIP path overlapping an input or metadata and ZIP resolving to the same file. Symlink/hard-link aliases of a target need the same protection.

**Fix:** resolve and validate all output destinations before reading/writing. Reject source/output collisions, overlapping output roles, and unsafe symlink destinations. Limit overwrite to recognized prior outputs or an explicit reviewed overwrite policy.

**Acceptance:** existing source and configuration bytes remain unchanged for same-path, symlink, hard-link, case-folded, and metadata/ZIP collision fixtures. Failure occurs before any destination is modified.

### RS-019 — P1 — Final files are written non-atomically with no run receipt

**Inspected;** `write_sample_zip`, `scan_command`. Preparing text first protects against some sanitizer errors, but disk-full, interruption, compression, and metadata-write failures can leave a partial ZIP or a new ZIP paired with stale metadata. A failed rerun can leave old files that look current. There is no checksum/readback validation or shared run identifier binding outputs together.

**Fix:** stage the full output set, validate ZIP contents and metadata consistency, record hashes/run identity, and atomically publish a manifest that points to the completed set. Preserve the last good run until the new one is complete. Do not print completion before publication.

**Acceptance:** inject failures at every write/publication stage and interrupt reruns. The last good delivery remains readable and identifiable; partial artifacts cannot look like completed current outputs. Reopen ZIP/JSON and verify checksums before success.

### RS-020 — P2 — Handwritten YAML serialization changes values and empty structures

**Reproduced/inspected;** `yaml_scalar`, `to_yaml`. Strings such as `Yes`, `null`, `0123`, and ISO dates are not quoted and can load as booleans/null/numbers/dates. Empty dictionaries/lists serialize as an empty value, becoming null. Mapping keys are not quoted generally. JSON and YAML can therefore describe different metadata.

**Fix:** use a tested serializer, or keep the supported subset small by quoting every string/key and explicitly serializing empty collections. Define supported YAML version/readers.

**Acceptance:** round-trip core and extended outputs, nested empty collections, ambiguous strings, Unicode, multiline values, and path keys through an independent parser; compare values and types to JSON.

### RS-024 — P2 — Metadata lacks a complete versioned schema and measurement provenance

**Inspected;** `METADATA_COLUMNS`, `build_metadata`, manifest creation. The rule set and ZIP format are versioned, but the metadata contract is not fully specified with types, enums, nullability, measurement status, or scope. Core output drops explanatory notes. ZIP metadata intentionally omits sale scoring and lacks final ZIP-size fields, but the supported comparison contract is undocumented. `--sample-loc` can override a measured value without core output explaining the override.

**Fix:** add a versioned schema and shared run/scanner/config provenance, define internal versus customer fields, and distinguish measured/estimated/inferred/unknown/overridden values. Specify numerator/denominator consistently for generated-code percentages. Supply a migration example for older consumers; do not silently reintroduce removed AI fields.

**Acceptance:** schema-validate JSON/JSONL/YAML and ZIP metadata, reject invalid finite/range/type values, test core/extended compatibility, and compare the expected common fields between archive and external metadata.

### RS-026 — P2 — Validation depends on the developer machine

**Inspected;** tests, `.pre-commit-config.yaml`, packaging. There is no committed CI workflow. Native C# and JavaScript syntax regressions skip when toolchains are absent. Local hooks are optional. There are no broad compiler/format fixtures, clean-install CI, corrupted-input/fault-injection coverage, or supported-platform matrix. One large scanner module couples discovery, metrics, rendering, privacy, and output publication.

**Fix:** add CI for supported Python/OS versions, require the existing Node/.NET checks in at least one job, and test a built wheel with runtime dependencies only. Add reviewed per-language fixtures and fault injection from the P1 issues. Extract small modules only when implementing these boundaries, without a speculative framework.

**Acceptance:** CI reports required checks actually executed, distinguishes skips/expected failures, installs the distributable, runs the default CLI without model/network dependencies, and rejects broken schemas or privacy regressions.

### RS-027 — P3 — Rerun hygiene and reproducibility are weak

**Inspected;** defaults, ZIP writing, `.gitignore`. Each run gets a new UUID unless supplied; ZIP timestamps vary. Repeated runs can accumulate obsolete samples beside one latest metadata file. The repo's ignore file does not cover `reposcanner_out/` or demo output directories. Default scans of the scanner itself include large generated fixtures as ordinary test source and can influence sample selection.

**Fix:** expose a clear run layout and stable inventory/selection hashes, document retention, and ignore this repo's known local output directories. Make deterministic fixture runs possible without requiring byte-identical production ZIPs. Do not silently delete prior deliveries.

**Acceptance:** two unchanged fixture scans have identical inventories/counts/selection identities, fresh output can be distinguished from old runs, and local scanner artifacts do not appear as accidental commit candidates.

### RS-028 — P2 — CLI errors and input validation lack a consistent contract

**Inspected;** `scan_command`, `main`, helpers. Missing description files, malformed notebooks, invalid repo IDs, unreadable paths, invalid bundles, and some sanitizer errors produce raw exceptions. Positional and `--repo` paths can disagree without an explicit error. Success exit code does not communicate scan completeness or QA. Error messages may expose local paths and logs use repository-derived text in Rich markup.

**Fix:** validate conflicting inputs and destinations first, define exit codes for usage/read/scan/QA/publication failures, sanitize user-facing diagnostics, escape terminal markup, and report per-file failures without confusing them with empty files. Keep detailed private diagnostics separate from customer artifacts.

**Acceptance:** CLI tests cover conflicting options, nonexistent inputs, unsupported encodings, non-Git paths, deliberate cancellation, malformed source/config, and partial failure; stdout remains machine-readable and outcomes are documented.

### RS-029 — P2 — Technical metrics do not establish the supplier's commercial claims

**Inspected;** README sale requirements and metadata surface. The scanner cannot establish private/internal status, ownership, rights to redistribute dependencies, consent to share personal data, production use, actual test coverage, or whole-delivery repository count from one local checkout. Nested repositories/submodules and duplicate copies are not represented as a delivery inventory. A generated-code percentage or sale-fit tier cannot substitute for these facts.

**Fix:** add an optional supplier intake/provenance record and a separate batch inventory keyed by repository/content identity when needed. Keep supplied declarations distinct from measured facts and make manual review status explicit. Define whether the deliverable is a review excerpt or a buildable complete project.

**Acceptance:** a client report clearly states repository count/scope, exclusions, generated numerator/denominator, sample coverage, unsupported measurements, and supplier-provided attestations. Never infer ownership, build success, or human authorship from missing tool markers.

## Recommended implementation order and release gates

1. **Protect inputs and make failure visible:** RS-018/019/010/016/028, then a shared inventory boundary (RS-008/009/011/025). No source overwrites, silent scan loss, stale-success artifacts, or out-of-root reads.
2. **Close privacy and rename defects:** RS-001/002/003/004/006/007, with syntax/format validation from RS-005. Promote the corresponding strict expected failures into normal passing tests. Keep review required until supported detection coverage is established.
3. **Make sample acceptance meaningful:** RS-012/013, exact deduplication, selection reasons, budgets, and per-dimension QA. Fail clearly when the sample cannot satisfy the requested purpose.
4. **Make metrics defensible:** RS-014/015/017/021/022/023/024 and YAML RS-020. Prefer explicit unknown values over invented precision. Preserve the generated-only scope and prohibit authorship scoring.
5. **Make releases repeatable:** required CI/built-package checks (RS-026), output hygiene (RS-027), and optional supplier/delivery intake (RS-029).

Minimum acceptance for unattended client preparation: no remaining P1 failures; passing output/source protection and final-archive privacy tests; valid schemas and archive readback; coverage/error ledger; reproducible sample selection; explicit unsupported-validation states; required language checks actually executed. This audit does not claim those gates are met in 0.3.0.
