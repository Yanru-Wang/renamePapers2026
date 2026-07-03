# Evidence hierarchy

`renamepapers` must be conservative.  A weak clue may fill a gap, but it must
not override a stronger source signal already visible in the PDF.

## Priority order

1. **Explicit identifiers**
   - DOI in text or trusted metadata, resolved by Crossref.
   - arXiv ID in the page text/header.  If Crossref has no arXiv record, use
     local arXiv parsing.
2. **Source-specific page headers**
   - Supplement and online-appendix headers such as `Online Appendices for:`.
   - Thesis/dissertation title pages.
   - Blank-venue preprints with `Preprint submitted to ...`.
   - Known journal/article mastheads.
3. **Crossref title search**
   - Only after a plausible title has been extracted from page text.
   - Reject Crossref records whose source type conflicts with page evidence.
4. **Embedded PDF metadata**
   - Use only when the title is not a placeholder and is confirmed by page text,
     or when metadata also carries trusted author/identifier evidence.
5. **Filename/already-renamed fast path**
   - Accept only when no stronger page evidence contradicts the current name.
6. **Book/journal heuristics**
   - Use only as fallback classification when Crossref/source-specific evidence
     is ambiguous.

## Non-overwrite rules

- arXiv or other `posted-content` evidence cannot be turned into `Book` by
  body-text words such as `Index Terms`, `preface`, or `index`.
- Supplement headers must beat generic title guessing and `Submitted to ...`
  text.
- Blank-venue preprint evidence must beat stale journal/book guesses and stale
  already-renamed basenames.
- IEEE template headers such as `JOURNAL OF LATEX CLASS FILES...` are layout
  artifacts, not titles or journals.
- `SIAM` journal names and `Networks` are explicit naming conventions, not
  candidates for generic one-letter initials.

## Regression policy

Every wrong-name fix should add at least one of:

- a focused unit test with a small text fixture for the parser rule;
- a golden dry-run entry in `tests/golden_renames.tsv` when the real PDF is
  available on this machine.

Before syncing the installed CLI, run:

```bash
python3 -m unittest tests/test_renamepapers.py
python3 tests/golden_dry_run.py --command /Users/wyr/.local/bin/renamepapers
```

