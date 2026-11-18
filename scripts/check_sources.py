"""Check configured publishers with GET only; requires Python 3.12+, no packages or credentials."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from digest import main


if __name__ == "__main__":
    raise SystemExit(main(["--check-sources", *sys.argv[1:]]))
