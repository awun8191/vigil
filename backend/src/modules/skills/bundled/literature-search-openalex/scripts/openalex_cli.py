#!/usr/bin/env python3
"""CLI tool for querying the OpenAlex API.

Uses only the Python standard library so it runs inside any sandbox or host
environment without external package installation, with automatic rate-limit
backoff and PDF fallback downloads.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from typing import Any, Sequence
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

BASE_URL = "https://api.openalex.org"
PDF_BASE_URL = "https://content.openalex.org"
USER_AGENT = "VigilAgent-OpenAlex/1.0 (https://help.openalex.org)"

DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 100
RESOLVE_PER_PAGE = 5
DEFAULT_TIMEOUT_SECS = 30.0
PDF_TIMEOUT_SECS = 60.0
MAX_RETRIES = 4
TRUNCATE_LINE_LIMIT = 500
PLAN_UPGRADE_ERROR = "Plan upgrade required"

ENTITY_TYPES = [
    "works",
    "authors",
    "sources",
    "institutions",
    "topics",
    "domains",
    "fields",
    "subfields",
    "sdgs",
    "countries",
    "continents",
    "languages",
    "keywords",
    "publishers",
    "funders",
    "awards",
    "work-types",
    "source-types",
    "institution-types",
    "licenses",
]


def _load_dotenv_if_present() -> None:
    env_path = os.path.expanduser("~/.env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                k, v = raw.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except OSError:
        pass


def _build_url_with_api_key(url: str, api_key: str | None) -> str:
    if not api_key:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urllib.parse.urlencode({'api_key': api_key})}"


def _http_get(full_url: str, timeout: float = DEFAULT_TIMEOUT_SECS) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.read(), headers


def fetch_with_retry(
    url: str,
    params: dict[str, Any],
    api_key: str | None = None,
    max_retries: int = MAX_RETRIES,
    exit_on_error: bool = True,
    return_headers: bool = False,
) -> dict[str, Any] | None:
    query_string = urllib.parse.urlencode(params, doseq=True)
    full_url = f"{url}?{query_string}" if query_string else url
    full_url = _build_url_with_api_key(full_url, api_key)

    for attempt in range(max_retries + 1):
        try:
            body, headers = _http_get(full_url)
            data = json.loads(body.decode("utf-8"))
            if return_headers:
                data["_response_headers"] = headers
            return data
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code == 429 and PLAN_UPGRADE_ERROR.lower() in err_body.lower():
                logging.error(
                    "OpenAlex says this request needs a paid plan (HTTP 429 '%s').",
                    PLAN_UPGRADE_ERROR,
                )
                break
            if e.code in {429, 500, 502, 503, 504} and attempt < max_retries:
                delay = min(1.5 * (2**attempt), 15.0) + random.uniform(0, 0.5)
                logging.info(
                    "OpenAlex request returned HTTP %s; retrying in %.1fs (%d/%d)",
                    e.code,
                    delay,
                    attempt + 1,
                    max_retries + 1,
                )
                time.sleep(delay)
                continue
            logging.error("HTTP Error %s while fetching %s: %s", e.code, url, err_body or e)
            break
        except OSError as e:
            if attempt < max_retries:
                time.sleep(1.5 * (2**attempt))
                continue
            logging.error("Network error while fetching %s: %s", url, e)
            break

    if exit_on_error:
        sys.exit(1)
    return None


def print_json(data: Any) -> None:
    output = json.dumps(data, indent=2)
    if not sys.stdout.isatty():
        print(output)
        return
    lines = output.splitlines()
    if len(lines) > TRUNCATE_LINE_LIMIT:
        remaining = len(lines) - TRUNCATE_LINE_LIMIT
        print("\n".join(lines[:TRUNCATE_LINE_LIMIT]))
        logging.warning(
            "Output truncated. %d additional lines not shown. Redirect output to a file if needed.",
            remaining,
        )
    else:
        print(output)


def handle_resolve(args: argparse.Namespace) -> None:
    url = f"{BASE_URL}/{args.entity_type}"
    params = {
        "search": args.query,
        "per_page": max(1, min(args.per_page, MAX_PER_PAGE)),
    }
    data = fetch_with_retry(url, params, api_key=args.api_key)
    if data is None:
        sys.exit(1)
    results = [
        {
            "id": item.get("id"),
            "display_name": item.get("display_name"),
            "hint": item.get("hint") or item.get("works_count"),
        }
        for item in data.get("results", [])
    ]
    print_json(results)


_OPENALEX_SHORT_ID_RE = re.compile(r"^[A-Za-z]\d+$")


def _is_valid_entity_id(entity_id: str) -> bool:
    return bool(
        _OPENALEX_SHORT_ID_RE.match(entity_id)
        or entity_id.startswith("https://openalex.org/")
        or entity_id.startswith("https://doi.org/")
    )


def handle_get(args: argparse.Namespace) -> None:
    if not _is_valid_entity_id(args.id):
        logging.error(
            "Invalid entity ID %r. Expected a short ID (e.g. W2741809807), "
            "a full OpenAlex URL, or a DOI URL.",
            args.id,
        )
        sys.exit(1)
    url = f"{BASE_URL}/{args.entity_type}/{args.id}"
    params: dict[str, Any] = {}
    if args.select is not None:
        params["select"] = args.select
    data = fetch_with_retry(url, params, api_key=args.api_key)
    print_json(data)


def _is_valid_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def _try_download_url(url: str, output_path: str) -> bool:
    try:
        data, _ = _http_get(url, timeout=PDF_TIMEOUT_SECS)
        if not _is_valid_pdf(data):
            return False
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        logging.warning("Failed to download from %s: %s", url, e)
        return False


def handle_download_pdf(args: argparse.Namespace) -> None:
    url = _build_url_with_api_key(f"{PDF_BASE_URL}/works/{args.id}.pdf", args.api_key)
    if _try_download_url(url, args.output):
        logging.info("Successfully downloaded PDF to %s", args.output)
        return

    work_url = f"{BASE_URL}/works/{args.id}"
    work_data = fetch_with_retry(work_url, {}, api_key=args.api_key, exit_on_error=False)
    if work_data:
        locations = work_data.get("locations", [])
        if not locations and "best_oa_location" in work_data:
            locations = [work_data["best_oa_location"]]
        for loc in locations:
            if loc and loc.get("pdf_url") and _try_download_url(loc["pdf_url"], args.output):
                logging.info("Successfully downloaded PDF from fallback location.")
                return
    logging.error("Could not download PDF for %s.", args.id)
    sys.exit(1)


def handle_filter(args: argparse.Namespace) -> None:
    url = f"{BASE_URL}/{args.entity_type}"
    optional_params = ["search", "filter", "group_by", "sample", "seed", "select"]
    params = {
        k: getattr(args, k)
        for k in optional_params
        if getattr(args, k) is not None
    }
    if args.sort is not None and args.sample is None:
        params["sort"] = args.sort
    if args.group_by is None and args.sample is None:
        params["per_page"] = max(1, min(args.per_page, MAX_PER_PAGE))
        params["page"] = args.page
    data = fetch_with_retry(url, params, api_key=args.api_key)
    print_json(data)


def handle_rate_limit(args: argparse.Namespace) -> None:
    url = f"{BASE_URL}/works"
    data = fetch_with_retry(url, {"per_page": 1}, api_key=args.api_key, return_headers=True)
    if data is None:
        sys.exit(1)
    headers = data.pop("_response_headers", {})
    limits = {
        "authenticated": bool(args.api_key),
        "x-ratelimit-limit": headers.get("x-ratelimit-limit"),
        "x-ratelimit-remaining": headers.get("x-ratelimit-remaining"),
        "x-ratelimit-reset": headers.get("x-ratelimit-reset"),
    }
    print_json({"rate_limits": limits})


def main(argv: Sequence[str]) -> None:
    _load_dotenv_if_present()
    parser = argparse.ArgumentParser(description="OpenAlex API CLI Utility")
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("OPENALEX_API_KEY"),
        help="Optional API key for higher rate limits.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_resolve = subparsers.add_parser("resolve", help="Resolve an entity name to its OpenAlex ID")
    parser_resolve.add_argument("entity_type", choices=ENTITY_TYPES)
    parser_resolve.add_argument("query", help="Name or part of the name to search for")
    parser_resolve.add_argument("--per-page", type=int, default=RESOLVE_PER_PAGE)
    parser_resolve.set_defaults(func=handle_resolve)

    parser_get = subparsers.add_parser("get", help="Get a single entity by its ID")
    parser_get.add_argument("entity_type", choices=ENTITY_TYPES)
    parser_get.add_argument("id", help="The OpenAlex ID (e.g., W2741809807)")
    parser_get.add_argument("--select", type=str, help="Limit returned fields")
    parser_get.set_defaults(func=handle_get)

    parser_pdf = subparsers.add_parser("download-pdf", help="Download PDF for a work")
    parser_pdf.add_argument("id", help="The OpenAlex Work ID")
    parser_pdf.add_argument("output", help="Output file path")
    parser_pdf.set_defaults(func=handle_download_pdf)

    parser_filter = subparsers.add_parser("filter", help="Filter and search entities")
    parser_filter.add_argument("entity_type", choices=ENTITY_TYPES)
    parser_filter.add_argument("--search", type=str)
    parser_filter.add_argument("--filter", type=str)
    parser_filter.add_argument("--sort", type=str)
    parser_filter.add_argument("--group-by", type=str)
    parser_filter.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    parser_filter.add_argument("--page", type=int, default=1)
    parser_filter.add_argument("--sample", type=int)
    parser_filter.add_argument("--seed", type=int)
    parser_filter.add_argument("--select", type=str)
    parser_filter.set_defaults(func=handle_filter)

    parser_rate_limit = subparsers.add_parser("rate-limit", help="Check current rate limit status")
    parser_rate_limit.set_defaults(func=handle_rate_limit)

    args = parser.parse_args(argv[1:])
    args.func(args)


if __name__ == "__main__":
    main(sys.argv)
