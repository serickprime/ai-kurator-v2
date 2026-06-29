"""Answer generation from evidence packs only."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any, Protocol

from app.rag.types import AnswerDraft, AnswerStatus, EvidencePack, QuestionAnalysis

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
            text=_out_of_base_answer(),
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
            if text:
                return AnswerDraft(
                    text=text,
                    status=_status_for_mode(evidence_pack.answer_mode),
                    used_evidence_ids=_used_evidence_ids(evidence_pack),
                    answer_mode=evidence_pack.answer_mode,
                    model_input={"messages": messages, "generation": generation_debug},
                )
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
        return _out_of_base_answer()
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


def _answer_from_materials(analysis: QuestionAnalysis, evidence: EvidencePack) -> str:
    sentences = _evidence_sentences(evidence, limit=5)
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
    for sentence in _evidence_sentences(evidence, limit=4):
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


def _out_of_base_answer() -> str:
    return "В материалах не нашел подтвержденного фрагмента по этому вопросу."


def _general_answer(analysis: QuestionAnalysis) -> str:
    if not analysis.source_required and analysis.intent == "small_talk":
        return "Привет! Я на связи. Задайте вопрос по материалам или загрузите материал, и я помогу разобраться."
    task = analysis.primary_intent if analysis.primary_intent != "unknown" else analysis.original_question
    if analysis.task_type == "setup":
        return f"В общем виде задача такая: {task}. Нужны способ запуска, конкретные действия и проверка результата."
    if analysis.task_type == "debug":
        return "Для диагностики нужны точный текст ошибки, где она возникает, и что уже пробовали."
    return f"Коротко: {task}."


def _evidence_sentences(evidence: EvidencePack, limit: int = 6) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for item in evidence.items:
        for sentence in _split_sentences(item.text):
            clean = _clean_evidence_sentence(sentence)
            if not clean or _is_low_value_sentence(clean):
                continue
            candidates.append((_fallback_sentence_score(clean), clean))

    result: list[str] = []
    seen: set[str] = set()
    for _, sentence in sorted(candidates, key=lambda item: (-item[0], item[1])):
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
