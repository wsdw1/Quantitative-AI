from __future__ import annotations

import unittest

from ai_scoring.service import _resolve_candidate_adjust


class CandidateAdjustResolutionTests(unittest.TestCase):
    def test_prefers_top_level_adjust(self) -> None:
        run = {"meta": {"adjust": "bfq", "config": {"global": {"adjust": "qfq"}}}}
        self.assertEqual(_resolve_candidate_adjust(run), "bfq")

    def test_falls_back_to_nested_global_adjust(self) -> None:
        run = {"meta": {"config": {"global": {"adjust": "hfq"}}}}
        self.assertEqual(_resolve_candidate_adjust(run), "hfq")

    def test_defaults_to_qfq_when_missing(self) -> None:
        run = {"meta": {"scanned": 10}}
        self.assertEqual(_resolve_candidate_adjust(run), "qfq")

    def test_normalizes_and_sanitizes_value(self) -> None:
        run = {"meta": {"adjust": "  QFQ  "}}
        self.assertEqual(_resolve_candidate_adjust(run), "qfq")
        run = {"meta": {"adjust": "../../etc"}}
        self.assertEqual(_resolve_candidate_adjust(run), "qfq")


if __name__ == "__main__":
    unittest.main()
