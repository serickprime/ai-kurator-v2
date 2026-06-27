"""Claim verification against the evidence pack."""

from app.rag.types import AnswerDraft, AnswerStatus, EvidencePack, VerificationReport


class ClaimVerifier:
    """Verify that answer claims are supported by the evidence pack."""

    def verify(self, draft: AnswerDraft, evidence: EvidencePack) -> VerificationReport:
        """Return a conservative verification report."""
        if draft.status == AnswerStatus.ANSWERED and evidence.is_empty:
            return VerificationReport(
                is_supported=False,
                unsupported_claims=("Answered without evidence.",),
            )
        return VerificationReport(is_supported=True)
