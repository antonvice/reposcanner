"""Executable examples of open issues, not successful privacy/quality checks.

Strict xfails make an implemented fix require promoting its regression to a normal
test. All credentials and identities below are synthetic. See issues.md for scope.
"""

import ast
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from reposcanner import scanner


def open_issue(issue):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"Open {issue}; see issues.md")


def scan(repo, **options):
    return scanner.build_metadata(repo, "audit", include_sale_prediction=False, **options)


@open_issue("RS-001")
@pytest.mark.parametrize(
    ("filename", "source", "secret"),
    [
        ("Config.cs", 'class Config { string password = @"plain-secret"; }', "plain-secret"),
        ("config.yaml", "password: |\n  plain-secret\n", "plain-secret"),
        ("config.xml", '<cfg xmlns:a="urn:auth"><a:password>plain-secret</a:password></cfg>', "plain-secret"),
        ("README.md", "password=short\n", "short"),
        ("config.py", 'API_TOKEN = "plain-secret"\n', "plain-secret"),
        ("config.py", 'password = """short"""\n', "short"),
    ],
)
def test_sensitive_literal_forms_are_removed_or_rejected(filename, source, secret):
    try:
        result = scanner.RepoAnonymizer(enabled=True).sanitize_code(source, Path(filename))
    except ValueError:
        return  # Refusing this sample is preferable to emitting a credential.
    assert secret not in result


@open_issue("RS-002")
def test_a_discovered_secret_is_removed_from_its_other_literal_occurrences():
    source = 'password = "plain-secret"\ncopy = "plain-secret"\n'
    result = scanner.RepoAnonymizer(enabled=True).sanitize_code(source, Path("config.py"))
    ast.parse(result)
    assert "plain-secret" not in result


@open_issue("RS-003")
def test_description_cannot_allocate_an_alias_that_collides_with_source(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("class ANON_ORG_001: pass\nclass AcmeCorp: pass\n")
    row = scan(
        repo,
        prep_sample=True,
        sample_output=str(tmp_path / "out"),
        anonymization_terms=["AcmeCorp"],
        description="AcmeCorp application",
    )
    with zipfile.ZipFile(tmp_path / "out" / row["sample_quality"]["zip_path"]) as archive:
        source = archive.read("data/audit/samples/app.py").decode()
    names = [node.name for node in ast.parse(source).body if isinstance(node, ast.ClassDef)]
    assert len(names) == len(set(names))


@open_issue("RS-004")
def test_package_scope_and_object_author_supply_identity_terms(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "@acme/budget", "author": {"name": "Private Person", "email": "person@example.test"}})
    )
    terms = scanner.manifest_identity_terms(tmp_path)
    assert {"acme", "Private Person"} <= terms


@open_issue("RS-006")
def test_notebook_custom_fields_cannot_bypass_sanitization():
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "private_export": "PrivateCompany",
        "cells": [{"cell_type": "code", "id": "PrivateCompany", "source": ["print(1)"], "metadata": {}, "outputs": []}],
    }
    result = scanner.RepoAnonymizer(enabled=True, terms=["PrivateCompany"]).sanitize_code(json.dumps(notebook), Path("demo.ipynb"))
    assert "PrivateCompany" not in result


@open_issue("RS-008")
def test_monorepo_packages_can_contain_first_party_code(tmp_path):
    source = tmp_path / "packages" / "core" / "rules.py"
    source.parent.mkdir(parents=True)
    source.write_text("def price(): return 42\n")
    assert {stat.rel for stat in scanner.collect_file_stats(tmp_path)} == {"packages/core/rules.py"}


@open_issue("RS-009")
def test_utf16_source_is_not_silently_lost(tmp_path):
    (tmp_path / "Legacy.cs").write_bytes("class Legacy {}\n".encode("utf-16"))
    assert scan(tmp_path)["logical_loc"] == 1


@open_issue("RS-010")
def test_output_ancestor_does_not_exclude_the_entire_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "rules.py").write_text("print(1)\n")
    assert scan(repo, sample_output=str(tmp_path))["logical_loc"] == 1


@open_issue("RS-012")
def test_secondary_tests_have_a_bounded_selection(tmp_path):
    (tmp_path / "Rules.cs").write_text("int value;\n" * 6000)
    (tmp_path / "tests").mkdir()
    for i in range(10):
        (tmp_path / "tests" / f"test_{i}.py").write_text("assert True\n")
    selected = scanner.select_sample_files(scanner.collect_file_stats(tmp_path), "C#")
    assert sum(stat.language == "Python" for stat in selected) <= 2


@open_issue("RS-013")
def test_markup_only_repository_cannot_pass_a_programming_sample_check(tmp_path):
    (tmp_path / "README.md").write_text("Documentation text.\n" * 6000)
    row = scan(tmp_path, prep_sample=True, sample_output=str(tmp_path / "out"))
    assert not row["sample_quality"]["status"].startswith("PASS")


@open_issue("RS-014")
@pytest.mark.parametrize(("language", "source"), [("XML", "<!--\ncomment\n-->\n<x/>\n"), ("C", "/* doc */ int value;\n")])
def test_mixed_and_multiline_comments_do_not_corrupt_code_loc(language, source):
    assert scanner.count_code_and_comments(source, language)[0] == 1


@open_issue("RS-016")
def test_failed_git_refresh_is_not_reported_as_ok(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(subprocess, "check_output", fail)
    assert scanner.refresh_git_refs(tmp_path) != "ok"


@open_issue("RS-018")
def test_metadata_output_cannot_overwrite_source(tmp_path):
    path = tmp_path / "app.py"
    original = "print(1)\n"
    path.write_text(original)
    subprocess.run(
        [sys.executable, "-m", "reposcanner", str(tmp_path), "--no-prep-sample", "--no-hud", "--output", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert path.read_text() == original


@open_issue("RS-020")
@pytest.mark.parametrize("value", ["Yes", "null", "0123", "2026-11-06"])
def test_yaml_ambiguous_strings_remain_quoted_strings(value):
    # JSON quoting is valid YAML and avoids a runtime PyYAML dependency.
    assert scanner.yaml_scalar(value) == json.dumps(value)


@open_issue("RS-021")
def test_sale_fit_uses_the_measured_sample_loc(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("print(1)\n")
    measured_at_scoring = []

    def capture(row):
        measured_at_scoring.append(row["sample_loc"])
        return None

    monkeypatch.setattr(scanner, "predict_sale", capture)
    row = scanner.build_metadata(tmp_path, "audit", prep_sample=True, sample_output=str(tmp_path / "out"))
    assert measured_at_scoring == [row["sample_loc"]] == [1]
