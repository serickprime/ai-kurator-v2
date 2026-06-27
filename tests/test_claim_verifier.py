from app.rag.claim_verifier import ClaimVerifier
from app.rag.types import AnswerDraft, AnswerStatus, EvidencePack


def test_claim_verifier_rejects_answer_without_evidence() -> None:
    draft = AnswerDraft(
        text="Supabase service role keys are safe in frontend code.",
        status=AnswerStatus.ANSWERED,
    )

    report = ClaimVerifier().verify(draft, EvidencePack())

    assert not report.is_supported
    assert report.unsupported_claims
