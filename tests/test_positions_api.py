from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.app as backend_app


class MarketPositionsApiTests(unittest.TestCase):
    def test_positions_endpoint_returns_payload(self) -> None:
        fake = {
            "available": True, "as_of": "2026-08-18", "regime": "risk",
            "market": [{"code": "000001.SH", "position": 54.8, "close": 3990.3, "reversal": False, "trade_date": "2026-08-18"}],
            "boards": {"main": {"position_risk": 54.8, "position_bottom": 54.8, "reversal": False, "codes": ["000001.SH"]}},
            "industries": [{"code": "801010.SI", "position": 23.8, "reversal": False, "trade_date": "2026-08-18"}],
        }
        with patch("backend.app.market_positions", return_value=fake), \
             patch("backend.app.board_positions", return_value={"available": True, "boards": fake["boards"]}), \
             patch("backend.app.industry_positions", return_value={"available": True, "industries": fake["industries"]}):
            client = TestClient(backend_app.app)
            response = client.get("/api/market/positions?as_of=2026-08-18")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["regime"], "risk")
        self.assertEqual(payload["market"][0]["code"], "000001.SH")


if __name__ == "__main__":
    unittest.main()
