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


def test_clean_answer_format_removes_orphan_headings_without_colon() -> None:
    raw = """HTTP Request node в n8n — это универсальный инструмент для вызова REST-API.
Как работает
Аутентификация – поддерживаются все типы.
Тело запроса – выбираете тип JSON.
Ключевые условия
Практический вывод
Чтобы выполнить запрос к внешнему сервису:
Добавьте HTTP Request node."""

    cleaned = clean_answer_format(raw)

    assert "Как работает" in cleaned
    assert "Аутентификация – поддерживаются все типы." in cleaned
    assert "Тело запроса – выбираете тип JSON." in cleaned
    assert "Ключевые условия" not in cleaned
    assert "\nПрактический вывод\n" not in f"\n{cleaned}\n"
    assert "Чтобы выполнить запрос к внешнему сервису:" in cleaned
    assert "Добавьте HTTP Request node." in cleaned


def test_clean_answer_format_keeps_normal_heading_with_content() -> None:
    raw = """Как работает
Аутентификация – поддерживаются все типы.
Тело запроса – выбираете тип JSON."""

    cleaned = clean_answer_format(raw)

    assert cleaned.splitlines()[0] == "Как работает"
    assert "Аутентификация – поддерживаются все типы." in cleaned
    assert "Тело запроса – выбираете тип JSON." in cleaned


def test_clean_answer_format_preserves_markdown_links() -> None:
    raw = """Подробности есть в [HTTP Request docs](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/).

Итог:
Используйте node для вызова внешнего API."""

    cleaned = clean_answer_format(raw)

    assert "[HTTP Request docs](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/)" in cleaned
    assert "Итог: Используйте node для вызова внешнего API." in cleaned


def test_clean_answer_format_preserves_code_blocks_with_commands_v2() -> None:
    raw = """Важно

```bash
npm install -g n8n
n8n start
```

Итог
Запустите команду из терминала."""

    cleaned = clean_answer_format(raw)

    assert "```bash\nnpm install -g n8n\nn8n start\n```" in cleaned
    assert "Итог: Запустите команду из терминала." in cleaned


def test_clean_answer_format_removes_evidence_artifact_line_and_keeps_sources() -> None:
    raw = """OpenRouter uses an Authorization header with a Bearer token.

Evidence: “The API requires an Authorization header.”

Sources:
- OpenRouter docs"""

    cleaned = clean_answer_format(raw)

    assert "OpenRouter uses an Authorization header" in cleaned
    assert "Evidence:" not in cleaned
    assert "The API requires an Authorization header" not in cleaned
    assert "Sources:" in cleaned
    assert "OpenRouter docs" in cleaned


def test_clean_answer_format_removes_multiple_evidence_fragments() -> None:
    raw = """Use the base URL from the official docs. Evidence: “The base URL is documented.”
- Evidence: “This bullet is a support quote.”
Send Authorization as Bearer token. Evidence: copied support sentence"""

    cleaned = clean_answer_format(raw)

    assert "Use the base URL from the official docs." in cleaned
    assert "Send Authorization as Bearer token." in cleaned
    assert "Evidence:" not in cleaned
    assert "support quote" not in cleaned
    assert "copied support sentence" not in cleaned


def test_clean_answer_format_preserves_evidence_text_inside_code_blocks() -> None:
    raw = """Keep this diagnostic example:

```text
Evidence: "this is literal code output"
| Parameter | Description |
| --- | --- |
| token | keep this table inside code |
```

Evidence: “remove this support quote”"""

    cleaned = clean_answer_format(raw)

    assert 'Evidence: "this is literal code output"' in cleaned
    assert "| token | keep this table inside code |" in cleaned
    assert "remove this support quote" not in cleaned


def test_clean_answer_format_rewrites_wide_api_parameter_table() -> None:
    raw = """Parameters:

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| chat_id | integer or string | yes | Unique identifier for the target chat or username of the target channel. |
| text | string | yes | Text of the message to send after entities are parsed. |"""

    cleaned = clean_answer_format(raw)

    assert "| Parameter | Type | Required | Description |" not in cleaned
    assert "- chat_id - Type: integer or string; Required: yes; Description: Unique identifier" in cleaned
    assert "- text - Type: string; Required: yes; Description: Text of the message" in cleaned


def test_clean_answer_format_keeps_short_markdown_table() -> None:
    raw = """Small table:

| Key | Value |
| --- | --- |
| mode | safe |"""

    cleaned = clean_answer_format(raw)

    assert "| Key | Value |" in cleaned
    assert "| mode | safe |" in cleaned


def test_clean_answer_format_keeps_normal_answer_unchanged() -> None:
    raw = "Use the official API base URL and pass the token in the Authorization header."

    cleaned = clean_answer_format(raw)

    assert cleaned == raw
