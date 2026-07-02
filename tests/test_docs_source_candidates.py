from pathlib import Path

import pytest

from app.docs_registry.candidates import DocsSourceCandidatesConfigError, load_docs_source_candidates_config


def test_docs_source_candidates_config_loads_default_catalog() -> None:
    config = load_docs_source_candidates_config()
    service_ids = {candidate.service_id for candidate in config.candidates}

    assert "claude_code" in service_ids
    assert "openrouter" in service_ids
    assert "ollama" in service_ids
    assert "flutterflow" not in service_ids
    assert len(config.candidates) == 12


def test_docs_source_candidates_rejects_duplicate_service_id(tmp_path: Path) -> None:
    path = tmp_path / "candidates.yaml"
    path.write_text(_catalog(_candidate("demo", "demo_docs"), _candidate("demo", "other_docs")), encoding="utf-8")

    with pytest.raises(DocsSourceCandidatesConfigError, match="duplicate service_id"):
        load_docs_source_candidates_config(path)


def test_docs_source_candidates_rejects_duplicate_docs_source(tmp_path: Path) -> None:
    path = tmp_path / "candidates.yaml"
    path.write_text(_catalog(_candidate("demo_one", "demo_docs"), _candidate("demo_two", "demo_docs")), encoding="utf-8")

    with pytest.raises(DocsSourceCandidatesConfigError, match="duplicate docs_source"):
        load_docs_source_candidates_config(path)


def test_docs_source_candidates_rejects_url_outside_allowed_domains(tmp_path: Path) -> None:
    path = tmp_path / "candidates.yaml"
    path.write_text(
        _catalog(_candidate("demo", "demo_docs", start_url="https://wrong.example.com/docs")),
        encoding="utf-8",
    )

    with pytest.raises(DocsSourceCandidatesConfigError, match="outside allowed_domains"):
        load_docs_source_candidates_config(path)


def test_docs_source_candidates_rejects_invalid_regex(tmp_path: Path) -> None:
    path = tmp_path / "candidates.yaml"
    path.write_text(_catalog(_candidate("demo", "demo_docs", allow_pattern="[")), encoding="utf-8")

    with pytest.raises(DocsSourceCandidatesConfigError, match="invalid regex"):
        load_docs_source_candidates_config(path)


def test_docs_source_candidates_rejects_non_positive_max_pages(tmp_path: Path) -> None:
    path = tmp_path / "candidates.yaml"
    path.write_text(_catalog(_candidate("demo", "demo_docs", max_pages=0)), encoding="utf-8")

    with pytest.raises(DocsSourceCandidatesConfigError, match="max_pages"):
        load_docs_source_candidates_config(path)


def test_docs_source_candidates_rejects_negative_crawl_depth(tmp_path: Path) -> None:
    path = tmp_path / "candidates.yaml"
    path.write_text(_catalog(_candidate("demo", "demo_docs", crawl_depth=-1)), encoding="utf-8")

    with pytest.raises(DocsSourceCandidatesConfigError, match="crawl_depth"):
        load_docs_source_candidates_config(path)


def _catalog(*entries: str) -> str:
    return "candidates:\n" + "\n".join(entries)


def _candidate(
    service_id: str,
    docs_source: str,
    *,
    start_url: str = "https://docs.example.com/start",
    allow_pattern: str = "^https://docs\\.example\\.com/",
    max_pages: int = 10,
    crawl_depth: int = 1,
) -> str:
    return f"""  - service_id: {service_id}
    display_name: Demo
    aliases:
      - demo
    docs_source: {docs_source}
    official_start_urls:
      - {start_url}
    allowed_domains:
      - docs.example.com
    allow_patterns:
      - "{allow_pattern}"
    deny_patterns:
      - "/login"
    max_pages: {max_pages}
    crawl_depth: {crawl_depth}
    risk_level: low
    notes: "test candidate"
"""
