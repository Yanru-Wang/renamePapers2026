"""Shared text normalization and filename-token helpers."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any


SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[^\W_]+")  # Unicode letters + digits, excluding underscore
ROMAN_NUMERAL_RE = re.compile(
    r"M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})"
)


def first_value(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None


def titles_match(query: str, result: str) -> bool:
    """Return True when *result* title is broadly consistent with *query*."""
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

    ratio = difflib.SequenceMatcher(None, query.lower(), result.lower()).ratio()
    return ratio >= 0.25


def clean_year(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(?:19|20)\d{2}\b", value)
    return match.group(0) if match else None


def shorten_title(title: str, max_chars: int = 80) -> str:
    title = html.unescape(re.sub(r"<[^>]+>", " ", title))
    words = WORD_RE.findall(ascii_fold(title))
    kept: list[str] = []
    for word in words:
        if not any(ch.isalnum() for ch in word):
            continue
        kept.append(format_word(word))

    compact = "_".join(kept) or "Untitled"
    return compact[:max_chars].rstrip("_-")


def ascii_fold(value: str) -> str:
    """Normalize Unicode diacritics to ASCII equivalents (e.g. 'ć' -> 'c')."""
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def clean_token(value: str, max_words: int) -> str:
    value = ascii_fold(value)
    words = WORD_RE.findall(value)[:max_words]
    return "".join(format_word(word) for word in words)


def format_word(word: str) -> str:
    if len(word) > 1 and word.isupper() and ROMAN_NUMERAL_RE.fullmatch(word):
        return word
    if len(word) > 1 and word.isupper():
        word = word.lower()
    return word[:1].upper() + word[1:]
