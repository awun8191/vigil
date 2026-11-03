#!/usr/bin/env python3
"""Downloads the LaTeX source (tar.gz) of a paper from arXiv given its ID."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request

_USER_AGENT = "VigilAgent-ArxivSearch/1.0 (https://info.arxiv.org/help/api/index.html)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download paper source (tar.gz) from arXiv")
    parser.add_argument("--id", type=str, required=True, help="arXiv ID (e.g., 2010.11645)")
    parser.add_argument("--output", type=str, required=True, help="Output file path for tar.gz")
    return parser.parse_args()


def download_source(args: argparse.Namespace) -> None:
    paper_id = args.id.strip()
    url = f"https://export.arxiv.org/e-print/{paper_id}"
    print(f"Attempting to download source from {url}...")
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            content = resp.read()
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "wb") as f:
            f.write(content)
        print(f"Success! Saved to {args.output}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(
                f"Error 404: Source not found (ID: {paper_id}). Not all papers have source available.",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


if __name__ == "__main__":
    download_source(parse_args())
