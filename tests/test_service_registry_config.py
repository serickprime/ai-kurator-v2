from pathlib import Path

import pytest

from app.service_registry.config import ServiceRegistryConfigError, load_service_registry_config


def test_service_registry_config_loads_default_yaml() -> None:
    config = load_service_registry_config()

    assert config.service("n8n").docs_source == "n8n_docs"
    assert config.service("supabase").docs_source == "supabase_docs"
    assert config.service("flutterflow").status == "not_configured"


def test_service_registry_config_loads_aliases(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
services:
  - service_id: example
    display_name: Example
    aliases:
      - example
      - пример
    docs_source: example_docs
    status: enabled
""".strip(),
        encoding="utf-8",
    )

    config = load_service_registry_config(path)

    assert config.service("пример").service_id == "example"
    assert config.service("example").aliases == ("example", "пример")


def test_service_registry_config_rejects_enabled_without_docs_source(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
services:
  - service_id: example
    display_name: Example
    aliases:
      - example
    docs_source: null
    status: enabled
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ServiceRegistryConfigError):
        load_service_registry_config(path)
