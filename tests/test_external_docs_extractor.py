from datetime import datetime, timezone

from app.external_docs.extractor import ExternalDocsExtractor
from app.external_docs.types import CrawledPage


def test_external_docs_extractor_removes_navigation_and_preserves_code() -> None:
    html = """
    <html>
      <head>
        <title>HTTP Request node</title>
        <link rel="canonical" href="https://docs.example.com/integrations/http-request" />
      </head>
      <body>
        <nav>Docs navigation should disappear</nav>
        <main>
          <h1>HTTP Request node</h1>
          <p>Use this node to call an API.</p>
          <pre><code>curl https://api.example.com/v1/items</code></pre>
          <table><tr><th>Name</th><td>Authorization</td></tr></table>
        </main>
        <footer>Footer should disappear</footer>
      </body>
    </html>
    """
    page = CrawledPage(
        source_name="docs",
        url="https://docs.example.com/integrations/http-request?ref=nav",
        html=html,
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )

    extracted = ExternalDocsExtractor().extract(page)

    assert extracted.title == "HTTP Request node"
    assert extracted.canonical_url == "https://docs.example.com/integrations/http-request"
    assert "Docs navigation" not in extracted.structured_text
    assert "Footer should disappear" not in extracted.structured_text
    assert "Use this node to call an API." in extracted.structured_text
    assert "```" in extracted.structured_text
    assert "curl https://api.example.com/v1/items" in extracted.structured_text
    assert extracted.content_hash


def test_external_docs_extractor_removes_markdown_anchor_html_and_copy_buttons() -> None:
    html = """
    <html>
      <head><title>Install with npm</title></head>
      <body>
        <main>
          <h1>Install with npm</h1>
          <p>For the complete documentation index, see llms.txt.</p>
          <p>This page is also available as Markdown.</p>
          <p># Terminal 1 &lt;a href="#terminal-1" id="terminal-1"&gt;&lt;/a&gt;</p>
          <p>Run n8n locally.</p>
          <pre>Copy</pre>
          <pre><code>npm install n8n -g</code></pre>
        </main>
      </body>
    </html>
    """
    page = CrawledPage(
        source_name="docs",
        url="https://docs.example.com/deploy/install-with-npm",
        html=html,
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )

    extracted = ExternalDocsExtractor().extract(page)

    assert '<a href="#terminal-1"' not in extracted.structured_text
    assert "id=\"terminal-1\"" not in extracted.structured_text
    assert "complete documentation index" not in extracted.structured_text
    assert "available as Markdown" not in extracted.structured_text
    assert "```\nCopy\n```" not in extracted.structured_text
    assert "npm install n8n -g" in extracted.structured_text
