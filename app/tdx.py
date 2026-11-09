from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
import math
from pathlib import Path
import re
from typing import Any

from app.backtest import Bar

_TNF_LAYOUTS = (
    (50, 360, 0, 31),
    (50, 314, 1, 7),
    (50, 314, 0, 7),
    (0, 314, 1, 7),
    (0, 314, 0, 7),
)


class TdxDataError(RuntimeError):
    pass


def _is_common_a_share(market: str, code: str, name: str) -> bool:
    if name.startswith(("退市", "退", "摘牌")):
        return False
    if market == "sh":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "sz":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "bj":
        return code.startswith(("4", "8", "92"))
    return False


def _tnf_stock_records(content: bytes) -> list[tuple[str, str]]:
    best_records: list[tuple[str, str]] = []
    for header_size, record_size, code_offset, name_offset in _TNF_LAYOUTS:
        body = content[header_size:]
        if len(body) < record_size:
            continue
        parsed: list[tuple[str, str]] = []
        for offset in range(0, len(body) - record_size + 1, record_size):
            record = body[offset : offset + record_size]
            code_bytes = record[code_offset : code_offset + 6]
            if not re.fullmatch(rb"\d{6}", code_bytes):
                continue
            raw_name = record[name_offset : name_offset + 64].split(b"\0", 1)[0]
            name = raw_name.decode("gbk", errors="ignore").strip()
            if name and not any(ord(char) < 32 for char in name):
                parsed.append((code_bytes.decode("ascii"), name))
        if len(parsed) > len(best_records):
            best_records = parsed
    return best_records


@lru_cache(maxsize=8)
def _load_stock_records(
    files: tuple[tuple[str, int, int], ...],
) -> tuple[tuple[str, str, str], ...]:
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError as error:
        raise TdxDataError("缺少 pypinyin 依赖，请运行 pip install -r requirements.txt。") from error

    stocks: dict[str, str] = {}
    for filename, _, _ in files:
        try:
            records = _tnf_stock_records(Path(filename).read_bytes())
        except OSError as error:
            raise TdxDataError(f"读取通达信股票目录失败：{error}") from error
        for code, name in records:
            market = Path(filename).name[:2].lower()
            if _is_common_a_share(market, code, name):
                stocks[code] = name
    return tuple(
        (code, name, "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower())
        for code, name in sorted(stocks.items())
    )


def search_stocks(tdxdir: str, query: str, limit: int = 10) -> list[dict[str, str]]:
    if not tdxdir:
        raise TdxDataError("尚未配置通达信数据目录，请先在“设置”页面配置。")
    root = Path(tdxdir)
    if not root.is_dir():
        raise TdxDataError(f"通达信数据目录不存在：{tdxdir}")
    index_files = tuple(
        path
        for path in (
            root / "T0002" / "hq_cache" / "shs.tnf",
            root / "T0002" / "hq_cache" / "szs.tnf",
            root / "T0002" / "hq_cache" / "bjs.tnf",
            root / "T0002" / "hq_cache" / "shm.tnf",
            root / "T0002" / "hq_cache" / "szm.tnf",
        )
        if path.is_file()
    )
    if not index_files:
        raise TdxDataError(
            "未找到通达信股票索引文件（T0002/hq_cache/shs.tnf、szs.tnf、bjs.tnf）。"
        )
    cache_key = tuple(
        (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
        for path in index_files
    )
    stocks = _load_stock_records(cache_key)
    if not stocks:
        raise TdxDataError("通达信索引中未找到符合条件的沪深北普通 A 股。")

    needle = query.strip().lower()
    if not needle:
        return []
    matches = [
        stock for stock in stocks
        if needle in stock[0].lower() or needle in stock[1].lower() or needle in stock[2]
    ]
    matches.sort(
        key=lambda stock: (
            0 if stock[0].lower().startswith(needle) else
            1 if stock[1].lower().startswith(needle) else
            2 if stock[2].startswith(needle) else 3,
            stock[0],
        )
    )
    return [{"code": code, "name": name} for code, name, _ in matches[:limit]]


def list_stock_records(tdxdir: str) -> list[dict[str, str]]:
    if not tdxdir:
        raise TdxDataError("尚未配置通达信数据目录，请先在“设置”页面配置。")
    root = Path(tdxdir)
    if not root.is_dir():
        raise TdxDataError(f"通达信数据目录不存在：{tdxdir}")
    index_files = tuple(
        path
        for path in (
            root / "T0002" / "hq_cache" / "shs.tnf",
            root / "T0002" / "hq_cache" / "szs.tnf",
            root / "T0002" / "hq_cache" / "bjs.tnf",
            root / "T0002" / "hq_cache" / "shm.tnf",
            root / "T0002" / "hq_cache" / "szm.tnf",
        )
        if path.is_file()
    )
    if not index_files:
        raise TdxDataError(
            "未找到通达信股票索引文件（T0002/hq_cache/shs.tnf、szs.tnf、bjs.tnf）。"
        )
    cache_key = tuple(
        (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
        for path in index_files
    )
    stocks = _load_stock_records(cache_key)
    if not stocks:
        raise TdxDataError("通达信索引中未找到符合条件的沪深北普通 A 股。")
    return [{"code": code, "name": name} for code, name, _ in stocks]


def _date_value(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if text.isdigit() and len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def read_daily_bars(
    tdxdir: str,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[Bar]:
    if not tdxdir:
        raise TdxDataError("尚未配置通达信数据目录，请先在“设置”页面配置。")
    if not Path(tdxdir).is_dir():
        raise TdxDataError(f"通达信数据目录不存在：{tdxdir}")
    try:
        from mootdx.reader import Reader
    except ImportError as error:
        raise TdxDataError("缺少 mootdx 依赖，请运行 pip install -r requirements.txt。") from error

    try:
        reader = Reader.factory(market="std", tdxdir=tdxdir)
        symbols = [symbol]
        if re.fullmatch(r"\d{6}", symbol):
            root = Path(tdxdir)
            symbols.extend(
                f"{market}{symbol}"
                for market in ("sh", "sz", "bj")
                if (root / "vipdoc" / market / "lday" / f"{market}{symbol}.day").is_file()
            )
        frame = None
        read_errors: list[Exception] = []
        for candidate in dict.fromkeys(symbols):
            try:
                candidate_frame = reader.daily(symbol=candidate)
            except Exception as error:
                read_errors.append(error)
                continue
            if candidate_frame is not None and not candidate_frame.empty:
                frame = candidate_frame
                break
        if frame is None and read_errors:
            raise read_errors[-1]
    except Exception as error:
        raise TdxDataError(f"读取通达信日线失败：{error}") from error

    if frame is None or frame.empty:
        raise TdxDataError(f"没有找到标的 {symbol} 的日线数据，请检查通达信目录和代码。")
    if "date" not in frame.columns:
        frame = frame.reset_index()
        if "date" not in frame.columns and len(frame.columns):
            frame = frame.rename(columns={frame.columns[0]: "date"})
    aliases = {"vol": "volume"}
    frame = frame.rename(columns=aliases)
    required = {"date", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        missing = ", ".join(sorted(required - set(frame.columns)))
        raise TdxDataError(f"通达信日线数据缺少必要字段：{missing}")

    bars: list[Bar] = []
    for row in frame.to_dict(orient="records"):
        day = _date_value(row["date"])
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        prices = [float(row[column]) for column in ("open", "high", "low", "close")]
        if not all(math.isfinite(price) and price > 0 for price in prices):
            continue
        bars.append(
            Bar(
                date=day,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                volume=float(row.get("volume", 0) or 0),
            )
        )
    bars.sort(key=lambda bar: bar.date)
    if not bars:
        raise TdxDataError("指定日期范围内没有日线数据。")
    return bars


def read_intraday_bars(
    tdxdir: str,
    symbol: str,
    requested_date: str,
    interval: int = 1,
) -> list[dict[str, Any]]:
    if not tdxdir:
        raise TdxDataError("尚未配置通达信数据目录，请先在“设置”页面配置。")
    if not Path(tdxdir).is_dir():
        raise TdxDataError(f"通达信数据目录不存在：{tdxdir}")
    try:
        from mootdx.reader import Reader
    except ImportError as error:
        raise TdxDataError("缺少 mootdx 依赖，请运行 pip install -r requirements.txt。") from error
    try:
        reader = Reader.factory(market="std", tdxdir=tdxdir)
        frame = reader.minute(symbol=symbol, suffix=interval)
    except Exception as error:
        raise TdxDataError(f"读取通达信分时数据失败：{error}") from error
    if frame is None or frame.empty:
        raise TdxDataError(f"没有找到标的 {symbol} 的本地 {interval} 分钟数据。")

    import pandas as pd

    bars: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        raw_timestamp = row.get("date", row.get("datetime", index))
        timestamp = pd.to_datetime(raw_timestamp, errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.to_datetime(index, errors="coerce")
        if pd.isna(timestamp) or timestamp.strftime("%Y-%m-%d") != requested_date:
            continue
        prices = [float(row[column]) for column in ("open", "high", "low", "close")]
        if not all(math.isfinite(price) and price > 0 for price in prices):
            continue
        bars.append(
            {
                "datetime": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "time": timestamp.strftime("%H:%M"),
                "open": prices[0],
                "high": prices[1],
                "low": prices[2],
                "close": prices[3],
                "volume": float(row.get("volume", 0) or 0),
            }
        )
    bars.sort(key=lambda bar: bar["datetime"])
    if not bars:
        raise TdxDataError(
            f"{requested_date} 没有可用的 {interval} 分钟行情；通达信本地分时文件可能未下载或已过期。"
        )
    return bars
