from app.rag.question_analysis import QuestionAnalyzer
from app.rag.evidence_retriever import evidence_query_text


def test_question_analysis_extracts_keywords() -> None:
    analysis = QuestionAnalyzer().analyze("Как подключить Supabase API в n8n?")

    assert analysis.intent == "question"
    assert "supabase" in analysis.keywords
    assert "api" in analysis.keywords
    assert "n8n" in analysis.keywords


def test_question_analysis_builds_evidence_questions() -> None:
    analysis = QuestionAnalyzer().analyze("как установить н8н локально?")

    assert analysis.task_type == "setup"
    assert analysis.source_required
    assert analysis.primary_intent == "объяснить локальную установку n8n"
    assert "способ запуска" in analysis.must_answer_points
    assert "команда или действие" in analysis.must_answer_points
    assert any("локальный запуск" in question for question in analysis.evidence_questions)
    assert any(facet.role == "platform" and facet.text == "n8n" for facet in analysis.query_facets)
    assert any(facet.role == "environment" and facet.text == "локально" for facet in analysis.query_facets)


def test_question_analysis_extracts_object_first_signals() -> None:
    analysis = QuestionAnalyzer().analyze("что делать с пригоревшей сковородой после готовки?")

    assert analysis.primary_object == "пригоревшей"
    assert "сковородой" in analysis.object_terms
    assert analysis.requested_action == "готовка"
    assert "после" in analysis.constraints


def test_question_analysis_marks_greeting_as_no_source_required() -> None:
    analysis = QuestionAnalyzer().analyze("привет")

    assert analysis.task_type == "general"
    assert analysis.intent == "small_talk"
    assert not analysis.source_required
    assert analysis.answer_scope == "general"
    assert analysis.must_answer_points == ()
    assert analysis.evidence_questions == ()


def test_question_analysis_builds_query_plan_content_types() -> None:
    analysis = QuestionAnalyzer().analyze("Where can I find official docs for homework review rules?")

    assert analysis.query_plan is not None
    assert "homework_review_rules" in analysis.query_plan.expected_content_types
    assert "official_docs" in analysis.query_plan.source_priority
    assert analysis.query_plan.needs_external_docs


def test_question_analysis_detects_course_catalog_intent() -> None:
    analysis = QuestionAnalyzer().analyze("What courses are available?")

    assert analysis.query_plan is not None
    assert "course_catalog" in analysis.query_plan.expected_content_types


def test_telegram_bot_api_send_message_query_is_enriched() -> None:
    question = "как отправить сообщение через Telegram Bot API?"
    analysis = QuestionAnalyzer().analyze(question)
    query_text = evidence_query_text(analysis)

    assert analysis.original_question == question
    assert "sendMessage" in analysis.exact_terms
    assert "chat_id" in analysis.config_terms
    assert "text" in analysis.config_terms
    assert any(facet.role == "exact" and facet.text == "sendMessage" for facet in analysis.query_facets)
    assert "sendMessage" in query_text
    assert "chat_id" in query_text
    assert "text" in query_text


def test_telegram_bot_api_existing_sendmessage_query_keeps_anchors() -> None:
    analysis = QuestionAnalyzer().analyze("как использовать sendMessage в Telegram Bot API? chat_id text")
    query_text = evidence_query_text(analysis)

    assert analysis.exact_terms.count("sendMessage") == 1
    assert "chat_id" in analysis.config_terms
    assert "text" in analysis.config_terms
    assert query_text.count("sendMessage") >= 1
    assert "chat_id" in query_text
    assert "text" in query_text


def test_telegram_bot_api_send_message_enrichment_is_service_scoped() -> None:
    analysis = QuestionAnalyzer().analyze("как отправить сообщение через n8n?")
    query_text = evidence_query_text(analysis)

    assert "sendMessage" not in query_text
    assert "chat_id" not in query_text
