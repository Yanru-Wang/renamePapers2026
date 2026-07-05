#!/usr/bin/env python3
"""Rename scientific PDFs as JournalAbbrev_AuthorYear_ShortTitle.pdf.

The script is intentionally dependency-light. It uses DOI metadata when possible,
falls back to a title search, and moves successful renames to ~/Papers/Renamed by
default so ~/Papers/Inbox stays as the intake queue.
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .common import (
    SPACE_RE,
    WORD_RE,
    first_value,
    titles_match,
)
from .files import move_or_deduplicate
from .naming import (
    build_filename,
    infer_kind,
    manual_metadata,
    normalize_journal,
    publication_year,
)
from .providers import (
    DEFAULT_MAILTO,
    crossref_by_arxiv,
    crossref_by_doi,
    crossref_by_isbn,
    crossref_by_title,
    find_arxiv,
    find_doi,
    find_isbn,
)


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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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
        choices=("auto", "journal", "book", "bookchapter", "thesis"),
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


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


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
    if not has_extractable_text(text) and (not pdf_metadata or has_placeholder_pdf_metadata(pdf)):
        ocr_text = extract_ocr_text(pdf)
        if ocr_text.strip():
            text = ocr_text

    metadata_text = metadata_to_text(pdf_metadata)
    doi = find_doi(text) or find_doi(metadata_text)
    metadata = crossref_by_doi(doi, mailto) if doi else None

    # Try arXiv ID lookup.
    if metadata is None and text.strip():
        arxiv_id = find_arxiv(text)
        if arxiv_id:
            metadata = crossref_by_arxiv(arxiv_id, mailto)
            if metadata is None:
                metadata = arxiv_metadata_from_text(text)

    # Try ISBN lookup — especially useful for books and book chapters.
    if metadata is None and text.strip():
        isbn = find_isbn(text)
        if isbn:
            metadata = crossref_by_isbn(isbn, mailto)

    if metadata is None and text.strip():
        metadata = handbook_chapter_metadata_from_text(text)

    if metadata is None and text.strip():
        metadata = thesis_metadata_from_text(text)

    if metadata is None and text.strip():
        metadata = preprint_metadata_from_text(text)

    if metadata is None and text.strip():
        metadata = article_metadata_from_text(text)

    if metadata is None and text.strip():
        metadata = book_metadata_from_text(text)

    if metadata is None and text.strip():
        main_title = _extract_main_title_from_supplement(text)
        if main_title:
            metadata = crossref_by_title(
                main_title,
                mailto,
                author=guess_author(text),
            )

    if metadata is None and text.strip():
        title_guess = guess_title(text)
        if title_guess:
            author_guess = guess_author(text)
            metadata = crossref_by_title(
                title_guess, mailto, author=author_guess
            )
            if metadata and crossref_result_conflicts_with_journal_text(metadata, text):
                metadata = None

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
                ) and not crossref_result_conflicts_with_journal_text(candidate, text):
                    metadata = candidate
            # Only fall back to bare pdf_metadata when the PDF also
            # carries author / DOI info, or the embedded title is confirmed
            # by first-page text and can be enriched from the masthead.
            if metadata is None:
                enrich_metadata_from_text(pdf_metadata, text)
                if (
                    pdf_metadata.get("author")
                    or pdf_metadata.get("DOI")
                    or metadata_title_appears_in_text(pdf_metadata, text)
                ):
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
        elif is_already_renamed(pdf) and not has_placeholder_pdf_metadata(pdf):
            new_name = pdf.name
            destination = (pdf.parent if in_place else outbox) / new_name
            if in_place and destination.resolve() == pdf.resolve():
                return f"OK  {pdf.name} -> {destination}"
            return move_or_deduplicate(pdf, destination, dry_run=dry_run)
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
    suffix = supplement_suffix(pdf, metadata, text)
    if suffix:
        resolved = resolve_supplement_metadata(metadata, pdf, text, outbox, mailto)
        if resolved:
            metadata = resolved
            _book_year = None
            title_override = None
    new_name = build_filename(
        metadata,
        suffix=suffix,
        kind=inferred_kind,
        title_override=title_override,
        year_override=year or _book_year,
        author_override=author,
    )
    destination = (pdf.parent if in_place else outbox) / new_name
    return move_or_deduplicate(pdf, destination, dry_run=dry_run)


def is_already_renamed(pdf: Path) -> bool:
    return bool(RENAMED_FILE_RE.match(pdf.stem))


# ---------------------------------------------------------------------------
# Text, OCR, and embedded PDF metadata extraction
# ---------------------------------------------------------------------------


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


def extract_ocr_text(pdf: Path, *, first_page: int = 1, last_page: int = 2) -> str:
    """OCR scanned title pages using Poppler and Tesseract when available."""
    try:
        with tempfile.TemporaryDirectory(prefix="renamepapers_ocr_") as tmp:
            prefix = Path(tmp) / "page"
            render = subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(first_page),
                    "-l",
                    str(last_page),
                    "-png",
                    "-r",
                    "180",
                    str(pdf),
                    str(prefix),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if render.returncode != 0:
                return ""

            parts: list[str] = []
            for image in sorted(Path(tmp).glob("page-*.png")):
                completed = subprocess.run(
                    ["tesseract", str(image), "stdout", "--psm", "6"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    parts.append(completed.stdout)
            return "\n\f\n".join(parts)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


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
        if not is_generic_pdf_title(title) and not is_placeholder_title(title):
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


def has_placeholder_pdf_metadata(pdf: Path) -> bool:
    raw = pdf.read_bytes().decode("latin-1", errors="ignore")
    title = first_regex(
        raw,
        (
            r"<dc:title>.*?<rdf:li[^>]*>(.*?)</rdf:li>.*?</dc:title>",
            r"/Title\s*\((.*?)\)",
        ),
    )
    if not title:
        return False
    title = clean_pdf_literal(title)
    return is_generic_pdf_title(title) or is_placeholder_title(title)


def first_regex(raw: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    return None


def clean_pdf_literal(value: str) -> str:
    value = value.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    value = re.sub(r"&#x\\?\s*([0-9A-Fa-f]+);", r"&#x\1;", value)
    value = re.sub(r"&#\\?\s*([0-9]+);", r"&#\1;", value)
    value = html.unescape(value)
    value = value.replace("\u2013", " - ").replace("\u2014", " - ")
    value = re.sub(r"<[^>]+>", " ", value)
    return SPACE_RE.sub(" ", value).strip()


def is_generic_pdf_title(value: str) -> bool:
    lowered = value.lower().strip()
    if lowered.endswith((".eps", ".ps", ".ai")) or "logo" in lowered:
        return True
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


def has_extractable_text(text: str) -> bool:
    """Return True when *text* contains enough substance to identify a paper.

    A scanned/OCR-less PDF yields essentially no alphabetic content.
    """
    alpha = sum(ch.isalpha() for ch in text)
    return alpha >= 30


# ---------------------------------------------------------------------------
# Generic title, author, and metadata confidence helpers
# ---------------------------------------------------------------------------


def guess_title(text: str) -> str | None:
    # Replace form-feeds and other control chars that splitlines would treat
    # as line breaks (they fragment short titles like "Integer Programming").
    text = re.sub(r"[\x0b\x0c\x1c\x1d\x1e]", " ", text)
    lines = []
    for raw_line in text.splitlines()[:80]:
        line = collapse_spaced_letters(SPACE_RE.sub(" ", raw_line).strip())
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
                "submitted to",
                "online appendices for",
                "online appendix for",
                "supplementary material for",
            )
        ):
            continue
        # Skip lines that look like journal masthead / running header.
        if re.match(
            r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\s*\(\d{4}\)\s*\d+:\d+[\-–]\d+$",
            line,
        ):
            continue  # e.g. "Mathematical Programming (2013) 141:507–526"
        if re.match(
            r"^[A-Z][A-Za-z& ]{2,80}\s+\d+\s*\((?:19|20)\d{2}\)\s+\d+[\-–]\d+\.?$",
            line,
        ):
            continue  # e.g. "Mathematical Programming 14 (1978) 265-294."
        if re.search(
            r"\b(?:north-holland|publishing company|springer|elsevier)\b",
            lower,
        ):
            continue
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
    lowered = title.lower()
    if "pdflib image sample" in lowered or (
        lowered.startswith("adopted from") and "image sample" in lowered
    ):
        return True

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


def enrich_metadata_from_text(metadata: dict[str, Any], text: str) -> None:
    """Fill missing PDF metadata from a first-page journal masthead."""
    match = journal_masthead_match(text)
    if match:
        metadata.setdefault(
            "container-title",
            [SPACE_RE.sub(" ", match.group(1)).strip()],
        )
        metadata.setdefault("issued", {"date-parts": [[int(match.group(2))]]})

    if "author" not in metadata:
        author = guess_author(text)
        if author:
            metadata["author"] = [{"family": author}]


def metadata_title_appears_in_text(metadata: dict[str, Any], text: str) -> bool:
    title = first_value(metadata.get("title")) or ""
    if not title:
        return False
    title_words = {w.lower() for w in WORD_RE.findall(title) if len(w) >= 3}
    text_words = {w.lower() for w in WORD_RE.findall(text[:5000]) if len(w) >= 3}
    if not title_words:
        return False
    return len(title_words & text_words) / len(title_words) >= 0.6


# ---------------------------------------------------------------------------
# Source-specific parsers
# ---------------------------------------------------------------------------


def thesis_metadata_from_text(text: str) -> dict[str, Any] | None:
    first_page = text.split("\f", 1)[0]
    lower_page = first_page.lower()
    if not any(
        marker in lower_page
        for marker in (
            "doctor of philosophy",
            "submitted in partial fulfillment",
            "thesis supervisor",
            "dissertation",
        )
    ):
        return None

    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]
    if not lines:
        return None

    by_index = next((i for i, line in enumerate(lines[:20]) if line.lower() == "by"), -1)
    if by_index <= 0:
        return None

    title_lines: list[str] = []
    for line in lines[:by_index]:
        lowered = line.lower()
        if lowered.startswith(("library", "copyright", "@")):
            continue
        words = WORD_RE.findall(line)
        if 2 <= len(words) <= 16 and sum(ch.isalpha() for ch in line) >= 10:
            title_lines.append(line)
    title = SPACE_RE.sub(" ", " ".join(title_lines)).strip()
    if not title:
        return None

    author_line = None
    for line in lines[by_index + 1 : by_index + 5]:
        if re.search(r"\b[A-Z]\.\s*[A-Z]", line) or line.isupper():
            author_line = line
            break
    if not author_line:
        return None
    author_words = WORD_RE.findall(author_line)
    if not author_words:
        return None

    year_match = re.search(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\s+((?:19|20)\d{2})\b",
        first_page,
        flags=re.IGNORECASE,
    ) or re.search(r"\b((?:19|20)\d{2})\b", first_page)

    metadata: dict[str, Any] = {
        "title": [title],
        "author": [{"family": author_words[-1]}],
        "type": "thesis",
    }
    if year_match:
        metadata["issued"] = {"date-parts": [[int(year_match.group(1))]]}
    return metadata


def article_metadata_from_text(text: str) -> dict[str, Any] | None:
    if jstor_meta := jstor_article_metadata_from_text(text):
        return jstor_meta
    if siam_meta := siam_article_metadata_from_text(text):
        return siam_meta
    if not looks_like_journal_article_text(text):
        return None
    first_page = text.split("\f", 1)[0]
    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]
    title = journal_article_title_from_lines(lines)
    if not title:
        return None

    metadata: dict[str, Any] = {
        "title": [title],
        "type": "journal-article",
    }
    if doi := find_doi(first_page):
        metadata["DOI"] = doi
    if journal := journal_name_from_article_lines(lines):
        metadata["container-title"] = [journal]
    if year := article_year_from_text(first_page):
        metadata["issued"] = {"date-parts": [[int(year)]]}
    if author := journal_article_author_from_lines(lines, title):
        metadata["author"] = [{"family": author}]
    return metadata


def arxiv_metadata_from_text(text: str) -> dict[str, Any] | None:
    first_page = text.split("\f", 1)[0]
    arxiv_id = find_arxiv(first_page)
    if not arxiv_id:
        return None

    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]

    arxiv_line_index = next(
        (i for i, line in enumerate(lines) if "arxiv:" in line.lower()),
        -1,
    )

    title_lines: list[str] = []
    for line in lines[: max(arxiv_line_index, 12)]:
        lower = line.lower()
        if (
            lower.startswith("arxiv:")
            or lower.startswith("journal of latex class files")
            or re.search(r"\b\d{4}\s+[a-z]{3,9}\s+\d{4}\b", lower)
        ):
            continue
        if looks_like_arxiv_author_line(line):
            break
        if (
            3 <= len(WORD_RE.findall(line)) <= 16
            and sum(ch.isalpha() for ch in line) >= 12
        ):
            title_lines.append(line)

    title = " ".join(title_lines).strip()
    if not title:
        title = guess_title(text) or ""
    if not title:
        return None

    metadata: dict[str, Any] = {
        "title": [title],
        "type": "posted-content",
        "container-title": ["arXiv"],
        "DOI": f"arXiv:{arxiv_id}",
    }

    for line in lines[:30]:
        if looks_like_arxiv_author_line(line):
            first_author = re.split(r"\s+and\s+|,", line, maxsplit=1)[0]
            first_author = re.sub(r"[*†‡§]+\d*", " ", first_author)
            words = [w for w in WORD_RE.findall(first_author) if not w.isdigit()]
            if words:
                metadata["author"] = [{"family": words[-1]}]
                break

    year = arxiv_year(arxiv_id)
    if year is None:
        for line in lines[:20]:
            match = re.search(r"\b((?:19|20)\d{2})\b", line)
            if match:
                year = match.group(1)
                break
    if year:
        metadata["issued"] = {"date-parts": [[int(year)]]}

    return metadata


def preprint_metadata_from_text(text: str) -> dict[str, Any] | None:
    first_page = text.split("\f", 1)[0]
    marker = re.search(
        r"(?im)^Preprint submitted to\s*(.*?)\s*"
        r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+\d{1,2},\s+((?:19|20)\d{2}))\s*$",
        first_page,
    )
    if not marker:
        return None

    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]
    marker_index = next(
        (
            i
            for i, line in enumerate(lines)
            if line.lower().startswith("preprint submitted to")
        ),
        len(lines),
    )

    title_lines: list[str] = []
    for line in lines[:marker_index]:
        lower = line.lower()
        if lower.startswith(("abstract", "keywords")):
            break
        if (
            "@" in line
            or re.search(
                r"\b(?:university|faculty|department|institute|email addresses?)\b",
                lower,
            )
            or looks_like_preprint_author_line(line)
        ):
            if title_lines:
                break
            continue
        words = WORD_RE.findall(line)
        if 2 <= len(words) <= 18 and sum(ch.isalpha() for ch in line) >= 10:
            title_lines.append(line)

    title = SPACE_RE.sub(" ", " ".join(title_lines)).strip()
    if not title:
        return None

    venue = SPACE_RE.sub(" ", marker.group(1)).strip()
    metadata: dict[str, Any] = {
        "title": [title],
        "type": "posted-content",
        "container-title": [venue or "Optimization Online"],
        "issued": {"date-parts": [[int(marker.group(3))]]},
    }
    if author := preprint_author_from_first_page(first_page, lines[:marker_index]):
        metadata["author"] = [{"family": author}]
    return metadata


def looks_like_preprint_author_line(line: str) -> bool:
    if "," not in line and not re.search(r"\b[A-Z]\.", line):
        return False
    words = WORD_RE.findall(line)
    if not (2 <= len(words) <= 14):
        return False
    return bool(re.search(r"\b[A-Z][a-z]+[A-Za-z]*\b", line))


def preprint_author_from_first_page(first_page: str, lines: list[str]) -> str | None:
    paren_names = re.findall(
        r"\(([A-Z][A-Za-z.\- ]+\s+[A-Z][A-Za-z.\- ]+)\)",
        first_page,
    )
    if paren_names:
        words = WORD_RE.findall(paren_names[0])
        if words:
            return words[-1]

    for line in lines:
        if not looks_like_preprint_author_line(line):
            continue
        first_author = re.split(r",|\s+and\s+", line, maxsplit=1)[0]
        first_author = re.sub(r"[*†‡§,]+", " ", first_author)
        words = WORD_RE.findall(first_author)
        if len(words) >= 2:
            surname = words[-1]
            if surname.endswith(("a", "b", "c")) and len(surname) > 3:
                surname = surname[:-1]
            return surname
    return None


def arxiv_year(arxiv_id: str) -> str | None:
    match = re.match(r"^(\d{2})(?:\d{2})?\.", arxiv_id)
    if not match:
        return None
    yy = int(match.group(1))
    return str(1900 + yy if yy >= 91 else 2000 + yy)


def looks_like_arxiv_author_line(line: str) -> bool:
    if "@" in line:
        return False
    words = WORD_RE.findall(line)
    if not (
        "," in line
        or re.search(r"\s+and\s+", line)
        or re.search(r"\b[A-Z]\.", line)
    ):
        return False
    return (
        2 <= len(words) <= 40
        and bool(re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+", line))
        and not line.lower().startswith(("abstract", "keywords", "arxiv"))
    )


def handbook_chapter_metadata_from_text(text: str) -> dict[str, Any] | None:
    first_page = text.split("\f", 1)[0]
    if "Handbooks in OR & MS" not in first_page[:2000]:
        return None

    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]
    title = None
    for i, line in enumerate(lines):
        if re.match(r"^Chapter\s+\d+\b", line, flags=re.IGNORECASE):
            for candidate in lines[i + 1 : i + 5]:
                if len(WORD_RE.findall(candidate)) >= 2:
                    title = candidate
                    break
            break
    if not title:
        return None

    metadata: dict[str, Any] = {
        "title": [title],
        "type": "book-chapter",
    }

    if year_match := re.search(r"\b((?:19|20)\d{2})\b", first_page[:1000]):
        metadata["issued"] = {"date-parts": [[int(year_match.group(1))]]}

    if doi := find_doi(first_page):
        metadata["DOI"] = doi

    title_seen = False
    for line in lines:
        if line == title:
            title_seen = True
            continue
        if not title_seen:
            continue
        if re.search(r"\b(?:Abstract|De|Institut|Department|University|E-mail)\b", line):
            continue
        words = WORD_RE.findall(line)
        if 2 <= len(words) <= 5 and any(ch.islower() for ch in line):
            metadata["author"] = [{"family": words[-1]}]
            break

    return metadata


def siam_article_metadata_from_text(text: str) -> dict[str, Any] | None:
    first_page = text.split("\f", 1)[0]
    if "SIAM J." not in first_page[:3000]:
        return None

    lines = [
        collapse_spaced_letters(SPACE_RE.sub(" ", line).strip())
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]
    head = "\n".join(lines[:30])

    journal = None
    if re.search(r"SIAM J\.?\s+ALG\.?\s+DISC\.?\s+METH\.?", head, re.IGNORECASE):
        journal = "SIAM Journal on Algebraic and Discrete Methods"
    elif re.search(r"SIAM J\..{0,20}MATH", head, re.IGNORECASE):
        journal = "SIAM Journal on Applied Mathematics"
    elif match := re.search(r"(SIAM J\.[^\n]+)", head):
        journal = match.group(1)

    year_match = re.search(r"\b((?:19|20)\d{2})\b", head)

    title_lines: list[str] = []
    for line in lines[:40]:
        lower = line.lower()
        if (
            "siam j." in lower
            or "society for industrial" in lower
            or lower.startswith(("vol.", "downloaded "))
            or re.fullmatch(r"\d{3,}", line)
        ):
            continue
        if title_lines and looks_like_author_line(line):
            break
        if title_lines and lower.startswith("abstract"):
            break
        if (
            sum(ch.isalpha() for ch in line) >= 10
            and len(WORD_RE.findall(line)) >= 3
            and line.upper() == line
        ):
            title_lines.append(line.rstrip("*"))
            continue
        if title_lines:
            break

    title = " ".join(title_lines).title().strip()
    if not title:
        return None

    metadata: dict[str, Any] = {
        "title": [title],
        "type": "journal-article",
    }
    if journal:
        metadata["container-title"] = [journal]
    if year_match:
        metadata["issued"] = {"date-parts": [[int(year_match.group(1))]]}

    title_seen = False
    for line in lines[:50]:
        lower = line.lower()
        if title_lines and line.rstrip("*") == title_lines[-1]:
            title_seen = True
            continue
        if not title_seen:
            continue
        if (
            "society for industrial" in lower
            or lower.startswith(("abstract", "downloaded", "vol."))
        ):
            continue
        cleaned_line = normalize_ocr_author_line(line)
        words = WORD_RE.findall(cleaned_line)
        if (
            2 <= len(words) <= 5
            and all(word.isupper() or len(word) == 1 for word in words)
        ):
            metadata["author"] = [{"family": surname_from_author_line(cleaned_line)}]
            break
        if looks_like_author_line(line):
            if words:
                metadata["author"] = [{"family": surname_from_author_line(cleaned_line)}]
                break

    return metadata


def normalize_ocr_author_line(line: str) -> str:
    line = line.replace('"', " ")
    line = re.sub(r"\bAN[ti]\)?\b", " and ", line, flags=re.IGNORECASE)
    line = re.sub(r"[^A-Za-z.\s-]", " ", line)
    return SPACE_RE.sub(" ", line).strip()


def surname_from_author_line(line: str) -> str:
    first = re.split(r"\s+and\s+|,", line, maxsplit=1, flags=re.IGNORECASE)[0]
    words = [w for w in WORD_RE.findall(first) if len(w) > 1]
    if len(words) >= 2 and words[-2].lower() in {"van", "von", "de", "da", "le"}:
        return format_word(words[-2]) + format_word(words[-1])
    return words[-1] if words else ""



def jstor_article_metadata_from_text(text: str) -> dict[str, Any] | None:
    first_page = text.split("\f", 1)[0]
    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]
    if not any("jstor.org/stable/" in line.lower() for line in lines):
        return None

    title_lines: list[str] = []
    author_line = None
    source_line = None
    for line in lines[:30]:
        lower = line.lower()
        if lower.startswith("author(s):"):
            author_line = line
            if title_lines:
                continue
        elif lower.startswith("source:"):
            source_line = line
        elif author_line is None:
            title_lines.append(line)

    title = " ".join(title_lines).strip()
    if not title or not source_line:
        return None

    metadata: dict[str, Any] = {
        "title": [title],
        "type": "journal-article",
    }

    source = re.sub(r"^Source:\s*", "", source_line, flags=re.IGNORECASE)
    journal = source.split(",", 1)[0].strip()
    if journal:
        metadata["container-title"] = [journal]

    year_match = re.search(r"\b((?:19|20)\d{2})\b", source)
    if year_match:
        metadata["issued"] = {"date-parts": [[int(year_match.group(1))]]}

    if author_line:
        authors = re.sub(r"^Author\(s\):\s*", "", author_line, flags=re.IGNORECASE)
        first = re.split(r",|\s+and\s+", authors, maxsplit=1)[0]
        words = WORD_RE.findall(first)
        if words:
            metadata["author"] = [{"family": words[-1]}]

    return metadata


def looks_like_journal_article_text(text: str) -> bool:
    head = text[:5000]
    lower = head.lower()
    return bool(
        find_doi(head)
        and (
            journal_masthead_match(head)
            or re.search(r"\bvol\.\s*\d+,\s*no\.\s*\d+", lower)
            or re.search(r"\bissn\b.*\beissn\b", lower)
        )
    )


def journal_name_from_article_lines(lines: list[str]) -> str | None:
    head_lines: list[str] = []
    for line in lines[:10]:
        lower = line.lower()
        if lower.startswith("vol.") or "doi " in lower or "issn" in lower:
            break
        cleaned = re.sub(r"\binforms\b|®", "", line, flags=re.IGNORECASE)
        if cleaned.strip():
            head_lines.append(cleaned.strip())
    if not head_lines:
        return None
    journal = SPACE_RE.sub(" ", " ".join(head_lines)).strip()
    return journal.title().replace("&", "and")


def article_year_from_text(text: str) -> str | None:
    head = text[:5000]
    for pattern in (
        r"\b(?:Spring|Summer|Fall|Winter)\s+((?:19|20)\d{2})\b",
        r"©\s*((?:19|20)\d{2})\b",
        r"\b(?:19|20)\d{2}\s+INFORMS\b",
    ):
        match = re.search(pattern, head, flags=re.IGNORECASE)
        if match:
            return match.group(1) if match.lastindex else match.group(0)[:4]
    return None


def journal_article_title_from_lines(lines: list[str]) -> str | None:
    start = None
    for i, line in enumerate(lines):
        lower = line.lower()
        if "issn" in lower or "doi " in lower or lower.startswith("vol."):
            start = i + 1
    if start is None:
        return None

    title_lines: list[str] = []
    for line in lines[start:]:
        lower = line.lower()
        if not title_lines and (
            lower.startswith(("issn", "eissn"))
            or "©" in line
            or "informs" in lower
        ):
            continue
        if looks_like_author_line(line):
            break
        if re.search(r"\b(?:school|department|university|institute)\b", lower):
            break
        if "@" in line or line.startswith("{"):
            break
        if 2 <= len(WORD_RE.findall(line)) <= 14:
            title_lines.append(line)
            continue
        if title_lines:
            break

    title = " ".join(title_lines).strip()
    if 10 <= len(title) <= 250:
        return title
    return None


def journal_article_author_from_lines(lines: list[str], title: str) -> str | None:
    title_words = set(WORD_RE.findall(title.lower()))
    for line in lines:
        lower = line.lower()
        if (
            lower.startswith(("vol.", "issn", "eissn"))
            or "doi " in lower
            or "©" in line
        ):
            continue
        if title_words and len(title_words & set(WORD_RE.findall(line.lower()))) >= 2:
            continue
        if looks_like_author_line(line):
            words = WORD_RE.findall(line.split(",")[0])
            if words:
                return words[-1]
    return None


def journal_masthead_match(text: str) -> re.Match[str] | None:
    head = "\n".join(text.splitlines()[:40])
    return re.search(
        r"(?m)^([A-Z][A-Za-z& ]{2,80})\s+\d+\s*\(((?:19|20)\d{2})\)\s+\d+[\-–]\d+\.?$",
        head,
    )


def crossref_result_conflicts_with_journal_text(
    metadata: dict[str, Any],
    text: str,
) -> bool:
    item_type = str(metadata.get("type") or "").lower()
    if item_type not in {
        "book",
        "monograph",
        "reference-book",
        "book-series",
        "book-chapter",
        "book-part",
        "reference-entry",
        "book-section",
    }:
        return False
    return journal_masthead_match(text) is not None


def guess_author(text: str) -> str | None:
    """Try to extract an author surname from the first page of *text*.

    Heuristic: after the title, look for a line that looks like an author
    name (contains an initial like ``A.`` or a comma-separated list).
    """
    text = re.sub(r"[\x0b\x0c\x1c\x1d\x1e]", " ", text)
    lines = [
        collapse_spaced_letters(SPACE_RE.sub(" ", ln).strip())
        for ln in text.splitlines()[:40]
    ]
    # Find the first author-like line after the first few lines.
    for i, line in enumerate(lines[2:], start=2):
        if not line or len(line) < 5:
            continue
        # Author line with initial: "Laurence A. Wolsey"
        if re.search(r"\b[A-Z]\.(?:\s+[A-Z]|$)", line):
            # Return the surname (last word).
            first_author_part = re.split(r"\s+and\s+|,", line, maxsplit=1)[0]
            words = WORD_RE.findall(first_author_part)
            if words:
                return words[-1]
        # Comma-separated: "Smith, J., Jones, P."
        if "," in line and len(line.split()) < 18:
            words = WORD_RE.findall(line.split(",")[0])
            if words:
                return words[0]
    return None


def collapse_spaced_letters(value: str) -> str:
    """Collapse OCR text like ``N E M H A U S E R`` into ``NEMHAUSER``."""
    return re.sub(
        r"\b(?:[A-Z]\s+){2,}[A-Z]\b",
        lambda match: match.group(0).replace(" ", ""),
        value,
    )


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
    head = text[:20_000]
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


def book_metadata_from_text(text: str) -> dict[str, Any] | None:
    """Build minimal book metadata from front matter when online lookup fails."""
    isbn = find_isbn(text)
    doi = find_doi(text)
    bookish_doi = bool(doi and re.search(r"/978-?\d", doi))
    if not (isbn or bookish_doi or has_book_front_matter(text)):
        return None

    title = springer_book_title(text) or book_title_guess(text)
    if not title:
        return None

    metadata: dict[str, Any] = {
        "title": [title],
        "type": "book",
    }
    if doi:
        metadata["DOI"] = doi
    if isbn:
        metadata["ISBN"] = [isbn]

    editors = springer_book_editors(text)
    if editors:
        metadata["editor"] = [{"family": editor} for editor in editors]

    year = book_year_guess(text)
    if year:
        metadata["issued"] = {"date-parts": [[int(year)]]}

    if "springer" in text[:20_000].lower():
        metadata["publisher"] = "Springer"

    return metadata


def has_book_front_matter(text: str) -> bool:
    head = text[:20_000].lower()
    return bool(
        re.search(r"\bisbn(?:-1[03])?\b", head)
        or re.search(r"(?m)^\s*(?:edited by|[A-Z][A-Za-z.\s]+ editors?)\s*$", text[:5000])
        or "international series in" in head
        or "this springer imprint" in head
        or "this book series" in head
    )


def springer_book_title(text: str) -> str | None:
    pages = text.split("\f", 1)
    first_page = pages[0] if pages else text
    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]
    if not lines:
        return None

    start = None
    for i, line in enumerate(lines):
        if re.search(r"\bEditors?\b", line):
            start = i + 1
            break
    if start is None:
        return None

    title_lines: list[str] = []
    for line in lines[start:]:
        lowered = line.lower()
        if lowered.startswith(("volume ", "series editor", "founding editor")):
            break
        if re.search(r"\b(?:department|faculty|university|isbn|issn|doi)\b", lowered):
            break
        if 1 <= len(WORD_RE.findall(line)) <= 8:
            title_lines.append(line)

    title = " ".join(title_lines).strip()
    if 10 <= len(title) <= 180:
        return title
    return None


def springer_book_editors(text: str) -> list[str]:
    first_page = text.split("\f", 1)[0]
    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if SPACE_RE.sub(" ", line).strip()
    ]
    editors: list[str] = []
    for i, line in enumerate(lines):
        if not re.search(r"\bEditors?\b", line):
            continue
        current = re.sub(r"\bEditors?\b", "", line).strip()
        candidates = []
        if i > 0:
            candidates.append(lines[i - 1])
        if current:
            candidates.append(current)
        for candidate in candidates:
            if looks_like_person_name(candidate):
                words = WORD_RE.findall(candidate)
                if words:
                    editors.append(words[-1])
        break
    return editors


def looks_like_person_name(value: str) -> bool:
    if any(token in value.lower() for token in ("university", "department", "series")):
        return False
    words = WORD_RE.findall(value)
    return 2 <= len(words) <= 5 and any(len(word) == 1 for word in words[:-1])


def looks_like_plain_author_line(value: str) -> bool:
    if any(token in value.lower() for token in ("university", "department", "abstract")):
        return False
    words = WORD_RE.findall(value)
    return (
        2 <= len(words) <= 4
        and all(word[:1].isupper() and word[1:].islower() for word in words)
    )


# ---------------------------------------------------------------------------
# Supplement matching
# ---------------------------------------------------------------------------


def supplement_suffix(pdf: Path, metadata: dict[str, Any], text: str) -> str:
    if looks_like_journal_article_text(text) or jstor_article_metadata_from_text(text):
        return ""
    title = first_value(metadata.get("title")) or ""
    search_text = "\n".join((pdf.stem, title, text[:10_000]))
    search_text = re.sub(r"[_\-.]+", " ", search_text)
    if SUPPLEMENT_RE.search(search_text):
        return "_supplement"
    return ""


def _supplement_to_doi(metadata: dict[str, Any]) -> str | None:
    """Return the main paper's DOI from a supplement's Crossref ``relation``."""
    relations = metadata.get("relation") or {}
    if not isinstance(relations, dict):
        return None
    for rel_type in ("is-supplement-to", "is-part-of"):
        items = relations.get(rel_type) or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("id-type") == "doi":
                    return item.get("id")
    return None


_SUPP_REF_RE = re.compile(
    r"(?:"
    r"supplementary\s+material\s+(?:for|to)[:\s]*|"
    r"online\s+supplement\s+(?:for|to)[:\s]*|"
    r"supplement(?:ary)?\s+(?:for|to)[:\s]*|"
    r"online\s+appendi(?:x|ces)\s+(?:for|to)[:\s]*|"
    r"appendi(?:x|ces)\s+to[:\s]*"
    r")"
    r"([^\n]{15,250})",
    re.IGNORECASE,
)


def _extract_main_title_from_supplement(text: str) -> str | None:
    """Extract the main paper's title from supplement text.

    Looks for patterns like "Supplementary Material for: <title>".
    """
    match = _SUPP_REF_RE.search(text)
    if not match:
        return None
    candidate = match.group(1).strip().strip('"\'')
    lines = [candidate.split("\n")[0].strip()]
    tail = text[match.end() :].splitlines()
    for line in tail[:5]:
        line = SPACE_RE.sub(" ", line).strip()
        if not line:
            continue
        lower = line.lower()
        if (
            looks_like_author_line(line)
            or looks_like_person_name(line)
            or (
                len(WORD_RE.findall(" ".join(lines))) >= 5
                and looks_like_plain_author_line(line)
            )
            or "@" in line
            or lower.startswith(("abstract", "keywords", "school ", "department "))
            or re.match(r"^[A-Z]\.?\s*$", line)
        ):
            break
        if 1 <= len(WORD_RE.findall(line)) <= 14:
            lines.append(line)
            continue
        break
    candidate = SPACE_RE.sub(" ", " ".join(lines)).strip()
    if candidate.endswith(".") and not re.search(r"\b[A-Z]\.$", candidate):
        candidate = candidate[:-1].strip()
    if 10 <= len(candidate) <= 250:
        return candidate
    return None


def _parse_renamed_stem(stem: str) -> dict[str, Any] | None:
    """Parse a renamed file stem back into Crossref-style metadata.

    Format: ``Journal-AuthorYear-ShortTitle`` (``_supplement`` suffix
    is stripped first).
    """
    stem = re.sub(r"_supplement$", "", stem)
    parts = stem.split("-", 2)
    if len(parts) < 3:
        return None
    journal, author_year, short_title = parts
    match = re.match(r"^([A-Za-z][A-Za-z0-9]*?)((?:19|20)\d{2})$", author_year)
    if not match:
        return None
    return {
        "short-container-title": [journal],
        "author": [{"family": match.group(1)}],
        "issued": {"date-parts": [[int(match.group(2))]]},
        "title": [short_title.replace("_", " ")],
    }


def _find_main_in_outbox(
    supp_metadata: dict[str, Any],
    outbox: Path,
) -> dict[str, Any] | None:
    """Find a matching main paper in *outbox* by title word overlap."""
    if not outbox.exists():
        return None
    supp_title = first_value(supp_metadata.get("title")) or ""
    supp_words = {
        w.lower() for w in WORD_RE.findall(supp_title) if len(w) >= 2
    }
    if len(supp_words) < 2:
        return None

    best_score = 0.0
    best_stem: str | None = None

    for f in outbox.glob("*.pdf"):
        stem = f.stem
        if stem.endswith("_supplement"):
            continue  # skip other supplements
        m = re.match(r"^[A-Za-z0-9]+-[A-Za-z][A-Za-z0-9]*\d{4}-(.+)$", stem)
        if not m:
            continue
        file_title = m.group(1)
        file_words = {
            w.lower() for w in WORD_RE.findall(file_title) if len(w) >= 2
        }
        if not file_words:
            continue
        overlap = supp_words & file_words
        score = len(overlap) / min(len(supp_words), len(file_words))
        if score > best_score:
            best_score = score
            best_stem = stem

    if best_score >= 0.5 and best_stem:
        return _parse_renamed_stem(best_stem)
    return None


def resolve_supplement_metadata(
    metadata: dict[str, Any],
    pdf: Path,
    text: str,
    outbox: Path,
    mailto: str,
) -> dict[str, Any] | None:
    """Try to find the main paper that *pdf* supplements.

    Returns the main paper's metadata when found so the supplement can
    share the same journal / author / year / title stem.
    """
    # Strategy 1: Crossref ``is-supplement-to`` relation → fetch main paper.
    main_doi = _supplement_to_doi(metadata)
    if main_doi:
        main_meta = crossref_by_doi(main_doi, mailto)
        if main_meta:
            return main_meta

    # Strategy 2: extract main-paper title from text → Crossref lookup.
    main_title = _extract_main_title_from_supplement(text)
    if main_title:
        main_meta = crossref_by_title(main_title, mailto)
        if main_meta:
            return main_meta

    # Strategy 3: compare with already-renamed files in the outbox.
    outbox_match = _find_main_in_outbox(metadata, outbox)
    if outbox_match:
        return outbox_match

    return None


if __name__ == "__main__":
    raise SystemExit(main())
