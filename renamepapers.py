#!/usr/bin/env python3
"""Rename scientific PDFs as JournalAbbrev_AuthorYear_ShortTitle.pdf.

The script is intentionally dependency-light. It uses DOI metadata when possible,
falls back to a title search, and moves successful renames to ~/Papers/Renamed by
default so ~/Papers/Inbox stays as the intake queue.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[A-Za-z0-9]+")
RENAMED_FILE_RE = re.compile(
    r"^(?:Book|BookChapter|[A-Z][A-Za-z0-9]{1,12})[-_][A-Za-z][A-Za-z0-9]*"
    r"(?:19|20)\d{2}[-_][A-Za-z0-9][A-Za-z0-9_]*(?:_supplement)?$"
)
SUPPLEMENT_RE = re.compile(
    r"\b("
    r"supplement(?:al|ary)?|"
    r"supp(?:lement)?|"
    r"supporting\s+information|"
    r"online\s+appendi(?:x|ces)|"
    r"appendi(?:x|ces)"
    r")\b",
    re.IGNORECASE,
)
BOOK_EVIDENCE_RE = re.compile(
    r"\b("
    r"isbn(?:-1[03])?|"
    r"table\s+of\s+contents|"
    r"preface|"
    r"index|"
    # Book-only publishers (not major journal publishers).
    r"crc\s+press|"
    r"oxford\s+university\s+press|"
    r"princeton\s+university\s+press|"
    r"mit\s+press"
    r")\b",
    re.IGNORECASE,
)
DEFAULT_MAILTO = os.environ.get("CROSSREF_MAILTO", "")
USER_AGENT = "renamepapers/1.0 (mailto:{mailto})" if DEFAULT_MAILTO else "renamepapers/1.0"
JOURNAL_ALIASES = {
    "annals of operations research": "AOR",
    "european journal of operational research": "EJOR",
    "informs journal on applied analytics": "IJAA",
    "informs journal on computing": "IJOC",
    "informs informs journal on computing": "IJOC",
    "journal of combinatorial optimization": "JOCO",
    "journal of global optimization": "JGO",
    "journal of optimization theory and applications": "JOTA",
    "management science": "MS",
    "manufacturing and service operations management": "MSOM",
    "mathematics of operations research": "MOR",
    "mathematics of or": "MOR",
    "math oper res": "MOR",
    "math or": "MOR",
    "naval research logistics": "NRL",
    "operations research": "OR",
    "operations research letters": "ORL",
    "production and operations management": "POM",
    "siam journal on computing": "SICOMP",
    "siam journal on discrete mathematics": "SIDMA",
    "siam journal on optimization": "SIOPT",
    "transportation science": "TS",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rename PDFs in ~/Papers/Inbox using DOI/Crossref metadata."
    )
    parser.add_argument(
        "pdfs",
        nargs="*",
        type=Path,
        help="PDF files to process. Defaults to every *.pdf in --inbox.",
    )
    parser.add_argument(
        "--inbox",
        type=Path,
        default=Path("~/Papers/Inbox").expanduser(),
        help="Inbox folder used when no PDF paths are supplied.",
    )
    parser.add_argument(
        "--outbox",
        type=Path,
        default=Path("~/Papers/Renamed").expanduser(),
        help="Destination folder for successfully renamed PDFs.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Rename in the source folder instead of moving to --outbox.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without renaming or moving files.",
    )
    parser.add_argument(
        "--mailto",
        default=DEFAULT_MAILTO,
        help="Email address sent in the Crossref User-Agent.",
    )
    parser.add_argument(
        "--kind",
        choices=("auto", "journal", "book", "bookchapter"),
        default="auto",
        help="Override source type when Crossref matches the wrong item.",
    )
    parser.add_argument(
        "--title",
        help="Override title. Best used when processing one PDF at a time.",
    )
    parser.add_argument(
        "--year",
        help="Override publication year. Best used when Crossref matches the wrong item.",
    )
    parser.add_argument(
        "--author",
        help="Override first author/editor surname used in the filename.",
    )
    args = parser.parse_args()

    pdfs = args.pdfs or sorted(args.inbox.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {args.inbox}")
        return 0

    if not args.dry_run and not args.in_place:
        args.outbox.mkdir(parents=True, exist_ok=True)

    failures = 0
    for pdf in pdfs:
        try:
            result = process_pdf(
                pdf.expanduser(),
                outbox=args.outbox.expanduser(),
                in_place=args.in_place,
                dry_run=args.dry_run,
                mailto=args.mailto,
                kind=args.kind,
                title=args.title,
                year=args.year,
                author=args.author,
            )
            print(result)
        except Exception as exc:  # noqa: BLE001 - keep batch processing useful.
            failures += 1
            print(f"FAIL {pdf}: {exc}", file=sys.stderr)

    return 1 if failures else 0


def process_pdf(
    pdf: Path,
    *,
    outbox: Path,
    in_place: bool,
    dry_run: bool,
    mailto: str,
    kind: str,
    title: str | None,
    year: str | None,
    author: str | None,
) -> str:
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    if pdf.suffix.lower() != ".pdf":
        raise ValueError("not a PDF")

    text = extract_text(pdf)
    pdf_metadata = extract_pdf_metadata(pdf)
    metadata_text = metadata_to_text(pdf_metadata)
    doi = find_doi(text) or find_doi(metadata_text)
    metadata = crossref_by_doi(doi, mailto) if doi else None

    # Try arXiv ID lookup.
    if metadata is None and text.strip():
        arxiv_id = find_arxiv(text)
        if arxiv_id:
            metadata = crossref_by_arxiv(arxiv_id, mailto)

    # Try ISBN lookup — especially useful for books and book chapters.
    if metadata is None and text.strip():
        isbn = find_isbn(text)
        if isbn:
            metadata = crossref_by_isbn(isbn, mailto)

    if metadata is None and text.strip():
        title_guess = guess_title(text)
        if title_guess:
            author_guess = guess_author(text)
            metadata = crossref_by_title(
                title_guess, mailto, author=author_guess
            )

    if metadata is None:
        if pdf_metadata:
            # Embedded XMP titles can be filename placeholders
            # (e.g. "Times LT 27 X 42").  Try Crossref, but only when
            # the title looks plausible and the result is consistent.
            pdf_title = first_value(pdf_metadata.get("title"))
            if pdf_title and not is_placeholder_title(pdf_title):
                candidate = crossref_by_title(pdf_title, mailto)
                if candidate and titles_match(
                    pdf_title,
                    first_value(candidate.get("title")) or "",
                ):
                    metadata = candidate
            # Only fall back to bare pdf_metadata when the PDF also
            # carries author / DOI info that gives us confidence.
            if metadata is None:
                if pdf_metadata.get("author") or pdf_metadata.get("DOI"):
                    metadata = pdf_metadata
                elif not title:
                    raise ValueError(
                        f'embedded PDF title "{pdf_title}" could not be '
                        f"verified via Crossref and no author/DOI was found; "
                        f"use --title/--author/--year"
                    )
                else:
                    metadata = manual_metadata(
                        title=title, author=author, year=year, kind=kind
                    )
        elif is_already_renamed(pdf):
            new_name = pdf.name
            destination = (pdf.parent if in_place else outbox) / new_name
            destination = unique_path(destination)
            if dry_run:
                return f"DRY {pdf.name} -> {destination}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pdf), str(destination))
            return f"OK  {pdf.name} -> {destination}"
        elif not title:
            if text.strip() and not has_extractable_text(text):
                raise ValueError(
                    "PDF appears to be a scanned document (no extractable text). "
                    "Try OCR, or use --title/--author/--year/--kind"
                )
            raise ValueError(
                "could not identify metadata; use --kind/--author/--year/--title"
            )
        else:
            metadata = manual_metadata(title=title, author=author, year=year, kind=kind)

    inferred_kind = infer_kind(pdf, metadata, text, forced_kind=kind)
    # Only use heuristic guesses when Crossref didn't already give us the answer.
    _book_year = (
        book_year_guess(text)
        if inferred_kind == "book" and not publication_year(metadata)
        else None
    )
    title_override = title or (
        book_title_guess(text)
        if inferred_kind == "book" and not first_value(metadata.get("title"))
        else None
    )
    new_name = build_filename(
        metadata,
        suffix=supplement_suffix(pdf, metadata, text),
        kind=inferred_kind,
        title_override=title_override,
        year_override=year or _book_year,
        author_override=author,
    )
    destination = (pdf.parent if in_place else outbox) / new_name
    destination = unique_path(destination)

    if dry_run:
        return f"DRY {pdf.name} -> {destination}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pdf), str(destination))
    return f"OK  {pdf.name} -> {destination}"


def is_already_renamed(pdf: Path) -> bool:
    return bool(RENAMED_FILE_RE.match(pdf.stem))


def extract_text(pdf: Path) -> str:
    parts: list[str] = []

    for command in (
        ["pdftotext", "-f", "1", "-l", "5", "-layout", str(pdf), "-"],
        ["mutool", "draw", "-F", "txt", "-o", "-", str(pdf), "1-5"],
    ):
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if completed.stdout.strip():
            parts.append(completed.stdout)
            break

    if not parts:
        # Fall back to pure-Python extraction — works without external tools.
        py_text = extract_text_python(pdf)
        if py_text:
            parts.append(py_text)

    return "\n".join(parts)


def extract_text_python(pdf: Path) -> str:
    """Extract readable text from a PDF using only the Python standard library.

    Handles FlateDecode-compressed content streams and basic BT/ET text
    blocks.  Font-size heuristics are used to de-duplicate running headers
    and prioritise title-page content.
    """
    import zlib

    try:
        raw = pdf.read_bytes()
    except OSError:
        return ""

    # Collect all decompressed content streams with their font resources.
    # ------------------------------------------------------------------
    # Find objects and cross-reference tables aren't fully parsed; we
    # scan for ``stream … endstream`` blocks and attempt to inflate them.
    text_blocks: list[tuple[float, str]] = []  # (font_size, text)

    # Crude scan: every FlateDecode stream that might contain text.
    for match in re.finditer(
        rb"/Filter\s+/FlateDecode.*?>>\s*stream\s+",
        raw,
        re.DOTALL,
    ):
        stream_start = match.end()
        # Find the matching endstream — naive, but works for well-formed PDFs.
        end = raw.find(b"endstream", stream_start)
        if end < 0:
            continue
        try:
            decompressed = zlib.decompress(raw[stream_start:end])
        except zlib.error:
            continue

        # Extract font-size info from the surrounding graphics state.
        font_sizes: list[float] = []
        for fm in re.finditer(
            rb"/([A-Za-z0-9_]+)\s+(\d+(?:\.\d+)?)\s+Tf",
            raw[max(0, match.start() - 2000) : match.start()],
        ):
            try:
                font_sizes.append(float(fm.group(2)))
            except ValueError:
                pass
        default_size = font_sizes[-1] if font_sizes else 12.0

        # Extract text from BT … ET blocks.
        for tm in re.finditer(rb"BT(.*?)ET", decompressed, re.DOTALL):
            block = tm.group(1)
            # Extract strings inside parentheses (Tj operator).
            tj_text: list[str] = []
            for sm in re.finditer(rb"\((.*?)\)\s*Tj", block):
                tj_text.append(sm.group(1).decode("latin-1", errors="ignore"))
            if tj_text:
                text_blocks.append((default_size, " ".join(tj_text)))

    if not text_blocks:
        return ""

    # Sort by font size descending — the largest text is usually the title.
    text_blocks.sort(key=lambda x: x[0], reverse=True)

    # Keep lines with the largest font (title page), plus one size down.
    seen: set[str] = set()
    lines: list[str] = []
    max_size = text_blocks[0][0]
    for size, text in text_blocks:
        if size < max_size * 0.7:  # skip body text / footnotes
            continue
        stripped = SPACE_RE.sub(" ", text).strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            lines.append(stripped)

    return "\n".join(lines)


def read_pdf_bytes(pdf: Path, limit: int = 5_000_000) -> str:
    data = pdf.read_bytes()[:limit]
    return data.decode("latin-1", errors="ignore")


def extract_pdf_metadata(pdf: Path) -> dict[str, Any]:
    raw = pdf.read_bytes().decode("latin-1", errors="ignore")
    metadata: dict[str, Any] = {}

    title = first_regex(
        raw,
        (
            r"<dc:title>.*?<rdf:li[^>]*>(.*?)</rdf:li>.*?</dc:title>",
            r"/Title\s*\((.*?)\)",
        ),
    )
    author = first_regex(
        raw,
        (
            r"<dc:creator>.*?<rdf:li[^>]*>(.*?)</rdf:li>.*?</dc:creator>",
            r"/Author\s*\((.*?)\)",
        ),
    )
    subject = first_regex(raw, (r"/Subject\s*\((.*?)\)",))
    year = first_regex(raw, (r"/CreationDate\s*\(D:((?:19|20)\d{2})",))

    if title:
        title = clean_pdf_literal(title)
        if not is_generic_pdf_title(title):
            metadata["title"] = [title]
    if author:
        author_parts = WORD_RE.findall(clean_pdf_literal(author))
        if author_parts and any(ch.isalpha() for ch in author_parts[-1]):
            metadata["author"] = [{"family": author_parts[-1]}]
    if year:
        metadata["issued"] = {"date-parts": [[int(year)]]}
    if subject:
        subject = clean_pdf_literal(subject)
        if "journal on computing" in subject.lower():
            metadata["container-title"] = ["INFORMS Journal on Computing"]
        else:
            metadata["container-title"] = [subject]
    if "title" not in metadata:
        return {}
    if metadata:
        metadata.setdefault("type", "journal-article")
    return metadata


def first_regex(raw: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    return None


def clean_pdf_literal(value: str) -> str:
    value = value.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    value = re.sub(r"<[^>]+>", " ", value)
    return SPACE_RE.sub(" ", value).strip()


def is_generic_pdf_title(value: str) -> bool:
    return normalize_journal(value) in {
        "bibliography",
        "contents",
        "index",
        "preface",
        "references",
    }


def metadata_to_text(metadata: dict[str, Any]) -> str:
    parts = [
        first_value(metadata.get("title")) or "",
        first_value(metadata.get("container-title")) or "",
    ]
    return "\n".join(parts)


def find_doi(text: str) -> str | None:
    match = DOI_RE.search(text)
    if not match:
        return None
    doi = match.group(0).strip()
    return doi.rstrip(".,;:)]}>").lower()


ISBN_RE = re.compile(
    r"\b(?:ISBN(?:-1[03])?[:\s]*)?((?:97[89])?[\d\-]{10,17})\b",
    re.IGNORECASE,
)
ARXIV_RE = re.compile(
    r"\barXiv[:\s]*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.IGNORECASE
)


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

    A single ISBN can match many chapters; we prefer the item whose type
    is ``book``, ``monograph`` or ``edited-book``.
    """
    params = urllib.parse.urlencode({"filter": f"isbn:{isbn}", "rows": "20"})
    payload = fetch_json(
        f"https://api.crossref.org/works?{params}", mailto
    )
    items = payload.get("message", {}).get("items", []) if payload else []
    if not items:
        return None
    # Validate: result must actually contain the ISBN we searched for.
    valid: list[dict[str, Any]] = []
    for item in items:
        item_isbns = item.get("isbn") or item.get("ISBN") or []
        if isinstance(item_isbns, str):
            item_isbns = [item_isbns]
        # Match either exact ISBN or cleaned digits.
        if isbn in item_isbns or any(
            re.sub(r"[^0-9X]", "", str(i).upper()) == isbn for i in item_isbns
        ):
            valid.append(item)
    if not valid:
        return None  # no result actually carries this ISBN — untrustworthy
    # Prefer the book-level record (not individual chapters).
    book_types = {"book", "monograph", "edited-book", "reference-book"}
    for item in valid:
        if str(item.get("type") or "").lower() in book_types:
            return item
    # Fall back to an item whose title matches its container-title (book).
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
    # Strip version suffix for lookup (2103.12345v2 → 2103.12345).
    return match.group(1).rstrip("v").split("v")[0]


def crossref_by_arxiv(arxiv_id: str, mailto: str) -> dict[str, Any] | None:
    """Look up an arXiv ID via Crossref."""
    params = urllib.parse.urlencode(
        {"filter": f"arxiv:{arxiv_id}", "rows": "1"}
    )
    payload = fetch_json(
        f"https://api.crossref.org/works?{params}", mailto
    )
    items = payload.get("message", {}).get("items", []) if payload else []
    return items[0] if items else None


def has_extractable_text(text: str) -> bool:
    """Return True when *text* contains enough substance to identify a paper.

    A scanned/OCR-less PDF yields essentially no alphabetic content.
    """
    alpha = sum(ch.isalpha() for ch in text)
    return alpha >= 30


def guess_title(text: str) -> str | None:
    # Replace form-feeds and other control chars that splitlines would treat
    # as line breaks (they fragment short titles like "Integer Programming").
    text = re.sub(r"[\x0b\x0c\x1c\x1d\x1e]", " ", text)
    lines = []
    for raw_line in text.splitlines()[:80]:
        line = SPACE_RE.sub(" ", raw_line).strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith(
            (
                "abstract", "keywords", "doi", "arxiv", "copyright", "isbn",
                "received:", "accepted:", "published:", "communicated by",
                "full length paper", "original paper", "research article",
                "research paper", "short communication", "technical note",
                "review article", "letter to the editor",
            )
        ):
            continue
        # Skip lines that look like journal masthead / running header.
        if re.match(
            r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\s*\(\d{4}\)\s*\d+:\d+[\-–]\d+$",
            line,
        ):
            continue  # e.g. "Mathematical Programming (2013) 141:507–526"
        # Accept titles as short as 10 chars (e.g. "Integer Programming" = 19).
        if len(line) < 10 or len(line) > 250:
            continue
        # At least 50 % alphabetic characters.
        alpha = sum(ch.isalpha() for ch in line)
        if alpha < 8 or alpha / len(line) < 0.5:
            continue
        lines.append(line)

    if not lines:
        return None

    # The title is usually the first substantial line, but some PDFs split it
    # across two lines.  Only glue when the second line doesn't look like an
    # author affiliation or a running header repeat.
    title = lines[0]
    if len(title) < 55 and len(lines) > 1:
        second = lines[1].strip()
        if (
            not looks_like_author_line(second)
            and second.lower() != title.lower()
        ):
            title = f"{title} {second}"
    return title


def looks_like_author_line(line: str) -> bool:
    lowered = line.lower()
    if any(token in lowered for token in ("university", "department", "institute")):
        return True
    # Initials pattern like "Laurence A. Wolsey" or "J. P. Smith".
    if re.search(r"\b[A-Z]\.(?:\s+[A-Z]|$)", line):
        return True
    # Comma-separated list of names (typical author line).
    return "," in line and len(line.split()) < 18


def is_placeholder_title(title: str) -> bool:
    """Return True when *title* looks like a filename or placeholder rather
    than a real paper title.

    Catches cases like ``Times LT 27 X 42`` that were embedded in XMP
    metadata by a PDF creation tool.
    """
    words = WORD_RE.findall(title)
    if not words:
        return True
    alpha_words = [w for w in words if w.isalpha()]
    num_words = [w for w in words if w.isdigit()]
    non_year_nums = [w for w in num_words if not (1900 <= int(w) <= 2099)]

    # Fewer than 3 real words is suspicious.
    if len(alpha_words) < 3:
        return True

    # Many isolated numbers that aren't years suggests a filename.
    if len(non_year_nums) >= 2 and len(alpha_words) < 6:
        return True

    # Alpha ratio below 55 % — mostly digits / symbols.
    alpha_chars = sum(ch.isalpha() for ch in title)
    if len(title) > 0 and alpha_chars / len(title) < 0.55:
        return True

    return False


def titles_match(query: str, result: str) -> bool:
    """Return True when *result* title is broadly consistent with *query*.

    Crossref ``query.title`` does loose matching so a garbage query like
    ``Times LT 27 X 42`` can match an unrelated work that happens to share
    one word.  We require at least two overlapping words, at least 50 %
    of the smaller word-set to be shared, and a reasonable sequence-match
    ratio (LCS distance, as used by Zotero's PDF recognizer).
    """
    import difflib

    query_words = {w.lower() for w in WORD_RE.findall(query) if len(w) >= 2}
    result_words = {w.lower() for w in WORD_RE.findall(result) if len(w) >= 2}
    if not query_words or not result_words:
        return False
    overlap = query_words & result_words
    if len(overlap) < 2:
        return False
    if len(overlap) / min(len(query_words), len(result_words)) < 0.5:
        return False

    # Sequence-matcher sanity check: the strings should look broadly alike.
    ratio = difflib.SequenceMatcher(
        None, query.lower(), result.lower()
    ).ratio()
    return ratio >= 0.25


def crossref_by_doi(doi: str | None, mailto: str) -> dict[str, Any] | None:
    if not doi:
        return None
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
    payload = fetch_json(url, mailto)
    return payload.get("message") if payload else None


def crossref_by_title(
    title: str, mailto: str, *, author: str | None = None
) -> dict[str, Any] | None:
    """Search Crossref by *title*.  When *author* is given, request multiple
    results and return the first whose author list contains *author*."""
    params = {
        "query.title": title,
        "rows": "5" if author else "1",
        "select": ",".join(
            (
                "DOI",
                "title",
                "author",
                "issued",
                "published-print",
                "published-online",
                "container-title",
                "short-container-title",
                "type",
                "ISBN",
                "publisher",
            )
        ),
    }
    payload = fetch_json(
        f"https://api.crossref.org/works?{urllib.parse.urlencode(params)}", mailto
    )
    items = payload.get("message", {}).get("items", []) if payload else []
    if not items:
        return None
    if author:
        # Filter to the first result that includes *author*.
        author_lower = author.lower()
        for item in items:
            for a in item.get("author") or []:
                family = (a.get("family") or "").lower()
                given = (a.get("given") or "").lower()
                if author_lower in family or author_lower in given:
                    return item
        # No author match — fall back to first result anyway.
        return items[0]
    return items[0]


def guess_author(text: str) -> str | None:
    """Try to extract an author surname from the first page of *text*.

    Heuristic: after the title, look for a line that looks like an author
    name (contains an initial like ``A.`` or a comma-separated list).
    """
    text = re.sub(r"[\x0b\x0c\x1c\x1d\x1e]", " ", text)
    lines = [
        SPACE_RE.sub(" ", ln).strip()
        for ln in text.splitlines()[:40]
    ]
    # Find the first author-like line after the first few lines.
    for i, line in enumerate(lines[2:], start=2):
        if not line or len(line) < 5:
            continue
        # Author line with initial: "Laurence A. Wolsey"
        if re.search(r"\b[A-Z]\.(?:\s+[A-Z]|$)", line):
            # Return the surname (last word).
            words = WORD_RE.findall(line)
            if words:
                return words[-1]
        # Comma-separated: "Smith, J., Jones, P."
        if "," in line and len(line.split()) < 18:
            words = WORD_RE.findall(line.split(",")[0])
            if words:
                return words[0]
    return None


def fetch_json(url: str, mailto: str) -> dict[str, Any] | None:
    agent = f"renamepapers/1.0 (mailto:{mailto})" if mailto else USER_AGENT
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


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
    title = title_override or first_value(metadata.get("title")) or metadata.get("DOI") or "Untitled"
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
    elif kind == "journal":
        metadata["type"] = "journal-article"
    return metadata


def source_prefix(metadata: dict[str, Any], kind: str | None = None) -> str:
    if kind == "book":
        return "Book"
    if kind == "bookchapter":
        return "BookChapter"
    if kind == "journal":
        return journal_abbrev(metadata) or "UnknownJournal"

    item_type = str(metadata.get("type") or "").lower()
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
    if item_type in {"book", "monograph", "reference-book", "book-series"}:
        return "book"
    if item_type in {
        "book-chapter",
        "book-part",
        "reference-entry",
        "book-section",
    }:
        return "bookchapter"
    # Crossref already told us it's a journal article — trust it.
    if item_type in {"journal-article", "proceedings-article", "journal-issue"}:
        return None

    # Only use text heuristics as a last resort when Crossref type is
    # ambiguous ("other", "posted-content", or missing entirely).
    search_text = re.sub(r"[_\-.]+", " ", "\n".join((pdf.stem, text[:20_000])))
    if BOOK_EVIDENCE_RE.search(search_text):
        return "book"

    return None


def book_title_guess(text: str) -> str | None:
    lines: list[str] = []
    for raw_line in text.splitlines()[:60]:
        line = SPACE_RE.sub(" ", raw_line).strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith(("isbn", "copyright", "contents", "preface", "chapter")):
            continue
        if any(
            token in lowered
            for token in (
                "university press",
                "springer",
                "wiley",
                "elsevier",
                "all rights reserved",
            )
        ):
            continue
        if 4 <= len(line) <= 100 and sum(ch.isalpha() for ch in line) >= 4:
            lines.append(line)

    for line in lines:
        words = WORD_RE.findall(line)
        if 1 <= len(words) <= 8 and not looks_like_author_line(line):
            return line
    return None


def book_year_guess(text: str) -> str | None:
    head = "\n".join(text.splitlines()[:100])
    candidates = [
        int(match.group(0))
        for match in re.finditer(r"\b(?:19|20)\d{2}\b", head)
        if 1900 <= int(match.group(0)) <= 2099
    ]
    if not candidates:
        return None
    # Return the *most recent* year — multi-edition books often mention
    # the first-edition year in the front matter, but the PDF is the
    # current edition.
    return str(max(candidates))


def supplement_suffix(pdf: Path, metadata: dict[str, Any], text: str) -> str:
    title = first_value(metadata.get("title")) or ""
    search_text = "\n".join((pdf.stem, title, text[:10_000]))
    search_text = re.sub(r"[_\-.]+", " ", search_text)
    if SUPPLEMENT_RE.search(search_text):
        return "_supplement"
    return ""


def journal_abbrev(metadata: dict[str, Any]) -> str | None:
    short_title = first_value(metadata.get("short-container-title"))
    container_title = first_value(metadata.get("container-title"))

    for candidate in (short_title, container_title):
        if candidate and (alias := journal_alias(candidate)):
            return alias

    if short_title and looks_like_abbreviated_journal(short_title):
        # "Math. Program." → initials give "MP", which is the standard abbrev.
        return journal_initials(short_title) or clean_journal(short_title)

    if container_title:
        return journal_initials(container_title) or clean_journal(container_title)

    if short_title:
        return journal_initials(short_title) or clean_journal(short_title)

    return None


def journal_alias(value: str) -> str | None:
    return JOURNAL_ALIASES.get(normalize_journal(value))


def normalize_journal(value: str) -> str:
    value = value.replace("&", " and ")
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).lower()
    return SPACE_RE.sub(" ", value).strip()


def looks_like_abbreviated_journal(value: str) -> bool:
    clean = clean_journal(value)
    return "." in value or clean.isupper() or len(clean) <= 10


def journal_initials(value: str) -> str | None:
    stop = {"and", "for", "in", "of", "on", "the"}
    initials = [
        word[0].upper()
        for word in WORD_RE.findall(value)
        if word.lower() not in stop and not word.isdigit()
    ]
    return "".join(initials[:8]) or None


def first_author(metadata: dict[str, Any]) -> str | None:
    authors = metadata.get("author") or []
    if not authors:
        return None
    first = authors[0]
    name = first.get("family") or first.get("name") or first.get("given")
    if not name:
        return None
    return clean_token(name, max_words=1) or None


def publication_year(metadata: dict[str, Any]) -> str | None:
    for key in ("issued", "published-print", "published-online"):
        date_parts = metadata.get(key, {}).get("date-parts")
        if date_parts and date_parts[0]:
            return str(date_parts[0][0])
    return None


def clean_year(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(?:19|20)\d{2}\b", value)
    return match.group(0) if match else None


def shorten_title(title: str, max_chars: int = 80) -> str:
    words = WORD_RE.findall(title)
    kept: list[str] = []
    for word in words:
        if not any(ch.isalnum() for ch in word):
            continue
        kept.append(word[:1].upper() + word[1:])

    compact = "_".join(kept) or "Untitled"
    return compact[:max_chars].rstrip("_-")


def first_value(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None


def clean_journal(value: str) -> str:
    words = WORD_RE.findall(value)
    return "".join(word[:1].upper() + word[1:] for word in words)[:32]


def clean_token(value: str, max_words: int) -> str:
    words = WORD_RE.findall(value)[:max_words]
    return "".join(word[:1].upper() + word[1:] for word in words)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


if __name__ == "__main__":
    raise SystemExit(main())
