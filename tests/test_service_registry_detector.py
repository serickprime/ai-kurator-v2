from app.service_registry.config import load_service_registry_config
from app.service_registry.detector import ServiceDetector


def test_service_registry_detector_matches_n8n_aliases() -> None:
    config = load_service_registry_config()
    detector = ServiceDetector(config.services)

    assert _service_ids(detector.detect("Как настроить n8n локально?")) == {"n8n"}
    assert _service_ids(detector.detect("Ошибка в н8н при запуске")) == {"n8n"}
    assert _service_ids(detector.detect("Что такое нейтн?")) == {"n8n"}


def test_service_registry_detector_matches_supabase_aliases() -> None:
    config = load_service_registry_config()
    detector = ServiceDetector(config.services)

    assert _service_ids(detector.detect("Supabase project keys")) == {"supabase"}
    assert _service_ids(detector.detect("Как открыть супабейс?")) == {"supabase"}
    assert _service_ids(detector.detect("Ошибка в супабейз auth")) == {"supabase"}


def test_service_registry_detector_returns_best_alias_once_per_service() -> None:
    config = load_service_registry_config()
    detector = ServiceDetector(config.services)

    mentions = detector.detect("supabase и супабейс в одном вопросе")

    assert len(mentions) == 1
    assert mentions[0].service_id == "supabase"
    assert mentions[0].confidence > 0


def _service_ids(mentions: tuple[object, ...]) -> set[str]:
    return {getattr(mention, "service_id") for mention in mentions}
