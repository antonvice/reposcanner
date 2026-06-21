#!/usr/bin/env python3
"""Generate a repository metadata JSON row.

Run from a repository root:

    reposcanner scan --repo . --output metadata.json

The script intentionally uses only the Python standard library. If the `scc`
binary is available in the environment you can still use this script; it does
its own counting so the primary-language override rules are explicit and easy
to audit.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


METADATA_COLUMNS = [
    "repo_id",
    "raw_loc",
    "logical_loc",
    "autogen_loc",
    "symbols_count",
    "source_files",
    "primary_language",
    "lang_distribution",
    "commit_count",
    "contributors_count",
    "total_pr_count",
    "reviewed_pr_count",
    "ci_checks",
    "deployment_infra",
    "monitoring",
    "test_suite",
    "containerized",
    "docstring_ratio",
    "readme_quality",
    "issue_tracker",
    "avg_func_length",
    "created_at",
    "branch_count",
    "repo_bundle_mb",
    "repo_git_history_mb",
    "repo_worktree_mb",
    "extensions",
    "documentation_cnt",
    "comment_ratio",
    "sample_loc",
]


DEPENDENCY_DIRS = {
    "node_modules",
    "vendor",
    "dist",
    "build",
    "bower_components",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    "bin",
    "obj",
    "packages",
    "PackageCache",
    ".gradle",
}

ALWAYS_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".DS_Store",
}

AUTOGEN_DIR_PARTS = {
    "generated",
    "__generated__",
    "migrations",
    ".next",
    ".nuxt",
    "out",
}

AUTOGEN_EXACT_FILES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "go.sum",
    "poetry.lock",
    "pipfile.lock",
    "composer.lock",
}

AUTOGEN_PATTERNS = [
    re.compile(r".*_generated\.[^.]+$", re.I),
    re.compile(r".*_pb2\.py$", re.I),
    re.compile(r".*\.pb\.go$", re.I),
    re.compile(r".*\.min\.(js|css)$", re.I),
    re.compile(r".*\.bundle\.js$", re.I),
]

NON_PRIMARY_LANGUAGES = {
    "JSON",
    "YAML",
    "XML",
    "HTML",
    "CSS",
    "SCSS",
    "Sass",
    "Less",
    "Markdown",
    "Text",
    "CSV",
    "TOML",
    "INI",
    "Properties",
    "SVG",
    "Dockerfile",
    "Makefile",
    "Protocol Buffer",
    "SQL",
}

LANG_BY_EXTENSION = {
    ".py": "Python",
    ".ipynb": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".groovy": "Groovy",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".hxx": "C++",
    ".c": "C",
    ".h": "C",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".dart": "Dart",
    ".r": "R",
    ".jl": "Julia",
    ".lua": "Lua",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hrl": "Erlang",
    ".fs": "F#",
    ".fsx": "F#",
    ".clj": "Clojure",
    ".cljs": "Clojure",
    ".sol": "Solidity",
    ".sql": "SQL",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".fish": "Shell",
    ".ps1": "PowerShell",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".json": "JSON",
    ".jsonc": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".xml": "XML",
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".toml": "TOML",
    ".ini": "INI",
    ".cfg": "INI",
    ".properties": "Properties",
    ".csv": "CSV",
    ".proto": "Protocol Buffer",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

SPECIAL_FILENAMES = {
    "Dockerfile": "Dockerfile",
    "Makefile": "Makefile",
    "makefile": "Makefile",
    "Rakefile": "Ruby",
    "Gemfile": "Ruby",
}

COMMENT_PREFIXES = {
    "Python": ["#"],
    "Ruby": ["#"],
    "R": ["#"],
    "Shell": ["#"],
    "PowerShell": ["#"],
    "JavaScript": ["//"],
    "TypeScript": ["//"],
    "Java": ["//"],
    "Kotlin": ["//"],
    "Scala": ["//"],
    "Groovy": ["//"],
    "C#": ["//"],
    "C++": ["//"],
    "C": ["//"],
    "Go": ["//"],
    "Rust": ["//"],
    "Swift": ["//"],
    "PHP": ["//", "#"],
    "Dart": ["//"],
    "Objective-C": ["//"],
    "Objective-C++": ["//"],
    "CSS": ["/*"],
    "SCSS": ["//", "/*"],
    "SQL": ["--"],
    "Lua": ["--"],
}


@dataclass
class FileStat:
    path: Path
    rel: str
    language: str
    extension: str
    raw_loc: int
    code_loc: int
    comment_loc: int
    symbols_count: int
    generated: bool
    dependency: bool


def run(cmd: list[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(
            cmd,
            cwd=str(cwd),
            stderr=subprocess.DEVNULL,
            text=True,
            errors="ignore",
        ).strip()
    except Exception:
        return ""


def is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\0" in chunk


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def language_for(path: Path) -> str | None:
    if path.name in SPECIAL_FILENAMES:
        return SPECIAL_FILENAMES[path.name]
    return LANG_BY_EXTENSION.get(path.suffix.lower())


def iter_candidate_files(repo: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in ALWAYS_SKIP_DIRS and not d.startswith(".cache")
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if language_for(path) is None:
                continue
            if is_binary(path):
                continue
            yield path


def is_dependency_path(rel: str) -> bool:
    parts = set(Path(rel).parts)
    return bool(parts & DEPENDENCY_DIRS)


def is_generated_file(path: Path, rel: str, text: str) -> bool:
    lowered_parts = {p.lower() for p in Path(rel).parts}
    if lowered_parts & AUTOGEN_DIR_PARTS:
        return True
    if path.name.lower() in AUTOGEN_EXACT_FILES:
        return True
    if any(pattern.match(path.name) for pattern in AUTOGEN_PATTERNS):
        return True
    header = text[:512].lower()
    return "code generated by" in header or "do not edit" in header


def count_code_and_comments(text: str, language: str) -> tuple[int, int]:
    code = 0
    comments = 0
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if in_block:
            comments += 1
            if "*/" in line:
                in_block = False
            continue
        if line.startswith(("/*", "/**")):
            comments += 1
            if "*/" not in line:
                in_block = True
            continue
        prefixes = COMMENT_PREFIXES.get(language, [])
        if any(line.startswith(prefix) for prefix in prefixes):
            comments += 1
            continue
        if language in {"HTML", "XML", "Markdown"} and line.startswith("<!--"):
            comments += 1
            continue
        code += 1
    return code, comments


def collect_file_stats(repo: Path) -> list[FileStat]:
    stats: list[FileStat] = []
    for path in iter_candidate_files(repo):
        rel = path.relative_to(repo).as_posix()
        language = language_for(path)
        if not language:
            continue
        text = read_text(path)
        raw_loc = len(text.splitlines())
        code_loc, comment_loc = count_code_and_comments(text, language)
        stats.append(
            FileStat(
                path=path,
                rel=rel,
                language=language,
                extension=path.suffix.lower() or path.name,
                raw_loc=raw_loc,
                code_loc=code_loc,
                comment_loc=comment_loc,
                symbols_count=len(text),
                generated=is_generated_file(path, rel, text),
                dependency=is_dependency_path(rel),
            )
        )
    return stats


def rounded_distribution(counter: Counter[str], total: int) -> dict[str, float]:
    if total <= 0:
        return {}
    out = {
        key: round(value / total, 6)
        for key, value in counter.items()
        if value > 0 and value / total >= 0.01
    }
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0].lower())))


def choose_primary_language(lang_loc: Counter[str]) -> str:
    ordered = sorted(lang_loc.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    if not ordered:
        return ""
    for language, loc in ordered:
        if loc > 0 and language not in NON_PRIMARY_LANGUAGES:
            return language
    return ordered[0][0]


def git_commit_count(repo: Path) -> int:
    output = run(["git", "log", "--no-merges", "--oneline"], repo)
    return sum(1 for line in output.splitlines() if line and "revert" not in line.lower())


def git_contributors_count(repo: Path) -> int:
    output = run(["git", "shortlog", "-sn", "--no-merges", "--all"], repo)
    bots = (
        "dependabot",
        "renovate",
        "github-actions",
        "snyk-bot",
        "greenkeeper",
        "semantic-release-bot",
    )
    names = set()
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        name = parts[1].lower()
        if not any(bot in name for bot in bots):
            names.add(name)
    return len(names)


def git_pr_count(repo: Path) -> int:
    bodies = run(["git", "log", "--all", "--merges", "--format=%B"], repo)
    subjects = run(["git", "log", "--all", "--format=%s"], repo)
    github_ids = set(re.findall(r"merge pull request #(\d+)", bodies, re.I))
    github_ids.update(re.findall(r"\(#(\d+)\)\s*$", subjects, re.I | re.M))
    gitlab_ids = set(re.findall(r"see merge request [^\n]*!(\d+)", bodies, re.I))
    return len(github_ids) + len(gitlab_ids)


def created_at(repo: Path) -> str:
    return run(["git", "log", "HEAD", "--reverse", "--format=%ai", "--max-count=1"], repo)


def branch_count(repo: Path) -> int:
    output = run(["git", "branch", "-a"], repo)
    return sum(1 for line in output.splitlines() if line.strip())


def du_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    output = run(["du", "-sk", str(path)], path.parent if path.parent.exists() else Path("."))
    try:
        kb = int(output.split()[0])
    except Exception:
        return 0.0
    return round(kb / 1024, 3)


CI_PATHS = [
    ".github/workflows",
    ".circleci",
    ".travis.yml",
    "Jenkinsfile",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    ".appveyor.yml",
    ".drone.yml",
    "bitbucket-pipelines.yml",
    ".buildkite",
    "circle.yml",
]


def has_ci(repo: Path) -> bool:
    return any((repo / path).exists() for path in CI_PATHS)


def ci_files(repo: Path) -> list[Path]:
    files: list[Path] = []
    for rel in CI_PATHS:
        path = repo / rel
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file())
    return files


def deployment_infra(repo: Path) -> str:
    enterprise_markers = [
        "*.tf",
        "Chart.yaml",
        "deployment.yaml",
        "*.k8s.yml",
        "*.k8s.yaml",
    ]
    if (repo / "k8s").is_dir() or (repo / "kubernetes").is_dir():
        return "Enterprise"
    for pattern in enterprise_markers:
        if any(repo.rglob(pattern)):
            return "Enterprise"
    if has_ci(repo):
        text = "\n".join(read_text(p).lower() for p in ci_files(repo))
        if any(word in text for word in ("deploy", "release", "publish", "ship")):
            return "Full CI-CD"
        return "Basic CI"
    return "None"


def containerized(repo: Path) -> str:
    root_files = ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"]
    if any((repo / name).exists() for name in root_files):
        return "Yes"
    if any(repo.rglob("Dockerfile")) or any(repo.rglob("Chart.yaml")):
        return "Yes"
    if any(repo.rglob("*.k8s.yml")) or any(repo.rglob("*.k8s.yaml")):
        return "Yes"
    for dirname in ("deploy", "infra", "k8s", "kubernetes", "docker"):
        d = repo / dirname
        if d.is_dir() and (any(d.rglob("*.yml")) or any(d.rglob("*.yaml"))):
            return "Yes"
    return "No"


def monitoring(stats: list[FileStat]) -> str:
    apm_full = re.compile(r"\b(opentelemetry|jaeger|honeycomb)\b", re.I)
    apm = re.compile(
        r"\b(sentry|datadog|newrelic|prometheus_client|pagerduty|opsgenie|"
        r"honeycomb|jaeger|opentelemetry|elastic_apm|rollbar|bugsnag|raygun|"
        r"instana|dynatrace)\b",
        re.I,
    )
    basic_patterns = (
        "console.log",
        "console.error",
        "console.warn",
        "logging.basicconfig",
        "logging.getlogger",
        "logrus.new",
        "zap.new",
        "winston.createlogger",
        "bunyan.createlogger",
        "log4j.getlogger",
        "logger.getlogger",
        "logback",
        "pino(",
    )
    saw_basic = False
    for stat in stats:
        if stat.dependency:
            continue
        if stat.language not in {"Python", "JavaScript", "TypeScript", "Go", "Java", "C#", "Ruby", "PHP", "Rust"}:
            continue
        text = read_text(stat.path)
        if apm_full.search(text):
            return "Full SRE"
        if apm.search(text):
            return "APM+Alerting"
        lowered = text.lower()
        if any(pattern in lowered for pattern in basic_patterns):
            saw_basic = True
    return "Basic" if saw_basic else "None"


TEST_PATTERNS = [
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.(py|go|rb|rs|cs)$"),
    re.compile(r"(^|/)[^/]+\.(spec|test)\.(ts|tsx|js|jsx)$"),
    re.compile(r"(^|/)[^/]+(Test|Spec)\.(java|cs)$"),
]


def test_suite(repo: Path, stats: list[FileStat]) -> str:
    test_files = []
    for stat in stats:
        rel = stat.rel
        if any(pattern.search(rel) for pattern in TEST_PATTERNS):
            test_files.append(stat.path)
    if test_files:
        parent_count = len({p.parent for p in test_files})
        return "Comprehensive" if len(test_files) >= 10 or parent_count >= 3 else "Basic"
    config_files = [
        "pytest.ini",
        "jest.config.js",
        "jest.config.ts",
        "jest.config.mjs",
        ".mocharc.js",
        ".mocharc.yml",
        "karma.conf.js",
        "phpunit.xml",
        ".rspec",
    ]
    return "Basic" if any((repo / name).exists() for name in config_files) else "None"


def readme_quality(repo: Path) -> str:
    candidates = [p for p in repo.iterdir() if p.is_file() and p.name.lower().startswith("readme")]
    if not candidates:
        return "None"
    text = "\n".join(read_text(p) for p in candidates).lower()
    if len(text) < 50:
        return "None"
    setup = any(w in text for w in ("install", "setup", "getting started", "requirements", "quickstart"))
    usage = any(w in text for w in ("usage", "example", "quick start", "how to use"))
    arch = any(w in text for w in ("architecture", "how it works", "overview", "design"))
    docs = (repo / "docs").is_dir() or (repo / "CONTRIBUTING.md").exists()
    if docs and setup and usage:
        return "Comprehensive"
    if setup and (usage or arch):
        return "Detailed"
    if len(text) > 200:
        return "Basic"
    return "None"


def issue_tracker(repo: Path) -> str:
    subjects = run(["git", "log", "--all", "--no-merges", "--format=%s", "-n", "200"], repo)
    has_refs = bool(re.search(r"(?:fixes?|closes?|resolves?)\s+#\d+|#\d+\b|JIRA-\w+|LINEAR-\w+|[A-Z]{2,10}-\d+", subjects, re.I))
    has_templates = (repo / ".github/ISSUE_TEMPLATE").exists() or (repo / ".github/ISSUE_TEMPLATE.md").exists()
    has_design = any((repo / p).is_dir() for p in ("docs/rfcs", "docs/adr", "rfcs", "adr"))
    if has_refs and has_design:
        return "Full+Design Docs"
    if has_refs:
        return "Linked to Commits"
    if has_templates:
        return "Basic"
    return "None"


def documentation_cnt(repo: Path) -> int:
    total = 0
    for p in repo.iterdir():
        if p.is_file() and p.name.lower().startswith("readme"):
            total += len(read_text(p).splitlines())
    return total


def python_function_metrics(paths: list[Path]) -> tuple[int, int, int]:
    count = 0
    total_length = 0
    doc_count = 0
    for path in paths:
        text = read_text(path)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end_lineno = getattr(node, "end_lineno", None) or node.lineno
                count += 1
                total_length += max(1, end_lineno - node.lineno + 1)
                if ast.get_docstring(node):
                    doc_count += 1
    return count, total_length, doc_count


FUNC_RE = re.compile(
    r"^\s*(?:public|private|protected|static|async|export|def|func|function|class|interface|"
    r"fn|go|override|internal|sealed|open|final|\w+\s+)*[A-Za-z_][\w<>]*\s+"
    r"[A-Za-z_]\w*\s*\([^;{}]*\)\s*(?:\{|:)",
    re.M,
)


def function_metrics(stats: list[FileStat]) -> tuple[float, float]:
    py_paths = [s.path for s in stats if not s.dependency and s.language == "Python"]
    count, total_length, doc_count = python_function_metrics(py_paths)
    for stat in stats:
        if stat.dependency or stat.language == "Python":
            continue
        if stat.language not in {"JavaScript", "TypeScript", "Java", "Kotlin", "C#", "C++", "C", "Go", "Rust", "PHP", "Swift", "Dart"}:
            continue
        text = read_text(stat.path)
        lines = text.splitlines()
        for match in FUNC_RE.finditer(text):
            start_line = text[: match.start()].count("\n")
            count += 1
            end_line = min(len(lines), start_line + 80)
            total_length += max(1, end_line - start_line)
            before = "\n".join(lines[max(0, start_line - 3) : start_line]).strip()
            if before.endswith("*/") or before.startswith(("///", "/**", "#")):
                doc_count += 1
    avg = round(total_length / count, 2) if count else 0.0
    ratio = round(doc_count / count, 6) if count else 0.0
    return ratio, avg


CODE_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*|0x[0-9A-Fa-f]+|\d+\.\d+|\d+|==|!=|<=|>=|=>|->|::|&&|\|\||"
    r"[{}()[\].,;:+\-*/%&|^~!?<>=$@#]"
)


def estimate_code_tokens(text: str) -> int:
    """Heuristic code token count.

    This is not a model-specific tokenizer. It behaves more like a lightweight
    code lexer: identifiers, numbers, operators, and punctuation count as
    tokens. It is useful for stable repo-to-repo comparison without requiring
    tiktoken or a network install.
    """

    return len(CODE_TOKEN_RE.findall(text))


def estimate_text_tokens(text: str) -> int:
    """Rough LLM text-token estimate using the common chars/4 heuristic."""

    return max(1, round(len(text) / 4)) if text else 0


def token_stats(stats: list[FileStat]) -> dict[str, Any]:
    by_language: Counter[str] = Counter()
    by_extension: Counter[str] = Counter()
    total_code_tokens = 0
    total_text_tokens = 0
    total_chars = 0
    for stat in stats:
        if stat.dependency:
            continue
        text = read_text(stat.path)
        code_tokens = estimate_code_tokens(text)
        text_tokens = estimate_text_tokens(text)
        by_language[stat.language] += code_tokens
        by_extension[stat.extension] += code_tokens
        total_code_tokens += code_tokens
        total_text_tokens += text_tokens
        total_chars += len(text)
    return {
        "tokenizer": "heuristic_code_lexer_v1",
        "estimated_code_tokens": total_code_tokens,
        "estimated_text_tokens": total_text_tokens,
        "total_chars": total_chars,
        "tokens_by_language": dict(sorted(by_language.items(), key=lambda kv: (-kv[1], kv[0].lower()))),
        "tokens_by_extension": dict(sorted(by_extension.items(), key=lambda kv: (-kv[1], kv[0].lower()))),
    }


def build_metadata(
    repo: Path,
    repo_id: str,
    bundle_path: Path | None = None,
    sample_loc_override: int | None = None,
    *,
    include_token_stats: bool = False,
    description: str | None = None,
) -> dict:
    stats = collect_file_stats(repo)
    no_deps = [s for s in stats if not s.dependency]
    raw_loc = sum(s.raw_loc for s in stats)
    logical_loc = sum(s.code_loc for s in no_deps)
    autogen_loc = sum(s.code_loc for s in no_deps if s.generated)
    symbols_count = sum(s.symbols_count for s in no_deps)
    source_files = len(stats)
    lang_loc = Counter()
    ext_loc = Counter()
    comments = 0
    for stat in no_deps:
        if stat.code_loc <= 0:
            continue
        lang_loc[stat.language] += stat.code_loc
        ext_loc[stat.extension] += stat.code_loc
        comments += stat.comment_loc
    primary_language = choose_primary_language(lang_loc)
    doc_ratio, avg_len = function_metrics(no_deps)
    git_mb = du_mb(repo / ".git")
    total_mb = du_mb(repo)
    bundle_mb = round(bundle_path.stat().st_size / 1024 / 1024, 3) if bundle_path and bundle_path.exists() else 0.0
    sample_loc = sample_loc_override if sample_loc_override is not None else logical_loc
    row = {
        "repo_id": repo_id,
        "raw_loc": raw_loc,
        "logical_loc": logical_loc,
        "autogen_loc": autogen_loc,
        "symbols_count": symbols_count,
        "source_files": source_files,
        "primary_language": primary_language,
        "lang_distribution": rounded_distribution(lang_loc, logical_loc),
        "commit_count": git_commit_count(repo),
        "contributors_count": git_contributors_count(repo),
        "total_pr_count": git_pr_count(repo),
        "reviewed_pr_count": 0,
        "ci_checks": "Yes" if has_ci(repo) else "No",
        "deployment_infra": deployment_infra(repo),
        "monitoring": monitoring(no_deps),
        "test_suite": test_suite(repo, stats),
        "containerized": containerized(repo),
        "docstring_ratio": doc_ratio,
        "readme_quality": readme_quality(repo),
        "issue_tracker": issue_tracker(repo),
        "avg_func_length": avg_len,
        "created_at": created_at(repo),
        "branch_count": branch_count(repo),
        "repo_bundle_mb": bundle_mb,
        "repo_git_history_mb": git_mb,
        "repo_worktree_mb": round(max(total_mb - git_mb, 0.0), 3),
        "extensions": rounded_distribution(ext_loc, logical_loc),
        "documentation_cnt": documentation_cnt(repo),
        "comment_ratio": round(comments / logical_loc, 6) if logical_loc else 0.0,
        "sample_loc": sample_loc,
    }
    row = {column: row.get(column) for column in METADATA_COLUMNS}
    if description:
        row["repo_description"] = description
    if include_token_stats:
        row["token_stats"] = token_stats(no_deps)
    return row


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(ch in text for ch in ":#{}[],&*?|\n\r\t\"'") or text.strip() != text:
        return json.dumps(text, ensure_ascii=False)
    return text


def to_yaml(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{yaml_scalar(value)}"


def format_row(row: dict, output_format: str, pretty: bool) -> str:
    if output_format == "json":
        return json.dumps(row, ensure_ascii=False, indent=2 if pretty else None, sort_keys=False)
    if output_format == "jsonl":
        return json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    if output_format == "yaml":
        return to_yaml(row)
    raise ValueError(f"unknown output format: {output_format}")


def description_prompt() -> str:
    return (Path(__file__).with_name("description_prompt.md")).read_text(encoding="utf-8")


def scan_command(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not repo.exists() or not repo.is_dir():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2
    repo_id = args.repo_id or str(uuid.uuid4())
    bundle_path = Path(args.bundle_path).resolve() if args.bundle_path else None
    description = None
    if args.description:
        description = args.description
    elif args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8").strip()
    row = build_metadata(
        repo,
        repo_id,
        bundle_path,
        args.sample_loc,
        include_token_stats=args.schema == "extended",
        description=description,
    )
    if args.schema == "core":
        row = {column: row.get(column) for column in METADATA_COLUMNS}
    text = format_row(row, args.format, args.pretty)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="Scan a repository and emit one metadata row.")
    scan.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    scan.add_argument("--repo-id", default=None, help="Repository UUID. Default: generate a UUID4.")
    scan.add_argument("--bundle-path", default=None, help="Optional .bundle/.zip path for repo_bundle_mb.")
    scan.add_argument("--sample-loc", type=int, default=None, help="Optional explicit sample_loc value. Default: logical_loc.")
    scan.add_argument("--schema", choices=["extended", "core"], default="extended", help="extended adds token_stats; core emits only the 30 source metadata columns.")
    scan.add_argument("--format", choices=["json", "jsonl", "yaml"], default="json", help="Output format.")
    scan.add_argument("--description", default=None, help="Optional repo description string to include in extended output.")
    scan.add_argument("--description-file", default=None, help="Optional file containing repo description text.")
    scan.add_argument("--output", "-o", default="-", help="Output path, or '-' for stdout.")
    scan.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")

    subparsers.add_parser("description-prompt", help="Print the Codex prompt for writing repo descriptions.")
    args = parser.parse_args()
    if args.command in (None, "scan"):
        if args.command is None:
            args = parser.parse_args(["scan", *sys.argv[1:]])
        return scan_command(args)
    if args.command == "description-prompt":
        print(description_prompt())
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
