"""Update command: orchestrate the importer.

Supports a simple --all mode (import canonical AQA programme). This script
is intentionally tolerant: it will still work (no-ops) before an API key is
configured because it uses the same importer that falls back to cached data.
"""
import argparse
import logging
from oak.importer import Importer

logging.basicConfig(level=logging.INFO)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--programme", default="science-secondary-aqa", help="Programme slug to import")
    args = p.parse_args()
    imp = Importer()
    imp.import_programme(args.programme)


if __name__ == "__main__":
    main()
