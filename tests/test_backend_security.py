from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import backend.app as backend_app


class BackendSecurityTests(unittest.TestCase):
    """Path-traversal / input-validity guards on stock data endpoints."""

    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def test_kline_rejects_non_digit_code(self) -> None:
        response = self.client.get("/api/stocks/ABC/kline")
        self.assertEqual(response.status_code, 400)

    def test_kline_rejects_short_code(self) -> None:
        response = self.client.get("/api/stocks/12345/kline")
        self.assertEqual(response.status_code, 400)

    def test_kline_rejects_traversal_adjust(self) -> None:
        response = self.client.get("/api/stocks/000001/kline?adjust=..%2F..%2Fetc")
        self.assertEqual(response.status_code, 400)

    def test_entry_plan_rejects_non_digit_code(self) -> None:
        response = self.client.get("/api/stocks/ABC/entry-plan")
        self.assertEqual(response.status_code, 400)

    def test_entry_plan_rejects_traversal_adjust(self) -> None:
        response = self.client.get("/api/stocks/000001/entry-plan?adjust=hfq%2F..%2F..")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
