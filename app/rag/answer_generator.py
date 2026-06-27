"""Answer generation from evidence packs only."""

from app.rag.types import AnswerDraft, AnswerStatus, EvidencePack, QuestionAnalysis


class AnswerGenerator:
    """Generate answers using only the evidence pack."""

    async def generate(self, analysis: QuestionAnalysis, evidence: EvidencePack) -> AnswerDraft:
        """Generate an answer draft from evidence."""
        if evidence.is_empty:
            return AnswerDraft(
                text="В базе пока нет подтвержденных фрагментов для ответа на этот вопрос.",
                status=AnswerStatus.INSUFFICIENT_EVIDENCE,
            )

        del analysis
        raise NotImplementedError("Answer generation is not implemented yet")
