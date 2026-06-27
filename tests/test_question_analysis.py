from app.rag.question_analysis import QuestionAnalyzer


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
