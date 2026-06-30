from app.bot.handlers import _format_debug_summary


def test_debug_documents_use_clean_labels() -> None:
    summary = _format_debug_summary(
        {
            "status": "AnswerStatus.ANSWERED",
            "sources": ["CLn02_text_double_deep"],
            "rag": {
                "selected_documents": [
                    {
                        "document_id": "doc-1",
                        "title": "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0444\u0430\u0439\u043b\u0430:",
                        "filename": "CLn02_text_double_deep.txt",
                        "score": 1.2,
                    }
                ]
            },
        }
    )

    assert "CLn02_text_double_deep score=1.2" in summary
    assert "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0444\u0430\u0439\u043b\u0430" not in summary
