class Strategy:
    """用双均线信号回测指定股票池中的标的。"""

    watchlist = ["000700"]
    short_window = 5
    long_window = 20
    

    def __init__(self):
        self.closes = []
        self.in_position = False

    def on_bar(self, bar, broker):
        self.closes.append(bar.close)
        if len(self.closes) < self.long_window + 1:
            return
        previous_short = sma(self.closes[-self.short_window - 1:-1], self.short_window)
        history = get_history(codes, count)
        previous_long = sma(self.closes[-self.long_window - 1:-1], self.long_window)
        short_ma = sma(self.closes[-self.short_window:], self.short_window)
        long_ma = sma(self.closes[-self.long_window:], self.long_window)
        if previous_short is None or previous_long is None or short_ma is None or long_ma is None:
            return
        if not self.in_position and previous_short <= previous_long and short_ma > long_ma:
            broker.order_value(broker.get_cash() * 0.9)
            self.in_position = True
        elif self.in_position and previous_short >= previous_long and short_ma < long_ma:
            broker.sell()
            self.in_position = False

    def on_day(self, day, broker):
        quotes = get_history(self.watchlist, self.long_window + 1)
        for code in self.watchlist:
            history = quotes.get(code, [])
            if len(history) <= self.long_window:
                continue
            closes = [item["C"] for item in reversed(history[: self.long_window + 1])]
            previous_short = sma(closes[:-1][-self.short_window :], self.short_window)
            previous_long = sma(closes[:-1][-self.long_window :], self.long_window)
            short_ma = sma(closes[-self.short_window :], self.short_window)
            long_ma = sma(closes[-self.long_window :], self.long_window)
            if None in (previous_short, previous_long, short_ma, long_ma):
                continue

            position = broker.get_position(code)
            if position["size"] == 0 and previous_short <= previous_long and short_ma > long_ma:
                broker.buy(code, broker.get_cash() * 0.9)
            elif position["size"] and previous_short >= previous_long and short_ma < long_ma:
                broker.sell(code)
