"""CLI for ingesting local files into Supabase."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.repositories import DocumentRepository  # noqa: E402
from app.db.supabase_client import SupabaseClient  # noqa: E402
from app.ingestion.document_cards import DocumentCardBuilder  # noqa: E402
from app.ingestion.indexing import IndexingService  # noqa: E402
from app.ingestion.loaders import FileLoader  # noqa: E402
from app.llm.embeddings import OllamaEmbeddingClient  # noqa: E402
from app.llm.openrouter_client import OpenRouterClient  # noqa: E402
from app.llm.vision import VisionTextifier  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Ingest files into AI Kurator V2.")
    parser.add_argument("--path", required=True, type=Path, help="File or directory to ingest.")
    parser.add_argument("--workspace", default="team", help="Workspace name.")
    parser.add_argument("--course", default=None, help="Course name for document metadata.")
    parser.add_argument("--module", default=None, help="Module name for document metadata.")
    parser.add_argument("--lesson", default=None, help="Lesson name for document metadata.")
    parser.add_argument("--vision", action="store_true", help="Describe images through the vision model.")
    parser.add_argument("--llm-card", action="store_true", help="Try OpenRouter card generation before fallback.")
    return parser.parse_args()


async def main_async() -> None:
    """Run ingestion."""
    args = parse_args()
    settings = get_settings()
    vision_enabled = bool(args.vision or settings.vision_enabled)

    async with SupabaseClient(settings) as supabase:
        repository = DocumentRepository(supabase)
        embedding_client = OllamaEmbeddingClient(settings)
        openrouter_client = OpenRouterClient(settings) if args.llm_card else None
        vision_client = VisionTextifier(settings) if vision_enabled else None

        try:
            service = IndexingService(
                repository=repository,
                embedding_client=embedding_client,
                loader=FileLoader(vision_describer=vision_client, vision_enabled=vision_enabled),
                card_builder=DocumentCardBuilder(openrouter_client),
            )
            results = await service.ingest_path(
                args.path,
                workspace=args.workspace,
                course=args.course,
                module=args.module,
                lesson=args.lesson,
            )
        finally:
            await embedding_client.close()
            if openrouter_client is not None:
                await openrouter_client.close()
            if vision_client is not None:
                await vision_client.close()

    for result in results:
        status = "skipped" if result.skipped else "indexed"
        print(
            f"{status}: {result.path} -> document={result.document_id} "
            f"version={result.version} sections={result.sections_count} chunks={result.chunks_count}"
        )


def main() -> None:
    """CLI entry point."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
