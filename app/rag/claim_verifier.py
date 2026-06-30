"""Claim verification against the evidence pack."""

from __future__ import annotations

import re

from app.rag.types import AnswerDraft, AnswerStatus, EvidencePack, VerificationReport

_TOKEN_RE = re.compile(r"[\w#+.-]{3,}", re.UNICODE)
_SOFTENED_MARKERS = (
    "не хватает",
    "не нашел",
    "нет подтверждения",
    "частичная информация",
    "нужно уточнить",
    "может потребоваться",
)


class ClaimVerifier:
    """Verify that answer claims are supported by the evidence pack."""

    def verify(self, draft: AnswerDraft, evidence: EvidencePack) -> VerificationReport:
        """Return a conservative verification report."""
        return verify_claims(draft, evidence)


def verify_claims(draft: AnswerDraft, evidence: EvidencePack) -> VerificationReport:
    """Verify answer claims and produce a safe answer when rewriting is needed."""
    if draft.status == AnswerStatus.ANSWERED and evidence.is_empty and draft.answer_mode != "general_answer_without_sources":
        return VerificationReport(
            is_supported=False,
            unsupported_claims=("Answered without evidence.",),
            verdict="fail",
            safe_answer="Нужно уточнить: подтвержденного фрагмента из материалов по этому вопросу.",
        )

    if draft.answer_mode in {"ask_for_missing_data", "general_answer_without_sources", "out_of_base"}:
        leakage = _has_source_leakage(draft.text, evidence)
        return VerificationReport(
            is_supported=not leakage,
            unsupported_claims=(),
            verdict="fail" if leakage else "pass",
            safe_answer="" if not leakage else _remove_source_lines(draft.text),
            source_leakage=leakage,
        )

    claims = _extract_claims(draft.text)
    unsupported = tuple(claim for claim in claims if not _is_supported(claim, evidence))
    leakage = _has_source_leakage(draft.text, evidence)
    if not unsupported and not leakage:
        return VerificationReport(
            is_supported=True,
            verdict="pass",
            safe_answer=draft.text,
            source_leakage=False,
        )

    safe_answer = _safe_answer(draft.text, unsupported, evidence)
    verdict = "rewrite" if safe_answer.strip() else "fail"
    if not safe_answer.strip():
        safe_answer = "В найденных фрагментах нет достаточно надежного подтверждения для ответа."

    return VerificationReport(
        is_supported=False,
        unsupported_claims=unsupported,
        verdict=verdict,
        safe_answer=safe_answer,
        source_leakage=leakage,
    )


def _extract_claims(answer: str) -> tuple[str, ...]:
    claims: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+|\n+-\s+", answer):
        clean = re.sub(r"\s+", " ", part).strip(" -")
        if not clean or _is_non_claim(clean):
            continue
        if len(_tokens(clean)) < 3:
            continue
        claims.append(clean)
    return tuple(claims)


def _is_non_claim(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SOFTENED_MARKERS) or lowered.endswith(":")


def _is_supported(claim: str, evidence: EvidencePack) -> bool:
    claim_tokens = _meaningful_roots(claim)
    if not claim_tokens:
        return True

    for item in evidence.items:
        evidence_tokens = _meaningful_roots(item.text)
        if not evidence_tokens:
            continue
        overlap = claim_tokens & evidence_tokens
        if len(overlap) >= min(3, len(claim_tokens)):
            return True
        if len(overlap) / max(len(claim_tokens), 1) >= 0.55:
            return True
    return False


def _safe_answer(answer: str, unsupported: tuple[str, ...], evidence: EvidencePack) -> str:
    if not unsupported:
        safe = _remove_source_lines(answer)
        if safe.strip():
            return safe
        return _supported_evidence_answer(evidence)

    unsupported_keys = {_normalize_sentence(claim) for claim in unsupported}
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+|\n", answer):
        clean = part.strip()
        if not clean:
            continue
        if _normalize_sentence(clean) in unsupported_keys:
            continue
        kept.append(clean)

    safe = "\n".join(kept).strip()
    if safe:
        return _remove_source_lines(safe)

    return _supported_evidence_answer(evidence)


def _has_source_leakage(answer: str, evidence: EvidencePack) -> bool:
    if evidence.answer_mode == "general_answer_without_sources":
        return bool(re.search(r"\b(источник|source|http://|https://)\b", answer.lower()))

    allowed_titles = {source.document_title.lower() for source in evidence.source_matches if source.document_title}
    allowed_uris = {source.source_uri for source in evidence.source_matches if source.source_uri}
    lowered = answer.lower()
    urls = set(re.findall(r"https?://\S+", answer))
    if urls - allowed_uris:
        return True
    if "источник:" in lowered or "sources:" in lowered:
        return True
    leaked_titles = [
        title
        for title in re.findall(r"\[(.+?)]", answer)
        if title.lower() not in allowed_titles
    ]
    return bool(leaked_titles)


def _remove_source_lines(answer: str) -> str:
    lines = [
        line
        for line in answer.splitlines()
        if not _looks_like_source_line(line)
    ]
    return "\n".join(lines).strip()


def _looks_like_source_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    normalized = stripped.lstrip(" ([{«\"'").lower()
    return normalized.startswith(("источник:", "источники:", "sources:", "source:"))


def _meaningful_roots(text: str) -> set[str]:
    stopwords = {
        "and",
        "the",
        "for",
        "with",
        "from",
        "это",
        "что",
        "как",
        "если",
        "или",
        "для",
        "при",
        "нужно",
        "можно",
        "есть",
    }
    roots: set[str] = set()
    for token in _tokens(text):
        if token in stopwords or token.isdigit():
            continue
        roots.add(token[:7] if len(token) > 7 else token)
    return roots


def _tokens(text: str) -> list[str]:
    return [token.lower().strip(".,:;!?()[]{}") for token in _TOKEN_RE.findall(text)]


def _normalize_sentence(sentence: str) -> str:
    return re.sub(r"\s+", " ", sentence).strip(" -.").lower()


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]


def _safe_evidence_sentences(text: str) -> list[str]:
    result: list[str] = []
    for sentence in _sentences(text):
        clean = _clean_sentence(sentence)
        if not clean or _is_low_value_sentence(clean):
            continue
        result.append(clean)
        if len(result) >= 3:
            break
    return result


def _supported_evidence_answer(evidence: EvidencePack) -> str:
    supported_sentences: list[str] = []
    for item in evidence.items:
        supported_sentences.extend(_safe_evidence_sentences(item.text))
        if len(supported_sentences) >= 3:
            break
    if not supported_sentences:
        return "В найденных фрагментах нет достаточно надежного подтверждения для ответа."
    return "В материалах указано:\n" + "\n".join(f"- {sentence}" for sentence in supported_sentences[:3]).strip()


def _clean_sentence(sentence: str) -> str:
    clean = re.sub(r"\s+", " ", sentence).strip(" -")
    clean = re.sub(r"^#+\s*", "", clean)
    if len(clean) > 220:
        clean = clean[:217].rstrip() + "..."
    return clean


def _is_low_value_sentence(sentence: str) -> bool:
    lowered = sentence.casefold()
    noisy_markers = (
        "страница ",
        "текст страницы",
        "визуальные элементы",
        "действия",
        "нравится",
        "подписаться",
        "http://",
        "https://",
    )
    return len(sentence) < 18 or any(marker in lowered for marker in noisy_markers)
