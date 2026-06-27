"""Question analysis for document-first routing."""

import re

from app.rag.types import QuestionAnalysis

_TOKEN_RE = re.compile(r"[\w#+.-]{3,}", re.UNICODE)


class QuestionAnalyzer:
    """Extract compact routing signals from a user question."""

    def analyze(self, question: str) -> QuestionAnalysis:
        """Return deterministic initial analysis for the question."""
        normalized = question.strip()
        keywords = tuple(dict.fromkeys(_TOKEN_RE.findall(normalized.lower())))[:12]
        intent = "question" if "?" in normalized else "request"
        return QuestionAnalysis(raw_question=normalized, intent=intent, keywords=keywords)
