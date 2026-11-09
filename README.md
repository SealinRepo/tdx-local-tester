## 鸣谢

感谢以下开源项目为本系统提供基础能力：

| 开源项目 | 用途 |
|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | 提供 Python 后端 Web 框架及 REST API。 |
| [Uvicorn](https://github.com/encode/uvicorn) | 运行 FastAPI 应用的 ASGI 服务器。 |
| [mootdx](https://github.com/mootdx/mootdx) | 读取本地通达信日线与分时行情数据。 |
| [pandas](https://github.com/pandas-dev/pandas) | 辅助处理和整理行情数据。 |
| [pypinyin](https://github.com/mozillazg/python-pinyin) | 将证券名称转换为拼音首字母，用于股票搜索。 |
| [CodeMirror](https://github.com/codemirror/dev)（含 `@codemirror/lang-python`、`@codemirror/autocomplete`） | 提供 Python 策略代码编辑器、语法高亮和自动补全。 |
| [Marked](https://github.com/markedjs/marked) | 将策略函数帮助文档中的 Markdown 转换为 HTML。 |
| [DOMPurify](https://github.com/cure53/DOMPurify) | 清理帮助文档渲染后的 HTML，降低插入页面时的风险。 |
| [esbuild](https://github.com/evanw/esbuild) | 将前端代码及依赖打包为可在本地直接运行的静态资源。 |

项目使用本地通达信数据，不通过上述依赖请求远程行情服务。

# 本地交易回测与看盘系统

单用户、单进程的 FastAPI 本地应用：直接读取通达信本地日线与分时数据，编辑并运行 Python 策略，并将完整回测结果保存为 `backtest_results/*.json`。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 <http://127.0.0.1:8000>，在“设置”页填写通达信安装目录。目录配置写入项目根目录的 `config.json`；回测结果目录会在首次访问时自动创建。

回测与看盘日期默认设置为本地日期往前一年至今天。股票搜索读取通达信目录中的 `T0002\hq_cache\shs.tnf`、`szs.tnf` 和 `bjs.tnf` 索引，可按数字代码、中文名称或拼音首字母搜索；若缺少索引文件，请先使用通达信更新本地数据。

前端编辑器资源已随项目打包，无需 Node.js 即可运行。修改 `static/app.js` 或 `static/editor.js` 后，使用 Node.js 执行 `npm install` 和 `npm run build` 更新本地 bundle。

## 使用策略

回测页面的策略工作区支持创建、查看、编辑、检查、保存和删除脚本；CodeMirror Python 编辑器提供语法高亮、括号匹配、行号和按当前作用域及对象类型筛选的补全，包括行情、持仓、股票信息字段和动态行情代码键。在表达式末尾输入 `.var` 可提取为变量，Tab 或 Enter 完成后变量名处于选中状态；再次按 Tab 或 Enter 确认名称并移至行尾。Tab 可接受普通补全，无候选时插入两个空格。页面回测无需选择标的，策略脚本存放于 `strategies/`，实现 `Strategy.on_day(day, broker)`，每日回调中自行选择股票池、查询行情并交易。旧版 `/api/backtest/run` 仍接受 `symbol` 参数以兼容单标的脚本。

内置股票池与行情函数：`get_stock_codes()`、`get_stock_names(codes=None)`、`get_history(codes, count)`。股票代码目录按市场和代码段筛选沪深北普通 A 股，排除 B 股、基金、指数及债券；退市股票根据本地名称尽力排除，无法仅凭名称索引保证完整识别。`get_stock_names()` 不传参数时返回筛选后的股票代码和名称，也可传入代码列表查询指定证券。历史行情按日期倒序排列，按每个代码最近 `count` 条返回，包含日期、O/C/H/L 及 V 成交量字段。账户查询：`broker.get_cash()` 返回可直接参与数值运算的现金信息对象，`total` 为总权益、`available` 为可用现金；另有 `broker.get_position(code)`、`broker.get_positions()`、`broker.get_equity()`。交易函数：`broker.buy(code, funds)`、`broker.sell(code, funds=None)`、`broker.cancel_all()`。买入和部分卖出按 A 股 100 股整手取整，并遵循佣金、印花税和 T+1 规则；多次买入后持仓成本按成交股数加权计算，部分卖出后根据已实现盈亏调整剩余持仓成本。指标函数：`sma`、`ema`、`cross_up`、`cross_down`。行情按需读取和缓存，策略应只查询实际所需的股票代码。新建策略会填入每日回调框架；编辑器工具栏的“函数帮助”可查看格式化后的完整说明。

回测使用下一交易日开盘价撮合（推荐），也可选择当日收盘价。买入按 100 股整手并计入最低佣金；A 股按 T+1 规则限制当天新买持仓卖出。现金不足时买入数量向下调整，部分卖出按整手撮合。绩效报告还包含盈利因子、平均/最大单笔盈亏、最大连胜连亏和平均仓位。收盘价成交模式按同一根已完成 K 线撮合，存在前视风险，应谨慎使用。多标的组合不模拟分红、拆并股、涨跌停和停牌撮合。

## API

- `GET/POST /api/config`：读取或保存通达信目录。
- `GET /api/kline?symbol=600036`：读取本地日线。
- `GET /api/kline/intraday?symbol=600036&date=2026-11-06&interval=1`：读取指定日期 1 或 5 分钟本地分时。
- `GET/POST /api/strategies`：列出或创建策略脚本。
- `GET /api/strategies/help`：读取内置函数说明文档。
- `GET/PUT/DELETE /api/strategies/{filename}`：读取、更新或删除脚本。
- `POST /api/strategies/validate`：检查 Python 语法及策略入口。
- `GET /api/stocks/search?q=600036`：按代码、名称或拼音首字母搜索本地股票索引。
- `POST /api/backtest/run`：同步运行并保存回测；返回体的 `saved` 与 `persistence_error` 显示持久化状态。
- `GET /api/backtest/list`：获取历史记录摘要。
- `GET /api/backtest/detail?id=...`：读取完整回测结果。
- `DELETE /api/backtest/delete?id=...`：删除一条本地记录。

日线图支持单击查看 OHLCV、滚轮缩放、拖动平移及双击打开所选日期的本地分时图。数据由 mootdx 实时读取，不缓存；本地没有该交易日的分时文件时会显示明确提示。历史结果会持续累积，可在页面或直接清理 `backtest_results/` 中的记录。

**安全提示：**Python 策略代码在本机后端进程中执行，执行能力相当于当前用户；只编辑和运行自己信任的脚本，不要将此单用户本地服务暴露到公网。

## 测试

```powershell
python -m unittest discover -s tests -v
```
