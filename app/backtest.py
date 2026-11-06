from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import math
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STRATEGIES_DIR = ROOT / "strategies"
RESULTS_DIR = ROOT / "backtest_results"
SAFE_ID = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]+$")
SAFE_STRATEGY_FILE = re.compile(r"^(?!_)[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}\.py$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class StrategyExecutionError(RuntimeError):
    pass


class CashInfo(float):
    total: float
    available: float

    def __new__(cls, available: float, total: float) -> CashInfo:
        value = super().__new__(cls, available)
        value.total = total
        value.available = available
        return value


class RecentHistory(list[dict[str, Any]]):
    def __getitem__(self, index: int | slice) -> Any:
        if isinstance(index, int) and index < 0:
            index = -index
        return super().__getitem__(index)


class Broker:
    def __init__(
        self,
        symbol: str,
        initial_cash: float,
        commission: float,
        stamp_tax: float,
        slippage: float,
        fill_mode: str,
        min_commission: float = 5.0,
        lot_size: int = 100,
    ) -> None:
        self.symbol = symbol
        self.cash = initial_cash
        self.commission_rate = commission
        self.stamp_tax_rate = stamp_tax
        self.slippage = slippage
        self.fill_mode = fill_mode
        self.min_commission = min_commission
        self.lot_size = lot_size
        self.position = 0
        self.average_cost = 0.0
        self.trades: list[dict[str, Any]] = []
        self.pending: list[tuple[str, int | None]] = []
        self.history: list[Bar] = []
        self.current_bar: Bar | None = None
        self._current_date: str | None = None
        self._bought_today = 0

    def buy(self, size: int) -> None:
        if size % self.lot_size:
            raise ValueError(f"A 股买入数量必须为 {self.lot_size} 股的整数倍")
        self._queue("buy", size)

    def sell(self, size: int | None = None) -> None:
        if size is not None and size % self.lot_size and size != self.position:
            raise ValueError(f"卖出数量须为 {self.lot_size} 股的整数倍，或一次性卖出全部持仓")
        self._queue("sell", size)

    def order_value(self, value: float) -> None:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("委托金额必须为正数")
        price = self._reference_price()
        self.buy(math.floor(value / price / self.lot_size) * self.lot_size)

    def order_target_value(self, value: float) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError("目标持仓金额不能为负数")
        price = self._reference_price()
        target = math.floor(value / price / self.lot_size) * self.lot_size
        self._target_position(target)

    def order_target_percent(self, percent: float) -> None:
        if not math.isfinite(percent) or not 0 <= percent <= 1:
            raise ValueError("目标仓位比例必须在 0 到 1 之间")
        self.order_target_value(self.get_equity() * percent)

    def cancel_all(self) -> None:
        self.pending.clear()

    def get_cash(self) -> CashInfo:
        total = self.get_equity() if self.current_bar is not None else self.cash
        return CashInfo(self.cash, total)

    def get_position(self) -> dict[str, Any]:
        price = self._reference_price()
        market_value = self.position * price
        return {
            "symbol": self.symbol,
            "size": self.position,
            "sellable_size": max(0, self.position - self._bought_today),
            "average_cost": round(self.average_cost, 6),
            "market_value": round(market_value, 2),
            "unrealized_pnl": round((price - self.average_cost) * self.position, 2),
        }

    def get_equity(self, price: float | None = None) -> float:
        mark = price if price is not None else self._reference_price()
        return self.cash + self.position * mark

    def update_bar(self, bar: Bar, history: list[Bar]) -> None:
        if bar.date != self._current_date:
            self._current_date = bar.date
            self._bought_today = 0
        self.current_bar = bar
        self.history = history

    def _reference_price(self) -> float:
        if self.current_bar is None:
            raise ValueError("当前尚无可用行情")
        return self.current_bar.close

    def _target_position(self, target: int) -> None:
        difference = target - self.position
        if difference >= self.lot_size:
            self.buy(difference)
        elif difference <= -self.lot_size:
            self.sell(self.position if target == 0 else abs(difference))

    def _queue(self, direction: str, size: int | None) -> None:
        if direction == "buy" and (not isinstance(size, int) or isinstance(size, bool) or size <= 0):
            raise ValueError("买入数量必须为正整数")
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size <= 0):
            raise ValueError("交易数量必须为正整数")
        self.pending.append((direction, size))

    def execute_pending(self, bar: Bar, price: float) -> None:
        if bar.date != self._current_date:
            self.update_bar(bar, self.history)
        orders, self.pending = self.pending, []
        for direction, requested_size in orders:
            fill = price * (1 + self.slippage if direction == "buy" else 1 - self.slippage)
            if fill <= 0:
                continue
            if direction == "buy":
                quantity = requested_size or 0
                commission = self._commission(fill * quantity)
                if fill * quantity + commission > self.cash:
                    quantity = math.floor(
                        self.cash / (fill * (1 + self.commission_rate)) / self.lot_size
                    ) * self.lot_size
                    commission = self._commission(fill * quantity)
                    while quantity and fill * quantity + commission > self.cash:
                        quantity -= self.lot_size
                        commission = self._commission(fill * quantity)
                if quantity <= 0:
                    continue
                amount = fill * quantity
                self.cash -= amount + commission
                self.average_cost = (
                    self.average_cost * self.position + amount + commission
                ) / (self.position + quantity)
                self.position += quantity
                self._bought_today += quantity
                self.trades.append(
                    self._trade(bar, "buy", fill, quantity, amount, commission, None, 0.0)
                )
            else:
                sellable = max(0, self.position - self._bought_today)
                quantity = min(requested_size or sellable, sellable)
                if quantity != self.position and quantity % self.lot_size:
                    quantity = quantity // self.lot_size * self.lot_size
                if quantity <= 0:
                    continue
                amount = fill * quantity
                commission = self._commission(amount)
                tax = amount * self.stamp_tax_rate
                pnl = (fill - self.average_cost) * quantity - commission - tax
                self.cash += amount - commission - tax
                remaining_position = self.position - quantity
                if remaining_position == 0:
                    self.average_cost = 0.0
                else:
                    remaining_cost = self.average_cost * remaining_position - pnl
                    self.average_cost = remaining_cost / remaining_position
                self.position = remaining_position
                self.trades.append(
                    self._trade(bar, "sell", fill, quantity, amount, commission, pnl, tax)
                )

    def _commission(self, amount: float) -> float:
        fee = amount * self.commission_rate
        return max(fee, self.min_commission) if fee > 0 else 0.0

    def _trade(
        self,
        bar: Bar,
        direction: str,
        price: float,
        size: int,
        amount: float,
        commission: float,
        pnl: float | None,
        stamp_tax: float,
    ) -> dict[str, Any]:
        return {
            "datetime": bar.date,
            "symbol": self.symbol,
            "direction": direction,
            "price": round(price, 6),
            "size": size,
            "amount": round(amount, 2),
            "commission": round(commission, 2),
            "stamp_tax": round(stamp_tax, 2),
            "pnl": round(pnl, 2) if pnl is not None else None,
        }


class PortfolioBroker:
    def __init__(
        self,
        initial_cash: float,
        commission: float,
        stamp_tax: float,
        slippage: float,
        fill_mode: str,
        min_commission: float,
        market_data_provider: Any,
        stock_records: list[dict[str, str]],
    ) -> None:
        self.cash = initial_cash
        self.commission_rate = commission
        self.stamp_tax_rate = stamp_tax
        self.slippage = slippage
        self.fill_mode = fill_mode
        self.min_commission = min_commission
        self.lot_size = 100
        self.market_data_provider = market_data_provider
        self.stock_records = stock_records
        self._stock_names = {item["code"]: item["name"] for item in stock_records}
        self._market_cache: dict[str, list[Bar]] = {}
        self._current_bars: dict[str, Bar] = {}
        self._histories: dict[str, list[Bar]] = {}
        self._positions: dict[str, int] = {}
        self._average_costs: dict[str, float] = {}
        self._bought_today: dict[str, int] = {}
        self._latest_prices: dict[str, float] = {}
        self._current_date = ""
        self._context_symbol = ""
        self.pending: list[tuple[str, str, float | int | None]] = []
        self.trades: list[dict[str, Any]] = []

    @property
    def current_bar(self) -> Bar | None:
        return self._current_bars.get(self._context_symbol)

    @property
    def symbol(self) -> str:
        return self._context_symbol

    @property
    def position(self) -> int:
        return self._positions.get(self._context_symbol, 0)

    @property
    def history(self) -> list[Bar]:
        return self._histories.get(self._context_symbol, [])

    def update_day(self, day: str, bars: dict[str, Bar], histories: dict[str, list[Bar]]) -> None:
        if day != self._current_date:
            self._current_date = day
            self._bought_today.clear()
        self._current_bars = bars
        self._histories = histories
        for code, bar in bars.items():
            self._latest_prices[code] = bar.close

    def set_context(self, code: str) -> None:
        self._context_symbol = code

    def get_stock_codes(self) -> list[str]:
        return [item["code"] for item in self.stock_records]

    def get_stock_names(self, codes: list[str]) -> list[dict[str, str]]:
        if not isinstance(codes, (list, tuple)) or any(not isinstance(code, str) for code in codes):
            raise ValueError("股票代码参数必须为字符串数组")
        return [
            {"code": code, "name": self._stock_names[code]}
            for code in codes
            if code in self._stock_names
        ]

    def get_history(self, codes: list[str], count: int) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("历史行情条数必须为正整数")
        if not isinstance(codes, (list, tuple)) or any(not isinstance(code, str) for code in codes):
            raise ValueError("行情代码参数必须为股票代码字符串数组")
        result: dict[str, list[dict[str, Any]]] = {}
        for code in dict.fromkeys(codes):
            if code not in self._stock_names:
                raise ValueError(f"股票代码 {code} 不在本地股票代码列表中")
            if code not in self._market_cache:
                self._market_cache[code] = self.market_data_provider(code)
            history = [bar for bar in self._market_cache[code] if bar.date <= self._current_date]
            if history:
                self._histories[code] = history
                self._latest_prices[code] = history[-1].close
                if history[-1].date == self._current_date:
                    self._current_bars[code] = history[-1]
            result[code] = RecentHistory([
                {
                    "date": bar.date,
                    "日期": bar.date,
                    "O": bar.open,
                    "C": bar.close,
                    "H": bar.high,
                    "L": bar.low,
                    "V": bar.volume,
                }
                for bar in reversed(history[-count:])
            ])
        return result

    def buy(self, code: str | int, funds: float | None = None) -> None:
        if funds is None and isinstance(code, int) and not isinstance(code, bool):
            legacy_size = code
            code = self._context_symbol
            if not isinstance(legacy_size, int) or legacy_size <= 0 or legacy_size % self.lot_size:
                raise ValueError(f"A 股买入数量必须为 {self.lot_size} 股的整数倍")
            self._queue("buy", code, legacy_size)
            return
        if not isinstance(code, str) or not code.strip():
            raise ValueError("买入时必须提供股票代码")
        if not isinstance(funds, (int, float)) or isinstance(funds, bool) or not math.isfinite(funds) or funds <= 0:
            raise ValueError("买入资金必须为正数")
        if code not in self._stock_names:
            raise ValueError(f"股票代码 {code} 不在本地股票代码列表中")
        reference_price = self._reference_price(code)
        quantity = math.floor(float(funds) / reference_price / self.lot_size) * self.lot_size
        if quantity <= 0:
            raise ValueError(f"买入资金不足以按收盘价购买 {code} 的一手股票")
        self._queue("buy", code, quantity)

    def sell(self, code: str | None = None, funds: float | None = None) -> None:
        legacy = code is None
        target_code = self._context_symbol if legacy else code
        if not target_code:
            raise ValueError("卖出时必须提供股票代码")
        if not isinstance(target_code, str) or target_code not in self._stock_names:
            raise ValueError(f"股票代码 {target_code} 不在本地股票代码列表中")
        if funds is not None and (
            not isinstance(funds, (int, float))
            or isinstance(funds, bool)
            or not math.isfinite(funds)
            or funds <= 0
        ):
            raise ValueError("卖出资金必须为正数")
        self._reference_price(target_code)
        self._queue("sell", target_code, float(funds) if funds is not None else None)

    def order_value(self, value: float) -> None:
        if not self._context_symbol:
            raise ValueError("请先指定当前交易标的代码")
        self.buy(self._context_symbol, value)

    def order_target_value(self, value: float) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError("目标持仓金额不能为负数")
        code = self._context_symbol
        price = self._reference_price(code)
        target = math.floor(value / price / self.lot_size) * self.lot_size
        current = self._positions.get(code, 0)
        if target > current:
            self.buy(code, (target - current) * price)
        elif target < current:
            self.sell(code, (current - target) * price)

    def order_target_percent(self, percent: float) -> None:
        if not math.isfinite(percent) or not 0 <= percent <= 1:
            raise ValueError("目标仓位比例必须在 0 到 1 之间")
        self.order_target_value(self.get_equity() * percent)

    def cancel_all(self) -> None:
        self.pending.clear()

    def get_cash(self) -> CashInfo:
        return CashInfo(self.cash, self.get_equity())

    def get_position(self, code: str | None = None) -> dict[str, Any]:
        target = code or self._context_symbol
        price = self._latest_prices.get(target, 0.0)
        size = self._positions.get(target, 0)
        average_cost = self._average_costs.get(target, 0.0)
        return {
            "symbol": target,
            "size": size,
            "sellable_size": max(0, size - self._bought_today.get(target, 0)),
            "average_cost": round(average_cost, 6),
            "market_value": round(size * price, 2),
            "unrealized_pnl": round((price - average_cost) * size, 2),
        }

    def get_positions(self) -> dict[str, dict[str, Any]]:
        return {code: self.get_position(code) for code, size in self._positions.items() if size}

    def get_equity(self, price: float | None = None) -> float:
        if price is not None:
            return self.cash + self.position * price
        return self.cash + sum(
            size * self._latest_prices.get(code, 0.0) for code, size in self._positions.items()
        )

    def execute_pending(self, bars: dict[str, Bar], price_field: str) -> None:
        remaining: list[tuple[str, str, float | int | None]] = []
        orders, self.pending = self.pending, []
        for direction, code, requested in orders:
            bar = bars.get(code)
            if bar is None:
                remaining.append((direction, code, requested))
                continue
            price = getattr(bar, price_field)
            fill = price * (1 + self.slippage if direction == "buy" else 1 - self.slippage)
            if fill <= 0:
                continue
            position = self._positions.get(code, 0)
            if direction == "buy":
                quantity = (
                    int(requested)
                    if isinstance(requested, int)
                    else math.floor(float(requested or 0) / fill / self.lot_size) * self.lot_size
                )
                quantity = quantity // self.lot_size * self.lot_size
                commission = self._commission(fill * quantity)
                while quantity and fill * quantity + commission > self.cash:
                    quantity -= self.lot_size
                    commission = self._commission(fill * quantity)
                if quantity <= 0:
                    continue
                amount = fill * quantity
                average = self._average_costs.get(code, 0.0)
                self.cash -= amount + commission
                self._average_costs[code] = (
                    average * position + amount + commission
                ) / (position + quantity)
                self._positions[code] = position + quantity
                self._bought_today[code] = self._bought_today.get(code, 0) + quantity
                self.trades.append(self._trade(bar, code, direction, fill, quantity, amount, commission, None, 0.0))
            else:
                sellable = max(0, position - self._bought_today.get(code, 0))
                if requested is None:
                    quantity = sellable
                else:
                    quantity = math.floor(float(requested) / fill / self.lot_size) * self.lot_size
                    quantity = min(quantity, sellable)
                quantity = quantity if quantity == position else quantity // self.lot_size * self.lot_size
                if quantity <= 0:
                    continue
                amount = fill * quantity
                commission = self._commission(amount)
                tax = amount * self.stamp_tax_rate
                pnl = (fill - self._average_costs.get(code, 0.0)) * quantity - commission - tax
                self.cash += amount - commission - tax
                remaining_position = position - quantity
                self._positions[code] = remaining_position
                if remaining_position == 0:
                    self._average_costs.pop(code, None)
                    self._positions.pop(code, None)
                else:
                    remaining_cost = self._average_costs[code] * remaining_position - pnl
                    self._average_costs[code] = remaining_cost / remaining_position
                self.trades.append(self._trade(bar, code, direction, fill, quantity, amount, commission, pnl, tax))
        self.pending.extend(remaining)

    def _reference_price(self, code: str) -> float:
        bar = self._current_bars.get(code)
        price = bar.close if bar else self._latest_prices.get(code)
        if not price:
            raise ValueError(f"当前没有股票 {code} 的可用行情")
        return price

    def _queue(self, direction: str, code: str, requested: float | int | None) -> None:
        self.pending.append((direction, code, requested))

    def _commission(self, amount: float) -> float:
        fee = amount * self.commission_rate
        return max(fee, self.min_commission) if fee > 0 else 0.0

    def _trade(
        self, bar: Bar, code: str, direction: str, price: float, size: int,
        amount: float, commission: float, pnl: float | None, stamp_tax: float,
    ) -> dict[str, Any]:
        return {
            "datetime": bar.date,
            "symbol": code,
            "symbol_name": self._stock_names.get(code, code),
            "direction": direction,
            "price": round(price, 6),
            "size": size,
            "amount": round(amount, 2),
            "commission": round(commission, 2),
            "stamp_tax": round(stamp_tax, 2),
            "pnl": round(pnl, 2) if pnl is not None else None,
        }


def discover_strategies() -> list[dict[str, Any]]:
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    return [
        {
            "file": path.name,
            "name": path.stem,
            "size": path.stat().st_size,
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        }
        for path in sorted(STRATEGIES_DIR.glob("*.py"))
        if not path.name.startswith("_")
    ]


def _strategy_path(filename: str) -> Path:
    if not SAFE_STRATEGY_FILE.fullmatch(filename):
        raise ValueError("策略文件名无效")
    path = (STRATEGIES_DIR / filename).resolve()
    if path.parent != STRATEGIES_DIR.resolve() or not path.is_file():
        raise FileNotFoundError("找不到指定策略")
    return path


def _load_strategy(
    path: Path, injected_functions: dict[str, Any] | None = None
) -> tuple[Any, str]:
    source = path.read_text(encoding="utf-8")
    module_name = f"strategy_{path.stem}_{abs(hash(source))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError("无法加载策略文件")
    module = importlib.util.module_from_spec(spec)
    module.sma = sma
    module.ema = ema
    module.cross_up = cross_up
    module.cross_down = cross_down
    for name, function in (injected_functions or {}).items():
        module.__dict__[name] = function
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise StrategyExecutionError(f"策略初始化失败：{error}") from error
    strategy_class = getattr(module, "Strategy", None)
    if strategy_class is None:
        raise ValueError("策略文件必须定义 Strategy 类")
    try:
        strategy = strategy_class()
    except Exception as error:
        raise StrategyExecutionError(f"策略初始化失败：{error}") from error
    if not callable(getattr(strategy, "on_bar", None)):
        if not callable(getattr(strategy, "on_day", None)):
            raise ValueError("Strategy 必须实现 on_day(day, broker) 或 on_bar(bar, broker)")
    if inspect.iscoroutinefunction(getattr(strategy, "on_bar", None)):
        raise ValueError("Strategy.on_bar 必须是同步方法")
    if inspect.iscoroutinefunction(getattr(strategy, "on_day", None)):
        raise ValueError("Strategy.on_day 必须是同步方法")
    return strategy, source


def sma(values: list[float], period: int) -> float | None:
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError("均线周期必须为正整数")
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError("均线周期必须为正整数")
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def cross_up(fast: float | list[float], slow: float | list[float]) -> bool:
    if isinstance(fast, (int, float)) and not isinstance(fast, bool):
        if not isinstance(slow, (int, float)) or isinstance(slow, bool):
            raise TypeError("fast 和 slow 必须同时为数值或数值序列")
        return fast > slow
    if isinstance(slow, (int, float)):
        raise TypeError("fast 和 slow 必须同时为数值或数值序列")
    return len(fast) >= 2 and len(slow) >= 2 and fast[-2] <= slow[-2] and fast[-1] > slow[-1]


def cross_down(fast: float | list[float], slow: float | list[float]) -> bool:
    if isinstance(fast, (int, float)) and not isinstance(fast, bool):
        if not isinstance(slow, (int, float)) or isinstance(slow, bool):
            raise TypeError("fast 和 slow 必须同时为数值或数值序列")
        return fast < slow
    if isinstance(slow, (int, float)):
        raise TypeError("fast 和 slow 必须同时为数值或数值序列")
    return len(fast) >= 2 and len(slow) >= 2 and fast[-2] >= slow[-2] and fast[-1] < slow[-1]


def _finite(value: float) -> float:
    return value if math.isfinite(value) else 0.0


def calculate_performance(
    equity_curve: list[dict[str, Any]], trades: list[dict[str, Any]]
) -> dict[str, Any]:
    if not equity_curve:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "trade_count": 0,
        }

    navs = [float(point["nav"]) for point in equity_curve]
    total_return = navs[-1] - 1
    first_date = date.fromisoformat(equity_curve[0]["date"])
    last_date = date.fromisoformat(equity_curve[-1]["date"])
    elapsed_days = max((last_date - first_date).days, 1)
    annual_return = (navs[-1] ** (365.25 / elapsed_days) - 1) if navs[-1] > 0 else -1.0
    peak = navs[0]
    max_drawdown = 0.0
    for nav in navs:
        peak = max(peak, nav)
        if peak:
            max_drawdown = min(max_drawdown, nav / peak - 1)
    returns = [
        navs[index] / navs[index - 1] - 1
        for index in range(1, len(navs))
        if navs[index - 1] != 0
    ]
    sharpe = 0.0
    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        if variance > 0:
            sharpe = mean / math.sqrt(variance) * math.sqrt(252)
    sells = [trade for trade in trades if trade["direction"] == "sell"]
    wins = sum(1 for trade in sells if (trade["pnl"] or 0) > 0)
    sell_pnls = [float(trade["pnl"] or 0) for trade in sells]
    profitable = [value for value in sell_pnls if value > 0]
    losing = [value for value in sell_pnls if value < 0]
    gross_profit = sum(profitable)
    gross_loss = sum(losing)
    max_consecutive_wins = max_consecutive_losses = streak_wins = streak_losses = 0
    for value in sell_pnls:
        if value > 0:
            streak_wins += 1
            streak_losses = 0
        elif value < 0:
            streak_losses += 1
            streak_wins = 0
        else:
            streak_wins = streak_losses = 0
        max_consecutive_wins = max(max_consecutive_wins, streak_wins)
        max_consecutive_losses = max(max_consecutive_losses, streak_losses)
    exposures = [
        float(point.get("market_value", 0)) / max(float(point.get("equity", 0)), 1e-12)
        for point in equity_curve
    ]
    return {
        "total_return": round(_finite(total_return), 8),
        "annual_return": round(_finite(annual_return), 8),
        "max_drawdown": round(_finite(max_drawdown), 8),
        "sharpe": round(_finite(sharpe), 6),
        "win_rate": round(wins / len(sells), 8) if sells else 0.0,
        "trade_count": len(sells),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / abs(gross_loss), 6) if gross_loss else None,
        "average_trade": round(sum(sell_pnls) / len(sell_pnls), 2) if sell_pnls else 0.0,
        "average_win": round(gross_profit / len(profitable), 2) if profitable else 0.0,
        "average_loss": round(gross_loss / len(losing), 2) if losing else 0.0,
        "best_trade": round(max(sell_pnls), 2) if sell_pnls else 0.0,
        "worst_trade": round(min(sell_pnls), 2) if sell_pnls else 0.0,
        "max_consecutive_wins": max_consecutive_wins,
        "max_consecutive_losses": max_consecutive_losses,
        "average_exposure": round(sum(exposures) / len(exposures), 6) if exposures else 0.0,
    }


def run_backtest(
    *,
    strategy_file: str,
    symbol: str,
    symbol_name: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
    fill_mode: str,
    commission: float,
    stamp_tax: float,
    slippage: float,
    min_commission: float = 5.0,
    bars: list[Bar],
) -> dict[str, Any]:
    if fill_mode not in {"next_open", "close"}:
        raise ValueError("成交模式只支持 next_open 或 close")
    if not bars:
        raise ValueError("指定区间没有可用行情数据")
    strategy_path = _strategy_path(strategy_file)
    strategy, source = _load_strategy(strategy_path)
    broker = Broker(
        symbol, initial_cash, commission, stamp_tax, slippage, fill_mode, min_commission
    )
    equity_curve: list[dict[str, Any]] = []

    for index, bar in enumerate(bars):
        broker.update_bar(bar, bars[: index + 1])
        if fill_mode == "next_open":
            broker.execute_pending(bar, bar.open)
        try:
            strategy.on_bar(bar, broker)
        except Exception as error:
            raise StrategyExecutionError(f"{bar.date} 策略执行失败：{error}") from error
        if fill_mode == "close":
            broker.execute_pending(bar, bar.close)
        equity = broker.get_equity(bar.close)
        market_value = broker.position * bar.close
        equity_curve.append(
            {
                "date": bar.date,
                "nav": round(equity / initial_cash, 8),
                "equity": round(equity, 2),
                "cash": round(broker.cash, 2),
                "position": broker.position,
                "market_value": round(market_value, 2),
            }
        )

    now = datetime.now().astimezone()
    safe_strategy = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", strategy_path.stem).strip("_") or "strategy"
    safe_symbol = re.sub(r"[^A-Za-z0-9_-]+", "", symbol)
    result_id = f"{now:%Y%m%d_%H%M%S_%f}_{safe_strategy}_{safe_symbol}"
    performance = calculate_performance(equity_curve, broker.trades)
    return {
        "meta": {
            "id": result_id,
            "created_at": now.isoformat(timespec="seconds"),
            "strategy_name": strategy_path.stem,
            "strategy_file": strategy_file,
            "symbol": symbol,
            "symbol_name": symbol_name or symbol,
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
            "fill_mode": fill_mode,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "slippage": slippage,
            "min_commission": min_commission,
        },
        "strategy_code": source,
        "performance": performance,
        "equity_curve": equity_curve,
        "trades": broker.trades,
        "unfilled_orders": [
            {"direction": direction, "size": size} for direction, size in broker.pending
        ],
    }


def run_portfolio_backtest(
    *,
    strategy_file: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
    fill_mode: str,
    commission: float,
    stamp_tax: float,
    slippage: float,
    min_commission: float,
    calendar_bars: list[Bar],
    stock_records: list[dict[str, str]],
    market_data_provider: Any,
) -> dict[str, Any]:
    if fill_mode not in {"next_open", "close"}:
        raise ValueError("成交模式只支持 next_open 或 close")
    if not calendar_bars:
        raise ValueError("指定区间没有可用交易日历")
    if not stock_records:
        raise ValueError("通达信股票目录为空")
    strategy_path = _strategy_path(strategy_file)
    broker = PortfolioBroker(
        initial_cash, commission, stamp_tax, slippage, fill_mode, min_commission,
        market_data_provider, stock_records,
    )
    injected_functions = {
        "get_stock_codes": broker.get_stock_codes,
        "get_stock_names": broker.get_stock_names,
        "get_history": broker.get_history,
    }
    strategy, source = _load_strategy(strategy_path, injected_functions)
    if not callable(getattr(strategy, "on_day", None)):
        raise ValueError("多标的回测策略必须实现 on_day(self, date, broker)。")

    equity_curve: list[dict[str, Any]] = []
    histories: dict[str, list[Bar]] = {}
    dates = sorted({bar.date for bar in calendar_bars if start_date <= bar.date <= end_date})
    for day in dates:
        current_bars: dict[str, Bar] = {}
        for code, cached_bars in broker._market_cache.items():
            history = [bar for bar in cached_bars if bar.date <= day]
            if history:
                histories[code] = history
                if history[-1].date == day:
                    current_bars[code] = history[-1]
        broker.update_day(day, current_bars, histories)
        if fill_mode == "next_open":
            broker.execute_pending(current_bars, "open")
        try:
            strategy.on_day(day, broker)
        except Exception as error:
            raise StrategyExecutionError(f"{day} 策略执行失败：{error}") from error
        if fill_mode == "close":
            broker.execute_pending(broker._current_bars, "close")
        market_value = sum(
            size * broker._latest_prices.get(code, 0.0)
            for code, size in broker._positions.items()
        )
        equity = broker.cash + market_value
        todays_trades = [trade for trade in broker.trades if trade["datetime"] == day]
        equity_curve.append(
            {
                "date": day,
                "nav": round(equity / initial_cash, 8),
                "equity": round(equity, 2),
                "cash": round(broker.cash, 2),
                "position": sum(broker._positions.values()),
                "market_value": round(market_value, 2),
                "buy_count": sum(trade["direction"] == "buy" for trade in todays_trades),
                "sell_count": sum(trade["direction"] == "sell" for trade in todays_trades),
            }
        )

    now = datetime.now().astimezone()
    safe_strategy = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", strategy_path.stem).strip("_") or "strategy"
    result_id = f"{now:%Y%m%d_%H%M%S_%f}_{safe_strategy}_portfolio"
    performance = calculate_performance(equity_curve, broker.trades)
    return {
        "meta": {
            "id": result_id,
            "created_at": now.isoformat(timespec="seconds"),
            "strategy_name": strategy_path.stem,
            "strategy_file": strategy_file,
            "symbol": "多标的",
            "symbol_name": "策略股票池",
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
            "fill_mode": fill_mode,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "slippage": slippage,
            "min_commission": min_commission,
        },
        "strategy_code": source,
        "performance": performance,
        "equity_curve": equity_curve,
        "trades": broker.trades,
        "unfilled_orders": [
            {"symbol": code, "direction": direction, "size_or_funds": requested}
            for direction, code, requested in broker.pending
        ],
    }


def save_result(result: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_id = result["meta"]["id"]
    if not SAFE_ID.fullmatch(result_id):
        raise ValueError("回测结果 ID 无效")
    destination = RESULTS_DIR / f"{result_id}.json"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=RESULTS_DIR,
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(result, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
        temporary_path.replace(destination)
        return destination
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def list_results() -> list[dict[str, Any]]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for path in RESULTS_DIR.glob("*.json"):
        try:
            with path.open(encoding="utf-8") as result_file:
                result = json.load(result_file)
            meta = result["meta"]
            performance = result["performance"]
            summaries.append(
                {
                    "id": meta["id"],
                    "created_at": meta["created_at"],
                    "strategy_name": meta["strategy_name"],
                    "symbol": meta["symbol"],
                    "symbol_name": meta.get("symbol_name", meta["symbol"]),
                    "start_date": meta["start_date"],
                    "end_date": meta["end_date"],
                    "total_return": performance["total_return"],
                    "max_drawdown": performance["max_drawdown"],
                }
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.warning("Skipping invalid backtest result %s: %s", path.name, error)
            continue
    return sorted(summaries, key=lambda item: item["created_at"], reverse=True)


def get_result(result_id: str) -> dict[str, Any] | None:
    if not SAFE_ID.fullmatch(result_id):
        return None
    path = RESULTS_DIR / f"{result_id}.json"
    try:
        with path.open(encoding="utf-8") as result_file:
            result = json.load(result_file)
    except FileNotFoundError:
        return None
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("meta"), dict)
        or result["meta"].get("id") != result_id
    ):
        return None
    return result
