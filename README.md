# renamePapers

Rename scientific PDFs as `JournalAbbrev_AuthorYear_ShortTitle.pdf`.

Scans `~/Papers/Inbox` by default and moves successfully renamed PDFs to
`~/Papers/Renamed`, keeping the inbox as an intake queue.

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
# Requires Python 3.10+.  Install pdftotext for best results:
brew install poppler

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
```

## How It Works

The script tries identifiers in cascade order:

| Step | Method | Fallback |
|------|--------|----------|
| 1 | **DOI** — regex from PDF text + XMP metadata → Crossref | |
| 2 | **arXiv ID** — regex → Crossref | |
| 3 | **ISBN** — regex from PDF text → Crossref (book-level only) | |
| 4 | **Title** — guessed from extracted text → Crossref (author-filtered) | |
| 5 | **PDF metadata** — XMP / Info dict title → Crossref (placeholder-checked) | |
| 6 | Error — prompts user for `--title/--author/--year/--kind` | |

**Text extraction** tries: `pdftotext` → `mutool` → pure-Python fallback
(decompresses PDF streams, uses font-size heuristics).

**Book detection**: trusts Crossref type; text heuristics (`preface`, `index`,
`isbn`) only used when Crossref type is ambiguous.

**Journal abbreviation**: uses Crossref `short-container-title`; period-separated
abbreviations ("Math. Program.") yield initials ("MP"); hardcoded aliases for
common journals (SIAM → SICOMP/SIOPT, Operations Research → OR, EJOR, etc.).

## Filename Format

```
{Source}_{Author}{Year}_{ShortTitle}.pdf

  Source:    Journal abbrev, "Book", or "BookChapter"
  Author:    First author surname
  Year:      Publication year
  ShortTitle: First 80 chars of title, TitleCased_With_Underscores
```

Examples:
- `MP-Dey2012-Some_Properties_Of_Convex_Hulls_Of_Integer_Points.pdf`
- `Book-Wolsey2020-Integer_Programming.pdf`
- `COA-Bernal2024-Convex_Mixed_Integer_Nonlinear_Programs_Derived_From_Generalized_Disjunctive_Pro.pdf`

## Options

| Flag | Description |
|------|-------------|
| `--inbox PATH` | Source folder (default: `~/Papers/Inbox`) |
| `--outbox PATH` | Destination folder (default: `~/Papers/Renamed`) |
| `--in-place` | Rename in source folder instead of moving |
| `--dry-run` | Preview without renaming |
| `--kind {auto,journal,book,bookchapter}` | Override source type |
| `--title TITLE` | Override title |
| `--author SURNAME` | Override first author |
| `--year YEAR` | Override publication year |
| `--mailto EMAIL` | Crossref User-Agent email |

## License

MIT
