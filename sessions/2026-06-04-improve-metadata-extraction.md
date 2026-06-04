# Session: 2026-06-04 — Improve PDF metadata extraction

## Problem

`renamepapers.py` produced `UnknownJournal-Unknown2020-Times_LT_27_X_42.pdf` for a
PDF of *Integer Programming* by Wolsey (2nd ed., 2020).

## Root Causes

1. **`guess_title` min length too high**: 20 chars excluded "Integer Programming" (19 chars)
2. **Form-feed breaks lines**: `\x0c` from pdftotext fragment short titles via `splitlines()`
3. **XMP placeholder titles trusted blindly**: "Times LT 27 X 42" (original filename) used verbatim
4. **`book_year_guess` picked oldest year**: `min()` returned 1998 (1st ed) instead of 2020
5. **Book heuristics overrode Crossref**: text-year took priority over API metadata
6. **`BOOK_EVIDENCE_RE` too broad**: `springer|wiley|elsevier` match journals too → `Book-` prefix
7. **Journal abbrev inconsistency**: `Math. Program.` → `MathProgram` instead of `MP`

## Changes

### Core pipeline (`process_pdf`)
- Added cascade: DOI → **arXiv** → **ISBN** → title → PDF metadata
- ISBN lookup validates result contains the searched ISBN (Zotero-style)
- Crossref metadata now takes priority over text heuristics
- OCR detection: warns when PDF is scanned (no extractable text)

### `guess_title`
- Min length: 20 → 10 chars; alpha: fixed 12 → 50% of line
- Replaces form-feeds before `splitlines()`
- Skips journal headers ("FULL LENGTH PAPER", "Research Article", etc.)
- Skips journal masthead lines (`Journal (Year) Vol:Pages`)
- Better author-line detection: period-initial pattern (`A. Wolsey`)

### New functions
- `extract_text_python()` — pure-Python PDF fallback (decompress streams, font-size heuristics)
- `find_isbn()` / `crossref_by_isbn()` — ISBN extraction + Crossref lookup (book-level preference)
- `find_arxiv()` / `crossref_by_arxiv()` — arXiv ID extraction + Crossref lookup
- `guess_author()` — extract author surname from text after title
- `is_placeholder_title()` — detect filename-like XMP titles
- `titles_match()` — SequenceMatcher LCS validation for Crossref results
- `has_extractable_text()` — OCR detection

### `infer_kind` / `source_prefix`
- Trusts Crossref `journal-article` type over text heuristics
- Removed `springer`, `wiley`, `elsevier` from `BOOK_EVIDENCE_RE`
- Removed overly-broad `contents` pattern

### `journal_abbrev`
- Period-separated short titles use `journal_initials()` instead of `clean_journal()`
- `Math. Program.` → `MP` (was `MathProgram`)

### `book_year_guess`
- `min()` → `max()` (return most recent edition year)

### `crossref_by_title`
- Accepts optional `author` param; fetches 5 results, filters by author match

### Other
- pdftotext page range: 2 → 5 (captures copyright page ISBN)

## Results

| File | Before | After |
|------|--------|-------|
| Integer Programming (Wolsey) | `UnknownJournal-Unknown2020-Times_LT_27_X_42` | `Book-Wolsey2020-Integer_Programming` |
| Dey 2012 | `Book-Dey2012-...` | `MP-Dey2012-...` |
| Bernal 2024 | `Book-Bernal2024-...` | `COA-Bernal2024-...` |

## Reference

- [zotero-attanger](https://github.com/MuiseDestiny/zotero-attanger) — Zotero attachment manager (LCS matching, multi-source)
- [Zotero recognizeDocument.js](https://github.com/zotero/zotero) — PDF → Zotero web service → arXiv/DOI/ISBN cascade
- Crossref API: `api.crossref.org/works`
