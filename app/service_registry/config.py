"""Load service/docs registry configuration."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.service_registry.types import ServiceConfigStatus, ServiceDefinition, ServiceRegistryConfig

DEFAULT_SERVICE_REGISTRY_CONFIG = Path("config/service_docs_registry.yaml")
ALLOWED_STATUSES: set[str] = {"enabled", "not_configured", "disabled", "needs_review"}
SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ServiceRegistryConfigError(ValueError):
    """Raised when service docs registry config is invalid."""


def load_service_registry_config(path: Path | str = DEFAULT_SERVICE_REGISTRY_CONFIG) -> ServiceRegistryConfig:
    """Load and validate service/docs registry config."""
    config_path = Path(path)
    if not config_path.exists():
        raise ServiceRegistryConfigError(f"Service registry config not found: {config_path}")
    rows = _parse_services_yaml(config_path.read_text(encoding="utf-8"))
    services = tuple(_service_from_mapping(row, index=index + 1) for index, row in enumerate(rows))
    if not services:
        raise ServiceRegistryConfigError("Service registry config must contain at least one service.")

    ids = [service.service_id for service in services]
    if len(ids) != len(set(ids)):
        raise ServiceRegistryConfigError("Service registry config contains duplicate service_id values.")

    aliases: dict[str, str] = {}
    for service in services:
        for alias in service.aliases:
            key = alias.casefold()
            previous = aliases.get(key)
            if previous and previous != service.service_id:
                raise ServiceRegistryConfigError(f"Alias {alias!r} is used by multiple services.")
            aliases[key] = service.service_id

    return ServiceRegistryConfig(services=services)


def _service_from_mapping(row: dict[str, Any], *, index: int) -> ServiceDefinition:
    service_id = _required_str(row, "service_id", index=index).casefold()
    display_name = _required_str(row, "display_name", index=index)
    aliases = tuple(_required_list(row, "aliases", index=index))
    docs_source = _optional_str(row.get("docs_source"))
    status = _required_str(row, "status", index=index)

    if not SERVICE_ID_RE.match(service_id):
        raise ServiceRegistryConfigError(f"Service #{index}: invalid service_id {service_id!r}.")
    if status not in ALLOWED_STATUSES:
        raise ServiceRegistryConfigError(f"Service {service_id}: invalid status {status!r}.")
    if status == "enabled" and not docs_source:
        raise ServiceRegistryConfigError(f"Service {service_id}: enabled services require docs_source.")
    if docs_source and not SERVICE_ID_RE.match(docs_source):
        raise ServiceRegistryConfigError(f"Service {service_id}: invalid docs_source {docs_source!r}.")

    normalized_aliases = tuple(dict.fromkeys(alias.strip() for alias in aliases if alias.strip()))
    if not normalized_aliases:
        raise ServiceRegistryConfigError(f"Service {service_id}: aliases must not be empty.")

    return ServiceDefinition(
        service_id=service_id,
        display_name=display_name,
        aliases=normalized_aliases,
        docs_source=docs_source,
        status=status,  # type: ignore[arg-type]
    )


def _parse_services_yaml(text: str) -> list[dict[str, Any]]:
    """Parse the limited YAML subset used by config/service_docs_registry.yaml."""
    services: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_list_key: str | None = None
    seen_services_key = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        raw_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not raw_without_comment.strip():
            continue
        indent = len(raw_without_comment) - len(raw_without_comment.lstrip(" "))
        line = raw_without_comment.strip()
        if line == "services:":
            seen_services_key = True
            continue
        if not seen_services_key:
            raise ServiceRegistryConfigError("Service registry config must start with services:.")

        if line.startswith("- "):
            item = line[2:].strip()
            if indent == 2:
                if current is not None:
                    services.append(current)
                current = {}
                current_list_key = None
                if item:
                    key, value = _split_key_value(item, line_number)
                    current[key] = _scalar(value)
                continue
            if current is None or current_list_key is None:
                raise ServiceRegistryConfigError(f"Line {line_number}: list item has no parent key.")
            current[current_list_key].append(_scalar(item))
            continue

        if current is None:
            raise ServiceRegistryConfigError(f"Line {line_number}: service field appears before a service item.")
        key, value = _split_key_value(line, line_number)
        if value == "":
            current[key] = []
            current_list_key = key
        else:
            current[key] = _scalar(value)
            current_list_key = None

    if current is not None:
        services.append(current)
    return services


def _split_key_value(text: str, line_number: int) -> tuple[str, str]:
    if ":" not in text:
        raise ServiceRegistryConfigError(f"Line {line_number}: expected key: value.")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise ServiceRegistryConfigError(f"Line {line_number}: empty key.")
    return key, value.strip()


def _scalar(value: str) -> object:
    clean = value.strip()
    if len(clean) >= 2 and clean[:1] == clean[-1:] and clean[:1] in {"'", '"'}:
        clean = clean[1:-1]
    clean = clean.replace("\\\\", "\\")
    if clean.casefold() in {"null", "none", "~"}:
        return None
    if clean.isdigit():
        return int(clean)
    return clean


def _required_str(row: dict[str, Any], key: str, *, index: int) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ServiceRegistryConfigError(f"Service #{index}: missing {key}.")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_list(row: dict[str, Any], key: str, *, index: int) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not value:
        raise ServiceRegistryConfigError(f"Service #{index}: {key} must be a non-empty list.")
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise ServiceRegistryConfigError(f"Service #{index}: {key} must contain non-empty values.")
    return result
