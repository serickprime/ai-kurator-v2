from app.rag.evidence_pack import EvidencePackBuilder, build_sources
from app.rag.types import EvidenceSpan, QuestionAnalysis


def test_evidence_pack_sources_are_derived_from_items() -> None:
    spans = (
        EvidenceSpan(
            evidence_id="ev-1",
            document_id="doc-1",
            document_title="Lesson 1",
            text="Use a service role key only on the server.",
            locator="p. 3",
        ),
        EvidenceSpan(
            evidence_id="ev-2",
            document_id="doc-1",
            document_title="Lesson 1",
            text="Never expose service role keys to clients.",
            locator="p. 4",
        ),
    )

    pack = EvidencePackBuilder().build(spans)

    assert pack.source_document_ids == ("doc-1",)
    assert {source.document_id for source in pack.sources()} == {"doc-1"}


def test_official_docs_missing_exact_object_is_out_of_base() -> None:
    analysis = QuestionAnalysis(
        original_question="According to official docs, how does OAuth callback work?",
        needs_official_docs=True,
        needs_external_docs=True,
        expected_content_types=("official_docs", "external_docs"),
        expected_source_kinds=("external_docs",),
        object_terms=("OAuth", "callback"),
        must_answer_points=("direct answer", "important constraints"),
    )
    builder = EvidencePackBuilder()

    pack = builder.build(
        (
            EvidenceSpan(
                evidence_id="overview",
                document_id="official-overview",
                document_title="Authentication overview",
                text="This official documentation page explains authentication setup and broad redirect concepts.",
                score=0.9,
                metadata={
                    "source_kind": "external_docs",
                    "content_type": ["official_docs", "external_docs"],
                    "source_uri": "https://docs.example.com/auth",
                },
            ),
        ),
        analysis=analysis,
    )

    assert pack.answer_mode == "out_of_base"
    assert pack.items == ()
    assert build_sources(pack) == []
    assert builder.last_decisions[0].status == "discarded"
    assert "missing_external_docs_object_coverage" in builder.last_decisions[0].reasons


def test_official_docs_exact_object_present_can_answer_with_url_source() -> None:
    analysis = QuestionAnalysis(
        original_question="According to official docs, how does OAuth callback work?",
        needs_official_docs=True,
        needs_external_docs=True,
        expected_content_types=("official_docs", "external_docs"),
        expected_source_kinds=("external_docs",),
        object_terms=("OAuth", "callback"),
        must_answer_points=("direct answer", "important constraints"),
    )

    pack = EvidencePackBuilder().build(
        (
            EvidenceSpan(
                evidence_id="oauth-callback",
                document_id="oauth-doc",
                document_title="OAuth reference",
                locator="OAuth callback",
                text=(
                    "OAuth callback URLs receive the redirect after authentication and complete the OAuth flow. "
                    "The callback endpoint is the specific URL configured in the application settings."
                ),
                source_uri="https://docs.example.com/oauth/callback",
                score=0.9,
                metadata={
                    "source_kind": "external_docs",
                    "content_type": ["official_docs", "external_docs"],
                },
            ),
        ),
        analysis=analysis,
    )

    assert pack.answer_mode == "answer_from_materials"
    assert pack.items[0].evidence_id == "oauth-callback"
    assert build_sources(pack) == ["OAuth reference — OAuth callback (https://docs.example.com/oauth/callback)"]
