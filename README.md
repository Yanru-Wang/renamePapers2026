# renamepapers

Safely rename academic PDFs as `Source-AuthorYear-ShortTitle.pdf`.

Scans `~/Papers/Inbox` by default and moves successfully renamed PDFs to
`~/Papers/Renamed`, keeping the inbox as an intake queue.

The tool is intentionally conservative: when the available evidence is weak, it
fails and asks for an explicit override instead of silently inventing a filename.
It also treats supplements and online appendices as first-class intake items,
so add-on PDFs can stay discoverable next to the main paper instead of getting
renamed from generic cover-page text.

## Workflow

The script is designed to be a **pre-Zotero filter** — handle the flood of
temporary PDFs without polluting your reference manager:

```
Downloads / browser downloads
  │
  ▼
~/Papers/Inbox          ←  dump everything here
  │
  │  renamepapers        ←  auto-identify & rename
  ▼
~/Papers/Renamed         ←  clean, searchable filenames
  │
  ├─ important  ──→  Zotero  (Rename and Move)
  ├─ maybe       ──→  keep in Renamed
  └─ junk        ──→  delete
```

**Why this works**: Zotero stays clean — only curated references enter your
library. Temporary PDFs, preprints you're skimming, and papers you might not
keep don't clutter Zotero. But they still get human-readable filenames so you
can find them later with Spotlight / Finder / fzf.

### Daily usage

```bash
# 1. Download PDFs → ~/Papers/Inbox (browser default, or drag from Downloads)
# 2. Run the script
renamepapers

# 3. Skim failures, fix manually if needed
renamepapers --title "..." --author "..." --year 2024 failed.pdf

# 4. Important papers → Zotero; rest → keep or delete
```

## Install

```bash
# Requires Python 3.10+. Install Poppler for text extraction:
brew install poppler

# Optional: OCR support for scanned title pages.
brew install tesseract

# One-line install:
./install_renamepapers.sh
#   … or manually …
cp renamepapers.py ~/.local/bin/renamepapers
chmod +x ~/.local/bin/renamepapers
```

## Usage

```bash
# Process all PDFs in ~/Papers/Inbox
renamepapers

# Process specific files
renamepapers paper1.pdf paper2.pdf

# Dry-run — preview renames without moving files
renamepapers --dry-run

# Manual override when auto-detection fails
renamepapers --title "Integer Programming" --author Wolsey --year 2020 --kind book paper.pdf

# Thesis/dissertation override
renamepapers --kind thesis --title "..." --author Bertsimas --year 1988 thesis.pdf
```

## How It Works

The script tries identifiers and evidence in cascade order.  Strong page
evidence is allowed to override weak heuristics, but weak heuristics must not
override explicit identifiers or source-specific headers; see
`docs/evidence_hierarchy.md`.

| Step | Method | Fallback |
|------|--------|----------|
| 1 | **DOI** — regex from PDF text + XMP metadata → Crossref | |
| 2 | **arXiv ID** — page/header regex → Crossref, then local arXiv parsing | |
| 3 | **ISBN** — regex from PDF text → Crossref (book-level only) | |
| 4 | **Known source layouts** — handbook chapter, thesis title page, journal article, book front matter, supplement header | |
| 5 | **Title** — guessed from extracted text → Crossref (author-filtered) | |
| 6 | **PDF metadata** — XMP / Info dict title → Crossref (placeholder-checked) | |
| 7 | Error — prompts user for `--title/--author/--year/--kind` | |

**Text extraction** tries: `pdftotext` → `mutool` → pure-Python fallback
(decompresses PDF streams, uses font-size heuristics).

**OCR fallback**: when a PDF has no useful text layer and its embedded metadata
is missing or looks like a placeholder, the script can render the first pages
with `pdftoppm` and OCR them with `tesseract`. This is used for scanned title
pages such as theses/dissertations.

**Bad metadata guard**: embedded titles such as `Adopted from pdflib image
sample` are treated as placeholders. They are not used for Crossref title
searches or final filenames.

**Thesis detection**: OCR/text title pages containing signals such as `by`,
`DOCTOR OF PHILOSOPHY`, `SUBMITTED IN PARTIAL FULFILLMENT`, or `Thesis
Supervisor` are classified as `Thesis`.

**Book detection**: trusts Crossref type; text heuristics (`preface`, `index`,
`isbn`) only used when Crossref type is ambiguous.

**Supplement handling**: online appendix and supplementary material PDFs are
common in research folders, but their first pages often start with generic text
such as `Submitted to ...`, `Online Appendices for:`, or supplementary-material
headers. The script parses those headers before generic title guessing, extracts
the main paper title when possible, and names the add-on with the main paper's
metadata plus a `_supplement` suffix. This keeps a paper and its supplement
together in filename search while avoiding false Crossref matches from the
appendix cover page.

**Duplicate handling**: if a destination already exists, identical files are
detected by SHA-256 and reported as `DUP`; different-content collisions are kept
with a numeric suffix instead of being overwritten.

**Journal abbreviation**: uses Crossref `short-container-title`, explicit aliases
for common journals, and conservative initials for already-abbreviated names.

## Regression Checks

Before syncing changes to the installed command, run both parser unit tests and
the real-PDF golden dry-run checks:

```bash
python3 -m unittest tests/test_renamepapers.py
python3 tests/golden_dry_run.py --command /Users/wyr/.local/bin/renamepapers --strict-missing
```

When fixing a wrong filename, add a unit test for the parser rule and, when the
real PDF should stay on this machine, add its expected dry-run output to
`tests/golden_renames.tsv`.

## Filename Format

```
{Source}-{Author}{Year}-{ShortTitle}.pdf

  Source:    Journal abbrev, "ArXiv", "Book", "BookChapter", or "Thesis"
  Author:    First author surname
  Year:      Publication year
  ShortTitle: First 80 chars of title, TitleCased_With_Underscores
```

Examples:
- `MP-Dey2012-Some_Properties_Of_Convex_Hulls_Of_Integer_Points.pdf`
- `Book-Wolsey2020-Integer_Programming.pdf`
- `Thesis-Bertsimas1988-Probabilistic_Combinatorial_Optimization_Problems.pdf`
- `TS-Wissink2023-Routing_Optimization_With_Stochastic_Service_Times_supplement.pdf`
- `COA-Bernal2024-Convex_Mixed_Integer_Nonlinear_Programs_Derived_From_Generalized_Disjunctive_Pro.pdf`

## Options

| Flag | Description |
|------|-------------|
| `--inbox PATH` | Source folder (default: `~/Papers/Inbox`) |
| `--outbox PATH` | Destination folder (default: `~/Papers/Renamed`) |
| `--in-place` | Rename in source folder instead of moving |
| `--dry-run` | Preview without renaming |
| `--kind {auto,journal,book,bookchapter,thesis}` | Override source type |
| `--title TITLE` | Override title |
| `--author SURNAME` | Override first author |
| `--year YEAR` | Override publication year |
| `--mailto EMAIL` | Crossref User-Agent email |

## License

MIT
