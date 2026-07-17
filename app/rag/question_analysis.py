"""Question analysis for document-first routing."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.external_docs.policy import freshness_required as external_docs_freshness_required
from app.rag.course_resolver import CourseHintResolver
from app.rag.query_enrichment import QueryEnricher
from app.rag.term_scoring import exact_terms as extract_exact_terms
from app.rag.term_scoring import guess_term_type
from app.rag.types import ContentType, QueryFacet, QueryPlan, QuestionAnalysis

_TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)
_QUOTED_RE = re.compile(r"[\"'`«»](.+?)[\"'`«»]", re.UNICODE)

_SOCIAL_TURN_TOKENS = {
    "hi",
    "hello",
    "hey",
    "ok",
    "okay",
    "thanks",
    "thank",
    "привет",
    "приветик",
    "здравствуйте",
    "здравствуй",
    "добрый",
    "доброе",
    "день",
    "утро",
    "вечер",
    "спасибо",
    "благодарю",
    "ок",
    "окей",
    "ясно",
}

_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "about",
    "into",
    "not",
    "как",
    "что",
    "где",
    "куда",
    "когда",
    "чем",
    "сколько",
    "если",
    "или",
    "не",
    "ни",
    "ли",
    "это",
    "этот",
    "эта",
    "эти",
    "такое",
    "его",
    "она",
    "оно",
    "мне",
    "в",
    "во",
    "на",
    "с",
    "со",
    "из",
    "за",
    "к",
    "ко",
    "по",
    "про",
    "от",
    "до",
    "у",
    "надо",
    "делать",
    "делаю",
    "сделать",
    "должен",
    "должна",
    "должно",
    "должны",
    "нужно",
    "нужен",
    "нужна",
    "нужны",
    "можно",
    "почему",
    "какой",
    "какая",
    "какие",
    "какого",
    "каком",
    "какую",
    "кто",
    "кого",
    "чей",
    "чья",
    "чье",
}

_GENERIC_TERMS = {
    "документ",
    "материал",
    "источник",
    "ответ",
    "вопрос",
    "шаг",
    "шаги",
    "пример",
    "основные",
    "правила",
    "частые",
    "ошибки",
    "ошибка",
    "делать",
    "действие",
    "уход",
    "общий",
    "обычный",
    "обычную",
    "предмет",
    "вещь",
    "это",
    "такое",
    "растение",
    "растения",
    "продукт",
    "продукты",
    "место",
    "воды",
    "вода",
}

_TASK_MARKERS = {
    "setup": (
        "install",
        "setup",
        "configure",
        "connect",
        "deploy",
        "run",
        "start",
        "установ",
        "настро",
        "подключ",
        "запуст",
        "разверн",
        "созда",
    ),
    "debug": (
        "error",
        "exception",
        "fail",
        "fails",
        "fix",
        "debug",
        "не работает",
        "ошиб",
        "слом",
        "исправ",
        "падает",
    ),
    "explain": (
        "explain",
        "overview",
        "why",
        "что такое",
        "объясни",
        "зачем",
        "почему",
        "как работает",
    ),
    "compare": (
        "compare",
        "versus",
        "vs",
        "difference",
        "сравн",
        "отлич",
        "лучше",
        "или",
    ),
    "admin": (
        "admin",
        "role",
        "policy",
        "permission",
        "access",
        "rls",
        "админ",
        "роль",
        "права",
        "доступ",
        "политик",
    ),
    "source_check": (
        "source",
        "citation",
        "reference",
        "источник",
        "цитат",
        "докаж",
        "где в материале",
        "по материал",
    ),
    "visual": (
        "screenshot",
        "image",
        "photo",
        "vision",
        "скрин",
        "изображ",
        "фото",
        "картин",
    ),
}

_ENVIRONMENT_MARKERS = (
    "local",
    "locally",
    "localhost",
    "docker",
    "compose",
    "windows",
    "linux",
    "macos",
    "server",
    "cloud",
    "port",
    "локаль",
    "локально",
    "сервер",
    "облако",
    "порт",
)

_CONSTRAINT_MARKERS = (
    "only",
    "without",
    "must",
    "только",
    "без",
    "обязательно",
    "нельзя",
    "нужен",
    "после",
    "перед",
)

_ACTION_MARKERS = (
    ("install", "установка"),
    ("setup", "настройка"),
    ("configure", "настройка"),
    ("connect", "подключение"),
    ("deploy", "развертывание"),
    ("run", "запуск"),
    ("start", "запуск"),
    ("установ", "установка"),
    ("настро", "настройка"),
    ("подключ", "подключение"),
    ("запуст", "запуск"),
    ("разверн", "развертывание"),
    ("полив", "полив"),
    ("поливат", "полив"),
    ("ухаж", "уход"),
    ("хран", "хранение"),
    ("готов", "готовка"),
    ("подготов", "подготовка"),
    ("убор", "уборка"),
    ("убрат", "уборка"),
    ("провер", "проверка"),
    ("упаков", "упаковка"),
    ("упак", "упаковка"),
    ("полож", "размещение"),
    ("взять", "сбор"),
    ("почин", "ремонт"),
    ("ремонт", "ремонт"),
    ("отлич", "сравнение"),
    ("сравн", "сравнение"),
    ("нельзя", "запрет"),
    ("должн", "ограничение"),
)

_CONTENT_TYPE_MARKERS: dict[ContentType, tuple[str, ...]] = {
    "homework_review_rules": (
        "check homework",
        "review homework",
        "homework review",
        "review rules",
        "criteria",
        "rubric",
        "проверить дз",
        "проверка дз",
        "критер",
        "рубри",
        "ошибки в дз",
    ),
    "homework_task": (
        "homework",
        "assignment",
        "deliverable",
        "дз",
        "домашн",
        "задани",
        "сдать",
        "что нужно сделать",
    ),
    "course_catalog": (
        "course list",
        "available courses",
        "courses are available",
        "what courses",
        "какие курсы",
        "список курсов",
        "есть курсы",
        "каталог курсов",
    ),
    "course_structure": (
        "course structure",
        "module",
        "lesson list",
        "структур",
        "модул",
        "урок",
        "программа курса",
    ),
    "course_terms": (
        "course terms",
        "deadline",
        "access",
        "услов",
        "доступ",
        "срок",
        "дедлайн",
        "тариф",
        "оплат",
    ),
    "student_case": (
        "student case",
        "student question",
        "case",
        "разбор студента",
        "вопрос студента",
        "кейс",
    ),
    "official_docs": (
        "official",
        "documentation",
        "docs",
        "официальн",
        "документац",
        "доки",
    ),
    "external_docs": (
        "external docs",
        "release notes",
        "changelog",
        "внешн",
        "изменилось в документации",
        "релиз",
        "changelog",
    ),
    "platform_navigation": (
        "where click",
        "which button",
        "screen",
        "куда наж",
        "где кноп",
        "экран",
        "интерфейс",
        "меню",
    ),
    "personal_data": (
        "my balance",
        "my account",
        "сколько у пользователя",
        "мой баланс",
        "мои данные",
        "у пользователя",
        "личн",
    ),
    "lesson_material": (
        "install",
        "setup",
        "configure",
        "explain",
        "lesson",
        "установ",
        "настро",
        "подключ",
        "объясн",
        "урок",
    ),
}

class QuestionAnalyzer:
    """Extract compact routing signals from a user question."""

    def __init__(self, query_enricher: QueryEnricher | None = None) -> None:
        self._query_enricher = query_enricher or QueryEnricher.default()

    def analyze(
        self,
        question: str,
        intake_sections: Sequence[str] | None = None,
        attachments: Sequence[object] | None = None,
    ) -> QuestionAnalysis:
        """Return deterministic initial analysis for the question."""
        return analyze_question(
            question,
            intake_sections=intake_sections,
            attachments=attachments,
            query_enricher=self._query_enricher,
        )


def analyze_question(
    question: str,
    intake_sections: Sequence[str] | None = None,
    attachments: Sequence[object] | None = None,
    query_enricher: QueryEnricher | None = None,
) -> QuestionAnalysis:
    """Analyze what the user is asking before routing to documents."""
    normalized = _normalize_question(question)
    combined = _combined_text(normalized, intake_sections)
    lowered = combined.lower()
    if _is_social_turn(normalized, lowered):
        query_plan = QueryPlan(
            user_question=normalized,
            normalized_question=normalized,
            question_type="general",
            expected_content_types=("unknown",),
            source_priority=("unknown",),
            evidence_requirements=(),
            source_required=False,
        )
        return QuestionAnalysis(
            original_question=normalized,
            raw_question=normalized,
            primary_intent="поприветствовать пользователя",
            task_type="general",
            source_required=False,
            answer_scope="general",
            intent="small_talk",
            query_plan=query_plan,
        )

    tokens = _extract_keywords(combined)
    task_type = _detect_task_type(lowered, attachments)
    course_hint = CourseHintResolver().resolve(normalized)
    requested_action = _requested_action(task_type, lowered)
    generic_terms = tuple(_generic_terms(tokens))
    exact_terms = tuple(extract_exact_terms(combined))
    enrichment_context = (query_enricher or QueryEnricher.default()).build_context(combined)
    exact_terms = tuple(_dedupe([*exact_terms, *enrichment_context.exact_terms], limit=16))
    config_terms = tuple(term for term in exact_terms if guess_term_type(term) in {"config", "identifier", "function", "path_or_parameter", "endpoint_or_address"})
    config_terms = tuple(_dedupe([*config_terms, *enrichment_context.config_terms], limit=16))
    object_terms = tuple(_object_terms(tokens, requested_action=requested_action, generic_terms=generic_terms))
    primary_object = object_terms[0] if object_terms else ""
    requested_attribute = _requested_attribute(lowered, object_terms)
    diagnostic = task_type == "debug"
    conceptual = task_type in {"explain", "compare", "general"} and _has_any(lowered, _TASK_MARKERS["explain"])
    needs_official_docs = task_type == "source_check" or _has_any(
        lowered,
        ("official", "docs", "documentation", "официальн", "документац"),
    )
    freshness_required = external_docs_freshness_required(lowered)
    needs_external_docs = (
        needs_official_docs
        or freshness_required
        or _has_any(lowered, _CONTENT_TYPE_MARKERS["external_docs"])
    )

    facets = _build_facets(
        normalized,
        lowered,
        tokens,
        task_type,
        attachments,
        requested_action=requested_action,
        object_terms=object_terms,
        generic_terms=generic_terms,
        exact_terms=exact_terms,
        config_terms=config_terms,
        extra_facets=enrichment_context.facets,
    )
    keywords = tuple(_dedupe([facet.text for facet in facets] + list(tokens), limit=16))
    constraints = tuple(facet.text for facet in facets if facet.role == "constraint")
    symptom_terms = tuple(facet.text for facet in facets if facet.role == "symptom")
    action_terms = tuple(facet.text for facet in facets if facet.role == "action")
    expected_content_types = tuple(_expected_content_types(lowered, task_type, attachments, needs_external_docs))
    source_priority = tuple(_source_priority(expected_content_types, needs_external_docs))
    evidence_questions = tuple(_evidence_questions(task_type, facets, expected_content_types=expected_content_types))
    primary_intent = _primary_intent(task_type, facets, normalized)
    must_answer_points = tuple(_must_answer_points(task_type, facets))
    missing_input = tuple(_missing_input_requirements(task_type, lowered, attachments))
    if "personal_data" in expected_content_types:
        missing_input = tuple(_dedupe([*missing_input, "authorized personal data source"], limit=8))
    answer_scope = "external_docs" if needs_external_docs else "knowledge_base"
    intent = "question" if normalized.endswith("?") or _looks_like_question(lowered) else "request"
    ambiguity = tuple(_ambiguity(normalized, object_terms, attachments))
    domain_hint, domain_confidence = _domain_hint(facets, tokens)
    rare_anchor_terms = tuple(facet.text for facet in facets if facet.role in {"rare_anchor", "exact", "config"})
    strongest_evidence_terms = tuple(facet.text for facet in facets if facet.role in {"object", "symptom", "exact", "config"})
    query_plan = QueryPlan(
        user_question=normalized,
        normalized_question=normalized,
        question_type=task_type,
        expected_content_types=expected_content_types,
        source_priority=source_priority,
        course_hint=course_hint.course,
        course_hint_confidence=course_hint.confidence,
        domain_hint=domain_hint,
        domain_hint_confidence=domain_confidence,
        action_terms=action_terms,
        object_terms=object_terms,
        symptom_terms=symptom_terms,
        constraint_terms=constraints,
        exact_terms=exact_terms,
        rare_anchor_terms=rare_anchor_terms,
        common_terms=generic_terms,
        evidence_requirements=evidence_questions,
        ambiguity=ambiguity,
        needs_external_docs=needs_external_docs,
        source_required=True,
        enrichment_context=enrichment_context,
    )

    return QuestionAnalysis(
        original_question=normalized,
        raw_question=normalized,
        user_question=normalized,
        normalized_question=normalized,
        question_type=task_type,
        primary_intent=primary_intent,
        task_type=task_type,
        source_required=True,
        diagnostic=diagnostic,
        conceptual=conceptual,
        needs_official_docs=needs_official_docs,
        needs_external_docs=needs_external_docs,
        expected_source_kinds=("external_docs",) if needs_external_docs else (),
        freshness_required=freshness_required,
        answer_scope=answer_scope,
        must_answer_points=must_answer_points,
        evidence_questions=evidence_questions,
        evidence_requirements=evidence_questions,
        missing_input_requirements=missing_input,
        query_facets=tuple(facets),
        intent=intent,
        keywords=keywords,
        constraints=constraints,
        constraint_terms=constraints,
        primary_object=primary_object,
        object_terms=object_terms,
        requested_action=requested_action,
        requested_attribute=requested_attribute,
        generic_terms=generic_terms,
        common_terms=generic_terms,
        platform_terms=tuple(facet.text for facet in facets if facet.role == "platform"),
        action_terms=action_terms,
        symptom_terms=symptom_terms,
        environment_terms=tuple(facet.text for facet in facets if facet.role == "environment"),
        config_terms=config_terms,
        exact_terms=exact_terms,
        rare_anchor_terms=rare_anchor_terms,
        ignored_weak_terms=generic_terms,
        strongest_evidence_terms=strongest_evidence_terms,
        expected_content_types=expected_content_types,
        source_priority=source_priority,
        course_hint=course_hint.course,
        course_hint_confidence=course_hint.confidence,
        domain_hint=domain_hint,
        domain_hint_confidence=domain_confidence,
        ambiguity=ambiguity,
        enrichment_context=enrichment_context,
        query_plan=query_plan,
    )


def _normalize_question(question: str) -> str:
    normalized = re.sub(r"\s+", " ", question.strip())
    return (
        normalized.replace("н8н", "n8n")
        .replace("Н8Н", "n8n")
        .replace("нейтн", "n8n")
        .replace("N8N", "n8n")
    )


def _is_social_turn(normalized: str, lowered: str) -> bool:
    if not normalized:
        return False
    clean = lowered.strip(" \t\r\n.,!?:;-()[]{}\"'`«»")
    if not clean:
        return False
    tokens = [_normalize_token(token) for token in _TOKEN_RE.findall(clean)]
    if not tokens or len(tokens) > 3:
        return False
    return all(token in _SOCIAL_TURN_TOKENS for token in tokens)


def _combined_text(question: str, intake_sections: Sequence[str] | None) -> str:
    if not intake_sections:
        return question
    return "\n".join([question, *[section for section in intake_sections if section]])


def _detect_task_type(lowered: str, attachments: Sequence[object] | None) -> str:
    if attachments and _has_any(lowered, _TASK_MARKERS["visual"]):
        return "visual"
    for task_type in ("source_check", "debug", "compare", "admin", "setup", "visual", "explain"):
        if _has_any(lowered, _TASK_MARKERS[task_type]):
            return task_type
    return "general"


def _build_facets(
    question: str,
    lowered: str,
    tokens: tuple[str, ...],
    task_type: str,
    attachments: Sequence[object] | None,
    *,
    requested_action: str,
    object_terms: tuple[str, ...],
    generic_terms: tuple[str, ...],
    exact_terms: tuple[str, ...],
    config_terms: tuple[str, ...],
    extra_facets: tuple[QueryFacet, ...] = (),
) -> list[QueryFacet]:
    facets: list[QueryFacet] = []
    facets.extend(extra_facets)
    platform_terms = [token for token in tokens if _is_platform_like(token)]
    facets.extend(QueryFacet("platform", token, 1.0) for token in platform_terms)

    if requested_action:
        facets.append(QueryFacet("action", requested_action, 1.0))

    for term in exact_terms:
        facets.append(QueryFacet("exact", term, 1.0))
    for term in config_terms:
        facets.append(QueryFacet("config", term, 1.0))

    for token in tokens:
        if (
            token in _STOPWORDS
            or token in platform_terms
            or token in generic_terms
            or token in exact_terms
            or token in config_terms
            or _is_marker_token(token)
            or _is_action_token(token, requested_action)
        ):
            continue
        importance = 0.9 if token in object_terms else 0.55
        facets.append(QueryFacet("object", token, importance))

    for marker in _ENVIRONMENT_MARKERS:
        if marker in lowered:
            facets.append(QueryFacet("environment", _canonical_environment(marker), 0.9))

    for marker in _CONSTRAINT_MARKERS:
        if marker in lowered:
            facets.append(QueryFacet("constraint", marker, 0.7))

    if task_type == "debug":
        symptoms = [match.group(1).strip() for match in _QUOTED_RE.finditer(question)]
        if not symptoms and _has_any(lowered, _TASK_MARKERS["debug"]):
            symptoms = ["ошибка или неработающее поведение"]
        facets.extend(QueryFacet("symptom", symptom, 1.0) for symptom in symptoms if symptom)

    if task_type == "source_check" or _has_any(lowered, ("источник", "official", "docs", "документац")):
        facets.append(QueryFacet("source", "подтверждение источником", 1.0))

    if attachments:
        facets.append(QueryFacet("source", "вложение пользователя", 0.9))

    return _dedupe_facets(facets)


def _extract_keywords(text: str) -> tuple[str, ...]:
    tokens = [_normalize_token(token) for token in _TOKEN_RE.findall(text.lower())]
    clean = [token for token in tokens if token and token not in _STOPWORDS]
    return tuple(_dedupe(clean, limit=24))


def _normalize_token(token: str) -> str:
    token = token.strip(".,:;!?()[]{}").lower()
    if token in {"н8н", "нейтн"}:
        return "n8n"
    return token


def _is_platform_like(token: str) -> bool:
    # Product-like identifiers are detected by shape. Commonness is handled by
    # corpus statistics, not by a fixed vendor/platform dictionary.
    return any(char.isdigit() for char in token) and any(char.isalpha() for char in token)


def _is_marker_token(token: str) -> bool:
    return any(token.startswith(marker[:6]) for marker in _ENVIRONMENT_MARKERS + _CONSTRAINT_MARKERS)


def _requested_action(task_type: str, lowered: str) -> str:
    action = _action_text(task_type, lowered)
    if action:
        return action
    for marker, label in _ACTION_MARKERS:
        if marker in lowered:
            return label
    return ""


def _generic_terms(tokens: tuple[str, ...]) -> list[str]:
    generic_roots = _roots(tuple(_GENERIC_TERMS))
    return [token for token in tokens if token in _GENERIC_TERMS or _root(token) in generic_roots]


def _object_terms(
    tokens: tuple[str, ...],
    *,
    requested_action: str,
    generic_terms: tuple[str, ...],
) -> list[str]:
    generic = set(generic_terms)
    objects: list[str] = []
    for token in tokens:
        if token in _STOPWORDS or token in generic or _is_platform_like(token):
            continue
        if _is_marker_token(token) or _is_action_token(token, requested_action):
            continue
        objects.append(token)
    return _dedupe(objects, limit=8)


def _is_action_token(token: str, requested_action: str) -> bool:
    token_root = _root(token)
    if requested_action and token_root in _roots([requested_action]):
        return True
    return any(token.startswith(marker[: min(len(marker), 6)]) for marker, _ in _ACTION_MARKERS)


def _requested_attribute(lowered: str, object_terms: tuple[str, ...]) -> str:
    attribute_markers = (
        "свет",
        "температур",
        "порт",
        "ошиб",
        "цвет",
        "часто",
        "сколько",
        "почему",
        "где",
        "куда",
    )
    for marker in attribute_markers:
        if marker in lowered:
            return marker
    return object_terms[1] if len(object_terms) > 1 else ""


def _action_text(task_type: str, lowered: str) -> str:
    if task_type == "setup":
        if _has_any(lowered, ("установ", "install")):
            return "установка"
        if _has_any(lowered, ("подключ", "connect")):
            return "подключение"
        if _has_any(lowered, ("настро", "configure", "setup")):
            return "настройка"
        return "запуск или настройка"
    if task_type == "debug":
        return "диагностика ошибки"
    if task_type == "explain":
        return "объяснение"
    if task_type == "compare":
        return "сравнение"
    if task_type == "admin":
        return "администрирование"
    if task_type == "source_check":
        return "проверка источника"
    if task_type == "visual":
        return "анализ изображения"
    return ""


def _canonical_environment(marker: str) -> str:
    if marker in {"local", "locally", "локаль", "локально", "localhost"}:
        return "локально"
    if marker in {"port", "порт"}:
        return "порт"
    return marker


def _primary_intent(task_type: str, facets: list[QueryFacet], question: str) -> str:
    platform = _first_facet(facets, "platform")
    primary_object = _first_facet(facets, "object")
    action = _first_facet(facets, "action")
    environment = _first_facet(facets, "environment")
    if task_type == "setup" and action:
        if action == "установка" and environment == "локально":
            return f"объяснить локальную установку{f' {platform}' if platform else ''}"
        parts = ["объяснить"]
        if environment == "локально":
            parts.append("локальную")
        parts.append(action)
        if platform:
            parts.append(platform)
        return " ".join(parts)
    if task_type == "debug":
        return f"помочь диагностировать проблему{f' в {platform}' if platform else ''}"
    if task_type == "explain":
        target = platform or primary_object
        return f"объяснить{f' {target}' if target else ''}"
    if task_type == "compare":
        return "сравнить варианты и условия применения"
    if task_type == "admin":
        return "объяснить административные действия и ограничения"
    if task_type == "source_check":
        return "проверить утверждение по источникам"
    if task_type == "visual":
        return "разобрать визуальный материал пользователя"
    if action and primary_object:
        return f"{action} для {primary_object}"
    return question[:180] or "unknown"


def _must_answer_points(task_type: str, facets: list[QueryFacet]) -> list[str]:
    if task_type == "setup":
        return [
            "способ запуска",
            "команда или действие",
            "где открыть интерфейс",
            "как проверить запуск",
            "что важно учесть",
        ]
    if task_type == "debug":
        return [
            "что означает симптом",
            "вероятная причина",
            "как проверить гипотезу",
            "что сделать дальше",
            "какие данные нужны, если доказательств мало",
        ]
    if task_type == "compare":
        return ["критерии сравнения", "когда выбрать каждый вариант", "ограничения"]
    if task_type == "admin":
        return ["требуемые права", "шаги настройки", "риски или ограничения"]
    if task_type == "source_check":
        return ["какой источник подтверждает", "что именно подтверждено", "что не подтверждено"]
    if task_type == "visual":
        return ["что видно на изображении", "какой фрагмент важен", "какой следующий шаг"]
    if _first_facet(facets, "source"):
        return ["ответ по источникам", "ограничения источников"]
    return ["прямой ответ", "ключевые условия", "практический вывод"]


def _evidence_questions(
    task_type: str,
    facets: list[QueryFacet],
    *,
    expected_content_types: tuple[ContentType, ...] = (),
) -> list[str]:
    platform = _first_facet(facets, "platform") or "нужную платформу"
    action = _first_facet(facets, "action") or "нужное действие"
    environment = _first_facet(facets, "environment")
    object_terms = [facet.text for facet in facets if facet.role == "object"]
    action_for_question = _action_for_question(action)

    questions = [
        f"источник действительно отвечает про {action_for_question}?",
        f"источник относится к {platform}, а не просто упоминает это слово?",
    ]
    if expected_content_types and expected_content_types != ("unknown",):
        questions.append(
            "источник относится к ожидаемому типу материала: "
            + ", ".join(expected_content_types)
            + "?"
        )
    if object_terms:
        questions.append("источник отвечает именно про объект вопроса: " + ", ".join(object_terms[:4]) + "?")

    if task_type == "setup":
        questions.append(f"источник объясняет установку или запуск {platform}?")
        if environment == "локально":
            questions.append("источник содержит локальный запуск, localhost, порт, Docker, npx или аналогичные шаги?")
        questions.append("источник позволяет дать практические шаги и проверку результата?")
    elif task_type == "debug":
        questions.append("источник описывает такой симптом, ошибку или диагностический шаг?")
        questions.append("источник содержит проверяемую причину или способ исправления?")
    elif task_type == "compare":
        questions.append("источник содержит оба сравниваемых варианта или явные критерии?")
    elif task_type == "admin":
        questions.append("источник говорит о правах, ролях, настройках или ограничениях доступа?")
    elif task_type == "source_check":
        questions.append("источник прямо подтверждает или опровергает утверждение пользователя?")
    elif task_type == "visual":
        questions.append("источник помогает интерпретировать видимый экран, ошибку или настройку?")

    return _dedupe(questions, limit=8)


def _expected_content_types(
    lowered: str,
    task_type: str,
    attachments: Sequence[object] | None,
    needs_external_docs: bool,
) -> list[ContentType]:
    matches: list[ContentType] = []
    if attachments:
        matches.append("platform_navigation")
    for content_type, markers in _CONTENT_TYPE_MARKERS.items():
        if _has_any(lowered, markers):
            matches.append(content_type)
    if needs_external_docs:
        matches.extend(["official_docs", "external_docs"])
    if not matches:
        if task_type in {"setup", "explain", "compare"}:
            matches.append("lesson_material")
        elif task_type == "debug":
            matches.extend(["student_case", "lesson_material"])
        elif task_type == "admin":
            matches.extend(["course_terms", "course_structure"])
        elif task_type == "visual":
            matches.append("platform_navigation")
        else:
            matches.append("unknown")
    return _dedupe(matches, limit=4)


def _source_priority(
    expected_content_types: tuple[ContentType, ...],
    needs_external_docs: bool,
) -> list[ContentType]:
    priority = list(expected_content_types)
    if needs_external_docs:
        priority = ["official_docs", "external_docs", *priority]
    if "course_catalog" in priority and "course_structure" not in priority:
        priority.append("course_structure")
    if "homework_review_rules" in priority and "homework_task" not in priority:
        priority.append("homework_task")
    return _dedupe(priority, limit=5)


def _domain_hint(facets: list[QueryFacet], tokens: tuple[str, ...]) -> tuple[str, float]:
    for facet in facets:
        if facet.role in {"platform", "config", "exact"}:
            return facet.text, 0.72
    for token in tokens:
        if guess_term_type(token) != "term":
            return token, 0.62
    return "", 0.0


def _ambiguity(
    question: str,
    object_terms: tuple[str, ...],
    attachments: Sequence[object] | None,
) -> list[str]:
    lowered = question.lower()
    issues: list[str] = []
    if len(_TOKEN_RE.findall(lowered)) <= 3 and not object_terms:
        issues.append("too_short_without_object")
    if _has_any(lowered, ("это", "this", "that", "туда", "сюда")) and not attachments:
        issues.append("deictic_reference_without_context")
    if _has_any(lowered, ("скрин", "screenshot", "image", "фото")) and not attachments:
        issues.append("mentions_visual_context_without_attachment")
    return issues


def _action_for_question(action: str) -> str:
    mapping = {
        "установка": "установку",
        "настройка": "настройку",
        "подключение": "подключение",
        "запуск или настройка": "запуск или настройку",
    }
    return mapping.get(action, action)


def _missing_input_requirements(
    task_type: str,
    lowered: str,
    attachments: Sequence[object] | None,
) -> list[str]:
    requirements: list[str] = []
    if task_type == "debug" and not _QUOTED_RE.search(lowered):
        requirements.append("точный текст ошибки или симптом")
    if task_type == "visual" and not attachments:
        requirements.append("скриншот или изображение")
    return requirements


def _looks_like_question(lowered: str) -> bool:
    return lowered.startswith(("как ", "что ", "где ", "почему ", "зачем ", "какой ", "какие "))


def _has_any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


def _first_facet(facets: list[QueryFacet], role: str) -> str | None:
    for facet in facets:
        if facet.role == role:
            return facet.text
    return None


def _dedupe(items: Sequence[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = re.sub(r"\s+", " ", str(item)).strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _dedupe_facets(facets: Sequence[QueryFacet]) -> list[QueryFacet]:
    seen: set[tuple[str, str]] = set()
    result: list[QueryFacet] = []
    for facet in facets:
        key = (facet.role, facet.text.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(facet)
    return result


def _roots(tokens: Sequence[str]) -> set[str]:
    return {_root(token) for token in tokens if token}


def _root(token: str) -> str:
    clean = token.casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»")
    clean = _stem_ru(clean)
    if len(clean) >= 8:
        return clean[:7]
    if len(clean) >= 6:
        return clean[:5]
    return clean


def _stem_ru(token: str) -> str:
    if not re.search(r"[а-я]", token):
        return token
    endings = (
        "иями",
        "ями",
        "ами",
        "ого",
        "ему",
        "ыми",
        "ими",
        "его",
        "ая",
        "яя",
        "ое",
        "ее",
        "ые",
        "ие",
        "ый",
        "ий",
        "ой",
        "ом",
        "ем",
        "ах",
        "ях",
        "ов",
        "ев",
        "ам",
        "ям",
        "ою",
        "ею",
        "ей",
        "у",
        "ю",
        "а",
        "я",
        "ы",
        "и",
        "е",
        "ь",
    )
    for ending in endings:
        if len(token) > len(ending) + 3 and token.endswith(ending):
            return token[: -len(ending)]
    return token
