from pathlib import Path

import pytest

from app.external_docs.config import ExternalDocsConfigError, load_external_docs_config


def test_external_docs_config_loads_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "external_docs.yaml"
    config_path.write_text(
        "\n".join(
            [
                "sources:",
                "  - name: docs",
                "    source_kind: external_docs",
                "    allowed_domains:",
                "      - docs.example.com",
                "    start_urls:",
                "      - https://docs.example.com/start",
                "    allow_patterns:",
                "      - '^https://docs\\.example\\.com/'",
                "    deny_patterns:",
                "      - '/blog/'",
                "    crawl_depth: 2",
                "    max_pages: 7",
                "    refresh_days: 3",
            ]
        ),
        encoding="utf-8",
    )

    config = load_external_docs_config(config_path)

    assert config.source("docs").allowed_domains == ("docs.example.com",)
    assert config.source("docs").max_pages == 7
    assert config.source("docs").allow_patterns == ("^https://docs\\.example\\.com/",)


def test_external_docs_config_rejects_start_url_outside_allowed_domain(tmp_path: Path) -> None:
    config_path = tmp_path / "external_docs.yaml"
    config_path.write_text(
        "\n".join(
            [
                "sources:",
                "  - name: docs",
                "    source_kind: external_docs",
                "    allowed_domains:",
                "      - docs.example.com",
                "    start_urls:",
                "      - https://evil.example.com/start",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ExternalDocsConfigError):
        load_external_docs_config(config_path)
