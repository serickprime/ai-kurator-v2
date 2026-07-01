from app.rag.answer_formatting import clean_answer_format


def test_clean_answer_format_removes_empty_numbered_items_and_orphan_refs() -> None:
    raw = """HTTP Request node в n8n — это универсальный инструмент для вызова REST-API.
Как работает:
1.
2.
Параметры тела – можно выбрать тип: JSON, Form-Data, Binary File и т.п.
(см.
раздел Form-Data).
3.
Аутентификация – поддерживаются Generic credentials.
(см.
раздел Generic credentials).
4.
раздел Import curl command).
5.
Внутри ноды доступны переменные $pageCount, $request, $response.
Ключевые условия:
Практический вывод:
HTTP Request node позволяет быстро интегрировать любые REST-API."""

    cleaned = clean_answer_format(raw)

    assert "\n1.\n" not in f"\n{cleaned}\n"
    assert "\n2.\n" not in f"\n{cleaned}\n"
    assert "\n3.\n" not in f"\n{cleaned}\n"
    assert "\n4.\n" not in f"\n{cleaned}\n"
    assert "\n5.\n" not in f"\n{cleaned}\n"
    assert "см.\n" not in cleaned
    assert "раздел Form-Data" not in cleaned
    assert "Ключевые условия:" not in cleaned
    assert "Практический вывод:\nHTTP Request" not in cleaned
    assert "HTTP Request node в n8n" in cleaned
    assert "Параметры тела" in cleaned
    assert "Generic credentials" in cleaned
    assert "$pageCount" in cleaned
    assert "Практический вывод: HTTP Request node позволяет" in cleaned


def test_clean_answer_format_preserves_sources_and_links() -> None:
    raw = """HTTP Request node вызывает REST API.

Источники:
- HTTP Request | Nodes | n8n Docs (https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/)"""

    cleaned = clean_answer_format(raw)

    assert "Источники:" in cleaned
    assert "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/" in cleaned


def test_clean_answer_format_does_not_touch_code_blocks() -> None:
    raw = """Команда:

```bash
1.
curl https://example.com
раздел keep-this
```

1.
Готово."""

    cleaned = clean_answer_format(raw)

    assert "```bash\n1.\ncurl https://example.com\nраздел keep-this\n```" in cleaned
    assert "\n1.\nГотово" not in cleaned
    assert "Готово." in cleaned
