"""Check tracked files for accidentally committed secrets."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "eval_runs", "logs"}
TEST_SAFE_MARKERS = (
    "fake",
    "test",
    "dummy",
    "example",
    "hidden",
    "redacted",
    "abc.def.ghi",
    "placeholder",
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("supabase secret key", re.compile(r"\bsb_" + r"secret_[A-Za-z0-9_-]{8,}\b")),
    ("openrouter key", re.compile(r"\bsk-" + r"or-v1-[A-Za-z0-9_-]{8,}\b")),
    ("github token", re.compile(r"\bgh" + r"p_[A-Za-z0-9_]{20,}\b")),
    ("github pat", re.compile(r"\bgithub_" + r"pat_[A-Za-z0-9_]{20,}\b")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{10,}\b")),
    ("telegram bot token", re.compile(r"\bbot[0-9]{6,}:[A-Za-z0-9_-]{20,}\b")),
    ("telegram token", re.compile(r"\b[0-9]{8,}:[A-Za-z0-9_-]{20,}\b")),
)


@dataclass(frozen=True)
class SecretHit:
    """One potential secret occurrence."""

    path: Path
    line_number: int
    kind: str
    preview: str

    def format(self) -> str:
        """Return a concise CLI-safe hit line."""
        return f"{self.path}:{self.line_number}: {self.kind}: {self.preview}"


def find_secret_hits(paths: Iterable[Path]) -> list[SecretHit]:
    """Return non-allowlisted secret-like values in files."""
    hits: list[SecretHit] = []
    for path in paths:
        if _should_skip_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            hits.extend(_line_hits(path, index, line))
    return hits


def tracked_files(root: Path = Path(".")) -> list[Path]:
    """Return git-tracked files below root."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [root / line.strip() for line in result.stdout.splitlines() if line.strip()]


def _line_hits(path: Path, line_number: int, line: str) -> list[SecretHit]:
    hits: list[SecretHit] = []
    for kind, pattern in SECRET_PATTERNS:
        if not pattern.search(line):
            continue
        if _is_allowed_false_positive(path, line):
            continue
        hits.append(SecretHit(path=path, line_number=line_number, kind=kind, preview=_preview(line)))
    return hits


def _is_allowed_false_positive(path: Path, line: str) -> bool:
    lowered = line.casefold()
    if "<redacted>" in lowered:
        return True
    if "re.compile" in line or "re.sub" in line or "_RE" in line:
        return True
    if _contains_split_secret_prefix(line):
        return True
    if _is_test_path(path) and any(marker in lowered for marker in TEST_SAFE_MARKERS):
        return True
    return False


def _contains_split_secret_prefix(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return any(
        marker in compact
        for marker in (
            '"sb_"+"secret_"',
            "'sb_'+'secret_'",
            '"sk-"+"or-v1-"',
            "'sk-'+'or-v1-'",
            '"gh"+"p_"',
            "'gh'+'p_'",
            '"github_"+"pat_"',
            "'github_'+'pat_'",
        )
    )


def _is_test_path(path: Path) -> bool:
    return any(part == "tests" for part in path.parts)


def _should_skip_path(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _preview(line: str, limit: int = 180) -> str:
    return line.strip()[:limit]


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Check tracked files for secret-like values.")
    parser.add_argument("paths", nargs="*", type=Path, help="Optional explicit paths for tests/debugging.")
    return parser.parse_args()


def main() -> int:
    """CLI entry point."""
    args = parse_args()
    paths = args.paths if args.paths else tracked_files()
    hits = find_secret_hits(paths)
    if hits:
        print("Potential secrets found:")
        print("\n".join(hit.format() for hit in hits))
        return 1
    print("Secret check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
