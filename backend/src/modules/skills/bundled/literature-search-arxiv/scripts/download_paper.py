#!/usr/bin/env python3
"""Downloads a paper from arXiv given its ID (PDF or HTML)."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request

_USER_AGENT = "VigilAgent-ArxivSearch/1.0 (https://info.arxiv.org/help/api/index.html)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download paper from arXiv (PDF or HTML)")
    parser.add_argument("--id", type=str, required=True, help="arXiv ID (e.g., 2305.10601)")
    parser.add_argument(
        "--format",
        type=str,
        choices=["pdf", "html"],
        required=True,
        help="Download format",
    )
    parser.add_argument("--output", type=str, required=True, help="Output file path")
    return parser.parse_args()


def download_paper(args: argparse.Namespace) -> None:
    paper_id = args.id.strip()
    if args.format == "pdf":
        url = f"https://arxiv.org/pdf/{paper_id}.pdf"
    elif args.format == "html":
        url = f"https://arxiv.org/html/{paper_id}"
    else:
        raise ValueError(f"Unsupported format: {args.format}")

    print(f"Attempting to download {args.format.upper()} from {url}...")
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
        if e.code == 404 and args.format == "html":
            print(
                f"Error 404: HTML format is not available for this paper (ID: {paper_id}). "
                "Please try --format pdf instead.",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


if __name__ == "__main__":
    download_paper(parse_args())
