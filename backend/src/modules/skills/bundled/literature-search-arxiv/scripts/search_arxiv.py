#!/usr/bin/env python3
"""Searches the arXiv API and returns results in a clean JSON format.

Uses only the Python standard library so it runs inside any sandbox or host
environment without external package installation, while enforcing arXiv's
1 request per 3 seconds rate limit.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

_BASE_URL = "http://export.arxiv.org/api/query?"
_USER_AGENT = "VigilAgent-ArxivSearch/1.0 (https://info.arxiv.org/help/api/index.html)"
_LAST_REQUEST_TS = 0.0


def _fetch_bytes(url: str, timeout: float = 30.0) -> bytes:
    global _LAST_REQUEST_TS
    elapsed = time.monotonic() - _LAST_REQUEST_TS
    if _LAST_REQUEST_TS > 0 and elapsed < 3.0:
        time.sleep(3.0 - elapsed)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    _LAST_REQUEST_TS = time.monotonic()
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search arXiv API and return clean JSON")
    parser.add_argument(
        "--query",
        type=str,
        help="Search query string (e.g., 'au:einstein AND ti:relativity')",
    )
    parser.add_argument("--id_list", type=str, help="Comma-separated list of arXiv IDs")
    parser.add_argument("--start", type=int, default=0, help="Pagination offset")
    parser.add_argument("--max_results", type=int, default=10, help="Number of results to return")
    parser.add_argument(
        "--sort_by",
        type=str,
        choices=["relevance", "lastUpdatedDate", "submittedDate"],
        help="Sort by",
    )
    parser.add_argument(
        "--sort_order",
        type=str,
        choices=["ascending", "descending"],
        help="Sort order",
    )
    return parser.parse_args()


def strip_namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def search_arxiv(args: argparse.Namespace) -> None:
    params: dict[str, object] = {"start": args.start, "max_results": args.max_results}
    if args.query:
        params["search_query"] = args.query
    if args.id_list:
        params["id_list"] = args.id_list
    if args.sort_by:
        params["sortBy"] = args.sort_by
    if args.sort_order:
        params["sortOrder"] = args.sort_order

    query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote_plus)
    url = _BASE_URL + query_string

    results: list[dict[str, object]] = []
    xml_data = _fetch_bytes(url)
    root = ET.fromstring(xml_data)

    for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
        paper: dict[str, object] = {}
        authors: list[str] = []

        for child in entry:
            tag = strip_namespace(child.tag)
            if tag == "id":
                if child.text:
                    paper["id"] = child.text.split("/abs/")[-1]
            elif tag == "title":
                paper["title"] = child.text.replace("\n", " ").strip() if child.text else ""
            elif tag == "summary":
                paper["summary"] = child.text.replace("\n", " ").strip() if child.text else ""
            elif tag == "published":
                paper["published"] = child.text
            elif tag == "author":
                for name_node in child.findall("{http://www.w3.org/2005/Atom}name"):
                    if name_node.text:
                        authors.append(name_node.text)
            elif tag == "link":
                if child.get("title") == "pdf":
                    paper["pdf_url"] = child.get("href")
            elif tag == "primary_category":
                paper["primary_category"] = child.get("term")
            elif tag in {"doi", "journal_ref", "comment"}:
                paper[tag] = child.text

        paper["authors"] = authors
        results.append(paper)

    print(
        json.dumps(
            {
                "status": "success",
                "results_count": len(results),
                "papers": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main_args = parse_args()
    if not main_args.query and not main_args.id_list:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "Must provide either --query or --id_list",
                },
                indent=2,
            )
        )
        sys.exit(1)
    search_arxiv(main_args)
