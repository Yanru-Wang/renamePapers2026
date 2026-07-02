from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from renamepapers import core as renamepapers
from renamepapers import files


BERTSIMAS_OCR_TEXT = """PROBABILISTIC COMBINATORIAL OPTIMIZATION PROBLEMS
by
DIMITRIS J. BERTSIMAS
B.S., Electrical Engineering
National Technical University of Athens (1985)
M.S., Operations Research
Massachusetts Institute of Technology (1987)
SUBMITTED IN PARTIAL FULFILLMENT
OF THE REQUIREMENTS OF THE
DEGREE OF
DOCTOR OF PHILOSOPHY
IN OPERATIONS RESEARCH AND APPLIED MATHEMATICS
at the
MASSACHUSETTS INSTITUTE OF TECHNOLOGY
August 1988
"""


class RenamePapersTests(unittest.TestCase):
    def test_thesis_title_page_metadata(self) -> None:
        metadata = renamepapers.thesis_metadata_from_text(BERTSIMAS_OCR_TEXT)

        self.assertIsNotNone(metadata)
        self.assertEqual(
            metadata["title"],
            ["PROBABILISTIC COMBINATORIAL OPTIMIZATION PROBLEMS"],
        )
        self.assertEqual(metadata["author"], [{"family": "BERTSIMAS"}])
        self.assertEqual(metadata["issued"], {"date-parts": [[1988]]})
        self.assertEqual(metadata["type"], "thesis")
        self.assertEqual(renamepapers.source_prefix(metadata), "Thesis")
        kind = renamepapers.infer_kind(
            Path("x.pdf"),
            metadata,
            BERTSIMAS_OCR_TEXT,
            forced_kind="auto",
        )
        self.assertEqual(
            renamepapers.build_filename(metadata, kind=kind),
            "Thesis-Bertsimas1988-Probabilistic_Combinatorial_Optimization_Problems.pdf",
        )

    def test_placeholder_pdf_metadata_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "scan.pdf"
            pdf.write_text(
                "/Title (Adopted from pdflib image sample \\(C\\))\n"
                "/Author (Carl Jones)\n"
                "/CreationDate (D:20050613052756)\n",
                encoding="latin-1",
            )

            self.assertTrue(renamepapers.has_placeholder_pdf_metadata(pdf))
            self.assertEqual(renamepapers.extract_pdf_metadata(pdf), {})

    def test_arxiv_metadata_from_page_header(self) -> None:
        text = """Distributionally Robust Routing Under Demand Ambiguity
Jane Doe and John Smith
arXiv:2604.02496v1 [math.OC] 3 Apr 2026
"""

        metadata = renamepapers.arxiv_metadata_from_text(text)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["container-title"], ["arXiv"])
        self.assertEqual(metadata["issued"], {"date-parts": [[2026]]})
        self.assertEqual(metadata["author"], [{"family": "Doe"}])
        self.assertEqual(renamepapers.source_prefix(metadata), "ArXiv")

    def test_extract_main_title_from_supplement_header(self) -> None:
        text = """Submitted to Transportation Science
Online Appendices for:
Routing Optimization with Stochastic Service Times
Jane Doe
"""

        self.assertEqual(
            renamepapers._extract_main_title_from_supplement(text),
            "Routing Optimization with Stochastic Service Times",
        )

    def test_duplicate_destination_reports_dup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            destination = root / "destination.pdf"
            source.write_bytes(b"same-content")
            destination.write_bytes(b"same-content")

            dry = files.move_or_deduplicate(source, destination, dry_run=True)
            self.assertTrue(dry.startswith("DUP "))
            self.assertTrue(source.exists())

            real = files.move_or_deduplicate(source, destination, dry_run=False)
            self.assertTrue(real.startswith("DUP "))
            self.assertFalse(source.exists())
            self.assertTrue(destination.exists())


if __name__ == "__main__":
    unittest.main()
