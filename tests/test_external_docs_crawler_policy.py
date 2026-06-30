import asyncio

import httpx

from app.external_docs.crawler import ExternalDocsCrawler
from app.external_docs.policy import is_url_allowed
from app.external_docs.types import ExternalDocSource


def test_crawler_policy_keeps_urls_inside_whitelist() -> None:
    source = _source()

    assert is_url_allowed(source, "https://docs.example.com/guide/setup")
    assert not is_url_allowed(source, "https://example.com/guide/setup")
    assert not is_url_allowed(source, "https://docs.example.com/blog/post")
    assert not is_url_allowed(source, "https://docs.example.com/login")
    assert not is_url_allowed(source, "https://docs.example.com/files/manual.pdf")


def test_crawler_respects_depth_limit_and_allowed_domains() -> None:
    source = _source(crawl_depth=1, max_pages=10)

    def handler(request: httpx.Request) -> httpx.Response:
        pages = {
            "https://docs.example.com/": (
                '<a href="/guide">Guide</a>'
                '<a href="https://evil.example.com/steal">Evil</a>'
                '<a href="/blog/post">Blog</a>'
            ),
            "https://docs.example.com/guide": '<h1>Guide</h1><a href="/deep">Deep</a>',
            "https://docs.example.com/deep": "<h1>Too deep</h1>",
        }
        return httpx.Response(200, text=pages.get(str(request.url), ""), headers={"content-type": "text/html"})

    async def run() -> list[str]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        crawler = ExternalDocsCrawler(client=client)
        try:
            pages = await crawler.crawl(source)
        finally:
            await client.aclose()
        return [page.url for page in pages]

    urls = asyncio.run(run())

    assert urls == ["https://docs.example.com/", "https://docs.example.com/guide"]


def _source(*, crawl_depth: int = 2, max_pages: int = 20) -> ExternalDocSource:
    return ExternalDocSource(
        name="docs",
        source_kind="external_docs",
        allowed_domains=("docs.example.com",),
        start_urls=("https://docs.example.com/",),
        allow_patterns=(r"^https://docs\.example\.com/",),
        deny_patterns=(r"/blog/",),
        crawl_depth=crawl_depth,
        max_pages=max_pages,
    )
