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
import math
import os
import re
import subprocess
import sys
import uuid
import zipfile
from collections import Counter, deque
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

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

TARGET_MIN_PRIMARY_LANGUAGE_LOC = 5000
UNDER_FAIL_MIN_PRIMARY_LANGUAGE_LOC = 1000
SMALL_REPO_CLOSE_RATIO = 0.80
SMALL_REPO_MAX_LOGICAL_LOC = 6500
SMALL_REPO_ABS_TOLERANCE = 500
AI_GENERATED_REJECTION_THRESHOLD = 0.10
DEFAULT_AI_DETECTOR_MODEL = "project-droid/DroidDetect-Base-Binary"
LARGE_AI_DETECTOR_MODEL = "project-droid/DroidDetect-Large-Binary"
DEFAULT_OUTPUT_DIR = "reposcanner_out"


DEPENDENCY_DIRS = {
    "node_modules",
    "vendor",
    "vendors",
    "dist",
    "build",
    "coverage",
    "bower_components",
    ".venv",
    ".vwnv",
    "venv",
    "env",
    ".env",
    "virtualenv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".parcel-cache",
    ".turbo",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".serverless",
    "target",
    "bin",
    "obj",
    "packages",
    "PackageCache",
    "packagecache",
    ".gradle",
    "gradle",
    ".m2",
    ".npm",
    ".yarn",
    ".pnpm-store",
    "Pods",
    "Carthage",
    "DerivedData",
    "cmake-build-debug",
    "cmake-build-release",
    "Debug",
    "Release",
    "x64",
    "x86",
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


class ScanHud(AbstractContextManager["ScanHud"]):
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.console = Console(stderr=True)
        self.progress: Progress | None = None
        self.live: Live | None = None
        self._tasks: dict[str, TaskID] = {}
        self._logs: deque[str] = deque(maxlen=8)
        self._row: dict[str, Any] = {}
        self._stats: list[FileStat] = []
        self._stage = "warming up"

    def __enter__(self) -> "ScanHud":
        if self.enabled:
            self.progress = Progress(
                SpinnerColumn("dots12", style="bold magenta"),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=None),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=self.console,
                transient=False,
                expand=True,
            )
            self.progress.start()
            self.live = Live(
                self.render(),
                console=self.console,
                refresh_per_second=8,
                transient=False,
                vertical_overflow="ellipsis",
            )
            self.live.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.enabled and exc:
            self._stage = "failed"
            self.log(f"[bold red]scan failed[/bold red] {exc}")
            self.refresh()
        elif self.enabled:
            self._stage = "complete"
            self.refresh()
        if self.live:
            self.live.stop()
        if self.progress:
            self.progress.stop()

    def refresh(self) -> None:
        if self.live:
            self.live.update(self.render(), refresh=True)

    def render(self) -> Panel:
        return Panel(
            self.layout(),
            title="[bold white]reposcanner[/bold white] [dim]metadata + sale-fit scanner[/dim]",
            subtitle=f"[dim]{self._stage}[/dim]",
            border_style="bright_magenta",
            box=box.DOUBLE_EDGE,
        )

    def layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(self.header(), name="header", size=5),
            Layout(name="main", ratio=1),
            Layout(self.progress_panel(), name="progress", size=6),
        )
        layout["main"].split_row(
            Layout(self.metrics_panel(), name="metrics", ratio=2),
            Layout(name="right", ratio=3),
        )
        layout["right"].split_column(
            Layout(self.language_panel(), name="languages", ratio=2),
            Layout(self.log_panel(), name="logs", ratio=1),
        )
        return layout

    def header(self) -> Panel:
        title = Text("Repository Sale Prep Console", style="bold bright_cyan")
        subtitle = Text("fair LOC • primary language QA • token stats • sale-fit model • AI-code gate", style="dim")
        pulse = Text("◆ " * 18, style="magenta")
        return Panel(Align.center(Group(title, subtitle, pulse)), border_style="cyan", box=box.ROUNDED)

    def metrics_panel(self) -> Panel:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan")
        table.add_column(justify="right")
        row = self._row
        table.add_row("Primary", str(row.get("primary_language") or "scanning..."))
        table.add_row("Logical LOC", self.format_int(row.get("logical_loc")))
        table.add_row("Raw LOC", self.format_int(row.get("raw_loc")))
        table.add_row("Files", self.format_int(row.get("source_files")))
        table.add_row("Code tokens", self.token_display(row))
        sale_prediction = row.get("sale_prediction")
        if isinstance(sale_prediction, dict):
            probability = float(sale_prediction.get("sale_probability", 0))
            tier = sale_prediction.get("tier")
            label = sale_prediction.get("label")
            table.add_row("Sale prob", f"[bold]{probability:.1%}[/bold]")
            table.add_row("Tier", f"[bold magenta]{tier}[/bold magenta] [dim]{label}[/dim]")
        ai_detection = row.get("ai_code_detection")
        if isinstance(ai_detection, dict):
            percent = float(ai_detection.get("ai_generated_code_percent") or 0)
            gate = ai_detection.get("sale_gate_status") or "unknown"
            gate_style = "red" if "BLOCKED" in str(gate) else "green"
            table.add_row("AI code", f"[bold]{percent:.1f}%[/bold]")
            table.add_row("AI gate", f"[{gate_style}]{gate}[/{gate_style}]")
        sample = row.get("sample_quality")
        if isinstance(sample, dict):
            style = "green" if str(sample.get("status", "")).startswith("PASS") else "red"
            table.add_row("Sample QA", f"[{style}]{sample.get('status')}[/{style}]")
            table.add_row("Sample LOC", self.format_int(sample.get("counted_primary_language_loc")))
        return Panel(table, title="Vitals", border_style="green", box=box.ROUNDED)

    def language_panel(self) -> Panel:
        lang_loc: Counter[str] = Counter()
        for stat in self._stats:
            if not stat.dependency and stat.code_loc > 0:
                lang_loc[stat.language] += stat.code_loc
        table = Table(box=box.SIMPLE_HEAVY, expand=True, show_edge=False)
        table.add_column("Language", style="bold")
        table.add_column("LOC", justify="right")
        table.add_column("Share", justify="right")
        table.add_column("Spark")
        if not lang_loc:
            table.add_row("scanning", "-", "-", "[dim]waiting for files[/dim]")
        else:
            total = sum(lang_loc.values())
            palette = ["bright_cyan", "bright_magenta", "green", "yellow", "blue", "red"]
            for index, (language, loc) in enumerate(sorted(lang_loc.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:8]):
                share = loc / total if total else 0
                width = max(1, round(share * 28))
                color = palette[index % len(palette)]
                table.add_row(language, f"{loc:,}", f"{share:.1%}", f"[{color}]{'█' * width}[/{color}]")
        return Panel(table, title="Language Signal", border_style="bright_blue", box=box.ROUNDED)

    def log_panel(self) -> Panel:
        lines = list(self._logs) or ["[dim]logs will appear here[/dim]"]
        return Panel("\n".join(lines), title="Scan Log", border_style="yellow", box=box.ROUNDED)

    def progress_panel(self) -> Panel:
        renderable = self.progress if self.progress else Text("progress initializing", style="dim")
        return Panel(renderable, title="Pipeline", border_style="bright_black", box=box.ROUNDED)

    @staticmethod
    def format_int(value: Any) -> str:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def token_display(row: dict[str, Any]) -> str:
        token_stats_row = row.get("token_stats")
        if isinstance(token_stats_row, dict):
            return f"{token_stats_row.get('estimated_code_tokens', 0):,}"
        return "not requested"

    def log(self, message: str) -> None:
        if self.enabled:
            self._logs.append(message)
            self.refresh()

    def task(self, key: str, description: str, total: int) -> None:
        if not self.progress:
            return
        total = max(total, 1)
        self._tasks[key] = self.progress.add_task(description, total=total)
        self._stage = description
        self.refresh()

    def advance(self, key: str, amount: int = 1) -> None:
        if self.progress and key in self._tasks:
            self.progress.advance(self._tasks[key], amount)
            task = self.progress.tasks[self._tasks[key]]
            if int(task.completed) % 25 == 0 or task.completed >= task.total:
                self.refresh()

    def complete(self, key: str) -> None:
        if self.progress and key in self._tasks:
            task_id = self._tasks[key]
            task = self.progress.tasks[task_id]
            self.progress.update(task_id, completed=task.total)
            self.refresh()

    def summary(self, row: dict, stats: list[FileStat]) -> None:
        if not self.enabled:
            return
        self._row = row
        self._stats = stats
        self._stage = "finalizing"
        self.refresh()


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
            if d not in ALWAYS_SKIP_DIRS and d not in DEPENDENCY_DIRS and not d.endswith(".egg-info") and not d.startswith(".cache")
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


def collect_file_stats(repo: Path, hud: ScanHud | None = None) -> list[FileStat]:
    stats: list[FileStat] = []
    if hud:
        hud.log(f"Discovering source-like files under [bold]{repo}[/bold]")
    candidates = list(iter_candidate_files(repo))
    if hud:
        hud.task("files", "Counting files", len(candidates))
        hud.log(f"Found [bold]{len(candidates):,}[/bold] candidate files")
    for path in candidates:
        rel = path.relative_to(repo).as_posix()
        language = language_for(path)
        if not language:
            if hud:
                hud.advance("files")
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
        if hud:
            hud.advance("files")
    return stats


def rounded_distribution(counter: Counter[str], total: int) -> dict[str, float]:
    if total <= 0:
        return {}
    out = {key: round(value / total, 6) for key, value in counter.items() if value > 0 and value / total >= 0.01}
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0].lower())))


def parse_distribution(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return {str(k): float(v or 0) for k, v in value.items()}
    if value is None:
        return {}
    text = str(value).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): float(v or 0) for k, v in data.items()}


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


def refresh_git_refs(repo: Path, hud: ScanHud | None = None) -> str:
    if not (repo / ".git").exists():
        return "skipped: not a git repository"
    if hud:
        hud.log("Refreshing git refs with fetch --all --tags --prune")
    output = run(["git", "fetch", "--all", "--tags", "--prune"], repo)
    return output or "ok"


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


def clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def detector_chunks(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunk = max(max_chars // 3, 1)
    middle_start = max((len(text) // 2) - (chunk // 2), 0)
    return [text[:chunk], text[middle_start : middle_start + chunk], text[-chunk:]]


def detector_snippet(text: str, max_chars: int) -> str:
    return "\n\n".join(detector_chunks(text, max_chars))


def select_ai_detection_files(stats: list[FileStat], max_files: int) -> list[FileStat]:
    eligible = [
        stat
        for stat in stats
        if not stat.dependency and not stat.generated and stat.code_loc > 0 and stat.language not in NON_PRIMARY_LANGUAGES
    ]
    if not eligible:
        eligible = [stat for stat in stats if not stat.dependency and not stat.generated and stat.code_loc > 0]
    eligible.sort(key=lambda stat: (-stat.code_loc, stat.rel))
    return eligible[: max(max_files, 0)]


def ai_generated_label_indexes(labels: dict[int, str], class_count: int) -> set[int]:
    generated_indexes: set[int] = set()
    for index, label in labels.items():
        lowered = label.lower()
        if "human" in lowered:
            continue
        if "machine" in lowered or "ai" in lowered:
            generated_indexes.add(index)
    if not generated_indexes and class_count == 2:
        generated_indexes.add(1)
    return generated_indexes


def droid_label_map(class_count: int) -> dict[int, str]:
    if class_count == 2:
        return {0: "HUMAN_GENERATED", 1: "MACHINE_GENERATED"}
    if class_count == 3:
        return {0: "HUMAN_GENERATED", 1: "MACHINE_GENERATED", 2: "MACHINE_REFINED"}
    if class_count == 4:
        return {
            0: "HUMAN_GENERATED",
            1: "MACHINE_GENERATED",
            2: "MACHINE_REFINED",
            3: "MACHINE_GENERATED_ADVERSARIAL",
        }
    return {index: f"LABEL_{index}" for index in range(class_count)}


def droid_base_model_name(model_name: str) -> str:
    return "answerdotai/ModernBERT-large" if "large" in model_name.lower() else "answerdotai/ModernBERT-base"


def load_droid_model(model_name: str) -> tuple[Any, Any, dict[int, str]]:
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F
        from huggingface_hub import hf_hub_download
        from transformers import AutoConfig, AutoModel, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("install AI dependencies with `uv tool install 'reposcanner[ai]'` or `uv sync --extra ai`") from exc

    class DroidDetectModel(nn.Module):
        def __init__(self, text_encoder: Any, hidden_dim: int, projection_dim: int, num_classes: int) -> None:
            super().__init__()
            self.text_encoder = text_encoder
            self.text_projection = nn.Linear(hidden_dim, projection_dim)
            self.classifier = nn.Linear(projection_dim, num_classes)

        def forward(self, input_ids: Any = None, attention_mask: Any = None, **_: Any) -> dict[str, Any]:
            sentence_embeddings = self.text_encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
            sentence_embeddings = sentence_embeddings.mean(dim=1)
            projected_text = F.relu(self.text_projection(sentence_embeddings))
            return {"logits": self.classifier(projected_text)}

    tokenizer = AutoTokenizer.from_pretrained(droid_base_model_name(model_name))
    checkpoint_path = hf_hub_download(model_name, "pytorch_model.bin")
    state = torch.load(checkpoint_path, map_location="cpu")
    projection_weight = state.get("text_projection.weight")
    classifier_weight = state.get("classifier.weight")
    if projection_weight is None or classifier_weight is None:
        raise RuntimeError("DroidDetect checkpoint is missing text_projection/classifier weights")
    projection_dim, hidden_dim = projection_weight.shape
    num_classes = classifier_weight.shape[0]
    base_config = AutoConfig.from_pretrained(droid_base_model_name(model_name))
    text_encoder = AutoModel.from_config(base_config)
    model = DroidDetectModel(text_encoder, int(hidden_dim), int(projection_dim), int(num_classes))
    missing, unexpected = model.load_state_dict(state, strict=False)
    meaningful_missing = [key for key in missing if not key.startswith("additional_loss.")]
    meaningful_unexpected = [key for key in unexpected if not key.startswith("additional_loss.")]
    if meaningful_missing or meaningful_unexpected:
        raise RuntimeError(
            "DroidDetect checkpoint did not match reconstructed model "
            f"(missing={meaningful_missing[:5]}, unexpected={meaningful_unexpected[:5]})"
        )
    return tokenizer, model, droid_label_map(int(num_classes))


def summarize_ai_detection(
    records: list[dict[str, Any]],
    *,
    backend: str,
    model_name: str | None,
    threshold: float,
    notes: list[str],
    status: str = "completed",
    detector_error: str | None = None,
) -> dict[str, Any]:
    scanned_loc = sum(int(record.get("code_loc") or 0) for record in records)
    weighted_ai_loc = sum(float(record.get("ai_probability") or 0) * int(record.get("code_loc") or 0) for record in records)
    ratio = weighted_ai_loc / scanned_loc if scanned_loc else 0.0
    likely_ai_loc = sum(int(record.get("code_loc") or 0) for record in records if float(record.get("ai_probability") or 0) >= 0.50)
    gate_status = (
        "BLOCKED_AI_GENERATED_CODE_APPEAL_REQUIRED"
        if status == "completed" and ratio > threshold
        else "PASS_AI_GENERATED_CODE_GATE"
        if status == "completed"
        else "UNKNOWN_AI_DETECTOR_UNAVAILABLE"
    )
    if gate_status == "BLOCKED_AI_GENERATED_CODE_APPEAL_REQUIRED":
        gate_reason = (
            f"Estimated AI-generated code is {ratio * 100:.2f}%, above the "
            f"{threshold * 100:.1f}% sale threshold. Explain and appeal before submitting."
        )
    elif gate_status == "PASS_AI_GENERATED_CODE_GATE":
        gate_reason = f"Estimated AI-generated code is {ratio * 100:.2f}%, within the {threshold * 100:.1f}% threshold."
    else:
        gate_reason = "AI detector could not complete; rerun with the optional AI dependencies before sale submission."
    label_counts = Counter(str(record.get("predicted_label") or "unknown") for record in records)
    result = {
        "enabled": True,
        "status": status,
        "backend": backend,
        "model": model_name or "",
        "ai_generated_code_ratio": round(ratio, 6),
        "ai_generated_code_percent": round(ratio * 100, 4),
        "expected_ai_generated_loc": round(weighted_ai_loc, 2),
        "likely_ai_generated_loc": likely_ai_loc,
        "scanned_loc": scanned_loc,
        "scanned_files": len(records),
        "sale_rejection_threshold_ratio": threshold,
        "sale_rejection_threshold_percent": round(threshold * 100, 4),
        "sale_gate_status": gate_status,
        "sale_gate_reason": gate_reason,
        "requires_explanation_or_appeal": gate_status == "BLOCKED_AI_GENERATED_CODE_APPEAL_REQUIRED",
        "label_counts": dict(sorted(label_counts.items())),
        "files": records,
        "notes": notes,
    }
    if detector_error:
        result["detector_error"] = detector_error[:500]
    return result


def classify_with_droid(
    files: list[FileStat],
    model_name: str,
    max_chars: int,
    threshold: float,
    hud: ScanHud | None = None,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("install AI dependencies with `uv tool install 'reposcanner[ai]'` or `uv sync --extra ai`") from exc

    if hud:
        hud.log(f"Loading AI-code detector [bold]{model_name}[/bold]")
    tokenizer, model, labels = load_droid_model(model_name)
    if torch.cuda.is_available():
        device = "cuda"
    elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    model.to(device)
    model.eval()

    configured_max_length = getattr(tokenizer, "model_max_length", 4096) or 4096
    if configured_max_length > 100_000:
        configured_max_length = 4096
    max_length = min(int(configured_max_length), 8192)
    generated_indexes: set[int] | None = None
    records: list[dict[str, Any]] = []
    if hud:
        hud.task("ai", "Detecting AI code", len(files))
    for stat in files:
        full_text = read_text(stat.path)
        chunk_probabilities: list[float] = []
        chunk_labels: list[str] = []
        for text in detector_chunks(full_text, max_chars):
            encoded = tokenizer(
                text,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.no_grad():
                output = model(**encoded)
                logits = output["logits"] if isinstance(output, dict) else output.logits
                probabilities = torch.softmax(logits[0].detach().cpu(), dim=-1).tolist()
            if generated_indexes is None:
                generated_indexes = ai_generated_label_indexes(labels, len(probabilities))
            generated_probability = sum(probabilities[index] for index in generated_indexes if index < len(probabilities))
            predicted_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
            chunk_probabilities.append(float(generated_probability))
            chunk_labels.append(labels.get(predicted_index, f"LABEL_{predicted_index}"))
        droid_probability = max(chunk_probabilities) if chunk_probabilities else 0.0
        best_chunk_index = chunk_probabilities.index(droid_probability) if chunk_probabilities else 0
        droid_label = chunk_labels[best_chunk_index] if chunk_labels else "unknown"
        guardrail_probability = heuristic_ai_probability(stat, detector_snippet(full_text, max_chars))
        final_probability = max(droid_probability, guardrail_probability)
        predicted_label = droid_label
        if guardrail_probability > droid_probability:
            predicted_label = "MACHINE_GENERATED_HEURISTIC_GUARDRAIL" if final_probability >= 0.50 else "HUMAN_GENERATED_LIKELY"
        records.append(
            {
                "path": stat.rel,
                "language": stat.language,
                "code_loc": stat.code_loc,
                "ai_probability": round(float(final_probability), 6),
                "human_probability": round(float(1.0 - final_probability), 6),
                "droid_ai_probability": round(float(droid_probability), 6),
                "heuristic_guardrail_probability": round(float(guardrail_probability), 6),
                "droid_chunk_ai_probabilities": [round(value, 6) for value in chunk_probabilities],
                "droid_predicted_label": droid_label,
                "predicted_label": predicted_label,
                "detector": "droiddetect_chunked_with_heuristic_guardrail",
            }
        )
        if hud:
            hud.advance("ai")
    if hud:
        hud.complete("ai")
    return summarize_ai_detection(
        records,
        backend="droid",
        model_name=model_name,
        threshold=threshold,
        notes=[
            "Repository-level percent is LOC-weighted over sampled real source files.",
            "DroidDetect scores head/middle/tail chunks for long files and uses the strongest generated-code signal per file.",
            "A local heuristic guardrail is recorded and can raise the final per-file probability when DroidDetect under-detects generated utility code.",
            "Dependency, generated, build, and virtual environment folders are excluded before detection.",
            "DroidDetect scores are probabilistic and should be used as a sale gate plus appeal signal.",
        ],
    )


def heuristic_ai_probability(stat: FileStat, text: str) -> float:
    nonblank = [line.strip() for line in text.splitlines() if line.strip()]
    if not nonblank:
        return 0.0
    lowered = text.lower()
    score = 0.30
    if stat.code_loc >= 1000:
        score += 0.28
    elif stat.code_loc >= 300:
        score += 0.20
    elif stat.code_loc >= 80:
        score += 0.10
    comment_ratio = stat.comment_loc / max(stat.code_loc, 1)
    if 0.02 <= comment_ratio <= 0.22:
        score += 0.06
    if stat.language in {"Python", "TypeScript", "JavaScript", "Java", "Kotlin", "C#", "Go", "Rust"}:
        functions = len(FUNC_RE.findall(text))
        if functions >= 20:
            score += 0.10
        elif functions >= 6:
            score += 0.06
    if stat.language == "Python":
        typed_defs = len(re.findall(r"def\s+\w+\([^)]*\)\s*->\s*[\w\[\], |.]+:", text))
        typed_args = len(re.findall(r"\b\w+:\s*[A-Za-z_][\w.[\], |]*", text))
        if typed_defs >= 8 or typed_args >= 30:
            score += 0.12
    repeated_phrases = sum(lowered.count(phrase) for phrase in ("helper", "metadata", "fallback", "threshold", "schema"))
    if repeated_phrases >= 25:
        score += 0.06
    long_lines = sum(1 for line in nonblank if len(line) > 100)
    if long_lines / max(len(nonblank), 1) < 0.12:
        score += 0.04
    human_markers = ("todo", "fixme", "hack", "wtf", "temporary", "quick and dirty", "console.log(")
    if any(marker in lowered for marker in human_markers):
        score -= 0.10
    if "generated by" in lowered or "do not edit" in lowered:
        score += 0.25
    return clamp01(score)


def classify_with_heuristic(files: list[FileStat], max_chars: int, threshold: float, hud: ScanHud | None = None) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    if hud:
        hud.task("ai", "Heuristic AI-code scan", len(files))
    for stat in files:
        text = detector_snippet(read_text(stat.path), max_chars)
        probability = heuristic_ai_probability(stat, text)
        label = "MACHINE_GENERATED_LIKELY" if probability >= 0.50 else "HUMAN_GENERATED_LIKELY"
        records.append(
            {
                "path": stat.rel,
                "language": stat.language,
                "code_loc": stat.code_loc,
                "ai_probability": round(probability, 6),
                "human_probability": round(1.0 - probability, 6),
                "predicted_label": label,
                "detector": "heuristic_fallback",
            }
        )
        if hud:
            hud.advance("ai")
    if hud:
        hud.complete("ai")
    return summarize_ai_detection(
        records,
        backend="heuristic",
        model_name="local_heuristic_v1",
        threshold=threshold,
        notes=[
            "Heuristic fallback is low-confidence and exists so the scan remains runnable without model dependencies.",
            "Use the DroidDetect backend for sale decisions whenever possible.",
            "Dependency, generated, build, and virtual environment folders are excluded before detection.",
        ],
    )


def detect_ai_generated_code(
    stats: list[FileStat],
    *,
    backend: str,
    model_name: str,
    max_files: int,
    max_chars: int,
    threshold: float,
    fallback_heuristic: bool,
    hud: ScanHud | None = None,
) -> dict[str, Any]:
    files = select_ai_detection_files(stats, max_files)
    if not files:
        return summarize_ai_detection(
            [],
            backend=backend,
            model_name=model_name if backend == "droid" else "local_heuristic_v1",
            threshold=threshold,
            notes=["No eligible real source files were available for AI-code detection."],
            status="completed",
        )
    if backend == "heuristic":
        return classify_with_heuristic(files, max_chars, threshold, hud)
    try:
        return classify_with_droid(files, model_name, max_chars, threshold, hud)
    except Exception as exc:
        if not fallback_heuristic:
            return summarize_ai_detection(
                [],
                backend="droid",
                model_name=model_name,
                threshold=threshold,
                notes=["DroidDetect backend failed and heuristic fallback was disabled."],
                status="unavailable",
                detector_error=str(exc),
            )
        if hud:
            hud.log(f"[yellow]DroidDetect unavailable; using heuristic fallback ({exc})[/yellow]")
        result = classify_with_heuristic(files, max_chars, threshold, hud)
        result["backend"] = "heuristic_fallback_after_droid_error"
        result["requested_model"] = model_name
        result["detector_error"] = str(exc)[:500]
        result["notes"].insert(0, "DroidDetect backend failed; heuristic fallback was used.")
        return result


ANONYMIZATION_STOPWORDS = {
    "admin",
    "api",
    "app",
    "build",
    "client",
    "code",
    "config",
    "core",
    "data",
    "default",
    "dev",
    "docs",
    "example",
    "github",
    "gitlab",
    "main",
    "package",
    "prod",
    "project",
    "repo",
    "sample",
    "server",
    "service",
    "source",
    "src",
    "test",
    "user",
    "web",
}

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\w)")
URL_RE = re.compile(r"\b(?:https?|ssh|git)://[^\s\"'<>]+", re.I)
IP_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
MENTION_RE = re.compile(r"(?<![\w/])@[A-Za-z0-9][A-Za-z0-9_-]{1,38}\b")
USER_PATH_RE = re.compile(r"(?P<prefix>(?:/Users|/home)/)(?P<user>[^/\s\"']+)")
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.S,
)
KNOWN_SECRET_RE = re.compile(
    r"\b(?:"
    r"AKIA[0-9A-Z]{16}|"
    r"ASIA[0-9A-Z]{16}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,255}|"
    r"github_pat_[A-Za-z0-9_]{20,255}|"
    r"glpat-[A-Za-z0-9_-]{20,255}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,255}|"
    r"sk_live_[A-Za-z0-9]{20,255}|"
    r"rk_live_[A-Za-z0-9]{20,255}|"
    r"AIza[0-9A-Za-z_-]{35}|"
    r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"
    r")\b"
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<prefix>\b(?:api[_-]?key|secret|token|password|passwd|pwd|client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|private[_-]?key|dsn)\b\s*[:=]\s*[\"']?)"
    r"(?P<value>[^\"'\s,;]{8,})"
    r"(?P<suffix>[\"']?)",
    re.I,
)
HOST_ASSIGNMENT_RE = re.compile(
    r"(?P<prefix>\b(?:host|hostname|server|endpoint|base[_-]?url|api[_-]?url|url)\b\s*[:=]\s*[\"']?)"
    r"(?P<value>(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?(?:/[^\s\"',;]*)?)"
    r"(?P<suffix>[\"']?)",
    re.I,
)
KEYED_NAME_QUOTED_RE = re.compile(
    r"(?P<prefix>\b(?:author|owner|maintainer|name|company|organization|org|client|customer|contact)\b"
    r"\s*[:=]\s*[\"'])"
    r"(?P<value>[^\"']{2,80})"
    r"(?P<suffix>[\"'])",
    re.I,
)
KEYED_NAME_UNQUOTED_RE = re.compile(
    r"(?P<prefix>\b(?:company|organization|org|client|customer|contact)\b\s*[:=]\s*)"
    r"(?P<value>[A-Z][A-Za-z0-9 &._-]{2,80})$",
    re.I | re.M,
)
HIGH_ENTROPY_RE = re.compile(r"\b[A-Za-z0-9+/=_]{32,}\b")


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def normalized_term(term: str) -> str:
    return re.sub(r"\s+", " ", term.strip())


def useful_anonymization_term(term: str) -> bool:
    term = normalized_term(term)
    lowered = term.lower()
    if len(term) < 4 or lowered in ANONYMIZATION_STOPWORDS:
        return False
    if re.fullmatch(r"[\W_]+", term):
        return False
    return True


def git_remote_identity_terms(repo: Path) -> set[str]:
    terms: set[str] = set()
    remote_output = run(["git", "remote", "-v"], repo)
    for owner, name in re.findall(r"[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:\s|\)|$)", remote_output):
        terms.update({owner, name.removesuffix(".git")})
    return terms


def git_author_identity_terms(repo: Path) -> set[str]:
    terms: set[str] = set()
    output = run(["git", "log", "--all", "--format=%an%x00%ae", "-n", "2000"], repo)
    for line in output.splitlines():
        if "\0" in line:
            name, email = line.split("\0", 1)
        else:
            name, email = line, ""
        if useful_anonymization_term(name):
            terms.add(name)
        if email and "@" in email:
            local = email.split("@", 1)[0]
            for part in re.split(r"[._+-]+", local):
                if useful_anonymization_term(part):
                    terms.add(part)
    return terms


def manifest_identity_terms(repo: Path) -> set[str]:
    terms: set[str] = {repo.name}
    package_json = repo / "package.json"
    if package_json.exists():
        try:
            package = json.loads(read_text(package_json))
        except json.JSONDecodeError:
            package = {}
        if isinstance(package, dict):
            for key in ("name", "author", "homepage"):
                value = package.get(key)
                if isinstance(value, str):
                    terms.add(value.split("/")[-1] if key == "name" else value)
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        text = read_text(pyproject)
        for match in re.findall(r"(?m)^\s*(?:name|authors?)\s*=\s*[\"']([^\"']+)[\"']", text):
            terms.add(match)
    return {term for term in terms if useful_anonymization_term(term)}


def default_anonymization_terms(repo: Path) -> list[str]:
    terms = set()
    terms.update(git_remote_identity_terms(repo))
    terms.update(git_author_identity_terms(repo))
    terms.update(manifest_identity_terms(repo))
    return sorted((term for term in terms if useful_anonymization_term(term)), key=lambda value: (-len(value), value.lower()))


def load_anonymization_terms(paths: list[str] | None) -> list[str]:
    terms: list[str] = []
    for raw_path in paths or []:
        path = Path(raw_path)
        if not path.exists():
            continue
        for line in read_text(path).splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms


class RepoAnonymizer:
    def __init__(self, *, enabled: bool, terms: list[str] | None = None) -> None:
        self.enabled = enabled
        self.terms = []
        seen_terms = set()
        for term in terms or []:
            term = normalized_term(term)
            lowered = term.lower()
            if useful_anonymization_term(term) and lowered not in seen_terms:
                self.terms.append(term)
                seen_terms.add(lowered)
        self.terms.sort(key=lambda value: (-len(value), value.lower()))
        self._values: dict[tuple[str, str], str] = {}
        self._tag_counts: Counter[str] = Counter()
        self._replacement_events: Counter[str] = Counter()

    def placeholder(self, tag: str, value: str, *, numbered: bool = True) -> str:
        tag = tag.upper()
        self._replacement_events[tag] += 1
        if tag == "SECRET" or not numbered:
            self._tag_counts[tag] += 1
            return f"[{tag}]"
        key = (tag, value.strip().lower())
        if key not in self._values:
            self._tag_counts[tag] += 1
            self._values[key] = f"[{tag}_{self._tag_counts[tag]:03d}]"
        return self._values[key]

    def report(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": "local_regex_entropy_deterministic_tags" if self.enabled else "disabled",
            "identity_terms_loaded": len(self.terms),
            "replacement_events_by_tag": dict(sorted(self._replacement_events.items())),
            "unique_replacements_by_tag": dict(sorted(self._tag_counts.items())),
            "notes": [
                "Anonymization runs locally and does not send code to external APIs.",
                "Secrets are redacted; identity values are replaced with deterministic tags.",
                "Add --anonymize-term or --anonymize-terms-file for company/project names the local heuristics cannot infer.",
            ],
        }

    def sanitize_text(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        text = PRIVATE_KEY_RE.sub(lambda match: self.placeholder("SECRET", match.group(0), numbered=False), text)
        text = KNOWN_SECRET_RE.sub(lambda match: self.placeholder("SECRET", match.group(0), numbered=False), text)
        text = SECRET_ASSIGNMENT_RE.sub(
            lambda match: (
                f"{match.group('prefix')}{self.placeholder('SECRET', match.group('value'), numbered=False)}{match.group('suffix')}"
            ),
            text,
        )
        text = HIGH_ENTROPY_RE.sub(
            lambda match: (
                self.placeholder("SECRET", match.group(0), numbered=False) if shannon_entropy(match.group(0)) >= 4.25 else match.group(0)
            ),
            text,
        )
        text = EMAIL_RE.sub(lambda match: self.placeholder("EMAIL", match.group(0)), text)
        text = PHONE_RE.sub(lambda match: self.placeholder("PHONE", match.group(0)), text)
        text = URL_RE.sub(lambda match: self.placeholder("URL", match.group(0)), text)
        text = HOST_ASSIGNMENT_RE.sub(
            lambda match: f"{match.group('prefix')}{self.placeholder('URL', match.group('value'))}{match.group('suffix')}",
            text,
        )
        text = IP_RE.sub(lambda match: self.placeholder("IP", match.group(0)), text)
        text = USER_PATH_RE.sub(lambda match: f"{match.group('prefix')}{self.placeholder('USER', match.group('user'))}", text)
        text = MENTION_RE.sub(lambda match: self.placeholder("USER", match.group(0)[1:]), text)

        def replace_keyed_name(match: re.Match[str]) -> str:
            key_text = match.group("prefix").lower()
            tag = "ORG" if any(word in key_text for word in ("company", "organization", "org", "client", "customer")) else "NAME"
            return f"{match.group('prefix')}{self.placeholder(tag, match.group('value'))}{match.group('suffix')}"

        text = KEYED_NAME_QUOTED_RE.sub(replace_keyed_name, text)
        text = KEYED_NAME_UNQUOTED_RE.sub(
            lambda match: f"{match.group('prefix')}{self.placeholder('ORG', match.group('value'))}",
            text,
        )
        for term in self.terms:
            tag = "ORG" if any(part in term.lower() for part in ("inc", "llc", "corp", "labs", "studio", "systems")) else "NAME"
            pattern = re.compile(rf"(?<![\w.-]){re.escape(term)}(?![\w.-])", re.I)
            text = pattern.sub(lambda match, tag=tag: self.placeholder(tag, match.group(0)), text)
        return text


def close_enough_to_whole_repo(target_lines: int, logical_loc: int) -> bool:
    if logical_loc <= 0 or target_lines <= 0:
        return False
    ratio = target_lines / logical_loc
    return logical_loc <= SMALL_REPO_MAX_LOGICAL_LOC and (
        ratio >= SMALL_REPO_CLOSE_RATIO or abs(logical_loc - target_lines) <= SMALL_REPO_ABS_TOLERANCE
    )


def sample_quality(primary_language: str, primary_lines: int, logical_loc: int) -> dict[str, Any]:
    if primary_lines >= TARGET_MIN_PRIMARY_LANGUAGE_LOC:
        status = "PASS"
        reason = f"{primary_lines} counted {primary_language} lines meets 5,000+ target."
    elif primary_lines >= UNDER_FAIL_MIN_PRIMARY_LANGUAGE_LOC:
        status = "PASS_UNDER_5K_ABOVE_1K"
        reason = f"{primary_lines} counted {primary_language} lines is below the 5,000 target, but above the 1,000-line failure floor."
    elif close_enough_to_whole_repo(primary_lines, logical_loc):
        status = "PASS_SMALL_WHOLE_PROJECT"
        reason = (
            f"Only {primary_lines} counted {primary_language} lines, but logical_loc is {logical_loc}; "
            "sample primary-language LOC is close to the whole repo."
        )
    elif not primary_language:
        status = "FAIL_UNKNOWN_PRIMARY_LANGUAGE"
        reason = "Could not infer primary_language."
    elif primary_lines == 0:
        status = "FAIL_NO_PRIMARY_LANGUAGE_LINES"
        reason = f"Primary language is {primary_language}, but sample has 0 counted {primary_language} lines."
    elif logical_loc <= SMALL_REPO_MAX_LOGICAL_LOC:
        status = "FAIL_SMALL_REPO_NOT_CLOSE_ENOUGH"
        reason = (
            f"Only {primary_lines} counted {primary_language} lines and logical_loc is {logical_loc}; "
            f"sample is not close enough to whole repo threshold ({SMALL_REPO_CLOSE_RATIO:.0%} or within "
            f"{SMALL_REPO_ABS_TOLERANCE} LOC)."
        )
    else:
        status = "FAIL_UNDER_PRIMARY_LANGUAGE_TARGET"
        reason = (
            f"Only {primary_lines} counted {primary_language} lines; below 1,000-line failure floor, "
            f"and logical_loc {logical_loc} is not a small whole repo."
        )
    return {
        "status": status,
        "reason": reason,
        "counted_primary_language": primary_language,
        "counted_primary_language_loc": primary_lines,
        "target_min_primary_language_loc": TARGET_MIN_PRIMARY_LANGUAGE_LOC,
        "under_fail_min_primary_language_loc": UNDER_FAIL_MIN_PRIMARY_LANGUAGE_LOC,
        "small_repo_close_rule": {
            "max_logical_loc": SMALL_REPO_MAX_LOGICAL_LOC,
            "minimum_ratio": SMALL_REPO_CLOSE_RATIO,
            "absolute_tolerance_loc": SMALL_REPO_ABS_TOLERANCE,
        },
    }


SUPPORT_FILE_NAMES = {
    "readme",
    "readme.md",
    "readme.rst",
    "readme.txt",
    "license",
    "license.md",
    "copying",
    "package.json",
    "pyproject.toml",
    "composer.json",
    "go.mod",
    "cargo.toml",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "requirements.txt",
}


def select_sample_files(stats: list[FileStat], primary_language: str) -> list[FileStat]:
    primary = [
        stat for stat in stats if not stat.dependency and not stat.generated and stat.language == primary_language and stat.code_loc > 0
    ]
    primary.sort(key=lambda stat: (-stat.code_loc, stat.rel))
    selected: list[FileStat] = []
    total = 0
    for stat in primary:
        selected.append(stat)
        total += stat.code_loc
        if total >= TARGET_MIN_PRIMARY_LANGUAGE_LOC:
            break
    if not selected:
        fallback = [stat for stat in stats if not stat.dependency and not stat.generated and stat.code_loc > 0]
        fallback.sort(key=lambda stat: (-stat.code_loc, stat.rel))
        selected = fallback[:30]
    selected_rels = {stat.rel for stat in selected}
    for stat in stats:
        if stat.rel in selected_rels or stat.dependency:
            continue
        if Path(stat.rel).parent == Path(".") and stat.path.name.lower() in SUPPORT_FILE_NAMES:
            selected.append(stat)
            selected_rels.add(stat.rel)
    return selected


def sample_zip_path(output: str | None, repo_id: str) -> Path:
    if not output:
        return Path.cwd() / f"{repo_id}_sample.zip"
    path = Path(output)
    if path.suffix.lower() == ".zip":
        return path
    return path / f"{repo_id}_sample.zip"


def write_sample_zip(
    repo: Path,
    repo_id: str,
    row: dict[str, Any],
    stats: list[FileStat],
    output: str | None,
    anonymizer: RepoAnonymizer | None = None,
) -> dict[str, Any]:
    primary_language = str(row.get("primary_language") or "")
    selected = select_sample_files(stats, primary_language)
    primary_lines = sum(stat.code_loc for stat in selected if stat.language == primary_language)
    quality = sample_quality(primary_language, primary_lines, int(row.get("logical_loc") or 0))
    quality.update(
        {
            "selected_files": len(selected),
            "selected_total_nonblank_loc": sum(stat.code_loc for stat in selected),
            "zip_shape": "data/{repo_id}/samples/...",
        }
    )
    ai_detection = row.get("ai_code_detection")
    if isinstance(ai_detection, dict):
        quality["ai_generated_code_percent"] = ai_detection.get("ai_generated_code_percent")
        quality["ai_gate_status"] = ai_detection.get("sale_gate_status")
        quality["requires_explanation_or_appeal"] = ai_detection.get("requires_explanation_or_appeal")
    out = sample_zip_path(output, repo_id).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    base = f"data/{repo_id}"
    manifest = {
        "repo_id": repo_id,
        "created_by": "reposcanner",
        "anonymization_required_before_sharing": True,
        "anonymization": {"enabled": bool(anonymizer and anonymizer.enabled)},
        "sample_quality": quality,
        "files": [{"path": stat.rel, "language": stat.language, "code_loc": stat.code_loc} for stat in selected],
    }
    summary = (
        "# Repository Summary\n\n"
        f"Primary language: {primary_language or 'unknown'}\n\n"
        f"Logical LOC: {row.get('logical_loc')}\n\n"
        f"Sample QA: {quality['status']} - {quality['reason']}\n"
    )
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for stat in selected:
            text = read_text(stat.path)
            if anonymizer:
                text = anonymizer.sanitize_text(text)
            zf.writestr(f"{base}/samples/{stat.rel}", text)
        if anonymizer:
            summary = anonymizer.sanitize_text(summary)
        zf.writestr(f"{base}/repo_summary.md", summary)
        metadata_json = json.dumps(row, indent=2, ensure_ascii=False)
        if anonymizer:
            metadata_json = anonymizer.sanitize_text(metadata_json)
        zf.writestr(f"{base}/metadata.json", metadata_json)
        if anonymizer:
            manifest["anonymization"] = anonymizer.report()
        zf.writestr(f"{base}/sample_quality.json", json.dumps(quality, indent=2, ensure_ascii=False))
        zf.writestr(f"{base}/sample_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        if anonymizer:
            zf.writestr(f"{base}/anonymization_report.json", json.dumps(anonymizer.report(), indent=2, ensure_ascii=False))
    quality["zip_path"] = out.name
    quality["zip_bytes"] = out.stat().st_size
    return quality


def load_sale_model() -> dict[str, Any] | None:
    model_path = Path(__file__).with_name("sale_model.json")
    if not model_path.exists():
        return None
    try:
        return json.loads(model_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sale_model_vector(row: dict[str, Any], model: dict[str, Any]) -> list[float]:
    weights = model.get("group_weights", {})
    numeric_weight = float(weights.get("numeric", 1.0))
    language_weight = float(weights.get("language", 1.0))
    categorical_weight = float(weights.get("categorical", 1.0))
    vector: list[float] = []

    for column in model.get("numeric_columns", []):
        stats = model.get("numeric_stats", {}).get(column, {})
        median = float(stats.get("median", 0.0))
        scale = max(float(stats.get("scale", 1.0)), 1e-6)
        value = row.get(column)
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = median
        if stats.get("log1p", False):
            number = math.log1p(max(number, 0.0))
        vector.append(((number - median) / scale) * numeric_weight)

    distribution = parse_distribution(row.get("lang_distribution"))
    primary_language = str(row.get("primary_language") or "")
    for language in model.get("languages", []):
        value = float(distribution.get(language, 0.0))
        if primary_language == language:
            value += 0.35
        vector.append(value * language_weight)

    for column in model.get("categorical_columns", []):
        value = str(row.get(column) or "")
        for expected in model.get("categorical_values", {}).get(column, []):
            vector.append((1.0 if value == expected else 0.0) * categorical_weight)

    return vector


def mean_k_distance(vector: list[float], references: list[list[float]], k: int) -> float:
    if not references:
        return 0.0
    distances = []
    for ref in references:
        size = min(len(vector), len(ref))
        distance = math.sqrt(sum((vector[i] - float(ref[i])) ** 2 for i in range(size)))
        distances.append(distance)
    distances.sort()
    k = max(1, min(k, len(distances)))
    return sum(distances[:k]) / k


def predict_sale(row: dict[str, Any]) -> dict[str, Any] | None:
    model = load_sale_model()
    if not model:
        return None
    vector = sale_model_vector(row, model)
    labels = model.get("reference_labels", [])
    references = model.get("reference_vectors", [])
    sold_refs = [ref for ref, label in zip(references, labels, strict=False) if int(label) == 1]
    background_refs = [ref for ref, label in zip(references, labels, strict=False) if int(label) == 0]
    k = int(model.get("k", 5))
    temperature = max(float(model.get("temperature", 1.0)), 1e-6)
    sold_distance = mean_k_distance(vector, sold_refs, k)
    background_distance = mean_k_distance(vector, background_refs, k)
    raw = max(min((background_distance - sold_distance) / temperature, 50.0), -50.0)
    probability = 1.0 / (1.0 + math.exp(-raw))
    thresholds = model.get("thresholds", {})
    tier_1 = float(thresholds.get("tier_1_similarity", 0.85))
    tier_2 = float(thresholds.get("tier_2_similarity", 0.70))
    if probability >= tier_1:
        tier = 1
        label = "high probability of sale"
    elif probability >= tier_2:
        tier = 2
        label = "promising"
    else:
        tier = 3
        label = "standard"
    return {
        "tier": tier,
        "label": label,
        "sale_probability": round(probability, 4),
        "similarity_to_sold": round(probability, 4),
        "nearest_sold_distance": round(sold_distance, 4),
        "nearest_background_distance": round(background_distance, 4),
        "model_version": model.get("version"),
    }


def build_metadata(
    repo: Path,
    repo_id: str,
    bundle_path: Path | None = None,
    sample_loc_override: int | None = None,
    *,
    include_token_stats: bool = False,
    include_sale_prediction: bool = True,
    include_ai_detection: bool = False,
    ai_detector_backend: str = "droid",
    ai_detector_model: str = DEFAULT_AI_DETECTOR_MODEL,
    ai_detector_max_files: int = 8,
    ai_detector_max_chars: int = 12_000,
    ai_detection_threshold: float = AI_GENERATED_REJECTION_THRESHOLD,
    ai_fallback_heuristic: bool = True,
    prep_sample: bool = False,
    sample_output: str | None = None,
    anonymize: bool = True,
    anonymization_terms: list[str] | None = None,
    description: str | None = None,
    hud: ScanHud | None = None,
) -> dict:
    stats = collect_file_stats(repo, hud)
    anonymizer = RepoAnonymizer(
        enabled=anonymize,
        terms=[*default_anonymization_terms(repo), *(anonymization_terms or [])],
    )
    no_deps = [s for s in stats if not s.dependency]
    if hud:
        hud.task("metrics", "Computing metrics", 8)
    raw_loc = sum(s.raw_loc for s in stats)
    logical_loc = sum(s.code_loc for s in no_deps)
    autogen_loc = sum(s.code_loc for s in no_deps if s.generated)
    symbols_count = sum(s.symbols_count for s in no_deps)
    source_files = len(stats)
    if hud:
        hud.advance("metrics")
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
    if hud:
        hud.log(f"Primary language: [bold green]{primary_language or 'unknown'}[/bold green]")
        hud.advance("metrics")
    doc_ratio, avg_len = function_metrics(no_deps)
    if hud:
        hud.advance("metrics")
    git_mb = du_mb(repo / ".git")
    total_mb = du_mb(repo)
    bundle_mb = round(bundle_path.stat().st_size / 1024 / 1024, 3) if bundle_path and bundle_path.exists() else 0.0
    sample_loc = sample_loc_override if sample_loc_override is not None else logical_loc
    if hud:
        hud.advance("metrics")
    commit_count = git_commit_count(repo)
    contributors_count = git_contributors_count(repo)
    total_pr_count = git_pr_count(repo)
    created = created_at(repo)
    branches = branch_count(repo)
    if hud:
        hud.advance("metrics")
    ci = "Yes" if has_ci(repo) else "No"
    deployment = deployment_infra(repo)
    mon = monitoring(no_deps)
    tests = test_suite(repo, stats)
    containers = containerized(repo)
    if hud:
        hud.advance("metrics")
    readme = readme_quality(repo)
    issues = issue_tracker(repo)
    docs = documentation_cnt(repo)
    if hud:
        hud.advance("metrics")
    row = {
        "repo_id": repo_id,
        "raw_loc": raw_loc,
        "logical_loc": logical_loc,
        "autogen_loc": autogen_loc,
        "symbols_count": symbols_count,
        "source_files": source_files,
        "primary_language": primary_language,
        "lang_distribution": rounded_distribution(lang_loc, logical_loc),
        "commit_count": commit_count,
        "contributors_count": contributors_count,
        "total_pr_count": total_pr_count,
        "reviewed_pr_count": 0,
        "ci_checks": ci,
        "deployment_infra": deployment,
        "monitoring": mon,
        "test_suite": tests,
        "containerized": containers,
        "docstring_ratio": doc_ratio,
        "readme_quality": readme,
        "issue_tracker": issues,
        "avg_func_length": avg_len,
        "created_at": created,
        "branch_count": branches,
        "repo_bundle_mb": bundle_mb,
        "repo_git_history_mb": git_mb,
        "repo_worktree_mb": round(max(total_mb - git_mb, 0.0), 3),
        "extensions": rounded_distribution(ext_loc, logical_loc),
        "documentation_cnt": docs,
        "comment_ratio": round(comments / logical_loc, 6) if logical_loc else 0.0,
        "sample_loc": sample_loc,
    }
    row = {column: row.get(column) for column in METADATA_COLUMNS}
    if description:
        row["repo_description"] = anonymizer.sanitize_text(description)
    if include_token_stats:
        if hud:
            hud.log("Estimating code tokens")
        row["token_stats"] = token_stats(no_deps)
    if include_sale_prediction:
        if hud:
            hud.log("Scoring sale probability")
        prediction = predict_sale(row)
        if prediction:
            row["sale_prediction"] = prediction
    if include_ai_detection:
        if hud:
            hud.log("Running AI-generated code detector")
        ai_detection = detect_ai_generated_code(
            no_deps,
            backend=ai_detector_backend,
            model_name=ai_detector_model,
            max_files=ai_detector_max_files,
            max_chars=ai_detector_max_chars,
            threshold=ai_detection_threshold,
            fallback_heuristic=ai_fallback_heuristic,
            hud=hud,
        )
        row["ai_generated_code_percent"] = ai_detection["ai_generated_code_percent"]
        row["ai_generated_code_ratio"] = ai_detection["ai_generated_code_ratio"]
        row["ai_generated_code_sale_gate"] = ai_detection["sale_gate_status"]
        row["ai_generated_code_requires_appeal"] = ai_detection["requires_explanation_or_appeal"]
        row["ai_code_detection"] = ai_detection
        if isinstance(row.get("sale_prediction"), dict):
            row["sale_prediction"]["ai_gate_status"] = ai_detection["sale_gate_status"]
            row["sale_prediction"]["eligible_without_ai_appeal"] = not ai_detection["requires_explanation_or_appeal"]
    if prep_sample:
        if hud:
            hud.log("Preparing anonymized-sample zip structure")
            hud.task("sample", "Writing sample zip", 1)
        quality = write_sample_zip(repo, repo_id, row, stats, sample_output, anonymizer if anonymize else None)
        if include_ai_detection and isinstance(row.get("ai_code_detection"), dict):
            ai_detection = row["ai_code_detection"]
            quality["ai_generated_code_percent"] = ai_detection["ai_generated_code_percent"]
            quality["ai_gate_status"] = ai_detection["sale_gate_status"]
            quality["requires_explanation_or_appeal"] = ai_detection["requires_explanation_or_appeal"]
        row["sample_quality"] = quality
        if hud:
            hud.complete("sample")
    if anonymize:
        row["anonymization"] = anonymizer.report()
    if hud:
        hud.complete("metrics")
        hud.summary(row, stats)
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
    repo = Path(args.path or args.repo).resolve()
    if not repo.exists() or not repo.is_dir():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2
    if not 0 <= args.ai_threshold <= 1:
        print("--ai-threshold must be a ratio from 0 to 1, for example 0.10 for 10%", file=sys.stderr)
        return 2
    repo_id = args.repo_id or str(uuid.uuid4())
    bundle_path = Path(args.bundle_path).resolve() if args.bundle_path else None
    output_dir = Path(args.output_dir).resolve()
    sample_output = args.sample_output or str(output_dir)
    explicit_terms = list(args.anonymize_term or [])
    explicit_terms.extend(load_anonymization_terms(args.anonymize_terms_file))
    description = None
    if args.description:
        description = args.description
    elif args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8").strip()
    with ScanHud(args.hud) as hud:
        if args.refresh_git:
            refresh_git_refs(repo, hud)
        row = build_metadata(
            repo,
            repo_id,
            bundle_path,
            args.sample_loc,
            include_token_stats=args.schema == "extended" and args.token_stats,
            include_sale_prediction=args.schema == "extended" and args.sale_prediction,
            include_ai_detection=args.schema == "extended" and args.ai_detect,
            ai_detector_backend=args.ai_detector_backend,
            ai_detector_model=args.ai_model,
            ai_detector_max_files=args.ai_max_files,
            ai_detector_max_chars=args.ai_max_chars,
            ai_detection_threshold=args.ai_threshold,
            ai_fallback_heuristic=args.ai_fallback_heuristic,
            prep_sample=args.prep_sample,
            sample_output=sample_output,
            anonymize=args.anonymize,
            anonymization_terms=explicit_terms,
            description=description,
            hud=hud,
        )
    if args.schema == "core":
        row = {column: row.get(column) for column in METADATA_COLUMNS}
    text = format_row(row, args.format, args.pretty)
    if args.output == "-":
        print(text)
    else:
        if args.output:
            output_path = Path(args.output)
        else:
            suffix = "yaml" if args.format == "yaml" else args.format
            output_path = output_dir / f"metadata.{suffix}"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="Scan a repository and emit one metadata row.")
    scan.add_argument("path", nargs="?", help="Repository root positional shortcut; same as --repo.")
    scan.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    scan.add_argument("--repo-id", default=None, help="Repository UUID. Default: generate a UUID4.")
    scan.add_argument("--bundle-path", default=None, help="Optional .bundle/.zip path for repo_bundle_mb.")
    scan.add_argument("--sample-loc", type=int, default=None, help="Optional explicit sample_loc value. Default: logical_loc.")
    scan.add_argument(
        "--schema",
        choices=["extended", "core"],
        default="extended",
        help="extended adds token_stats; core emits only the 30 source metadata columns.",
    )
    scan.add_argument("--format", choices=["json", "jsonl", "yaml"], default="json", help="Output format.")
    scan.add_argument("--description", default=None, help="Optional repo description string to include in extended output.")
    scan.add_argument("--description-file", default=None, help="Optional file containing repo description text.")
    scan.add_argument(
        "--output", "-o", default=None, help=f"Metadata output path, or '-' for stdout. Default: ./{DEFAULT_OUTPUT_DIR}/metadata.<format>."
    )
    scan.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Default folder for metadata/sample outputs. Default: ./{DEFAULT_OUTPUT_DIR}."
    )
    scan.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    sample_group = scan.add_mutually_exclusive_group()
    sample_group.add_argument(
        "--prep-sample",
        dest="prep_sample",
        action="store_true",
        default=True,
        help="Create a customer-facing code sample zip. Enabled by default.",
    )
    sample_group.add_argument("--no-prep-sample", dest="prep_sample", action="store_false", help="Skip sample zip creation.")
    scan.add_argument(
        "--sample-output", default=None, help=f"Sample zip path or output directory. Default: ./{DEFAULT_OUTPUT_DIR}/<repo_id>_sample.zip"
    )
    token_group = scan.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token-stats",
        dest="token_stats",
        action="store_true",
        default=True,
        help="Add token estimates to extended metadata. Enabled by default.",
    )
    token_group.add_argument("--no-token-stats", dest="token_stats", action="store_false", help="Skip token estimates.")
    sale_group = scan.add_mutually_exclusive_group()
    sale_group.add_argument(
        "--sale-prediction",
        dest="sale_prediction",
        action="store_true",
        default=True,
        help="Add local sale-fit tier/probability. Enabled by default.",
    )
    sale_group.add_argument("--no-sale-prediction", dest="sale_prediction", action="store_false", help="Skip sale-fit scoring.")
    ai_group = scan.add_mutually_exclusive_group()
    ai_group.add_argument(
        "--ai-detect",
        dest="ai_detect",
        action="store_true",
        default=True,
        help="Add AI-generated code detection to extended metadata. Enabled by default.",
    )
    ai_group.add_argument("--no-ai-detect", dest="ai_detect", action="store_false", help="Skip AI-generated code detection.")
    scan.add_argument(
        "--ai-detector-backend", choices=["droid", "heuristic"], default="droid", help="AI-code detector backend. Default: droid."
    )
    scan.add_argument(
        "--ai-model",
        default=DEFAULT_AI_DETECTOR_MODEL,
        help=f"Hugging Face model for --ai-detector-backend droid. Default: {DEFAULT_AI_DETECTOR_MODEL}",
    )
    scan.add_argument(
        "--ai-max-files", type=int, default=8, help="Maximum largest real source files to score for AI detection. Default: 8."
    )
    scan.add_argument(
        "--ai-max-chars", type=int, default=12_000, help="Maximum characters sampled per file for AI detection. Default: 12000."
    )
    scan.add_argument(
        "--ai-threshold",
        type=float,
        default=AI_GENERATED_REJECTION_THRESHOLD,
        help="AI-generated code ratio above which sale requires explanation/appeal. Default: 0.10.",
    )
    scan.add_argument(
        "--no-ai-fallback-heuristic",
        dest="ai_fallback_heuristic",
        action="store_false",
        default=True,
        help="Do not use the local heuristic if DroidDetect cannot load.",
    )
    anonymize_group = scan.add_mutually_exclusive_group()
    anonymize_group.add_argument(
        "--anonymize",
        dest="anonymize",
        action="store_true",
        default=True,
        help="Anonymize generated sample/description outputs. Enabled by default.",
    )
    anonymize_group.add_argument("--no-anonymize", dest="anonymize", action="store_false", help="Do not anonymize generated outputs.")
    scan.add_argument(
        "--anonymize-term",
        action="append",
        default=[],
        help="Extra company/project/person term to replace in generated outputs. Repeatable.",
    )
    scan.add_argument(
        "--anonymize-terms-file", action="append", default=[], help="File with one extra anonymization term per line. Repeatable."
    )
    scan.add_argument(
        "--refresh-git", action="store_true", help="Fetch all git refs/tags before scanning metadata. Does not pull or modify branches."
    )
    hud_group = scan.add_mutually_exclusive_group()
    hud_group.add_argument(
        "--hud", dest="hud", action="store_true", default=True, help="Show the rich progress HUD on stderr. Enabled by default."
    )
    hud_group.add_argument("--no-hud", dest="hud", action="store_false", help="Disable the rich progress HUD.")

    subparsers.add_parser("description-prompt", help="Print the Codex prompt for writing repo descriptions.")
    argv = sys.argv[1:]
    known_commands = {"scan", "description-prompt"}
    if not argv or argv[0].startswith("-"):
        argv = ["scan", *argv]
    elif argv[0] not in known_commands:
        argv = ["scan", *argv]
    args = parser.parse_args(argv)
    if args.command == "scan":
        return scan_command(args)
    if args.command == "description-prompt":
        print(description_prompt())
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
