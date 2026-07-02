"""Filename construction, source classification, and journal abbreviations."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from .common import (
    SPACE_RE,
    WORD_RE,
    ascii_fold,
    clean_token,
    clean_year,
    first_value,
    format_word,
    shorten_title,
)


BOOK_EVIDENCE_RE = re.compile(
    r"\b("
    r"isbn(?:-1[03])?|"
    r"table\s+of\s+contents|"
    r"preface|"
    r"index|"
    r"crc\s+press|"
    r"oxford\s+university\s+press|"
    r"princeton\s+university\s+press|"
    r"mit\s+press"
    r")\b",
    re.IGNORECASE,
)
JOURNAL_ALIASES = {
    "annals of operations research": "AOR",
    "arxiv": "ArXiv",
    "european journal of operational research": "EJOR",
    "informs journal on applied analytics": "IJAA",
    "informs journal on computing": "IJOC",
    "informs informs journal on computing": "IJOC",
    "journal of combinatorial optimization": "JOCO",
    "journal of global optimization": "JGO",
    "journal of optimization theory and applications": "JOTA",
    "management science": "MS",
    "m and som": "MSOM",
    "m som": "MSOM",
    "manufacturing and service operations management": "MSOM",
    "manufacturing service operations management": "MSOM",
    "mathematics of operations research": "MOR",
    "mathematics of or": "MOR",
    "math oper res": "MOR",
    "math or": "MOR",
    "naval research logistics": "NRL",
    "networks": "Networks",
    "operations research": "OR",
    "operations research letters": "ORL",
    "production and operations management": "POM",
    "siam journal on algebraic and discrete methods": "SIAMJADM",
    "siam journal on applied mathematics": "SIAMJAM",
    "siam journal on computing": "SIAMJC",
    "siam journal on discrete mathematics": "SIAMJDM",
    "siam journal on optimization": "SIAMJO",
    "transportation science": "TS",
    "aor": "AOR",
    "ejor": "EJOR",
    "ijaa": "IJAA",
    "ijoc": "IJOC",
    "joco": "JOCO",
    "jgo": "JGO",
    "jota": "JOTA",
    "ms": "MS",
    "msom": "MSOM",
    "mor": "MOR",
    "nrl": "NRL",
    "or": "OR",
    "orl": "ORL",
    "pom": "POM",
    "siamjadm": "SIAMJADM",
    "siamjam": "SIAMJAM",
    "siamjc": "SIAMJC",
    "siamjdm": "SIAMJDM",
    "siamjo": "SIAMJO",
    "ts": "TS",
}


def build_filename(
    metadata: dict[str, Any],
    suffix: str = "",
    kind: str | None = None,
    title_override: str | None = None,
    year_override: str | None = None,
    author_override: str | None = None,
) -> str:
    source = source_prefix(metadata, kind=kind)
    author = clean_token(author_override, max_words=1) if author_override else None
    author = author or first_author(metadata) or "Unknown"
    year = clean_year(year_override) or publication_year(metadata) or "Undated"
    title = (
        title_override
        or first_value(metadata.get("title"))
        or metadata.get("DOI")
        or "Untitled"
    )
    short_title = shorten_title(title)
    return f"{source}-{author}{year}-{short_title}{suffix}.pdf"


def manual_metadata(
    *,
    title: str,
    author: str | None,
    year: str | None,
    kind: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"title": [title]}
    if author:
        metadata["author"] = [{"family": author}]
    if year and (cleaned_year := clean_year(year)):
        metadata["issued"] = {"date-parts": [[int(cleaned_year)]]}
    if kind == "book":
        metadata["type"] = "book"
    elif kind == "bookchapter":
        metadata["type"] = "book-chapter"
    elif kind == "thesis":
        metadata["type"] = "thesis"
    elif kind == "journal":
        metadata["type"] = "journal-article"
    return metadata


def source_prefix(metadata: dict[str, Any], kind: str | None = None) -> str:
    if kind == "book":
        return "Book"
    if kind == "bookchapter":
        return "BookChapter"
    if kind == "thesis":
        return "Thesis"
    if kind == "journal":
        return journal_abbrev(metadata) or "UnknownJournal"

    item_type = str(metadata.get("type") or "").lower()
    if item_type in {"thesis", "dissertation"}:
        return "Thesis"
    if item_type in {"book", "monograph", "reference-book", "book-series"}:
        return "Book"
    if item_type in {
        "book-chapter",
        "book-part",
        "reference-entry",
        "book-section",
    }:
        return "BookChapter"

    if journal := journal_abbrev(metadata):
        return journal

    if metadata.get("ISBN") or metadata.get("isbn") or metadata.get("publisher"):
        return "Book"

    return "UnknownJournal"


def infer_kind(
    pdf: Path,
    metadata: dict[str, Any],
    text: str,
    *,
    forced_kind: str,
) -> str | None:
    if forced_kind != "auto":
        return forced_kind

    item_type = str(metadata.get("type") or "").lower()
    if item_type in {"thesis", "dissertation"}:
        return "thesis"
    if item_type in {"book", "monograph", "reference-book", "book-series"}:
        return "book"
    if item_type in {
        "book-chapter",
        "book-part",
        "reference-entry",
        "book-section",
    }:
        return "bookchapter"
    if item_type in {"journal-article", "proceedings-article", "journal-issue"}:
        return None

    search_text = re.sub(r"[_\-.]+", " ", "\n".join((pdf.stem, text[:20_000])))
    if BOOK_EVIDENCE_RE.search(search_text):
        return "book"

    return None


def journal_abbrev(metadata: dict[str, Any]) -> str | None:
    short_title = first_value(metadata.get("short-container-title"))
    container_title = first_value(metadata.get("container-title"))

    for candidate in (short_title, container_title):
        if candidate and (alias := journal_alias(candidate)):
            return alias
        if candidate and (siam := siam_journal_abbrev(candidate)):
            return siam

    if short_title and looks_like_abbreviated_journal(short_title):
        return journal_initials(short_title) or clean_journal(short_title)

    if container_title:
        return journal_initials(container_title) or clean_journal(container_title)

    if short_title:
        return journal_initials(short_title) or clean_journal(short_title)

    return None


def journal_alias(value: str) -> str | None:
    return JOURNAL_ALIASES.get(normalize_journal(value))


def normalize_journal(value: str) -> str:
    value = html.unescape(value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).lower()
    return SPACE_RE.sub(" ", value).strip()


def looks_like_abbreviated_journal(value: str) -> bool:
    clean = clean_journal(value)
    return "." in value or clean.isupper() or len(clean) <= 10


def siam_journal_abbrev(value: str) -> str | None:
    normalized = normalize_journal(value)
    if not normalized.startswith("siam journal"):
        return None
    rest = normalized.removeprefix("siam journal").strip()
    rest = re.sub(r"^(on|of|in|for)\s+", "", rest)
    initials = [
        word[0].upper()
        for word in rest.split()
        if word not in {"and", "for", "in", "of", "on", "the"}
    ]
    return "SIAMJ" + "".join(initials[:5]) if initials else "SIAMJ"


def journal_initials(value: str) -> str | None:
    stop = {"and", "for", "in", "of", "on", "the"}
    initials = [
        word[0].upper()
        for word in WORD_RE.findall(value)
        if word.lower() not in stop and not word.isdigit()
    ]
    return "".join(initials[:8]) or None


def first_author(metadata: dict[str, Any]) -> str | None:
    contributors = metadata.get("author") or metadata.get("editor") or []
    if not contributors:
        return None
    first = contributors[0]
    name = first.get("family") or first.get("name") or first.get("given")
    if not name:
        return None
    return clean_token(name, max_words=1) or None


def publication_year(metadata: dict[str, Any]) -> str | None:
    for key in ("published-print", "issued", "published-online"):
        date_parts = metadata.get(key, {}).get("date-parts")
        if date_parts and date_parts[0]:
            return str(date_parts[0][0])
    return None


def clean_journal(value: str) -> str:
    words = WORD_RE.findall(ascii_fold(value))
    return "".join(format_word(word) for word in words)[:32]
