import json
import zipfile

from reposcanner.scanner import RepoAnonymizer, build_metadata


def test_anonymizer_redacts_identity_and_secret_values() -> None:
    anonymizer = RepoAnonymizer(enabled=True, terms=["Acme Corp", "Jane Doe"])
    text = """
author = "Jane Doe"
company = "Acme Corp"
email = "jane.doe@acme.example"
phone = "+1 (415) 555-1212"
api_url = "https://api.acme.example/v1/users"
password = "super-secret-value-12345"
home = "/Users/janedoe/work"
"""

    sanitized = anonymizer.sanitize_text(text)

    assert "Jane Doe" not in sanitized
    assert "Acme Corp" not in sanitized
    assert "jane.doe@acme.example" not in sanitized
    assert "+1 (415) 555-1212" not in sanitized
    assert "https://api.acme.example" not in sanitized
    assert "super-secret-value-12345" not in sanitized
    assert "/Users/janedoe" not in sanitized
    assert "[NAME_" in sanitized
    assert "[ORG_" in sanitized
    assert "[EMAIL_" in sanitized
    assert "[PHONE_" in sanitized
    assert "[URL_" in sanitized
    assert "[SECRET]" in sanitized
    assert "[USER_" in sanitized


def test_primary_language_skips_non_primary_data_languages(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.json").write_text("\n".join(["{}"] * 100), encoding="utf-8")
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")

    row = build_metadata(
        repo,
        "repo-id",
        include_token_stats=False,
        include_sale_prediction=False,
        include_ai_detection=False,
        prep_sample=False,
        anonymize=False,
    )

    assert row["primary_language"] == "Python"


def test_sample_zip_is_anonymized_by_default(tmp_path) -> None:
    repo = tmp_path / "repo"
    out_dir = tmp_path / "out"
    repo.mkdir()
    (repo / "main.py").write_text(
        "\n".join(
            [
                'OWNER = "Jane Doe"',
                'COMPANY = "Acme Corp"',
                'EMAIL = "jane.doe@acme.example"',
                'API_URL = "https://api.acme.example/v1"',
                'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"',
                "def handler():",
                "    return OWNER, COMPANY, EMAIL, API_URL, TOKEN",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    row = build_metadata(
        repo,
        "repo-id",
        include_token_stats=False,
        include_sale_prediction=False,
        include_ai_detection=False,
        prep_sample=True,
        sample_output=str(out_dir),
        anonymize=True,
        anonymization_terms=["Acme Corp", "Jane Doe"],
    )

    zip_path = out_dir / row["sample_quality"]["zip_path"]
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        sample_name = next(name for name in zf.namelist() if name.endswith("samples/main.py"))
        sample_text = zf.read(sample_name).decode("utf-8")
        report_name = next(name for name in zf.namelist() if name.endswith("anonymization_report.json"))
        report = json.loads(zf.read(report_name))

    assert "Jane Doe" not in sample_text
    assert "Acme Corp" not in sample_text
    assert "jane.doe@acme.example" not in sample_text
    assert "https://api.acme.example" not in sample_text
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in sample_text
    assert report["enabled"] is True
    assert report["replacement_events_by_tag"]["SECRET"] >= 1
