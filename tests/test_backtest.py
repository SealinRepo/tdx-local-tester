import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app import backtest
from app.backtest import (
    Bar,
    Broker,
    PortfolioBroker,
    calculate_performance,
    cross_up,
    ema,
    run_backtest,
    run_portfolio_backtest,
    sma,
)
from app.tdx import read_daily_bars, read_intraday_bars


class BacktestTests(unittest.TestCase):
    def test_portfolio_average_cost_is_weighted_across_buys_and_survives_partial_sell(self):
        broker = PortfolioBroker(
            initial_cash=100_000,
            commission=0,
            stamp_tax=0,
            slippage=0,
            fill_mode="next_open",
            min_commission=0,
            market_data_provider=lambda code: [],
            stock_records=[{"code": "600036", "name": "招商银行"}],
        )
        first = Bar("2024-01-01", 10, 10, 10, 10)
        broker.update_day(first.date, {"600036": first}, {"600036": [first]})
        broker.buy("600036", 2_000)

        second = Bar("2024-01-02", 11, 11, 11, 11)
        broker.update_day(second.date, {"600036": second}, {"600036": [first, second]})
        broker.execute_pending({"600036": second}, "open")
        self.assertEqual(broker.get_position("600036")["average_cost"], 11)
        broker.buy("600036", 2_400)

        third = Bar("2024-01-03", 13, 13, 13, 13)
        broker.update_day(third.date, {"600036": third}, {"600036": [first, second, third]})
        broker.execute_pending({"600036": third}, "open")
        self.assertEqual(broker.get_position("600036")["size"], 400)
        self.assertEqual(broker.get_position("600036")["average_cost"], 12)
        broker.sell("600036", 1_500)

        fourth = Bar("2024-01-04", 14, 14, 14, 14)
        broker.update_day(fourth.date, {"600036": fourth}, {"600036": [first, second, third, fourth]})
        broker.execute_pending({"600036": fourth}, "open")
        position = broker.get_position("600036")
        self.assertEqual(position["size"], 300)
        self.assertAlmostEqual(position["average_cost"], 34 / 3, places=5)

    def test_portfolio_strategy_queries_descending_market_data_and_trades_by_code(self):
        source = """class Strategy:
    def on_day(self, day, broker):
        codes = get_stock_codes()
        names = get_stock_names(codes)
        history = get_history(["600036"], 2)["600036"]
        if day == "2024-01-01":
            assert history[0]["date"] == day
            assert history[0]["V"] == 1000
            assert names[0] == {"code": "600036", "name": "招商银行"}
            broker.buy("600036", 2999)
        elif day == "2024-01-02":
            assert history[0]["date"] == day
            assert history[1]["date"] == "2024-01-01"
            assert history[-1]["date"] == "2024-01-01"
            assert history[0]["V"] == 2000
            broker.sell("600036")
        elif day == "2024-01-03":
            assert len(history) == 2
            assert history[0]["date"] == day
"""
        bars = [
            Bar("2024-01-01", 10, 10, 10, 10, 1000),
            Bar("2024-01-02", 15, 11, 11, 11, 2000),
            Bar("2024-01-03", 12, 12, 12, 12, 3000),
        ]
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "portfolio.py").write_text(source, encoding="utf-8")
            with patch.object(backtest, "STRATEGIES_DIR", Path(directory)):
                result = run_portfolio_backtest(
                    strategy_file="portfolio.py",
                    start_date="2024-01-01",
                    end_date="2024-01-03",
                    initial_cash=10_000,
                    fill_mode="next_open",
                    commission=0,
                    stamp_tax=0,
                    slippage=0,
                    min_commission=0,
                    calendar_bars=bars,
                    stock_records=[{"code": "600036", "name": "招商银行"}],
                    market_data_provider=lambda code: bars,
                )
        self.assertEqual([trade["direction"] for trade in result["trades"]], ["buy", "sell"])
        self.assertEqual([trade["size"] for trade in result["trades"]], [200, 200])
        self.assertEqual(result["equity_curve"][1]["position"], 200)
        self.assertEqual(result["equity_curve"][1]["buy_count"], 1)
        self.assertEqual(result["equity_curve"][2]["position"], 0)
        self.assertEqual(result["equity_curve"][2]["sell_count"], 1)
        self.assertEqual(result["meta"]["symbol"], "多标的")

    def test_get_history_rejects_nonpositive_count_and_query_helpers_are_removed(self):
        broker = PortfolioBroker(
            initial_cash=10_000,
            commission=0,
            stamp_tax=0,
            slippage=0,
            fill_mode="next_open",
            min_commission=0,
            market_data_provider=lambda code: [],
            stock_records=[{"code": "600036", "name": "招商银行"}],
        )
        self.assertFalse(hasattr(broker, "query_quote"))
        self.assertFalse(hasattr(broker, "query_history"))
        with self.assertRaises(ValueError):
            broker.get_history(["600036"], 0)
        with self.assertRaises(ValueError):
            broker.get_history(["600036"], True)

    def test_get_stock_names_without_codes_returns_entire_local_directory(self):
        stock_records = [
            {"code": "600036", "name": "招商银行"},
            {"code": "000001", "name": "平安银行"},
        ]
        broker = PortfolioBroker(
            initial_cash=10_000,
            commission=0,
            stamp_tax=0,
            slippage=0,
            fill_mode="next_open",
            min_commission=0,
            market_data_provider=lambda code: [],
            stock_records=stock_records,
        )
        self.assertEqual(broker.get_stock_names(), stock_records)
        self.assertEqual(broker.get_stock_names(None), stock_records)
        self.assertEqual(broker.get_stock_names(["000001"]), [stock_records[1]])

    def test_portfolio_get_cash_exposes_total_and_available(self):
        broker = PortfolioBroker(
            initial_cash=10_000,
            commission=0,
            stamp_tax=0,
            slippage=0,
            fill_mode="next_open",
            min_commission=0,
            market_data_provider=lambda code: [],
            stock_records=[{"code": "600036", "name": "招商银行"}],
        )
        bar = Bar("2024-01-01", 10, 10, 10, 10)
        broker.update_day(bar.date, {"600036": bar}, {"600036": [bar]})
        broker.buy("600036", 2_000)
        broker.execute_pending({"600036": bar}, "open")
        cash = broker.get_cash()
        self.assertIsInstance(cash, float)
        self.assertEqual(cash.available, 8_000)
        self.assertEqual(cash.total, broker.get_equity())
        self.assertEqual(cash.total, 10_000)

    def test_next_open_orders_fill_on_following_bar_and_charge_costs(self):
        broker = Broker("600036", 10_000, 0.001, 0.01, 0.0, "next_open", min_commission=0)
        first = Bar("2024-01-01", 10, 10, 10, 10)
        following = Bar("2024-01-02", 12, 14, 12, 14)
        last = Bar("2024-01-03", 14, 14, 14, 14)
        broker.execute_pending(first, first.open)
        broker.buy(100)
        broker.execute_pending(following, following.open)
        self.assertEqual(broker.position, 100)
        self.assertEqual(broker.trades[0]["price"], 12)
        self.assertEqual(broker.trades[0]["commission"], 1.2)
        broker.sell()
        broker.execute_pending(last, last.open)
        self.assertEqual(broker.trades[-1]["stamp_tax"], 14.0)
        self.assertAlmostEqual(broker.trades[-1]["pnl"], 183.4)

    def test_partial_sales_adjust_single_symbol_average_cost_by_realized_pnl(self):
        broker = Broker("600036", 100_000, 0, 0, 0, "close", min_commission=0)
        buy_bar = Bar("2024-01-01", 10, 10, 10, 10)
        broker.update_bar(buy_bar, [buy_bar])
        broker.buy(400)
        broker.execute_pending(buy_bar, buy_bar.close)

        profit_bar = Bar("2024-01-02", 12, 12, 12, 12)
        broker.update_bar(profit_bar, [buy_bar, profit_bar])
        broker.sell(100)
        broker.execute_pending(profit_bar, profit_bar.close)
        self.assertAlmostEqual(broker.average_cost, 28 / 3)

        loss_bar = Bar("2024-01-03", 8, 8, 8, 8)
        broker.update_bar(loss_bar, [buy_bar, profit_bar, loss_bar])
        broker.sell(100)
        broker.execute_pending(loss_bar, loss_bar.close)
        self.assertAlmostEqual(broker.average_cost, 10)

    def test_t_plus_one_and_minimum_commission_are_enforced(self):
        broker = Broker("600036", 10_000, 0.001, 0.001, 0.0, "close")
        first = Bar("2024-01-01", 10, 10, 10, 10)
        next_day = Bar("2024-01-02", 11, 11, 11, 11)
        broker.update_bar(first, [first])
        broker.buy(100)
        broker.execute_pending(first, first.close)
        self.assertEqual(broker.trades[0]["commission"], 5.0)
        broker.sell()
        broker.execute_pending(first, first.close)
        self.assertEqual(broker.position, 100)
        self.assertEqual(broker.get_position()["sellable_size"], 0)
        broker.update_bar(next_day, [first, next_day])
        broker.sell()
        broker.execute_pending(next_day, next_day.close)
        self.assertEqual(broker.position, 0)

    def test_broker_target_order_helpers(self):
        broker = Broker("600036", 100_000, 0, 0, 0, "next_open", min_commission=0)
        bars = [Bar("2024-01-01", 10, 12, 9, 11, 1000)]
        broker.update_bar(bars[0], bars)
        self.assertEqual(broker.current_bar.close, 11)
        cash = broker.get_cash()
        self.assertIsInstance(cash, float)
        self.assertEqual(cash, 100_000)
        self.assertEqual(cash.available, 100_000)
        self.assertEqual(cash.total, 100_000)
        self.assertEqual(cash * 0.9, 90_000)
        broker.buy(100)
        broker.execute_pending(bars[0], bars[0].close)
        cash = broker.get_cash()
        self.assertEqual(cash.available, 98_900)
        self.assertEqual(cash.total, 100_000)
        broker.order_target_percent(0.5)
        self.assertEqual(broker.pending, [("buy", 4_400)])
        self.assertAlmostEqual(sma([1, 2, 3], 2), 2.5)
        self.assertAlmostEqual(ema([1, 2, 3], 2), 2.5)
        self.assertTrue(cross_up([1, 3], [2, 2]))
        self.assertTrue(cross_up([2, 3], [2, 2]))
        self.assertFalse(cross_up([3, 3], [2, 2]))
        self.assertTrue(backtest.cross_down([3, 1], [2, 2]))
        self.assertTrue(backtest.cross_down([2, 1], [2, 2]))
        self.assertFalse(backtest.cross_down([1, 1], [2, 2]))
        self.assertTrue(cross_up(3.0, 2.0))
        self.assertFalse(cross_up(2.0, 3.0))
        self.assertTrue(backtest.cross_down(2.0, 3.0))
        self.assertFalse(backtest.cross_down(3.0, 2.0))
        with self.assertRaises(TypeError):
            cross_up([1, 2], 1.5)

    def test_performance_metrics_and_trade_count_are_round_trips(self):
        curve = [
            {"date": "2024-01-01", "nav": 1.0},
            {"date": "2024-01-02", "nav": 1.1},
            {"date": "2024-01-03", "nav": 0.99},
        ]
        trades = [
            {"direction": "buy", "pnl": None},
            {"direction": "sell", "pnl": 5},
            {"direction": "sell", "pnl": -1},
        ]
        performance = calculate_performance(curve, trades)
        self.assertAlmostEqual(performance["total_return"], -0.01)
        self.assertAlmostEqual(performance["max_drawdown"], -0.1)
        self.assertEqual(performance["trade_count"], 2)
        self.assertEqual(performance["win_rate"], 0.5)
        self.assertAlmostEqual(performance["profit_factor"], 5.0)
        self.assertEqual(performance["average_trade"], 2.0)
        self.assertEqual(performance["max_consecutive_wins"], 1)
        self.assertEqual(performance["max_consecutive_losses"], 1)

    def test_result_embeds_strategy_and_json_round_trips(self):
        bars = [Bar("2024-01-01", 10, 10, 10, 10), Bar("2024-01-02", 11, 11, 11, 11)]
        result = run_backtest(
            strategy_file="dual_ma.py",
            symbol="600036",
            symbol_name="招商银行",
            start_date="2024-01-01",
            end_date="2024-01-02",
            initial_cash=100_000,
            fill_mode="close",
            commission=0.00025,
            stamp_tax=0.001,
            slippage=0,
            bars=bars,
        )
        self.assertIn("class Strategy", result["strategy_code"])
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(backtest, "RESULTS_DIR", Path(directory)):
                saved_path = backtest.save_result(result)
                self.assertEqual(json.loads(saved_path.read_text(encoding="utf-8")), result)
                self.assertEqual(backtest.get_result(result["meta"]["id"]), result)
                self.assertEqual(len(backtest.list_results()), 1)
                self.assertIsNone(backtest.get_result("../outside"))

    def test_strategy_can_use_injected_indicators_and_broker_api(self):
        source = """class Strategy:
    def __init__(self):
        self.traded = False

    def on_bar(self, bar, broker):
        if not self.traded and bar.close > 0:
            broker.order_value(1200)
            self.traded = True
        prices = [item.close for item in broker.history]
        sma(prices, 2)
        ema(prices, 2)
        cross_up(prices, prices)
"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "custom.py").write_text(source, encoding="utf-8")
            bars = [
                Bar("2024-01-01", 10, 10, 10, 10),
                Bar("2024-01-02", 11, 11, 11, 11),
            ]
            with patch.object(backtest, "STRATEGIES_DIR", Path(directory)):
                result = run_backtest(
                    strategy_file="custom.py",
                    symbol="600036",
                    symbol_name="测试",
                    start_date="2024-01-01",
                    end_date="2024-01-02",
                    initial_cash=100_000,
                    fill_mode="close",
                    commission=0.00025,
                    stamp_tax=0.001,
                    slippage=0,
                    min_commission=5,
                    bars=bars,
                )
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["size"], 100)

    def test_tdx_reader_normalizes_daily_rows_and_filters_dates(self):
        class FakeReader:
            def daily(self, symbol):
                self.symbol = symbol
                return pd.DataFrame(
                    [
                        {"date": "20240101", "open": 10, "high": 12, "low": 9, "close": 11, "vol": 20},
                        {"date": "20240102", "open": 11, "high": 13, "low": 10, "close": 12, "vol": 30},
                    ]
                )

        with tempfile.TemporaryDirectory() as directory:
            with patch("mootdx.reader.Reader.factory", return_value=FakeReader()):
                bars = read_daily_bars(directory, "600036", "2024-01-02", "2024-01-02")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].date, "2024-01-02")
        self.assertEqual(bars[0].volume, 30)

    def test_tdx_reader_falls_back_to_market_prefixed_index_file(self):
        class FakeReader:
            def __init__(self):
                self.requested_symbols = []

            def daily(self, symbol):
                self.requested_symbols.append(symbol)
                if symbol == "sh000300":
                    return pd.DataFrame(
                        [{
                            "date": 20240102,
                            "open": 3500,
                            "high": 3550,
                            "low": 3480,
                            "close": 3525,
                            "vol": 123,
                        }]
                    )
                return pd.DataFrame()

        with tempfile.TemporaryDirectory() as directory:
            daily_dir = Path(directory) / "vipdoc" / "sh" / "lday"
            daily_dir.mkdir(parents=True)
            (daily_dir / "sh000300.day").touch()
            reader = FakeReader()
            with patch("mootdx.reader.Reader.factory", return_value=reader):
                bars = read_daily_bars(directory, "000300")
        self.assertEqual(reader.requested_symbols, ["000300", "sh000300"])
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].date, "2024-01-02")
        self.assertEqual(bars[0].close, 3525)

    def test_tdx_minute_reader_filters_the_requested_session(self):
        class FakeReader:
            def minute(self, symbol, suffix):
                self.request = (symbol, suffix)
                return pd.DataFrame(
                    [
                        {
                            "date": "2024-01-02 09:31:00",
                            "open": 10,
                            "high": 12,
                            "low": 9,
                            "close": 11,
                            "volume": 20,
                        },
                        {
                            "date": "2024-01-03 09:31:00",
                            "open": 11,
                            "high": 13,
                            "low": 10,
                            "close": 12,
                            "volume": 30,
                        },
                    ],
                    index=pd.to_datetime(["2024-01-02 09:31", "2024-01-03 09:31"]),
                )

        with tempfile.TemporaryDirectory() as directory:
            with patch("mootdx.reader.Reader.factory", return_value=FakeReader()):
                bars = read_intraday_bars(directory, "600036", "2024-01-02", 1)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["datetime"], "2024-01-02 09:31:00")
        self.assertEqual(bars[0]["volume"], 20)


if __name__ == "__main__":
    unittest.main()
