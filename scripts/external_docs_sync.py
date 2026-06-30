"""Sync whitelisted external documentation into Supabase evidence tables."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.repositories import DocumentRepository  # noqa: E402
from app.db.supabase_client import SupabaseClient  # noqa: E402
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG, load_external_docs_config  # noqa: E402
from app.external_docs.crawler import ExternalDocsCrawler  # noqa: E402
from app.external_docs.extractor import ExternalDocsExtractor  # noqa: E402
from app.external_docs.indexer import ExternalDocsIndexer  # noqa: E402
from app.external_docs.types import ExternalDocsSyncStats  # noqa: E402
from app.llm.embeddings import OllamaEmbeddingClient  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Sync whitelisted external docs into the RAG index.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source", help="Source name from config/external_docs.yaml.")
    source_group.add_argument("--all", action="store_true", help="Sync all configured sources.")
    parser.add_argument("--limit", type=int, default=None, help="Optional per-source page limit.")
    parser.add_argument("--workspace", default="team", help="Workspace name.")
    parser.add_argument("--config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG, help="External docs YAML path.")
    return parser.parse_args()


async def main_async() -> int:
    """Run external docs sync."""
    args = parse_args()
    config = load_external_docs_config(args.config)
    sources = config.sources if args.all else (config.source(args.source),)
    settings = get_settings()
    summaries: list[dict[str, object]] = []

    async with SupabaseClient(settings) as supabase:
        embedding_client = OllamaEmbeddingClient(settings)
        crawler = ExternalDocsCrawler()
        try:
            repository = DocumentRepository(supabase)
            extractor = ExternalDocsExtractor()
            indexer = ExternalDocsIndexer(repository=repository, embedding_client=embedding_client)
            for source in sources:
                stats = ExternalDocsSyncStats(source_name=source.name, domains=source.allowed_domains)
                pages = await crawler.crawl(source, limit=args.limit)
                stats.fetched = len(pages)
                for crawled in pages:
                    try:
                        extracted = extractor.extract(crawled)
                        result = await indexer.index_page(extracted, source, workspace=args.workspace)
                    except Exception as exc:  # noqa: BLE001 - one page should not abort the whole source
                        stats.failed += 1
                        stats.errors.append(f"{crawled.url}: {_safe_error(exc)}")
                        continue
                    stats.add(result)
                summaries.append(stats.to_dict())
        finally:
            await crawler.close()
            await embedding_client.close()

    print(json.dumps({"status": "ok", "sources": summaries}, ensure_ascii=False, indent=2))
    return 0


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300] or exc.__class__.__name__


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
