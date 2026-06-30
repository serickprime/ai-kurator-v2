from app.ingestion.text_normalizer import TextNormalizer, join_pdf_spans, title_from_text_or_filename
from pathlib import Path


def test_text_normalizer_repairs_glued_cyrillic_without_breaking_technical_tokens() -> None:
    text = (
        "\u0412\u043d\u0438\u043c\u0430\u0442\u0435\u043b\u044c\u043d\u043e"
        "\u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0439"
        "\u0432\u0441\u0451, \u0447\u0442\u043e \u044f \u043d\u0430\u043f\u0438\u0448\u0443.\n"
        "https://example.com/docs/CLAUDE.md\n"
        "Run `npx tool --config .env`."
    )

    normalized = TextNormalizer().normalize(text)

    assert "\u0412\u043d\u0438\u043c\u0430\u0442\u0435\u043b\u044c\u043d\u043e \u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0439 \u0432\u0441\u0451" in normalized
    assert "https://example.com/docs/CLAUDE.md" in normalized
    assert ".env" in normalized
    assert "npx tool" in normalized


def test_text_normalizer_preserves_code_blocks() -> None:
    text = '```json\n{"file": "CLAUDE.md", "ok": true}\n```\n\nSome prose.'

    normalized = TextNormalizer().normalize(text)

    assert '```json\n{"file": "CLAUDE.md", "ok": true}\n```' in normalized


def test_join_pdf_spans_does_not_glue_words() -> None:
    line = join_pdf_spans(
        [
            {"text": "\u0412\u043d\u0438\u043c\u0430\u0442\u0435\u043b\u044c\u043d\u043e", "bbox": [0, 0, 80, 10]},
            {"text": "\u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0439", "bbox": [88, 0, 150, 10]},
            {"text": "CLAUDE", "bbox": [156, 0, 210, 10]},
            {"text": ".md", "bbox": [211, 0, 230, 10]},
        ]
    )

    assert "\u0412\u043d\u0438\u043c\u0430\u0442\u0435\u043b\u044c\u043d\u043e \u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0439" in line
    assert "CLAUDE.md" in line


def test_title_from_text_skips_boilerplate_heading() -> None:
    title = title_from_text_or_filename(
        "# \u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0444\u0430\u0439\u043b\u0430:\n\nBody text.",
        Path("lesson-file.md"),
    )

    assert title == "lesson file"
