from pathlib import Path

from app.ingestion.chunker import ParentChildChunker
from app.ingestion.loaders import FileLoader


def test_text_loader_skips_boilerplate_title_and_keeps_source_file_out_of_text(tmp_path: Path) -> None:
    material = tmp_path / "lesson-file.md"
    material.write_text(
        "\n".join(
            [
                "# \u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0444\u0430\u0439\u043b\u0430:",
                "",
                "Source file: leaked.md",
                "",
                "## \u041f\u0440\u043e\u0447\u0435\u0435",
                "",
                "\u041f\u043e\u043b\u0435\u0437\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442 \u0443\u0440\u043e\u043a\u0430.",
            ]
        ),
        encoding="utf-8",
    )

    loaded = FileLoader()._load_text(material)
    sections = ParentChildChunker().split_sections(loaded)
    chunks = ParentChildChunker().split_chunks(sections)

    assert loaded.title == "lesson file"
    assert "Source file:" not in loaded.structured_text
    assert all(section.heading == "lesson file" for section in sections)
    assert all("\u041f\u0440\u043e\u0447\u0435\u0435" not in section.heading for section in sections)
    assert all("Source file:" not in chunk.content for chunk in chunks)


def test_meaningful_markdown_heading_becomes_title(tmp_path: Path) -> None:
    material = tmp_path / "setup.md"
    material.write_text(
        "# Local setup\n\n## Install\n\nRun `npm install`.",
        encoding="utf-8",
    )

    loaded = FileLoader()._load_text(material)

    assert loaded.title == "Local setup"
    assert loaded.structured_text.startswith("# Local setup")
