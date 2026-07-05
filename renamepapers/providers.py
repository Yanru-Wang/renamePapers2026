"""Identifier extraction and external metadata providers."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .common import first_value, titles_match


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ISBN_RE = re.compile(
    r"\b(?:ISBN(?:-1[03])?[:\s]*)?((?:97[89])?[\d\-]{10,17})\b",
    re.IGNORECASE,
)
ARXIV_RE = re.compile(
    r"\barXiv[:\s]*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.IGNORECASE
)
DEFAULT_MAILTO = os.environ.get("CROSSREF_MAILTO", "")
USER_AGENT = "renamepapers/1.0 (mailto:{mailto})" if DEFAULT_MAILTO else "renamepapers/1.0"


def find_doi(text: str) -> str | None:
    text = normalize_extracted_doi_text(text)
    match = DOI_RE.search(text)
    if not match:
        return None
    doi = match.group(0).strip()
    return doi.rstrip(".,;:)]}>").lower()


def normalize_extracted_doi_text(text: str) -> str:
    """Repair common PDF text-extraction spaces inside DOI suffixes."""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            r"(?i)(10\.\d{4,9}/[-._;()/:A-Z0-9]*[._;()/:])\s+([A-Z0-9])",
            r"\1\2",
            text,
        )
    return text


def find_isbn(text: str) -> str | None:
    """Extract an ISBN from *text* and return it normalised (digits only)."""
    match = ISBN_RE.search(text)
    if not match:
        return None
    raw = match.group(1)
    digits = re.sub(r"[^0-9X]", "", raw.upper())
    if len(digits) not in (10, 13):
        return None
    return digits


def crossref_by_isbn(isbn: str, mailto: str) -> dict[str, Any] | None:
    """Look up *isbn* via Crossref and return the *book-level* metadata.

    A single ISBN can match many chapters; prefer a validated book-level record.
    """
    params = urllib.parse.urlencode({"filter": f"isbn:{isbn}", "rows": "20"})
    payload = fetch_json(f"https://api.crossref.org/works?{params}", mailto)
    items = payload.get("message", {}).get("items", []) if payload else []
    if not items:
        return None

    valid: list[dict[str, Any]] = []
    for item in items:
        item_isbns = item.get("isbn") or item.get("ISBN") or []
        if isinstance(item_isbns, str):
            item_isbns = [item_isbns]
        if isbn in item_isbns or any(
            re.sub(r"[^0-9X]", "", str(i).upper()) == isbn for i in item_isbns
        ):
            valid.append(item)
    if not valid:
        return None

    book_types = {"book", "monograph", "edited-book", "reference-book"}
    for item in valid:
        if str(item.get("type") or "").lower() in book_types:
            return item

    for item in valid:
        item_title = first_value(item.get("title")) or ""
        container = first_value(item.get("container-title")) or ""
        if container and item_title != container:
            continue
        if not container:
            return item

    return valid[0]


def find_arxiv(text: str) -> str | None:
    """Extract an arXiv ID from *text* (e.g. ``2103.12345``)."""
    match = ARXIV_RE.search(text)
    if not match:
        return None
    return match.group(1).rstrip("v").split("v")[0]


def crossref_by_arxiv(arxiv_id: str, mailto: str) -> dict[str, Any] | None:
    """Look up an arXiv ID via Crossref."""
    params = urllib.parse.urlencode(
        {"filter": f"arxiv:{arxiv_id}", "rows": "1"}
    )
    payload = fetch_json(f"https://api.crossref.org/works?{params}", mailto)
    items = payload.get("message", {}).get("items", []) if payload else []
    return items[0] if items else None


def crossref_by_doi(doi: str | None, mailto: str) -> dict[str, Any] | None:
    if not doi:
        return None
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
    payload = fetch_json(url, mailto)
    return payload.get("message") if payload else None


def crossref_by_title(
    title: str, mailto: str, *, author: str | None = None
) -> dict[str, Any] | None:
    """Search Crossref by *title*.

    When *author* is given, request multiple results and return the first whose
    author list contains *author*.
    """
    params = {
        "query.title": title,
        "rows": "5",
        "select": ",".join(
            [
                "DOI",
                "title",
                "author",
                "editor",
                "issued",
                "published-print",
                "published-online",
                "container-title",
                "short-container-title",
                "type",
                "ISBN",
                "publisher",
                "relation",
            ]
        ),
    }
    payload = fetch_json(
        f"https://api.crossref.org/works?{urllib.parse.urlencode(params)}",
        mailto,
    )
    items = payload.get("message", {}).get("items", []) if payload else []
    title_matches = [
        item
        for item in items
        if titles_match(title, first_value(item.get("title")) or "")
    ]
    if not title_matches:
        return None
    if author:
        author_lower = author.lower()
        for item in title_matches:
            for a in item.get("author") or []:
                family = str(a.get("family") or "").lower()
                given = str(a.get("given") or "").lower()
                if author_lower in family or author_lower in given:
                    return item
    return title_matches[0]


def fetch_json(url: str, mailto: str) -> dict[str, Any] | None:
    agent = f"renamepapers/1.0 (mailto:{mailto})" if mailto else USER_AGENT
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
