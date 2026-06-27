"""Question analysis for document-first routing."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.rag.types import QueryFacet, QuestionAnalysis

_TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)
_QUOTED_RE = re.compile(r"[\"'`«»](.+?)[\"'`«»]", re.UNICODE)

_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "как",
    "что",
    "где",
    "куда",
    "если",
    "или",
    "это",
    "его",
    "она",
    "оно",
    "мне",
    "надо",
    "нужно",
    "можно",
    "почему",
    "какой",
    "какая",
    "какие",
}

_TECH_TERMS = {
    "api",
    "cli",
    "docker",
    "github",
    "http",
    "json",
    "localhost",
    "n8n",
    "postgres",
    "python",
    "supabase",
    "telegram",
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
)


class QuestionAnalyzer:
    """Extract compact routing signals from a user question."""

    def analyze(
        self,
        question: str,
        intake_sections: Sequence[str] | None = None,
        attachments: Sequence[object] | None = None,
    ) -> QuestionAnalysis:
        """Return deterministic initial analysis for the question."""
        return analyze_question(question, intake_sections=intake_sections, attachments=attachments)


def analyze_question(
    question: str,
    intake_sections: Sequence[str] | None = None,
    attachments: Sequence[object] | None = None,
) -> QuestionAnalysis:
    """Analyze what the user is asking before routing to documents."""
    normalized = _normalize_question(question)
    combined = _combined_text(normalized, intake_sections)
    lowered = combined.lower()
    tokens = _extract_keywords(combined)
    task_type = _detect_task_type(lowered, attachments)
    diagnostic = task_type == "debug"
    conceptual = task_type in {"explain", "compare", "general"} and _has_any(lowered, _TASK_MARKERS["explain"])
    needs_official_docs = task_type == "source_check" or _has_any(
        lowered,
        ("official", "docs", "documentation", "официальн", "документац"),
    )

    facets = _build_facets(normalized, lowered, tokens, task_type, attachments)
    keywords = tuple(_dedupe([facet.text for facet in facets] + list(tokens), limit=16))
    constraints = tuple(facet.text for facet in facets if facet.role == "constraint")
    primary_intent = _primary_intent(task_type, facets, normalized)
    must_answer_points = tuple(_must_answer_points(task_type, facets))
    evidence_questions = tuple(_evidence_questions(task_type, facets))
    missing_input = tuple(_missing_input_requirements(task_type, lowered, attachments))
    answer_scope = "official_docs" if needs_official_docs else "knowledge_base"
    intent = "question" if normalized.endswith("?") or _looks_like_question(lowered) else "request"

    return QuestionAnalysis(
        original_question=normalized,
        raw_question=normalized,
        primary_intent=primary_intent,
        task_type=task_type,
        source_required=True,
        diagnostic=diagnostic,
        conceptual=conceptual,
        needs_official_docs=needs_official_docs,
        answer_scope=answer_scope,
        must_answer_points=must_answer_points,
        evidence_questions=evidence_questions,
        missing_input_requirements=missing_input,
        query_facets=tuple(facets),
        intent=intent,
        keywords=keywords,
        constraints=constraints,
    )


def _normalize_question(question: str) -> str:
    normalized = re.sub(r"\s+", " ", question.strip())
    return (
        normalized.replace("н8н", "n8n")
        .replace("Н8Н", "n8n")
        .replace("нейтн", "n8n")
        .replace("N8N", "n8n")
    )


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
) -> list[QueryFacet]:
    facets: list[QueryFacet] = []
    platform_terms = [token for token in tokens if _is_platform_like(token)]
    facets.extend(QueryFacet("platform", token, 1.0) for token in platform_terms)

    action_text = _action_text(task_type, lowered)
    if action_text:
        facets.append(QueryFacet("action", action_text, 1.0))

    for token in tokens:
        if token in _STOPWORDS or token in platform_terms or _is_marker_token(token):
            continue
        role = "object"
        if token in {"api", "cli", "json", "webhook"}:
            role = "object"
        facets.append(QueryFacet(role, token, 0.65))

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
    if token in _TECH_TERMS:
        return True
    return any(char.isdigit() for char in token) and any(char.isalpha() for char in token)


def _is_marker_token(token: str) -> bool:
    return any(token.startswith(marker[:6]) for marker in _ENVIRONMENT_MARKERS + _CONSTRAINT_MARKERS)


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
        return f"объяснить{f' {platform}' if platform else ''}"
    if task_type == "compare":
        return "сравнить варианты и условия применения"
    if task_type == "admin":
        return "объяснить административные действия и ограничения"
    if task_type == "source_check":
        return "проверить утверждение по источникам"
    if task_type == "visual":
        return "разобрать визуальный материал пользователя"
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


def _evidence_questions(task_type: str, facets: list[QueryFacet]) -> list[str]:
    platform = _first_facet(facets, "platform") or "нужную платформу"
    action = _first_facet(facets, "action") or "нужное действие"
    environment = _first_facet(facets, "environment")
    action_for_question = _action_for_question(action)

    questions = [
        f"источник действительно отвечает про {action_for_question}?",
        f"источник относится к {platform}, а не просто упоминает это слово?",
    ]

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
