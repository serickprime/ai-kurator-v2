"""Inspect question analysis and future RAG routing steps."""

import argparse

from app.rag.question_analysis import QuestionAnalyzer


def main() -> None:
    """Inspect deterministic question analysis."""
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    args = parser.parse_args()

    analysis = QuestionAnalyzer().analyze(args.question)
    print(analysis)


if __name__ == "__main__":
    main()
