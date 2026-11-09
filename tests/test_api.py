import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.backtest as backtest_store
import app.main as main
from app.backtest import Bar


class StrategyApiTests(unittest.TestCase):
    def test_stock_search_matches_code_and_pinyin_initials(self):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "T0002" / "hq_cache"
            index.mkdir(parents=True)
            def write_index(filename, entries):
                records = bytearray(360 * len(entries))
                for position, (code, name) in enumerate(entries):
                    offset = position * 360
                    records[offset:offset + 6] = code.encode("ascii")
                    encoded_name = name.encode("gbk")
                    records[offset + 31:offset + 31 + len(encoded_name)] = encoded_name
                (index / filename).write_bytes(bytes(50) + records)

            write_index("shs.tnf", [
                ("600036", "招商银行"),
                ("000001", "上证指数"),
                ("243074", "25厦轨01"),
                ("688001", "退市测试"),
            ])
            write_index("szs.tnf", [
                ("000001", "平安银行"),
                ("300750", "宁德时代"),
                ("159001", "基金示例"),
                ("200001", "深B示例"),
            ])
            write_index("bjs.tnf", [("830001", "普通北交所股")])
            with (
                patch.object(main, "_read_config", return_value={"tdxdir": directory}),
                TestClient(main.app) as client,
            ):
                by_code = client.get("/api/stocks/search", params={"q": "6000"})
                by_initials = client.get("/api/stocks/search", params={"q": "zsyh"})
                by_name = client.get("/api/stocks/search", params={"q": "平安"})
                bond = client.get("/api/stocks/search", params={"q": "243074"})
                index_quote = client.get("/api/stocks/search", params={"q": "上证"})
                fund = client.get("/api/stocks/search", params={"q": "159001"})
                delisted = client.get("/api/stocks/search", params={"q": "688001"})
                b_share = client.get("/api/stocks/search", params={"q": "200001"})
                beijing = client.get("/api/stocks/search", params={"q": "830001"})
                stock_records = main.list_stock_records(directory)
        self.assertEqual(by_code.status_code, 200, by_code.text)
        self.assertEqual(by_initials.status_code, 200, by_initials.text)
        self.assertEqual(by_name.status_code, 200, by_name.text)
        self.assertEqual(by_code.json()[0], {"code": "600036", "name": "招商银行"})
        self.assertEqual(by_initials.json()[0], {"code": "600036", "name": "招商银行"})
        self.assertEqual(by_name.json()[0], {"code": "000001", "name": "平安银行"})
        self.assertEqual(bond.json(), [])
        self.assertEqual(index_quote.json(), [])
        self.assertEqual(fund.json(), [])
        self.assertEqual(delisted.json(), [])
        self.assertEqual(b_share.json(), [])
        self.assertEqual(beijing.json()[0], {"code": "830001", "name": "普通北交所股"})
        self.assertEqual(
            {stock["code"] for stock in stock_records},
            {"600036", "000001", "300750", "830001"},
        )

    def test_strategy_help_serves_documentation(self):
        with tempfile.TemporaryDirectory() as directory:
            manual = Path(directory) / "api.md"
            manual.write_text("# Strategy API\n", encoding="utf-8")
            with patch.object(main, "API_MANUAL", manual), TestClient(main.app) as client:
                response = client.get("/api/strategies/help")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Strategy API", response.text)

    def test_strategy_create_read_validate_update_delete(self):
        source = "class Strategy:\n    def on_bar(self, bar, broker):\n        broker.buy(100)\n"
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(main, "STRATEGIES_DIR", Path(directory)),
                patch.object(backtest_store, "STRATEGIES_DIR", Path(directory)),
                TestClient(main.app) as client,
            ):
                created = client.post(
                    "/api/strategies", json={"filename": "test_strategy.py", "code": source}
                )
                self.assertEqual(created.status_code, 200, created.text)
                self.assertEqual(client.get("/api/strategies/test_strategy.py").json()["code"], source)
                self.assertEqual(
                    client.post(
                        "/api/strategies/validate",
                        json={"filename": "test_strategy.py", "code": source},
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.put(
                        "/api/strategies/test_strategy.py",
                        json={"code": "class Strategy:\n    def on_bar(self):\n"},
                    ).status_code,
                    422,
                )
                updated_source = "class Strategy:\n    def on_bar(self, bar, broker):\n        broker.sell()\n"
                updated = client.put(
                    "/api/strategies/test_strategy.py", json={"code": updated_source}
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(client.get("/api/strategies").json()[0]["file"], "test_strategy.py")
                self.assertEqual(client.delete("/api/strategies/test_strategy.py").status_code, 200)
                self.assertEqual(client.get("/api/strategies/test_strategy.py").status_code, 404)

    def test_backtest_api_returns_memory_result_if_json_save_fails(self):
        payload = {
            "strategy_file": "dual_ma.py",
            "symbol": "600036",
            "start_date": "2024-01-01",
            "end_date": "2024-01-02",
            "initial_cash": 100_000,
            "fill_mode": "next_open",
        }
        bars = [Bar("2024-01-01", 10, 10, 10, 10), Bar("2024-01-02", 11, 11, 11, 11)]
        with (
            patch.object(main, "read_daily_bars", return_value=bars),
            patch.object(main, "save_result", side_effect=OSError("disk full")),
            TestClient(main.app) as client,
        ):
            result = client.post("/api/backtest/run", json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()["saved"])
        self.assertIn("disk full", result.json()["persistence_error"])

    def test_backtest_api_uses_portfolio_mode_when_symbol_is_omitted(self):
        bars = [Bar("2024-01-01", 10, 10, 10, 10)]
        result_payload = {
            "meta": {"id": "portfolio-test", "symbol": "多标的"},
            "saved": True,
            "persistence_error": None,
        }
        with (
            patch.object(main, "_read_config", return_value={"tdxdir": "local-tdx"}),
            patch.object(main, "read_daily_bars", return_value=bars),
            patch.object(main, "list_stock_records", return_value=[{"code": "600036", "name": "招商银行"}]),
            patch.object(main, "run_portfolio_backtest", return_value=result_payload) as run_portfolio,
            patch.object(main, "save_result"),
            TestClient(main.app) as client,
        ):
            response = client.post(
                "/api/backtest/run",
                json={
                    "strategy_file": "portfolio.py",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-01",
                    "initial_cash": 100_000,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        run_portfolio.assert_called_once()
        self.assertEqual(response.json()["meta"]["symbol"], "多标的")


if __name__ == "__main__":
    unittest.main()
