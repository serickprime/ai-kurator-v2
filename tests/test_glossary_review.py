from difflib import unified_diff
from pathlib import Path

import pytest

from app.rag.glossary_candidates import GlossaryCandidate, GlossaryCandidateReport
from app.rag.glossary_review import (
    GlossaryReviewError,
    GlossaryReviewFile,
    ReviewCandidate,
    apply_reviewed_candidates,
    build_apply_plan,
    dump_review_file,
    load_review_file,
    parse_review_file,
    review_file_from_report,
)
from app.rag.query_enrichment import QueryGlossaryConfig, QueryGlossaryRule, QueryGlossaryService, load_query_glossary_config


def test_export_review_creates_owner_review_required_file() -> None:
    review = review_file_from_report(_candidate_report(), generated_at="2026-07-03T00:00:00Z")
    text = dump_review_file(review)
    parsed = parse_review_file(text)

    assert "mode: 'owner-review-required'" in text
    assert "owner_decision: 'pending'" in text
    assert parsed.mode == "owner-review-required"
    assert parsed.candidates[0].owner_decision == "pending"
    assert parsed.candidates[0].current_status == "suggested"


def test_pending_and_rejected_candidates_are_not_applied() -> None:
    review = _review_with(
        _review_candidate("pending-1", owner_decision="pending", exact_terms=("sendMessage",)),
        _review_candidate("rejected-1", owner_decision="rejected", exact_terms=("setWebhook",)),
    )

    plan = build_apply_plan(review, _existing_glossary())

    assert not plan.items
    assert plan.pending_skipped == 1
    assert plan.rejected == 1


def test_approved_candidates_enter_apply_plan() -> None:
    review = _review_with(
        _review_candidate(
            "approved-1",
            owner_decision="approved",
            user_phrases=("send a photo",),
            exact_terms=("sendPhoto",),
            config_terms=("photo",),
        )
    )

    plan = build_apply_plan(review, _existing_glossary())

    assert plan.approved == 1
    assert len(plan.items) == 1
    assert plan.items[0].phrases == ("send a photo",)
    assert plan.items[0].exact_terms == ("sendPhoto",)
    assert plan.items[0].config_terms == ("photo",)


def test_edited_candidates_use_edited_terms() -> None:
    review = _review_with(
        _review_candidate(
            "edited-1",
            owner_decision="edited",
            exact_terms=("noisyOriginal",),
            config_terms=("bad_original",),
            edited_terms=("/chat/completions", "model"),
        )
    )

    plan = build_apply_plan(review, _existing_glossary())

    assert plan.edited == 1
    assert plan.items[0].exact_terms == ("/chat/completions",)
    assert plan.items[0].config_terms == ("model",)
    assert "noisyOriginal" not in plan.items[0].exact_terms
    assert "bad_original" not in plan.items[0].config_terms


def test_sensitive_review_is_skipped_without_allow_sensitive_apply() -> None:
    review = _review_with(
        _review_candidate(
            "sensitive-1",
            owner_decision="approved",
            current_status="sensitive-review",
            review_flags=("sensitive-review",),
            config_terms=("service_role",),
            allow_sensitive_apply=False,
        )
    )

    plan = build_apply_plan(review, _existing_glossary())

    assert not plan.items
    assert plan.sensitive_skipped == 1
    assert plan.warnings


def test_sensitive_review_applies_only_with_allow_sensitive_apply() -> None:
    review = _review_with(
        _review_candidate(
            "sensitive-1",
            owner_decision="approved",
            current_status="sensitive-review",
            review_flags=("sensitive-review",),
            config_terms=("service_role",),
            allow_sensitive_apply=True,
        )
    )

    plan = build_apply_plan(review, _existing_glossary())

    assert plan.sensitive_skipped == 0
    assert len(plan.items) == 1
    assert plan.items[0].config_terms == ("service_role",)


def test_duplicates_are_not_added_to_apply_plan() -> None:
    review = _review_with(
        _review_candidate(
            "approved-1",
            owner_decision="approved",
            exact_terms=("sendMessage", "sendPhoto"),
            config_terms=("chat_id", "caption"),
        )
    )

    plan = build_apply_plan(review, _existing_glossary())

    assert len(plan.items) == 1
    assert plan.items[0].exact_terms == ("sendPhoto",)
    assert plan.items[0].config_terms == ("caption",)
    assert plan.duplicate_terms_skipped == 2


def test_apply_reviewed_writes_output_file_without_changing_config_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "query_glossary.yaml"
    config_path.write_text(_existing_glossary_text(), encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")
    review = _review_with(
        _review_candidate("approved-1", owner_decision="approved", exact_terms=("sendPhoto",), config_terms=("caption",))
    )

    plan, written = apply_reviewed_candidates(
        review=review,
        config_path=config_path,
        output_path=Path("reports/query_glossary.reviewed.yaml"),
    )

    assert plan.has_changes
    assert written == (tmp_path / "reports/query_glossary.reviewed.yaml").resolve()
    assert written.exists()
    assert "sendPhoto" in written.read_text(encoding="utf-8")
    assert config_path.read_text(encoding="utf-8") == original
    loaded = load_query_glossary_config(written)
    assert any("sendPhoto" in rule.exact_terms for service in loaded.services for rule in service.rules)


def test_apply_reviewed_output_preserves_existing_glossary_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "query_glossary.yaml"
    original = _existing_glossary_with_header_and_unrelated_text()
    config_path.write_text(original, encoding="utf-8")
    review = _review_with(
        _review_candidate(
            "approved-1",
            owner_decision="approved",
            exact_terms=("sendPhoto",),
            config_terms=("caption",),
        ),
        _review_candidate(
            "edited-1",
            service_id="n8n",
            source_id="n8n_docs",
            owner_decision="edited",
            edited_terms=("Webhook Response node", "response_code"),
        ),
        _review_candidate("rejected-1", owner_decision="rejected", exact_terms=("setChatMenuButton",)),
        _review_candidate("pending-1", service_id="n8n", owner_decision="pending", exact_terms=("Execute Workflow node",)),
    )

    plan, written = apply_reviewed_candidates(
        review=review,
        config_path=config_path,
        output_path=Path("tmp/query_glossary.reviewed.yaml"),
    )

    assert plan.approved == 1
    assert plan.edited == 1
    assert plan.rejected == 1
    assert plan.pending_skipped == 1
    assert written is not None
    output = written.read_text(encoding="utf-8")
    assert config_path.read_text(encoding="utf-8") == original
    assert "# Keep this header comment." in output
    assert "    - telegram bot api\n" in output
    assert "        - webhook\n      exact_terms:\n        - Webhook node\n" in output
    assert "sendPhoto" in output
    assert "Webhook Response node" in output
    assert "setChatMenuButton" not in output
    assert "Execute Workflow node" not in output

    diff_lines = list(unified_diff(original.splitlines(), output.splitlines(), lineterm=""))
    removed = [line for line in diff_lines if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in diff_lines if line.startswith("+") and not line.startswith("+++")]
    assert removed == []
    assert set(added) <= {
        "    - phrases:",
        "        - send a message",
        "      exact_terms:",
        "        - sendPhoto",
        "      config_terms:",
        "        - caption",
        "        - Webhook Response node",
        "        - response_code",
    }

    loaded = load_query_glossary_config(written)
    assert any("sendPhoto" in rule.exact_terms for service in loaded.services for rule in service.rules)
    assert any("Webhook Response node" in rule.exact_terms for service in loaded.services for rule in service.rules)


def test_direct_write_requires_both_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "query_glossary.yaml"
    config_path.write_text(_existing_glossary_text(), encoding="utf-8")
    review = _review_with(
        _review_candidate("approved-1", owner_decision="approved", exact_terms=("sendPhoto",), config_terms=("caption",))
    )

    with pytest.raises(GlossaryReviewError):
        apply_reviewed_candidates(review=review, config_path=config_path, write_config=True)
    with pytest.raises(GlossaryReviewError):
        apply_reviewed_candidates(review=review, config_path=config_path, confirm_reviewed_apply=True)


def test_review_file_round_trip_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "review.yaml"
    path.write_text(dump_review_file(_review_with(_review_candidate("c1"))), encoding="utf-8")

    review = load_review_file(path)

    assert review.candidates[0].id == "c1"
    assert review.candidates[0].owner_decision == "pending"


def _candidate_report() -> GlossaryCandidateReport:
    return GlossaryCandidateReport(
        workspace="fake",
        candidates=(
            GlossaryCandidate(
                service_id="telegram_bot_api",
                source_id="telegram_bot_api_docs",
                topic="sendMessage",
                user_phrases=("send a message",),
                technical_terms=("sendMessage", "chat_id"),
                exact_terms=("sendMessage",),
                config_terms=("chat_id",),
                confidence=0.9,
                status="suggested",
            ),
        ),
    )


def _review_with(*candidates: ReviewCandidate):
    return GlossaryReviewFile(
        generated_at="2026-07-03T00:00:00Z",
        candidates=tuple(candidates),
    )


def _review_candidate(
    candidate_id: str,
    *,
    service_id: str = "telegram_bot_api",
    source_id: str = "telegram_bot_api_docs",
    topic: str = "sendMessage",
    user_phrases: tuple[str, ...] = ("send a message",),
    exact_terms: tuple[str, ...] = (),
    config_terms: tuple[str, ...] = (),
    current_status: str = "suggested",
    review_flags: tuple[str, ...] = (),
    owner_decision: str = "pending",
    edited_terms: tuple[str, ...] = (),
    allow_sensitive_apply: bool = False,
) -> ReviewCandidate:
    return ReviewCandidate(
        id=candidate_id,
        service_id=service_id,
        source_id=source_id,
        topic=topic,
        user_phrases=user_phrases,
        technical_terms=(*exact_terms, *config_terms),
        exact_terms=exact_terms,
        config_terms=config_terms,
        confidence=0.9,
        current_status=current_status,
        review_flags=review_flags,
        owner_decision=owner_decision,
        edited_terms=edited_terms,
        allow_sensitive_apply=allow_sensitive_apply,
    )


def _existing_glossary():
    return QueryGlossaryConfig(
        services=(
            QueryGlossaryService(
                service_id="telegram_bot_api",
                display_name="Telegram Bot API",
                aliases=("Telegram Bot API",),
                rules=(
                    QueryGlossaryRule(
                        phrases=("send a message",),
                        exact_terms=("sendMessage",),
                        config_terms=("chat_id",),
                    ),
                ),
            ),
        )
    )


def _existing_glossary_text() -> str:
    return """telegram_bot_api:
  display_name: Telegram Bot API
  aliases:
    - Telegram Bot API
  rules:
    - phrases:
        - send a message
      exact_terms:
        - sendMessage
      config_terms:
        - chat_id
"""


def _existing_glossary_with_header_and_unrelated_text() -> str:
    return """# Seed glossary for retrieval-only query enrichment.
# Keep this header comment.

telegram_bot_api:
  display_name: Telegram Bot API
  aliases:
    - Telegram Bot API
    - telegram bot api
  rules:
    - phrases:
        - send a message
      exact_terms:
        - sendMessage
      config_terms:
        - chat_id

n8n:
  display_name: n8n
  aliases:
    - n8n
  rules:
    - phrases:
        - webhook
      exact_terms:
        - Webhook node
      config_terms:
        - production URL
"""
