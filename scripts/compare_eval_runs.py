"""Compare two evaluation reports."""

import argparse
from pathlib import Path


def main() -> None:
    """Placeholder comparison command."""
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()

    print(f"Comparison is not implemented yet: {args.baseline} vs {args.candidate}")


if __name__ == "__main__":
    main()
