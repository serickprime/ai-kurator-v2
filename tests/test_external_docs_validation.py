import json
from datetime import datetime, timezone

from app.external_docs.chunk_quality import is_low_value_external_chunk
from app.external_docs.extractor import ExternalDocsExtractor
from app.external_docs.types import CrawledPage
from app.external_docs.validation import validate_external_docs


def test_external_docs_validation_raw_html_fails() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "<div class='content'>Raw HTML should not be indexed.</div>")],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1
    assert any("raw HTML" in failure for failure in result.failures)


def test_external_docs_validation_missing_url_fails() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1", metadata={"source_name": "future_docs"})],
        chunks=[_chunk("doc-1", "Clean evidence text with enough useful words for validation.")],
    )

    assert result.quality == "FAIL"
    assert result.metrics["missing_url_docs"] == 1
    assert result.metrics["source_labels_without_url"] == 1


def test_external_docs_validation_allows_html_inside_fenced_code() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "Render the component with this example.\n\n```\n<div>Loading...</div>\n```",
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0
    assert result.metrics["code_blocks_count"] == 1


def test_external_docs_validation_allows_useful_inline_html_examples() -> None:
    result = validate_external_docs(
        source_name="telegram_bot_api_docs",
        documents=[_doc("doc-1", source_name="telegram_bot_api_docs")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Use parse_mode HTML with sendMessage. MessageEntity supports examples like "
                    "<b>bold</b>, <a href=\"https://example.com\">link</a>, and "
                    "<span class=\"tg-spoiler\">spoiler</span>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_html_inside_inline_code() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "Use the inline code `<div class=\"example\">demo</div>` when explaining HTML examples.",
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_multibacktick_inline_code() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "Use ``<button onclick=\"run()\">unsafe if rendered</button>`` only as literal code text.",
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_isolated_structural_tag_mention() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "Place the script before the <body> tag when documenting page structure.")],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_balanced_semantic_formatting_examples() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Documented formatting examples include <b>bold</b>, <strong>strong</strong>, "
                    "<i>italic</i>, <em>emphasis</em>, <u>underline</u>, "
                    "<ins>inserted</ins>, <s>strike</s>, <del>deleted</del>, "
                    "<code>value</code>, and <pre>block</pre>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_anchor_and_media_examples_with_safe_attributes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Examples can include <a href=\"https://docs.example.com/guide\">guide</a>, "
                    "<figure><img src=\"https://cdn.example.com/image.png\" alt=\"Diagram\"/>"
                    "<figcaption><cite>Docs team</cite></figcaption></figure>, "
                    "and <time datetime=\"2026-07-13\">today</time>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_details_and_blockquote_boolean_attributes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Disclosure examples include <details open><summary>More</summary>Text</details> "
                    "and <blockquote expandable>Long quote</blockquote>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_generic_custom_elements_with_safe_attributes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Custom documented elements include <x-emoji item-id=\"123\"></x-emoji>, "
                    "<x-time unix=\"1234567890\"></x-time>, and "
                    "<rich-spoiler>hidden text</rich-spoiler>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_multiple_small_documented_markup_examples() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "The documented snippet supports <mark>marked</mark>, <small>small</small>, "
                    "<sub>subscript</sub>, <sup>superscript</sup>, "
                    "<q cite=\"https://docs.example.com/source\">short quote</q>, and "
                    "<span class=\"rich-spoiler\">spoiler</span>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_escaped_html_entities_and_comparisons() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "Escaped examples like &lt;b&gt;text&lt;/b&gt; and comparisons such as 2 < 3 > 1 remain prose.",
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_reproduces_documented_markup_pattern_classes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Formatting tags: <span class=\"rich-spoiler\">spoiler</span>, "
                    "<x-emoji emoji-id=\"123\"></x-emoji>, and <x-time unix=\"123\" format=\"short\"></x-time>. "
                    "Rich blocks: <aside>Pull quote<cite>The Author</cite></aside>. "
                    "Headings: <h1>Main</h1><p>Paragraph</p><pre>code</pre>. "
                    "Media: <figure><figcaption><cite>Credit</cite></figcaption></figure>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_placeholder_pseudo_tags_in_template_contexts() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Call https://api.example.test/bot<token>/METHOD and then download "
                    "https://cdn.example.test/file/bot<token>/<file_path>. "
                    "Use curl https://api.example.test/bot<YOURTOKEN>/setWebhook with "
                    "url=https://<YOURDOMAIN.EXAMPLE>/<WEBHOOKLOCATION> and "
                    "certificate=@<YOURCAROOTCERTIFICATE>.pem."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_openrouter_like_placeholders_without_source_specific_rules() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "The command accepts the <provider>/<service> reference. "
                    "A generated API key may be labeled Service MCP: <app name>. "
                    "Open the dashboard at https://docs.example.test/workspaces/<slug>/settings. "
                    "The cache header X-Example-Cache-TTL accepts <seconds>. "
                    "Analytics are available at docs.example.test/apps?url=<your-app-url> "
                    "and referer=<your-referer-url>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_hash_template_placeholders() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Build the check string in key=<value> format, for example "
                    "auth_date=<auth_date>, query_id=<query_id>, and user=<user>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_placeholder_prose_substitution_context() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "We will use simply <secret> in this document."),
            _chunk("doc-1", "The identifier is shown as <resource_id> in this example."),
            _chunk("doc-1", "Replace the value with <access-key> before running the documented request example."),
            _chunk("doc-1", "Refer to this field as <item name> when describing the template configuration example."),
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_placeholder_prose_across_punctuation_and_line_breaks() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "For short examples, use simply\n<secret>\nin this document."),
            _chunk("doc-1", "The generated identifier is shown as:\n<resource-id>\nin this example."),
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_requires_placeholder_context_for_unknown_tags() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "Ordinary prose mentions <widget> in a normal sentence.")],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_keeps_context_free_placeholders_and_dangerous_tags_raw() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "<secret>"),
            _chunk("doc-1", "Unexpected markup: <unknown>."),
            _chunk("doc-1", "Use simply <div> in this document."),
            _chunk("doc-1", "Use simply <script> in this document."),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 4


def test_external_docs_validation_allows_rich_table_and_divider_examples() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Documented rich text examples include <hr/>, "
                    "<table bordered striped><caption>Metrics</caption><thead><tr>"
                    "<th scope=\"col\">Name</th><th scope=\"col\">Value</th></tr></thead>"
                    "<tbody><tr><td colspan=\"2\" valign=\"bottom\">Total</td></tr></tbody></table>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_table_alignment_attributes_for_cells() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Documented table example: <table><tr>"
                    '<td colspan="2" rowspan="2" align="left">Cell</td>'
                    '<th scope="col" align="center">Header</th>'
                    "</tr></table>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_documented_checkbox_examples() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Documented HTML example: <ul>"
                    '<li><input type="checkbox" checked>Selected option</li>'
                    '<li><input type="checkbox">Unselected option</li>'
                    "</ul> with explanatory markup text."
                ),
            ),
            _chunk(
                "doc-1",
                (
                    "Rich markup example:\n"
                    '<blockquote>Checklist</blockquote>\n'
                    '<input type="checkbox" checked> remains a literal documented control.'
                ),
            ),
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_flags_unsafe_checkbox_variants() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", '<input type="checkbox" checked>'),
            _chunk("doc-1", "Documented HTML example: <input checked>"),
            _chunk("doc-1", 'Documented HTML example: <input type="text" checked>'),
            _chunk("doc-1", 'Documented HTML example: <input type="radio" checked>'),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked="false">'),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked="checked">'),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked="">'),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked class="filter-toggle">'),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked style="display:block">'),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked onchange="run()">'),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked data-controller="filters">'),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 11


def test_external_docs_validation_flags_checkbox_form_page_and_mixed_residue() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                'Documented HTML example: <form action="/submit" method="post">'
                '<input type="checkbox" checked></form>',
            ),
            _chunk("doc-1", "Documented HTML example: <nav><input type=\"checkbox\" checked></nav>"),
            _chunk(
                "doc-1",
                'Documented HTML example: <div class="filter-panel">'
                '<input type="checkbox" checked></div>',
            ),
            _chunk(
                "doc-1",
                "Documented HTML example: <main><section>"
                '<input type="checkbox" checked><input type="text"></section></main>',
            ),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked><script>run()</script>'),
            _chunk(
                "doc-1",
                (
                    'Documented HTML example: <input type="checkbox" checked> '
                    '<figure class="dev_page_image"><img srcset="small.png 1x" src="/file/page.png"></figure>'
                ),
            ),
            _chunk(
                "doc-1",
                (
                    'Documented HTML example: <input type="checkbox" checked> '
                    '<a href="/file/page.png" target="_blank"><img class="dev_page_image" src="/file/page.png"/></a></div>'
                ),
            ),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked> </a></div>'),
            _chunk("doc-1", 'Documented HTML example: <input type="checkbox" checked> <div class="navigation">Docs</div>'),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 9


def test_external_docs_validation_checkbox_policy_preserves_existing_safe_examples() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "Custom documented elements include <x-emoji item-id=\"123\"></x-emoji>."),
            _chunk("doc-1", "Documented HTML example: <footer>Footer text</footer> with enough contextual words."),
            _chunk("doc-1", 'Documented table example: <table><tr><td align="left">Cell</td></tr></table>.'),
            _chunk("doc-1", "Use https://api.example.test/<provider>/<service> as the endpoint template."),
            _chunk("doc-1", "Place the script before the <body> tag when documenting page structure."),
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_flags_unsafe_table_alignment_attributes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", '<table><tr><td align="expression(run)">Cell</td></tr></table>'),
            _chunk("doc-1", '<table><tr><td align="left; color:red">Cell</td></tr></table>'),
            _chunk("doc-1", "<table><tr><td align=left>Cell</td></tr></table>"),
            _chunk("doc-1", '<div align="left">Layout</div>'),
            _chunk("doc-1", '<table class="layout"><tr><td align="left">Cell</td></tr></table>'),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 5


def test_external_docs_validation_allows_code_language_classes_only_for_code_examples() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Documented examples include <pre><code class=\"language-python\">print(1)</code></pre> "
                    "and <code class=\"language-c++\">std::cout</code>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_flags_unsafe_code_classes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", '<code class="navigation">bad</code>'),
            _chunk("doc-1", '<code class="language-python navigation">bad</code>'),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 2


def test_external_docs_validation_allows_split_documented_tag_lists() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Supported HTML tags include <u>/<ins> for inserted text and "
                    "<s>/<strike>/<del> for deleted text. "
                    "A preformatted block corresponds to the nested HTML tags <pre> and <code>. "
                    "A divider corresponds to the HTML tag <hr/>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_safe_trailing_semantic_closing_tag_in_documented_fragment() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "Rich disclosure markup example: <x-spoiler>hidden text</x-spoiler> </details>.",
            ),
            _chunk(
                "doc-1",
                "Supported tags include <blockquote>, <figure>, and <figcaption> </blockquote>.",
            ),
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_flags_stray_page_or_context_free_closing_tags() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "Plain prose ends with </details>."),
            _chunk("doc-1", "Documented example followed by </div>."),
            _chunk("doc-1", "Documented example followed by </a></div>."),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 3


def test_external_docs_validation_allows_small_footer_documented_example() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "Documented HTML example: <footer>Footer text</footer> with enough contextual words.",
            ),
            _chunk("doc-1", "Markup example uses <footer>Generated by service</footer> as a small semantic element."),
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_flags_page_footer_residue() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", '<footer class="site-footer">Links</footer>'),
            _chunk("doc-1", '<footer style="display:flex">Links</footer>'),
            _chunk("doc-1", "<footer><nav>Menu</nav></footer>"),
            _chunk("doc-1", "<main><section><div>Large page layout</div></section></main>"),
            _chunk("doc-1", "Documented HTML example: <footer>Footer text</footer><script>run()</script>"),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 5


def test_external_docs_validation_allows_safe_placeholder_plus_safe_documented_markup() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Request https://api.example.test/bot<token>/send and format output with "
                    "<b>bold</b>, <blockquote expandable>quote</blockquote>, and "
                    "<x-rich-block item-id=\"123\"></x-rich-block>."
                ),
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_allows_escaped_comparison_prose() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "Literal < and > characters are documented as < with &lt; , > with &gt; and & with &amp;.",
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.metrics["raw_html_count"] == 0


def test_external_docs_validation_flags_placeholder_mixed_with_screenshot_residue() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Use https://api.example.test/bot<token>/send. "
                    '<figure class="dev_page_image"><img srcset="small.png 1x" src="/file/example.png"></figure>'
                ),
            )
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_placeholder_mixed_with_navigation_residue() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                'Use /api/<token>/send plus <div class="navigation"><a href="/docs">Docs</a></div>.',
            )
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_safe_examples_mixed_with_real_residue() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    "Rich disclosure markup example: <x-spoiler>hidden</x-spoiler> </details> "
                    '<div class="navigation">Docs</div>'
                ),
            ),
            _chunk(
                "doc-1",
                (
                    "Documented HTML example: <footer>Footer text</footer> "
                    '<figure class="dev_page_image"><img srcset="small.png 1x" src="/file/page.png"></figure>'
                ),
            ),
            _chunk(
                "doc-1",
                (
                    'Table example: <table><tr><td align="left">Cell</td></tr></table> '
                    '<a href="/file/page.png" target="_blank"><img class="dev_page_image" src="/file/page.png"/></a></div>'
                ),
            ),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 3


def test_external_docs_validation_flags_placeholder_like_real_attributes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", '<token value="secret"></token>'),
            _chunk("doc-1", '<token onclick="run()">'),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 2


def test_external_docs_validation_local_fixture_health_smoke_for_safe_and_residue_chunks() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "Template URL: https://api.example.test/bot<token>/send", heading="Template"),
            _chunk("doc-1", "Documented rich table: <table><tr><td>Value</td></tr></table>", heading="Markup"),
            _chunk("doc-1", '<div class="navigation"><a href="/docs">Docs</a></div>', heading="Residue"),
            _chunk("doc-1", '<figure class="dev_page_image"><img srcset="a.png 1x"></figure>', heading="Image"),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 2


def test_external_docs_validation_does_not_warn_for_short_technical_chunks() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "###### Terminal\n\n```\nnpm run dev\n```", heading="Terminal")],
    )

    assert result.quality == "PASS"
    assert result.metrics["very_short_chunks"] == 0
    assert result.metrics["chunks_without_useful_text"] == 0


def test_external_docs_chunk_quality_is_source_agnostic() -> None:
    assert is_low_value_external_chunk("### AI Tools", heading="AI Tools")
    assert is_low_value_external_chunk("###### Project URL\n\nNo project found", heading="Project URL")
    assert not is_low_value_external_chunk("```\nnpm run dev\n```", heading="Terminal")
    assert not is_low_value_external_chunk("GET /rest/v1/items", heading="API")
    assert not is_low_value_external_chunk("PUBLIC_API_URL=https://docs.example.com", heading="Config")


def test_external_docs_validation_duplicate_active_versions_fail() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[
            _doc("doc-1", canonical_url="https://docs.example.com/same"),
            _doc("doc-2", canonical_url="https://docs.example.com/same"),
        ],
        chunks=[
            _chunk("doc-1", "Clean evidence text with enough useful words for validation."),
            _chunk("doc-2", "Another clean evidence paragraph with enough useful words."),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["duplicate_active_versions"] == 1


def test_external_docs_validation_title_only_chunks_warn() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "# Overview", heading="Overview"),
            _chunk("doc-1", "Useful reference text with enough words to avoid being empty."),
        ],
    )

    assert result.quality == "WARN"
    assert not result.failures
    assert result.metrics["title_only_chunks"] == 1
    assert any("title-only" in warning for warning in result.warnings)


def test_external_docs_validation_clean_source_passes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                "This page explains a product concept with enough useful text for grounded answers.",
            )
        ],
    )

    assert result.quality == "PASS"
    assert result.failures == ()
    assert result.warnings == ()


def test_cleaned_openrouter_fixture_passes_without_generator_warning() -> None:
    page = CrawledPage(
        source_name="openrouter_docs",
        url="https://openrouter.ai/docs/api-reference/overview",
        html="""
        <html><body><main>
          <h1>OpenRouter API</h1>
          <p>For the complete documentation index, see llms.txt</p>
          <p>This page is also available as Markdown.</p>
          <p>Use /completions, /chat/completions, and /api/v1/models.</p>
          <pre><code>curl https://openrouter.ai/api/v1/models</code></pre>
        </main></body></html>
        """,
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )
    extracted = ExternalDocsExtractor().extract(page)
    result = validate_external_docs(
        source_name="openrouter_docs",
        documents=[_doc("doc-1", source_name="openrouter_docs")],
        chunks=[_chunk("doc-1", extracted.structured_text)],
    )

    assert "llms.txt" not in extracted.structured_text
    assert "/chat/completions" in extracted.structured_text
    assert "/api/v1/models" in extracted.structured_text
    assert result.metrics["generator_boilerplate_count"] == 0
    assert "generator boilerplate found" not in result.warnings


def test_cleaned_telegram_fixture_passes_without_raw_html_or_nav_noise() -> None:
    page = CrawledPage(
        source_name="telegram_bot_api_docs",
        url="https://core.telegram.org/bots/api",
        html="""
        <html><body><main>
          <p>Skip to content</p>
          <p>Cookie settings Accept all cookies</p>
          <p>&lt;div class="footer"&gt;Navigation menu&lt;/div&gt;</p>
          <h1>sendMessage</h1>
          <p>The sendMessage method sends text messages. Parameters include chat_id and parse_mode.</p>
          <p>MessageEntity can describe &lt;b&gt;bold&lt;/b&gt; HTML parse mode examples.</p>
        </main></body></html>
        """,
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )
    extracted = ExternalDocsExtractor().extract(page)
    result = validate_external_docs(
        source_name="telegram_bot_api_docs",
        documents=[_doc("doc-1", source_name="telegram_bot_api_docs")],
        chunks=[_chunk("doc-1", extracted.structured_text)],
    )

    assert "Skip to content" not in extracted.structured_text
    assert "Cookie settings" not in extracted.structured_text
    assert "<div" not in extracted.structured_text
    assert "sendMessage" in extracted.structured_text
    assert "chat_id" in extracted.structured_text
    assert "parse_mode" in extracted.structured_text
    assert "MessageEntity" in extracted.structured_text
    assert result.metrics["raw_html_count"] == 0
    assert result.metrics["nav_footer_noise_count"] == 0


def test_external_docs_validation_flags_synthetic_telegram_template_and_navigation_noise() -> None:
    result = validate_external_docs(
        source_name="telegram_bot_api_docs",
        documents=[_doc("doc-1", source_name="telegram_bot_api_docs")],
        chunks=[
            _chunk(
                "doc-1",
                (
                    '<div id="dev_page_image" data-template="telegram">Back to the Bot API Manual</div>\n'
                    "Navigation menu\n"
                    "sendMessage accepts chat_id and text."
                ),
            )
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1
    assert result.metrics["nav_footer_noise_count"] == 1
    assert any("raw HTML" in failure for failure in result.failures)
    assert any("navigation/footer/cookie" in warning for warning in result.warnings)


def test_external_docs_validation_flags_navigation_wrapper_residue() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", '<div class="navigation"><a href="/docs">Docs</a></div>')],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_styled_layout_residue() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", '<section style="display:flex">Layout residue</section>')],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_data_and_framework_attributes() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", '<div data-controller="menu" aria-label="Navigation">Menu</div>')],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_event_handlers() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", '<img src="https://cdn.example.com/x.png" onerror="run()">')],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_unbalanced_markup() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "<div><span>broken")],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_script_and_style_fragments() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", "<script>run()</script>"),
            _chunk("doc-1", "<style>.page { display: flex; }</style>"),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 2


def test_external_docs_validation_flags_scraped_image_page_block() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                '<figure class="screenshot"><img srcset="small.png 1x, large.png 2x" src="large.png"/></figure>',
            )
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_mixed_useful_markup_and_residue() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk(
                "doc-1",
                '<b>documented example</b> plus <div class="navigation"><a href="/docs">Docs</a></div>',
            )
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_large_arbitrary_dom_subtree() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "<div><header>Top</header><main><section>Body</section></main></div>")],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_malformed_custom_element() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "<x-emoji item-id=123></x-emoji>")],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 1


def test_external_docs_validation_flags_custom_element_with_event_or_style_attrs() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[
            _chunk("doc-1", '<x-emoji item-id="123" onclick="run()"></x-emoji>'),
            _chunk("doc-1", '<rich-spoiler style="display:none">hidden</rich-spoiler>'),
        ],
    )

    assert result.quality == "FAIL"
    assert result.metrics["raw_html_count"] == 2


def test_external_docs_validation_json_output_is_valid() -> None:
    result = validate_external_docs(
        source_name="future_docs",
        documents=[_doc("doc-1")],
        chunks=[_chunk("doc-1", "Clean evidence text with enough useful words for validation.")],
    )

    encoded = json.dumps(result.to_dict())
    decoded = json.loads(encoded)

    assert decoded["source_name"] == "future_docs"
    assert decoded["quality"] == "PASS"


def test_external_docs_validation_thresholds_are_source_agnostic() -> None:
    result = validate_external_docs(
        source_name="another_vendor_docs",
        documents=[_doc("doc-1", source_name="another_vendor_docs")],
        chunks=[
            _chunk("doc-1", "# API", heading="API"),
            _chunk("doc-1", "Useful vendor-neutral documentation text with enough words."),
        ],
    )

    assert result.quality == "WARN"
    assert result.metrics["title_only_chunks_ratio"] == 0.5
    assert result.samples["title_only_chunks"]


def _doc(
    document_id: str,
    *,
    source_name: str = "future_docs",
    canonical_url: str = "https://docs.example.com/page",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    row_metadata = {
        "source_name": source_name,
        "source_url": canonical_url,
        "canonical_url": canonical_url,
    }
    if metadata is not None:
        row_metadata = metadata
    return {
        "id": document_id,
        "filename": f"{document_id}.html",
        "document_key": canonical_url,
        "title": f"Page {document_id}",
        "status": "active",
        "metadata": row_metadata,
    }


def _chunk(document_id: str, content: str, *, heading: str = "Overview") -> dict[str, object]:
    return {
        "id": f"chunk-{document_id}",
        "document_id": document_id,
        "chunk_index": 0,
        "content": content,
        "heading": heading,
        "metadata": {},
    }
