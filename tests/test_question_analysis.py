from app.rag.question_analysis import QuestionAnalyzer


def test_question_analysis_extracts_keywords() -> None:
    analysis = QuestionAnalyzer().analyze("Как подключить Supabase API в n8n?")

    assert analysis.intent == "question"
    assert "supabase" in analysis.keywords
    assert "api" in analysis.keywords
    assert "n8n" in analysis.keywords
