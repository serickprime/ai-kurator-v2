"""Answer generation from evidence packs only."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any, Protocol

from app.rag.source_labels import SourceLabelBuilder
from app.rag.types import AnswerDraft, AnswerStatus, EvidencePack, QuestionAnalysis

TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)
SOURCE_MARKER_RE = re.compile(
    r"\b(?:source|sources)\b|"
    r"\u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a(?:\u0438|\u043e\u0432)?",
    re.IGNORECASE,
)
DEFINITION_MARKERS = (
    "what is",
    "what are",
    "what does",
    "explain",
    "overview",
    "\u0447\u0442\u043e \u0442\u0430\u043a\u043e\u0435",
    "\u0447\u0442\u043e \u0437\u043d\u0430\u0447\u0438\u0442",
    "\u043e\u0431\u044a\u044f\u0441\u043d\u0438",
)
DEFINITION_TARGET_STOPWORDS = {
    "according",
    "docs",
    "documentation",
    "external",
    "official",
    "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442",
    "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446\u0438\u0438",
    "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446\u0438\u044f",
    "\u043e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f",
    "\u043e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0439",
    "\u043e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u044b\u0435",
}

ANSWER_GENERATION_SYSTEM_PROMPT = """
Ты отвечаешь в evidence-first RAG pipeline.

Входные данные ограничены:
- QuestionAnalysis;
- EvidencePack;
- исходный вопрос пользователя;
- краткий контекст диалога, если это follow-up.

Правила:
- Используй только EvidencePack.
- Raw candidates, retrieval candidates и discarded candidates запрещены и не должны учитываться.
- Не добавляй строку "По материалам" вручную.
- Не выдумывай точные команды, SQL, API-параметры, настройки нод, версии, сроки курса или ссылки без evidence.
- Не добавляй источники вручную: sources строит приложение после проверки.
- Если evidence не хватает, коротко скажи, чего именно нет.
- Пиши кратко, как куратор: дружелюбно, делово, без декоративного markdown.
""".strip()


class AnswerLlm(Protocol):
    """Optional text generation adapter."""

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        """Return model text for chat messages."""


class AnswerGenerator:
    """Generate answers using only the evidence pack."""

    def __init__(self, llm_client: AnswerLlm | None = None) -> None:
        self._llm_client = llm_client

    async def generate(
        self,
        analysis: QuestionAnalysis,
        evidence: EvidencePack,
        dialog_context: object | None = None,
    ) -> AnswerDraft:
        """Generate an answer draft from evidence."""
        return await generate_answer(
            analysis,
            evidence,
            dialog_context=dialog_context,
            llm_client=self._llm_client,
        )


async def generate_answer(
    question_analysis: QuestionAnalysis,
    evidence_pack: EvidencePack,
    dialog_context: object | None = None,
    llm_client: AnswerLlm | None = None,
) -> AnswerDraft:
    """Generate a draft answer from QuestionAnalysis and EvidencePack only."""
    model_input = _model_input(question_analysis, evidence_pack, dialog_context)
    messages = _messages(model_input)

    if evidence_pack.answer_mode == "ask_for_missing_data":
        return AnswerDraft(
            text=_ask_for_missing_data(question_analysis, evidence_pack),
            status=AnswerStatus.NEEDS_CLARIFICATION,
            answer_mode=evidence_pack.answer_mode,
            model_input={"messages": messages, "generation": _generation_metadata(None)},
        )

    if evidence_pack.answer_mode == "out_of_base":
        return AnswerDraft(
            text=_out_of_base_answer(question_analysis),
            status=AnswerStatus.NEEDS_CLARIFICATION,
            answer_mode=evidence_pack.answer_mode,
            model_input={"messages": messages, "generation": _generation_metadata(None)},
        )

    if evidence_pack.answer_mode == "general_answer_without_sources" and not question_analysis.source_required:
        return AnswerDraft(
            text=_general_answer(question_analysis),
            status=AnswerStatus.ANSWERED,
            answer_mode=evidence_pack.answer_mode,
            model_input={"messages": messages, "generation": _generation_metadata(None)},
        )

    generation_debug: dict[str, object] = _generation_metadata(llm_client)
    if llm_client is not None:
        try:
            text = _clean_model_answer(await _complete_text(llm_client, messages, dialog_context)).strip()
            generation_debug = _generation_metadata(llm_client)
            weak_reason = _weak_model_answer_reason(text, evidence_pack)
            if text and not weak_reason:
                return AnswerDraft(
                    text=text,
                    status=_status_for_mode(evidence_pack.answer_mode),
                    used_evidence_ids=_used_evidence_ids(evidence_pack),
                    answer_mode=evidence_pack.answer_mode,
                    model_input={"messages": messages, "generation": generation_debug},
                )
            if weak_reason:
                generation_debug = generation_debug | {
                    "fallback_used": True,
                    "weak_llm_answer_reason": weak_reason,
                }
        except Exception as exc:
            generation_debug = _generation_metadata(llm_client, error=exc, fallback_used=True)

    return AnswerDraft(
        text=_fallback_answer(question_analysis, evidence_pack),
        status=_status_for_mode(evidence_pack.answer_mode),
        used_evidence_ids=_used_evidence_ids(evidence_pack),
        answer_mode=evidence_pack.answer_mode,
        model_input={"messages": messages, "generation": generation_debug | {"fallback_used": True}},
    )


def _messages(model_input: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": ANSWER_GENERATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(model_input, ensure_ascii=False, indent=2),
        },
    ]


async def _complete_text(
    llm_client: AnswerLlm,
    messages: list[dict[str, str]],
    dialog_context: object | None,
) -> str:
    dialog_aware = getattr(llm_client, "complete_text_for_dialog", None)
    if dialog_aware is not None:
        return await dialog_aware(messages, dialog_context)
    return await llm_client.complete_text(messages)


def _model_input(
    analysis: QuestionAnalysis,
    evidence: EvidencePack,
    dialog_context: object | None,
) -> dict[str, Any]:
    return {
        "user_question": analysis.original_question or analysis.raw_question,
        "question_analysis": {
            "primary_intent": analysis.primary_intent,
            "task_type": analysis.task_type,
            "answer_scope": analysis.answer_scope,
            "must_answer_points": list(analysis.must_answer_points),
            "evidence_questions": list(analysis.evidence_questions),
            "missing_input_requirements": list(analysis.missing_input_requirements),
            "query_facets": [asdict(facet) for facet in analysis.query_facets],
        },
        "dialog_context": _compact_dialog_context(dialog_context),
        "evidence_pack": {
            "answer_mode": evidence.answer_mode,
            "missing_requirements": list(evidence.missing_requirements),
            "evidence_items": [
                {
                    "evidence_id": item.evidence_id,
                    "document_id": item.document_id,
                    "document_title": item.document_title,
                    "locator": item.locator,
                    "text": item.text,
                }
                for item in evidence.items
            ],
            "source_matches": [
                {
                    "document_id": source.document_id,
                    "document_title": source.document_title,
                    "locator": source.locator,
                    "evidence_id": source.evidence_id,
                }
                for source in evidence.source_matches
            ],
        },
    }


def _fallback_answer(analysis: QuestionAnalysis, evidence: EvidencePack) -> str:
    if evidence.answer_mode == "general_answer_without_sources":
        return _general_answer(analysis)
    if evidence.answer_mode == "partial_answer":
        return _partial_answer(analysis, evidence)
    if evidence.answer_mode == "out_of_base":
        return _out_of_base_answer(analysis)
    if evidence.is_empty:
        return _ask_for_missing_data(analysis, evidence)
    return _answer_from_materials(analysis, evidence)


def _clean_model_answer(text: str) -> str:
    """Remove decorative markdown artifacts while preserving copyable commands."""
    clean = str(text or "").replace("**", "").replace("__", "")
    clean = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", clean)
    clean = re.sub(r"(?m)^\s{0,3}>\s?", "", clean)
    clean = re.sub(r"(?m)^1\.\s+(?=2\.)", "", clean)
    return clean.strip()


def _weak_model_answer_reason(text: str, evidence: EvidencePack) -> str:
    if _looks_like_source_only_answer(text, evidence):
        return "source_label_only"
    return ""


def _looks_like_source_only_answer(text: str, evidence: EvidencePack) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    clean = re.sub(r"\s+", " ", raw).strip()
    lowered = clean.casefold()
    sourceish = bool(SOURCE_MARKER_RE.search(clean) or "http://" in lowered or "https://" in lowered or "docs" in lowered)
    factual_body = _strip_source_references(raw, evidence)
    body_tokens = _meaningful_tokens(factual_body)
    overlap = _evidence_overlap_roots(factual_body, evidence)
    if not body_tokens and sourceish:
        return True
    if sourceish and len(body_tokens) <= 6 and len(overlap) < 2:
        return True
    if len(clean) <= 160 and sourceish and len(overlap) < 2:
        return True
    source_lines = [
        line.strip()
        for line in raw.splitlines()
        if SOURCE_MARKER_RE.search(line) or line.strip().casefold().startswith(("- http", "- https"))
    ]
    non_empty_lines = [line for line in raw.splitlines() if line.strip()]
    return bool(source_lines and len(source_lines) == len(non_empty_lines))


def _strip_source_references(text: str, evidence: EvidencePack) -> str:
    source_labels = SourceLabelBuilder().build_many(evidence.source_matches, max_per_document=10, max_labels=20)
    source_bits = [
        *source_labels,
        *(source.document_title for source in evidence.source_matches if source.document_title),
        *(source.locator or "" for source in evidence.source_matches),
        *(source.source_uri or "" for source in evidence.source_matches),
    ]
    source_bits = [bit for bit in source_bits if str(bit).strip()]
    lines: list[str] = []
    in_source_block = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if SOURCE_MARKER_RE.search(stripped) and len(_meaningful_tokens(stripped)) <= 8:
            in_source_block = True
            continue
        if in_source_block and stripped.startswith(("-", "*")):
            continue
        in_source_block = False
        lines.append(stripped)
    body = "\n".join(lines)
    body = re.sub(r"https?://\S+", " ", body)
    for bit in sorted(source_bits, key=len, reverse=True):
        body = re.sub(re.escape(str(bit)), " ", body, flags=re.IGNORECASE)
    body = SOURCE_MARKER_RE.sub(" ", body)
    body = re.sub(r"[()\[\]|:;,\-]+", " ", body)
    return re.sub(r"\s+", " ", body).strip()


def _answer_from_materials(analysis: QuestionAnalysis, evidence: EvidencePack) -> str:
    sentences = _evidence_sentences(evidence, limit=5, analysis=analysis)
    if not sentences:
        return _ask_for_missing_data(analysis, evidence)

    lines = ["В материалах указано:"]
    for sentence in sentences:
        lines.append(f"- {sentence}")

    uncovered = _uncovered_points(analysis, evidence)
    if uncovered:
        lines.append("")
        lines.append("В найденных фрагментах нет подтверждения для: " + ", ".join(uncovered) + ".")
    return "\n".join(lines).strip()


def _partial_answer(analysis: QuestionAnalysis, evidence: EvidencePack) -> str:
    lines = ["В материалах есть только частичная информация."]
    for sentence in _evidence_sentences(evidence, limit=4, analysis=analysis):
        lines.append(f"- {sentence}")

    missing = list(evidence.missing_requirements) or _uncovered_points(analysis, evidence)
    if missing:
        lines.append("")
        lines.append("Чтобы ответить точнее, не хватает: " + ", ".join(missing) + ".")
    return "\n".join(lines).strip()


def _ask_for_missing_data(analysis: QuestionAnalysis, evidence: EvidencePack) -> str:
    missing = list(evidence.missing_requirements) or list(analysis.missing_input_requirements)
    if not missing:
        missing = ["подтвержденного фрагмента из материалов по этому вопросу"]
    return "Нужно уточнить: " + ", ".join(missing) + "."


def _out_of_base_answer(analysis: QuestionAnalysis | None = None) -> str:
    if analysis is not None and _expects_indexed_external_docs(analysis):
        target = _missing_target_label(analysis)
        if target:
            return f"В проиндексированной официальной документации пока не нашел подтвержденного фрагмента про {target}."
        return "В проиндексированной официальной документации пока не нашел подтвержденного фрагмента по этому вопросу."
    return "В материалах не нашел подтвержденного фрагмента по этому вопросу."


def _expects_indexed_external_docs(analysis: QuestionAnalysis) -> bool:
    expected = {
        re.sub(r"[\s-]+", "_", str(value or "").strip().casefold())
        for value in (
            *analysis.expected_content_types,
            *analysis.source_priority,
            *analysis.expected_source_kinds,
        )
    }
    return bool(analysis.needs_official_docs or analysis.needs_external_docs or expected & {"official_docs", "external_docs"})


def _missing_target_label(analysis: QuestionAnalysis) -> str:
    blocked = {
        "according",
        "docs",
        "documentation",
        "external",
        "official",
        "work",
        "works",
        "документации",
        "документация",
        "официальная",
        "официальной",
        "работает",
    }
    platform_terms = {item.casefold() for item in analysis.platform_terms}
    terms: list[str] = []
    for term in (*analysis.config_terms, *analysis.object_terms):
        clean = str(term or "").strip()
        if not clean:
            continue
        if clean.casefold() in blocked or clean.casefold() in platform_terms:
            continue
        terms.append(clean)
    return " ".join(dict.fromkeys(terms[:4]))


def _general_answer(analysis: QuestionAnalysis) -> str:
    if not analysis.source_required and analysis.intent == "small_talk":
        return "Привет! Я на связи. Задайте вопрос по материалам или загрузите материал, и я помогу разобраться."
    task = analysis.primary_intent if analysis.primary_intent != "unknown" else analysis.original_question
    if analysis.task_type == "setup":
        return f"В общем виде задача такая: {task}. Нужны способ запуска, конкретные действия и проверка результата."
    if analysis.task_type == "debug":
        return "Для диагностики нужны точный текст ошибки, где она возникает, и что уже пробовали."
    return f"Коротко: {task}."


def _evidence_sentences(
    evidence: EvidencePack,
    limit: int = 6,
    analysis: QuestionAnalysis | None = None,
) -> list[str]:
    candidates: list[tuple[float, int, str]] = []
    for evidence_index, item in enumerate(evidence.items):
        for sentence_index, sentence in enumerate(_split_sentences(item.text)):
            clean = _clean_evidence_sentence(sentence)
            if not clean or _is_low_value_sentence(clean):
                continue
            score = _fallback_sentence_score(clean)
            score += _definition_sentence_bonus(clean, item.metadata, analysis, sentence_index)
            score += max(0.0, 0.12 - evidence_index * 0.02)
            candidates.append((score, evidence_index, clean))

    result: list[str] = []
    seen: set[str] = set()
    for _, _, sentence in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        key = _sentence_key(sentence)
        if key in seen:
            continue
        seen.add(key)
        result.append(sentence)
        if len(result) >= limit:
            break
    return result


def _split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts.extend(re.split(r"(?<=[.!?])\s+", line))
    return [part.strip(" -") for part in parts if part.strip(" -")]


def _clean_evidence_sentence(sentence: str) -> str:
    clean = re.sub(r"\s+", " ", sentence).strip(" -")
    clean = re.sub(r"^#+\s*", "", clean)
    clean = re.sub(r"^[0-9]+[.)]\s*", "", clean)
    if len(clean) > 240:
        clean = clean[:237].rstrip() + "..."
    return clean


def _is_low_value_sentence(sentence: str) -> bool:
    lowered = sentence.casefold()
    if len(sentence) < 18:
        return True
    if re.fullmatch(r"[=\-_\s]+", sentence):
        return True
    noisy_markers = (
        "страница ",
        "текст страницы",
        "визуальные элементы",
        "действия",
        "нравится",
        "подписаться",
        "отправить",
        "следующий урок",
        "предыдущий урок",
        "http://",
        "https://",
    )
    return any(marker in lowered for marker in noisy_markers)


def _fallback_sentence_score(sentence: str) -> float:
    lowered = sentence.casefold()
    score = 0.0
    if re.search(r"\b[0-9]+[.)]\s+|(^|\s)[-•]\s+", sentence):
        score += 0.25
    if re.search(r"`[^`]+`|```|[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+", sentence):
        score += 0.25
    if re.search(r"\b(?:python|pip|npm|npx|docker|git|curl|touch|mkdir|cd)\b", lowered):
        score += 0.25
    if any(word in lowered for word in ("нужно", "используйте", "проверь", "создай", "добавь", "настрой", "укажи")):
        score += 0.2
    if any(word in lowered for word in ("пример", "формат", "параметр", "команда", "файл", "условие", "результат")):
        score += 0.15
    return score


def _definition_sentence_bonus(
    sentence: str,
    metadata: dict[str, object],
    analysis: QuestionAnalysis | None,
    sentence_index: int,
) -> float:
    if analysis is None or not _is_definition_question(analysis):
        return 0.0
    target_roots = _definition_target_roots(analysis)
    if not target_roots:
        return 0.0
    roots = _roots(_tokens(sentence))
    if not (roots & target_roots):
        return 0.0
    bonus = 0.25
    if metadata.get("primary_definition_candidate"):
        bonus += 0.45
    if sentence_index <= 1:
        bonus += 0.16
    lowered = sentence.casefold()
    if re.search(r"\b(?:is|are|means|refers\s+to|lets|allows|provides|calls)\b", lowered):
        bonus += 0.22
    if any(lowered.startswith(term.casefold()) for term in _definition_target_terms(analysis)):
        bonus += 0.18
    return bonus


def _is_definition_question(analysis: QuestionAnalysis) -> bool:
    lowered = " ".join([analysis.original_question, analysis.primary_intent]).casefold()
    return bool(
        analysis.conceptual
        or analysis.task_type == "explain"
        or any(marker in lowered for marker in DEFINITION_MARKERS)
    )


def _definition_target_terms(analysis: QuestionAnalysis) -> tuple[str, ...]:
    blocked = {term.casefold() for term in DEFINITION_TARGET_STOPWORDS}
    platform = {term.casefold() for term in analysis.platform_terms}
    result: list[str] = []
    for term in (analysis.primary_object, *analysis.object_terms, *analysis.config_terms, *analysis.strongest_evidence_terms):
        clean = str(term or "").strip()
        if not clean or clean.casefold() in blocked or clean.casefold() in platform:
            continue
        result.append(clean)
    return tuple(dict.fromkeys(result))


def _definition_target_roots(analysis: QuestionAnalysis) -> set[str]:
    return _roots(_tokens(" ".join(_definition_target_terms(analysis))))


def _evidence_overlap_roots(text: str, evidence: EvidencePack) -> set[str]:
    answer_roots = _meaningful_roots(text)
    if not answer_roots:
        return set()
    evidence_roots = _meaningful_roots(" ".join(item.text for item in evidence.items))
    return answer_roots & evidence_roots


def _meaningful_tokens(text: str) -> list[str]:
    return [
        token
        for token in _tokens(text)
        if token not in {"docs", "source", "sources", "http", "https"} and len(token) >= 3
    ]


def _meaningful_roots(text: str) -> set[str]:
    return _roots(_meaningful_tokens(text))


def _tokens(text: str) -> list[str]:
    return [token.casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»") for token in TOKEN_RE.findall(text)]


def _roots(tokens: list[str] | tuple[str, ...]) -> set[str]:
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


def _sentence_key(sentence: str) -> str:
    words = re.findall(r"[\w#+.-]{3,}", sentence.casefold(), flags=re.UNICODE)
    return " ".join(words[:18])


def _uncovered_points(analysis: QuestionAnalysis, evidence: EvidencePack) -> list[str]:
    if not analysis.must_answer_points:
        return []
    evidence_text = " ".join(item.text.lower() for item in evidence.items)
    uncovered: list[str] = []
    for point in analysis.must_answer_points:
        terms = [term for term in re.findall(r"[\w#+.-]{4,}", point.lower(), re.UNICODE)]
        if terms and any(term[:5] in evidence_text for term in terms):
            continue
        uncovered.append(point)
    return uncovered


def _status_for_mode(answer_mode: str) -> AnswerStatus:
    if answer_mode in {"ask_for_missing_data", "out_of_base"}:
        return AnswerStatus.NEEDS_CLARIFICATION
    if answer_mode == "partial_answer":
        return AnswerStatus.INSUFFICIENT_EVIDENCE
    return AnswerStatus.ANSWERED


def _used_evidence_ids(evidence: EvidencePack) -> tuple[str, ...]:
    return tuple(item.evidence_id for item in evidence.items if item.text.strip())


def _compact_dialog_context(dialog_context: object | None) -> object | None:
    if dialog_context is None:
        return None
    if isinstance(dialog_context, str):
        return dialog_context[:1200]
    if isinstance(dialog_context, dict):
        blocked = ("candidate", "discarded", "raw", "retrieval")
        clean: dict[str, object] = {}
        for key, value in dialog_context.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in blocked):
                continue
            if isinstance(value, dict):
                clean[str(key)] = {
                    str(child_key): str(child_value)[:200]
                    for child_key, child_value in value.items()
                    if not any(marker in str(child_key).lower() for marker in blocked)
                }
            else:
                clean[str(key)] = str(value)[:400]
        return clean
    if isinstance(dialog_context, (list, tuple)):
        return [str(item)[:400] for item in dialog_context[-6:]]
    return str(dialog_context)[:1200]


def _generation_metadata(
    llm_client: AnswerLlm | None,
    *,
    error: Exception | None = None,
    fallback_used: bool = False,
) -> dict[str, object]:
    metadata = getattr(llm_client, "last_metadata", None) if llm_client is not None else None
    if metadata is None and error is not None:
        metadata = getattr(error, "metadata", None)
    result: dict[str, object] = {
        "llm_model_attempts": tuple(getattr(metadata, "attempted_models", ()) or ()),
        "llm_errors_sanitized": tuple(getattr(metadata, "provider_errors", ()) or ()),
        "final_model_used": getattr(metadata, "successful_model", None),
        "fallback_used": fallback_used or llm_client is None,
    }
    if error is not None and not result["llm_errors_sanitized"]:
        result["llm_errors_sanitized"] = (_safe_error(error),)
    return result


def _safe_error(error: Exception) -> str:
    text = re.sub(r"\s+", " ", str(error)).strip()
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", text)
    text = re.sub(r"bot[0-9]{6,}(?::|%3[Aa])[A-Za-z0-9_-]+", "bot<redacted>", text)
    text = re.sub(r"sb_secret_[A-Za-z0-9_-]+", "sb_secret_<redacted>", text)
    return text[:500]
