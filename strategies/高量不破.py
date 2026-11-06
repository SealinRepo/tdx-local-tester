class Strategy:
    """在每日回调中查询股票池与行情，并按代码提交交易委托。"""

    def __init__(self):
        self.watchlist = ["600036"]
        self.holdings = set()

    def on_day(self, day, broker):
        """每天收盘后调用；下单将在所选成交模式下执行。"""
        quotes = get_history(self.watchlist, 21)
        for code in self.watchlist:
            history = quotes.get(code, [])
            if len(history) < 21:
                continue
            closes = [item["C"] for item in reversed(history[:21])]
            short_ma = sma(closes[-5:], 5)
            long_ma = sma(closes, 20)
            if short_ma is None or long_ma is None:
                continue
            latest_close = history[0]["C"]
            position = broker.get_position(code)
            if cross_up(short_ma, long_ma) and position["size"] == 0:
                broker.buy(code, broker.get_cash() * 0.9)
            if cross_down(short_ma, long_ma) and position["size"] > 0:
                broker.sell(code)
