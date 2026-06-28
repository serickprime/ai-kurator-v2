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
            model_input={"messages": messages},
        )

    if evidence_pack.answer_mode == "out_of_base":
        return AnswerDraft(
            text=_out_of_base_answer(),
            status=AnswerStatus.NEEDS_CLARIFICATION,
            answer_mode=evidence_pack.answer_mode,
            model_input={"messages": messages},
        )

    if llm_client is not None:
        try:
            text = (await _complete_text(llm_client, messages, dialog_context)).strip()
            if text:
                return AnswerDraft(
                    text=text,
                    status=_status_for_mode(evidence_pack.answer_mode),
                    used_evidence_ids=_used_evidence_ids(evidence_pack),
                    answer_mode=evidence_pack.answer_mode,
                    model_input={"messages": messages},
                )
        except Exception as exc:
            user_message = str(getattr(exc, "user_message", "")).strip()
            if user_message:
                return AnswerDraft(
                    text=user_message,
                    status=AnswerStatus.NEEDS_CLARIFICATION,
                    used_evidence_ids=_used_evidence_ids(evidence_pack),
                    answer_mode=evidence_pack.answer_mode,
                    model_input={"messages": messages},
                )

    return AnswerDraft(
        text=_fallback_answer(question_analysis, evidence_pack),
        status=_status_for_mode(evidence_pack.answer_mode),
        used_evidence_ids=_used_evidence_ids(evidence_pack),
        answer_mode=evidence_pack.answer_mode,
        model_input={"messages": messages},
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


def _answer_from_materials(analysis: QuestionAnalysis, evidence: EvidencePack) -> str:
    lines = ["Подтверждено в evidence pack:"]
    for sentence in _evidence_sentences(evidence):
        lines.append(f"- {sentence}")

    uncovered = _uncovered_points(analysis, evidence)
    if uncovered:
        lines.append("")
        lines.append("В evidence pack не нашел подтверждения для пунктов: " + ", ".join(uncovered) + ".")
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
    task = analysis.primary_intent if analysis.primary_intent != "unknown" else analysis.original_question
    if analysis.task_type == "setup":
        return f"В общем виде задача такая: {task}. Нужны способ запуска, конкретные действия и проверка результата."
    if analysis.task_type == "debug":
        return "Для диагностики нужны точный текст ошибки, где она возникает, и что уже пробовали."
    return f"Коротко: {task}."


def _evidence_sentences(evidence: EvidencePack, limit: int = 6) -> list[str]:
    sentences: list[str] = []
    for item in evidence.items:
        for sentence in _split_sentences(item.text):
            if len(sentence) < 8:
                continue
            sentences.append(sentence)
            if len(sentences) >= limit:
                return sentences
    return sentences


def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return [part.strip(" -") for part in parts if part.strip(" -")]


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
            clean[str(key)] = str(value)[:400]
        return clean
    if isinstance(dialog_context, (list, tuple)):
        return [str(item)[:400] for item in dialog_context[-6:]]
    return str(dialog_context)[:1200]
