from app.rag.claim_verifier import ClaimVerifier
from app.rag.types import AnswerDraft, AnswerStatus, EvidencePack, EvidenceSpan, SourceRef


def test_claim_verifier_rejects_answer_without_evidence() -> None:
    draft = AnswerDraft(
        text="Supabase service role keys are safe in frontend code.",
        status=AnswerStatus.ANSWERED,
    )

    report = ClaimVerifier().verify(draft, EvidencePack())

    assert not report.is_supported
    assert report.unsupported_claims


def test_claim_verifier_rewrites_parenthetical_source_only_answer_from_evidence() -> None:
    draft = AnswerDraft(
        text="(Источник: «Workflow docs»).",
        status=AnswerStatus.ANSWERED,
    )
    evidence = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-definition",
                document_id="doc-workflows",
                document_title="Workflow docs",
                text="Workflows are automated processes made of connected steps. They define how data moves through a task.",
            ),
        ),
        source_matches=(
            SourceRef(
                document_id="doc-workflows",
                document_title="Workflow docs",
                evidence_id="ev-definition",
            ),
        ),
    )

    report = ClaimVerifier().verify(draft, evidence)

    assert report.verdict == "rewrite"
    assert report.source_leakage
    assert "Workflows are automated processes" in report.safe_answer
    assert "Источник" not in report.safe_answer
