from __future__ import annotations

import ast
import json
import mimetypes
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.backtest import (
    ROOT,
    RESULTS_DIR,
    STRATEGIES_DIR,
    SAFE_STRATEGY_FILE,
    StrategyExecutionError,
    discover_strategies,
    get_result,
    list_results,
    run_backtest,
    run_portfolio_backtest,
    save_result,
)
from app.tdx import (
    TdxDataError,
    list_stock_records,
    read_daily_bars,
    read_intraday_bars,
    search_stocks,
)


CONFIG_PATH = ROOT / "config.json"
STATIC_DIR = ROOT / "static"
API_MANUAL = ROOT / "document" / "策略函数说明.md"
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")

@asynccontextmanager
async def lifespan(_: FastAPI):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="本地交易回测与看盘系统", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ConfigInput(BaseModel):
    tdxdir: str = Field(min_length=1, max_length=1024)


class BacktestInput(BaseModel):
    strategy_file: str = Field(min_length=1)
    symbol: str | None = Field(default=None, min_length=1, max_length=16)
    symbol_name: str | None = Field(default=None, max_length=80)
    start_date: str
    end_date: str
    initial_cash: float = Field(gt=0, le=1_000_000_000_000)
    fill_mode: str = "next_open"
    commission: float = Field(default=0.00025, ge=0, le=0.1)
    stamp_tax: float = Field(default=0.001, ge=0, le=0.1)
    slippage: float = Field(default=0, ge=0, le=0.5)
    min_commission: float = Field(default=5, ge=0, le=100_000)

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value.isalnum():
            raise ValueError("标的代码只能包含字母和数字")
        return value

    @field_validator("start_date", "end_date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        from datetime import date

        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("日期格式必须为 YYYY-MM-DD") from error
        return value

    @field_validator("fill_mode")
    @classmethod
    def valid_fill_mode(cls, value: str) -> str:
        if value not in {"next_open", "close"}:
            raise ValueError("成交模式只支持 next_open 或 close")
        return value


class StrategyInput(BaseModel):
    filename: str = Field(min_length=4, max_length=68)
    code: str = Field(min_length=1, max_length=300_000)


class StrategySource(BaseModel):
    code: str = Field(min_length=1, max_length=300_000)


class StrategyValidation(StrategySource):
    filename: str = "strategy.py"


def _strategy_file(filename: str) -> Path:
    if not SAFE_STRATEGY_FILE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="脚本名仅支持中英文、数字、下划线和连字符，并以 .py 结尾。")
    path = (STRATEGIES_DIR / filename).resolve()
    if path.parent != STRATEGIES_DIR.resolve():
        raise HTTPException(status_code=400, detail="策略路径无效。")
    return path


def _validate_strategy_code(filename: str, source: str) -> None:
    try:
        tree = ast.parse(source, filename=filename)
        compile(tree, filename, "exec")
    except SyntaxError as error:
        raise HTTPException(
            status_code=422,
            detail=f"第 {error.lineno or 1} 行语法错误：{error.msg}",
        ) from error
    strategy_class = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Strategy"),
        None,
    )
    if strategy_class is None:
        raise HTTPException(status_code=422, detail="脚本必须定义 class Strategy。")
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"on_bar", "on_day"}
        for node in strategy_class.body
    ):
        raise HTTPException(status_code=422, detail="Strategy 必须实现 on_day(day, broker) 或 on_bar(bar, broker)。")


def _save_strategy_file(path: Path, source: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            suffix=".py.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(source)
        temporary_path.replace(path)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"保存策略失败：{error}") from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _read_config() -> dict[str, Any]:
    try:
        with CONFIG_PATH.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
        if not isinstance(config, dict):
            raise ValueError("配置文件格式错误")
        return {"tdxdir": str(config.get("tdxdir", ""))}
    except FileNotFoundError:
        return {"tdxdir": ""}


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict[str, str]:
    return _read_config()


@app.post("/api/config")
def set_config(config: ConfigInput) -> dict[str, str]:
    tdxdir = str(Path(config.tdxdir).expanduser().resolve())
    if not Path(tdxdir).is_dir():
        raise HTTPException(status_code=400, detail="该通达信数据目录不存在或不是文件夹。")
    temporary = CONFIG_PATH.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as config_file:
            json.dump({"tdxdir": tdxdir}, config_file, ensure_ascii=False, indent=2)
            config_file.write("\n")
        temporary.replace(CONFIG_PATH)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"保存配置失败：{error}") from error
    return {"tdxdir": tdxdir}


@app.get("/api/strategies")
def strategies() -> list[dict[str, Any]]:
    return discover_strategies()


@app.get("/api/strategies/help", response_class=PlainTextResponse)
def strategy_help() -> PlainTextResponse:
    try:
        return PlainTextResponse(API_MANUAL.read_text(encoding="utf-8"))
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"读取策略函数说明失败：{error}") from error


@app.post("/api/strategies")
def create_strategy(request: StrategyInput) -> dict[str, Any]:
    path = _strategy_file(request.filename)
    if path.exists():
        raise HTTPException(status_code=409, detail="同名策略文件已存在。")
    _validate_strategy_code(request.filename, request.code)
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    _save_strategy_file(path, request.code)
    return {"file": path.name, "name": path.stem, "code": request.code}


@app.post("/api/strategies/validate")
def validate_strategy(request: StrategyValidation) -> dict[str, bool]:
    if not SAFE_STRATEGY_FILE.fullmatch(request.filename):
        raise HTTPException(status_code=400, detail="策略文件名无效。")
    _validate_strategy_code(request.filename, request.code)
    return {"valid": True}


@app.get("/api/strategies/{filename}")
def get_strategy(filename: str) -> dict[str, str]:
    path = _strategy_file(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="策略文件不存在。")
    try:
        return {"file": path.name, "name": path.stem, "code": path.read_text(encoding="utf-8")}
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"读取策略失败：{error}") from error


@app.put("/api/strategies/{filename}")
def update_strategy(filename: str, request: StrategySource) -> dict[str, Any]:
    path = _strategy_file(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="策略文件不存在。")
    _validate_strategy_code(filename, request.code)
    _save_strategy_file(path, request.code)
    return {"file": path.name, "name": path.stem, "code": request.code}


@app.delete("/api/strategies/{filename}")
def delete_strategy(filename: str) -> dict[str, bool]:
    path = _strategy_file(filename)
    try:
        path.unlink()
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="策略文件不存在。") from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"删除策略失败：{error}") from error
    return {"deleted": True}


@app.get("/api/stocks/search")
def stock_search(
    q: str = Query(min_length=1, max_length=80),
    limit: int = Query(default=10, ge=1, le=30),
) -> list[dict[str, str]]:
    try:
        return search_stocks(_read_config()["tdxdir"], q, limit)
    except TdxDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/kline")
def kline(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    if not symbol.strip().isalnum():
        raise HTTPException(status_code=400, detail="标的代码只能包含字母和数字。")
    try:
        bars = read_daily_bars(_read_config()["tdxdir"], symbol.strip(), start_date, end_date)
    except TdxDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return [
        {
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]


@app.get("/api/kline/intraday")
def intraday(
    symbol: str,
    date: str,
    interval: int = 1,
) -> list[dict[str, Any]]:
    if not symbol.strip().isalnum():
        raise HTTPException(status_code=400, detail="标的代码只能包含字母和数字。")
    if interval not in {1, 5}:
        raise HTTPException(status_code=400, detail="分时周期只支持 1 分钟或 5 分钟。")
    from datetime import date as date_type

    try:
        date_type.fromisoformat(date)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="日期格式必须为 YYYY-MM-DD。") from error
    try:
        bars = read_intraday_bars(_read_config()["tdxdir"], symbol.strip(), date, interval)
    except TdxDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return bars


@app.post("/api/backtest/run")
def backtest(request: BacktestInput) -> dict[str, Any]:
    if request.start_date > request.end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期。")
    try:
        tdxdir = _read_config()["tdxdir"]
        if request.symbol:
            bars = read_daily_bars(
                tdxdir, request.symbol, request.start_date, request.end_date
            )
            result = run_backtest(
                strategy_file=request.strategy_file,
                symbol=request.symbol,
                symbol_name=request.symbol_name or request.symbol,
                start_date=request.start_date,
                end_date=request.end_date,
                initial_cash=request.initial_cash,
                fill_mode=request.fill_mode,
                commission=request.commission,
                stamp_tax=request.stamp_tax,
                slippage=request.slippage,
                min_commission=request.min_commission,
                bars=bars,
            )
        else:
            calendar_bars = read_daily_bars(
                tdxdir, "sh000001", request.start_date, request.end_date
            )
            stock_records = list_stock_records(tdxdir)

            def market_data_provider(code: str):
                return read_daily_bars(tdxdir, code, request.start_date, request.end_date)

            result = run_portfolio_backtest(
                strategy_file=request.strategy_file,
                start_date=request.start_date,
                end_date=request.end_date,
                initial_cash=request.initial_cash,
                fill_mode=request.fill_mode,
                commission=request.commission,
                stamp_tax=request.stamp_tax,
                slippage=request.slippage,
                min_commission=request.min_commission,
                calendar_bars=calendar_bars,
                stock_records=stock_records,
                market_data_provider=market_data_provider,
            )
    except TdxDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except StrategyExecutionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        save_result(result)
        result["saved"] = True
        result["persistence_error"] = None
    except (OSError, ValueError) as error:
        result["saved"] = False
        result["persistence_error"] = f"回测已完成，但结果写入失败：{error}"
    return result


@app.get("/api/backtest/list")
def backtest_list() -> list[dict[str, Any]]:
    return list_results()


@app.get("/api/backtest/detail")
def backtest_detail(id: str) -> dict[str, Any]:
    result = get_result(id)
    if result is None:
        raise HTTPException(status_code=404, detail="找不到该回测记录。")
    return result


@app.delete("/api/backtest/delete")
def delete_backtest(id: str) -> dict[str, bool]:
    from app.backtest import SAFE_ID

    if not SAFE_ID.fullmatch(id):
        raise HTTPException(status_code=400, detail="回测结果 ID 无效。")
    path = RESULTS_DIR / f"{id}.json"
    try:
        path.unlink()
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="找不到该回测记录。") from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"删除回测记录失败：{error}") from error
    return {"deleted": True}
