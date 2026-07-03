from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from renamepapers import core as renamepapers
from renamepapers import files
from renamepapers import naming


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
        self.assertEqual(naming.source_prefix(metadata), "Thesis")
        kind = naming.infer_kind(
            Path("x.pdf"),
            metadata,
            BERTSIMAS_OCR_TEXT,
            forced_kind="auto",
        )
        self.assertEqual(
            naming.build_filename(metadata, kind=kind),
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
        self.assertEqual(naming.source_prefix(metadata), "ArXiv")

    def test_arxiv_ieee_template_header_is_not_title_or_book(self) -> None:
        text = """JOURNAL OF LATEX CLASS FILES, VOL. 14, NO. 8, AUGUST 2021 1
Open3DBench: Open-Source Benchmark for 3D-IC Backend
Implementation and PPA Evaluation
Yunqi Shi*, Chengrui Gao*, Wanqi Ren, Chao Qian, and Zhi-Hua Zhou

Abstract-This paper introduces Open3DBench.
Index Terms-3D-IC, open-source, OpenROAD.

arXiv:2503.12946v2 [cs.AR] 5 Apr 2026
"""

        metadata = renamepapers.arxiv_metadata_from_text(text)

        self.assertIsNotNone(metadata)
        self.assertEqual(
            metadata["title"],
            [
                "Open3DBench: Open-Source Benchmark for 3D-IC Backend "
                "Implementation and PPA Evaluation"
            ],
        )
        self.assertEqual(metadata["author"], [{"family": "Shi"}])
        self.assertEqual(metadata["issued"], {"date-parts": [[2025]]})
        self.assertEqual(
            naming.infer_kind(
                Path("Book-Qian2025-Journal_Of_Latex_Class_Files.pdf"),
                metadata,
                text,
                forced_kind="auto",
            ),
            None,
        )
        self.assertEqual(naming.source_prefix(metadata), "ArXiv")

    def test_apostrophe_surname_is_kept_as_one_author_token(self) -> None:
        metadata = {
            "title": [
                "Probability chains: A general linearization technique for "
                "modeling reliability in facility location and related problems"
            ],
            "author": [{"family": "O’Hanley"}],
            "issued": {"date-parts": [[2013]]},
            "container-title": ["European Journal of Operational Research"],
            "type": "journal-article",
        }

        self.assertEqual(naming.first_author(metadata), "OHanley")
        self.assertEqual(
            naming.build_filename(metadata),
            "EJOR-OHanley2013-Probability_Chains_A_General_Linearization_"
            "Technique_For_Modeling_Reliability_In.pdf",
        )
        self.assertEqual(
            naming.build_filename(metadata, author_override="O'Hanley"),
            "EJOR-OHanley2013-Probability_Chains_A_General_Linearization_"
            "Technique_For_Modeling_Reliability_In.pdf",
        )

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

    def test_blank_venue_preprint_uses_optimization_online(self) -> None:
        text = """Skip or Insert? A Priori Optimization for the Vehicle Routing
Problem with Time Windows and Stochastic Customers

Yulin Hana,*, Hande Yamana

Abstract

Preprint submitted to March 7, 2026
"""

        metadata = renamepapers.preprint_metadata_from_text(text)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["container-title"], ["Optimization Online"])
        self.assertEqual(metadata["issued"], {"date-parts": [[2026]]})
        self.assertEqual(metadata["author"], [{"family": "Han"}])
        self.assertEqual(naming.source_prefix(metadata), "OptimizationOnline")

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
