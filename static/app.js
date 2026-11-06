import { createStrategyEditor } from "./editor.js";
import { attachStockSearch } from "./stock-search.js";
import DOMPurify from "dompurify";
import { marked } from "marked";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let activeResult = null;
let currentResult = null;
let currentCurve = [];
let lastKlineBars = [];
let intradayBars = [];
let intradayDate = "";
let editor = null;
let editorDirty = false;
let activeStrategyFile = null;
let visibleRange = { start: 0, end: 0 };
let selectedKlineIndex = -1;
let crosshairKlineIndex = -1;
let selectedIntradayIndex = -1;
let toastTimer;
const curveStates = new WeakMap();
const curveListeners = new WeakSet();

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 4500);
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `请求失败 (${response.status})`);
  return payload;
}

function number(value, digits = 2) {
  return Number(value || 0).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(2)}%`;
}

function metricCards(performance) {
  const values = [
    ["总收益", percent(performance.total_return), performance.total_return],
    ["年化收益", percent(performance.annual_return), performance.annual_return],
    ["最大回撤", percent(performance.max_drawdown), performance.max_drawdown],
    ["夏普比率", number(performance.sharpe, 2), performance.sharpe],
    ["胜率", percent(performance.win_rate), performance.win_rate],
    ["完成交易", `${performance.trade_count} 笔`, 0],
    ["盈利因子", performance.profit_factor == null ? "∞" : number(performance.profit_factor, 2), 0],
    ["平均单笔盈亏", `${number(performance.average_trade)} 元`, performance.average_trade],
    ["平均盈利", `${number(performance.average_win)} 元`, performance.average_win],
    ["平均亏损", `${number(performance.average_loss)} 元`, performance.average_loss],
    ["最大连胜 / 连亏", `${performance.max_consecutive_wins || 0} / ${performance.max_consecutive_losses || 0}`, 0],
    ["平均仓位", percent(performance.average_exposure), performance.average_exposure],
  ];
  return values.map(([label, value, tone]) =>
    `<div class="metric"><span>${label}</span><strong class="${tone > 0 ? "positive" : tone < 0 ? "negative" : ""}">${value}</strong></div>`
  ).join("");
}

function drawLine(canvas, points, options = {}) {
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * ratio);
  canvas.height = Math.max(1, rect.height * ratio);
  ctx.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);
  if (!points.length) return;
  const left = 54, right = 18, top = 18, bottom = 32;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const values = points.map(point => point.value);
  let min = Math.min(...values), max = Math.max(...values);
  if (options.includeZero) min = Math.min(0, min);
  if (max === min) { max += 1; min -= 1; }
  const pad = (max - min) * .08;
  min -= pad; max += pad;
  const x = index => left + index / Math.max(points.length - 1, 1) * plotWidth;
  const y = value => top + (max - value) / (max - min) * plotHeight;
  ctx.font = "11px Segoe UI, sans-serif";
  ctx.fillStyle = "#78857e";
  ctx.strokeStyle = "#edf0ed";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const val = min + (max - min) * i / 4;
    const py = y(val);
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(width - right, py); ctx.stroke();
    ctx.fillText(options.format ? options.format(val) : val.toFixed(2), 3, py + 4);
  }
  const positive = options.color || "#157b55";
  ctx.beginPath();
  points.forEach((point, index) => {
    if (index === 0) ctx.moveTo(x(index), y(point.value));
    else ctx.lineTo(x(index), y(point.value));
  });
  ctx.strokeStyle = positive; ctx.lineWidth = 2; ctx.stroke();
  if (!options.noFill) {
    ctx.lineTo(x(points.length - 1), height - bottom);
    ctx.lineTo(x(0), height - bottom);
    ctx.closePath();
    ctx.globalAlpha = .07; ctx.fillStyle = positive; ctx.fill(); ctx.globalAlpha = 1;
  }
  ctx.fillStyle = "#78857e";
  ctx.fillText(points[0].label || "", left, height - 8);
  const lastLabel = points.at(-1).label || "";
  const labelWidth = ctx.measureText(lastLabel).width;
  ctx.fillText(lastLabel, width - right - labelWidth, height - 8);
  points.forEach((point, index) => {
    const hasBuy = Number(point.buy_count || 0) > 0;
    const hasSell = Number(point.sell_count || 0) > 0;
    if (!hasBuy && !hasSell) return;
    const cx = x(index);
    if (hasSell) {
      ctx.fillStyle = "#c34d4d";
      ctx.beginPath(); ctx.arc(cx, Math.max(top + 6, y(point.value) - 13), 4, 0, Math.PI * 2); ctx.fill();
      ctx.fillText("卖", cx + 5, Math.max(top + 10, y(point.value) - 10));
    }
    if (hasBuy) {
      ctx.fillStyle = "#157b55";
      ctx.beginPath(); ctx.arc(cx, Math.min(height - bottom - 6, y(point.value) + 13), 4, 0, Math.PI * 2); ctx.fill();
      ctx.fillText("买", cx + 5, Math.min(height - bottom - 2, y(point.value) + 16));
    }
  });
  if (options.activeIndex >= 0 && options.activeIndex < points.length) {
    const activeX = x(options.activeIndex);
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "#596b60";
    ctx.beginPath(); ctx.moveTo(activeX, top); ctx.lineTo(activeX, height - bottom); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#fff";
    ctx.strokeStyle = positive;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(activeX, y(points[options.activeIndex].value), 4, 0, Math.PI * 2);
    ctx.fill(); ctx.stroke();
    ctx.restore();
  }
}

function drawCandles(canvas, bars, selectedIndex = -1, crosshairIndex = -1, movingAverages = []) {
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * ratio);
  canvas.height = Math.max(1, rect.height * ratio);
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!bars.length) return;
  const left = 58, right = 18, top = 24, bottom = 30;
  const width = rect.width - left - right, height = rect.height - top - bottom;
  const volumeHeight = Math.min(54, Math.max(30, height * .2));
  const priceHeight = Math.max(40, height - volumeHeight - 10);
  const volumeTop = top + priceHeight + 10;
  const volumeBase = volumeTop + volumeHeight;
  const min = Math.min(...bars.map(bar => bar.low)), max = Math.max(...bars.map(bar => bar.high));
  const range = max - min || 1;
  const y = price => top + (max - price) / range * priceHeight;
  ctx.strokeStyle = "#edf0ed"; ctx.fillStyle = "#78857e"; ctx.font = "11px Segoe UI,sans-serif";
  for (let i = 0; i <= 4; i++) {
    const value = min + range * i / 4, py = y(value);
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(rect.width - right, py); ctx.stroke();
    ctx.fillText(value.toFixed(2), 3, py + 4);
  }
  const step = width / bars.length, candleWidth = Math.max(1, Math.min(9, step * .64));
  const maxVolume = Math.max(1, ...bars.map(bar => Number(bar.volume) || 0));
  bars.forEach((bar, i) => {
    const cx = left + step * (i + .5);
    const up = bar.close >= bar.open;
    const color = i === selectedIndex ? "#dc9a37" : up ? "#d45a57" : "#12855c";
    ctx.strokeStyle = color; ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(cx, y(bar.high)); ctx.lineTo(cx, y(bar.low)); ctx.stroke();
    const bodyTop = y(Math.max(bar.open, bar.close));
    const bodyHeight = Math.max(1, Math.abs(y(bar.open) - y(bar.close)));
    ctx.fillRect(cx - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    const volumeBarHeight = (Number(bar.volume) || 0) / maxVolume * volumeHeight;
    ctx.globalAlpha = .68;
    ctx.fillRect(cx - candleWidth / 2, volumeBase - volumeBarHeight, candleWidth, volumeBarHeight);
    ctx.globalAlpha = 1;
  });
  for (const averageConfig of movingAverages) {
    if (!averageConfig.enabled || bars.length < averageConfig.period) continue;
    const { period, color } = averageConfig;
    ctx.beginPath();
    let sum = 0;
    for (let index = 0; index < bars.length; index++) {
      sum += bars[index].close;
      if (index >= period) sum -= bars[index - period].close;
      if (index < period - 1) continue;
      const average = sum / period;
      const cx = left + step * (index + .5);
      if (index === period - 1) ctx.moveTo(cx, y(average));
      else ctx.lineTo(cx, y(average));
    }
    ctx.strokeStyle = color; ctx.lineWidth = 1.25; ctx.stroke();
  }
  let legendX = left + 6;
  for (const averageConfig of movingAverages) {
    if (!averageConfig.enabled) continue;
    ctx.fillStyle = averageConfig.color;
    const label = `MA${averageConfig.period}`;
    ctx.fillText(label, legendX, 14);
    legendX += ctx.measureText(label).width + 12;
  }
  ctx.fillStyle = "#78857e"; ctx.fillText("VOL", 8, volumeTop + 12);
  if (crosshairIndex >= 0 && crosshairIndex < bars.length) {
    const cx = left + step * (crosshairIndex + .5);
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "#596b60";
    ctx.beginPath(); ctx.moveTo(cx, top); ctx.lineTo(cx, volumeBase); ctx.stroke();
    ctx.restore();
  }
  ctx.fillStyle = "#78857e";
  ctx.fillText(bars[0].date || bars[0].time || bars[0].datetime, left, rect.height - 8);
  const end = bars.at(-1).date || bars.at(-1).time || bars.at(-1).datetime;
  const endWidth = ctx.measureText(end).width;
  ctx.fillText(end, rect.width - right - endWidth, rect.height - 8);
}

function candleAt(canvas, bars, event) {
  const rect = canvas.getBoundingClientRect();
  const left = 56, right = 18;
  const plotWidth = rect.width - left - right;
  const x = event.clientX - rect.left - left;
  if (x < 0 || x > plotWidth || !bars.length) return -1;
  return Math.min(bars.length - 1, Math.floor(x / (plotWidth / bars.length)));
}

function renderBarDetails(container, bar, title = "") {
  if (!bar) return;
  container.innerHTML = `${title ? `<strong>${escapeHtml(title)}</strong>` : ""}
    <span>开 <b>${number(bar.open, 3)}</b></span><span>高 <b>${number(bar.high, 3)}</b></span>
    <span>低 <b>${number(bar.low, 3)}</b></span><span>收 <b>${number(bar.close, 3)}</b></span>
    <span>成交量 <b>${number(bar.volume, 0)}</b></span>`;
}

function amountClass(value) {
  return Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
}

async function loadStrategies() {
  const strategies = await api("/api/strategies");
  const select = $("#strategy-select");
  const list = $("#script-list");
  select.innerHTML = "";
  list.innerHTML = "";
  for (const strategy of strategies) {
    const option = document.createElement("option");
    option.value = strategy.file;
    option.textContent = `${strategy.name} (${strategy.file})`;
    select.append(option);
    const row = document.createElement("button");
    row.type = "button";
    row.className = `script-row${strategy.file === activeStrategyFile ? " active" : ""}`;
    row.textContent = strategy.file;
    row.title = `${strategy.file} · ${(strategy.size / 1024).toFixed(1)} KB`;
    row.addEventListener("click", () => openStrategy(strategy.file));
    list.append(row);
  }
  if (!strategies.length) {
    list.innerHTML = '<div class="empty">还没有策略<br>点击“新建”开始编写。</div>';
    if (editor) editor.setValue("");
    editorDirty = false;
    updateEditorStatus("就绪");
    activeStrategyFile = null;
    $("#editor-filename").textContent = "尚未选择策略";
  } else {
    select.value = activeStrategyFile && strategies.some(item => item.file === activeStrategyFile)
      ? activeStrategyFile : strategies[0].file;
    if (!activeStrategyFile || !strategies.some(item => item.file === activeStrategyFile)) {
      await openStrategy(select.value);
    }
  }
}

function setEditorSource(source) {
  editor.setValue(source);
  editorDirty = false;
  updateEditorStatus("已保存");
}

function updateEditorStatus(message, kind = "") {
  const status = $("#editor-status");
  status.textContent = message;
  status.classList.toggle("dirty", kind === "dirty");
  status.classList.toggle("error", kind === "error");
}

async function openStrategy(filename) {
  if (editorDirty && !window.confirm("当前策略有未保存的修改，确定切换并放弃修改吗？")) {
    $("#strategy-select").value = activeStrategyFile || "";
    return;
  }
  try {
    const strategy = await api(`/api/strategies/${encodeURIComponent(filename)}`);
    activeStrategyFile = strategy.file;
    $("#editor-filename").textContent = strategy.file;
    $("#strategy-select").value = strategy.file;
    setEditorSource(strategy.code);
    $$(".script-row").forEach(row => row.classList.toggle("active", row.textContent === strategy.file));
  } catch (error) {
    showToast(error.message);
  }
}

async function saveStrategy() {
  if (!activeStrategyFile) throw new Error("请先新建或选择策略脚本。");
  const code = editor.getValue();
  await api("/api/strategies/validate", {
    method: "POST",
    body: JSON.stringify({ filename: activeStrategyFile, code }),
  });
  const saved = await api(`/api/strategies/${encodeURIComponent(activeStrategyFile)}`, {
    method: "PUT",
    body: JSON.stringify({ code }),
  });
  setEditorSource(saved.code);
  await loadStrategies();
  $("#strategy-select").value = saved.file;
  showToast(`策略 ${saved.file} 已保存。`);
}

async function validateStrategy() {
  if (!activeStrategyFile) throw new Error("请先新建或选择策略脚本。");
  await api("/api/strategies/validate", {
    method: "POST",
    body: JSON.stringify({ filename: activeStrategyFile, code: editor.getValue() }),
  });
  updateEditorStatus("语法和策略入口检查通过");
  showToast("策略检查通过。运行时错误会在回测执行后报告。");
}

async function loadHistory() {
  const body = $("#history-body");
  try {
    const items = await api("/api/backtest/list");
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">暂无历史回测。先运行一次策略回测。</td></tr>';
      return;
    }
    body.innerHTML = items.map(item => `<tr>
      <td>${escapeHtml(item.created_at.replace("T", " "))}</td>
      <td>${escapeHtml(item.strategy_name)}</td>
      <td>${escapeHtml(item.symbol)} <span class="status">${escapeHtml(item.symbol_name || "")}</span></td>
      <td>${escapeHtml(item.start_date)} 至 ${escapeHtml(item.end_date)}</td>
      <td class="${amountClass(item.total_return)}">${percent(item.total_return)}</td>
      <td class="negative">${percent(item.max_drawdown)}</td>
      <td class="history-actions"><button class="button subtle" data-result-id="${escapeHtml(item.id)}">查看详情</button>
        <button class="button danger" data-delete-result="${escapeHtml(item.id)}">删除</button></td>
    </tr>`).join("");
    $$("[data-result-id]", body).forEach(button => button.addEventListener("click", () => openDetail(button.dataset.resultId)));
    $$("[data-delete-result]", body).forEach(button => button.addEventListener("click", async () => {
      const id = button.dataset.deleteResult;
      if (!window.confirm(`确定删除回测记录 ${id}？此操作不可撤销。`)) return;
      try {
        await api(`/api/backtest/delete?id=${encodeURIComponent(id)}`, { method: "DELETE" });
        await loadHistory();
        showToast("回测记录已删除。");
      } catch (error) {
        showToast(error.message);
      }
    }));
  } catch (error) {
    body.innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(error.message)}</td></tr>`;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function renderDetail(result) {
  activeResult = result;
  const { meta, performance, equity_curve: curve, trades, strategy_code: code } = result;
  $("#detail-title").textContent = `${meta.strategy_name} · ${meta.symbol}`;
  $("#detail-meta").textContent = `${meta.created_at.replace("T", " ")}  |  ${meta.start_date} 至 ${meta.end_date}  |  初始资金 ${number(meta.initial_cash)} 元`;
  $("#detail-metrics").innerHTML = metricCards(performance);
  $("#strategy-code").textContent = code;
  renderTradeRows(trades);
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === "performance"));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === "tab-performance"));
  $("#detail-modal").classList.remove("hidden");
  requestAnimationFrame(() => renderEquityCurve($("#detail-equity"), curve, trades, result));
}

function renderTradeRows(trades, dateFilter = "") {
  const filtered = dateFilter ? trades.filter(trade => trade.datetime === dateFilter) : trades;
  $("#trade-count").textContent = dateFilter
    ? `${dateFilter} · ${filtered.length} 条成交记录`
    : `共 ${filtered.length} 条成交记录`;
  $("#trade-body").innerHTML = filtered.map(trade => `<tr>
    <td>${escapeHtml(trade.datetime)}</td>
    <td>${escapeHtml(trade.symbol || "")} ${escapeHtml(trade.symbol_name || "")}</td>
    <td class="${trade.direction === "buy" ? "positive" : "negative"}">${trade.direction === "buy" ? "买入" : "卖出"}</td>
    <td>${number(trade.price, 3)}</td><td>${number(trade.size, 0)}</td>
    <td>${number(trade.amount)}</td><td>${number(trade.commission)}</td>
    <td>${number(trade.stamp_tax || 0)}</td>
    <td class="${amountClass(trade.pnl)}">${trade.pnl == null ? "—" : number(trade.pnl)}</td></tr>`).join("")
    || '<tr><td colspan="9" class="empty">该日没有成交记录</td></tr>';
}

function renderEquityCurve(canvas, curve, trades, result = null) {
  const tradesByDate = new Map();
  for (const trade of trades) {
    const counts = tradesByDate.get(trade.datetime) || { buy_count: 0, sell_count: 0 };
    counts[trade.direction === "buy" ? "buy_count" : "sell_count"] += 1;
    tradesByDate.set(trade.datetime, counts);
  }
  const points = curve.map(point => ({
    ...point,
    label: point.date,
    value: point.nav,
    ...(tradesByDate.get(point.date) || {}),
  }));
  const previousState = curveStates.get(canvas);
  const state = !previousState || previousState.source !== curve
    ? { start: 0, end: points.length }
    : previousState;
  state.source = curve;
  state.end = Math.min(state.end, points.length);
  if (!state.end || state.end <= state.start) {
    state.start = 0;
    state.end = points.length;
  }
  state.points = points;
  state.trades = trades;
  state.result = result;
  curveStates.set(canvas, state);
  const draw = () => {
    const current = curveStates.get(canvas);
    drawLine(canvas, current.points.slice(current.start, current.end), { activeIndex: current.hoverIndex ?? -1 });
  };
  draw();
  if (curveListeners.has(canvas)) return;
  curveListeners.add(canvas);
  canvas.addEventListener("wheel", event => {
    const current = curveStates.get(canvas);
    if (!current || current.points.length < 2) return;
    event.preventDefault();
    const length = current.end - current.start;
    const minLength = Math.min(10, current.points.length);
    const nextLength = Math.max(
      minLength,
      Math.min(current.points.length, Math.round(length * (event.deltaY < 0 ? 0.8 : 1.25))),
    );
    const ratio = Math.min(1, Math.max(0, (event.clientX - canvas.getBoundingClientRect().left) / canvas.clientWidth));
    const center = current.start + length * ratio;
    current.start = Math.max(0, Math.min(current.points.length - nextLength, Math.round(center - nextLength * ratio)));
    current.end = current.start + nextLength;
    current.hoverIndex = -1;
    canvas.parentElement.querySelector(".equity-tooltip").classList.add("hidden");
    draw();
  }, { passive: false });
  canvas.addEventListener("pointermove", event => {
    const current = curveStates.get(canvas);
    if (!current) return;
    const visible = current.points.slice(current.start, current.end);
    const index = curvePointAt(canvas, visible, event);
    current.hoverIndex = index;
    draw();
    const tooltip = canvas.parentElement.querySelector(".equity-tooltip");
    if (index < 0) {
      tooltip.classList.add("hidden");
      return;
    }
    const point = visible[index];
    const absoluteIndex = current.start + index;
    const initialCash = Number(current.result?.meta?.initial_cash) || 0;
    const equity = Number(point.equity ?? point.nav * initialCash);
    const previousEquity = absoluteIndex > 0
      ? Number(current.points[absoluteIndex - 1].equity ?? current.points[absoluteIndex - 1].nav * initialCash)
      : initialCash;
    const dailyPnl = equity - previousEquity;
    const totalPnl = equity - initialCash;
    tooltip.innerHTML = `<strong>${escapeHtml(point.label)}</strong>
      <div class="equity-tooltip-row"><span>账户权益</span><b>${number(equity)} 元</b></div>
      <div class="equity-tooltip-row"><span>当日盈亏</span><b class="${amountClass(dailyPnl)}">${dailyPnl > 0 ? "+" : ""}${number(dailyPnl)} 元</b></div>
      <div class="equity-tooltip-row"><span>累计盈亏</span><b class="${amountClass(totalPnl)}">${totalPnl > 0 ? "+" : ""}${number(totalPnl)} 元</b></div>
      <div class="equity-tooltip-row"><span>可用现金</span><b>${number(point.cash)} 元</b></div>
      <div class="equity-tooltip-row"><span>持仓市值</span><b>${number(point.market_value)} 元</b></div>`;
    const wrapper = canvas.parentElement;
    const bounds = canvas.getBoundingClientRect();
    const left = event.clientX - bounds.left;
    const top = event.clientY - bounds.top;
    tooltip.style.left = `${Math.max(0, Math.min(left + 14, wrapper.clientWidth - tooltip.offsetWidth))}px`;
    tooltip.style.top = `${Math.max(0, Math.min(top + 14, wrapper.clientHeight - tooltip.offsetHeight))}px`;
    tooltip.classList.remove("hidden");
  });
  canvas.addEventListener("pointerleave", () => {
    const current = curveStates.get(canvas);
    if (current) current.hoverIndex = -1;
    canvas.parentElement.querySelector(".equity-tooltip").classList.add("hidden");
    draw();
  });
  canvas.addEventListener("dblclick", event => {
    const current = curveStates.get(canvas);
    if (!current || !current.points.length) return;
    const visible = current.points.slice(current.start, current.end);
    const index = curvePointAt(canvas, visible, event);
    if (index < 0) return;
    const day = visible[index].label;
    if (!day) return;
    if (current.result) renderDetail(current.result);
    else if (activeResult) renderDetail(activeResult);
    renderTradeRows(current.trades, day);
    $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === "trades"));
    $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === "tab-trades"));
  });
}

function curvePointAt(canvas, points, event) {
  if (!points.length) return -1;
  const bounds = canvas.getBoundingClientRect();
  const left = 54, right = 18;
  const plotWidth = bounds.width - left - right;
  const relative = event.clientX - bounds.left - left;
  if (relative < 0 || relative > plotWidth || plotWidth <= 0) return -1;
  return Math.min(points.length - 1, Math.round(relative / plotWidth * Math.max(points.length - 1, 0)));
}

async function openDetail(id) {
  try {
    renderDetail(await api(`/api/backtest/detail?id=${encodeURIComponent(id)}`));
  } catch (error) {
    showToast(error.message);
  }
}

function showCurrentResult(result) {
  currentResult = result;
  activeResult = result;
  const { meta, performance, equity_curve: curve } = result;
  $("#current-result").classList.remove("hidden");
  $("#result-caption").textContent = `${meta.strategy_name} · ${meta.symbol_name || meta.symbol} · ${meta.start_date} 至 ${meta.end_date}`;
  $("#current-metrics").innerHTML = metricCards(performance);
  const warning = $("#save-warning");
  warning.textContent = result.persistence_error || "";
  warning.classList.toggle("hidden", result.saved);
  const orderWarning = $("#order-warning");
  orderWarning.textContent = result.unfilled_orders?.length
    ? `回测区间结束时仍有 ${result.unfilled_orders.length} 笔未成交委托；请留意最后交易日产生的信号。`
    : "";
  orderWarning.classList.toggle("hidden", !result.unfilled_orders?.length);
  currentCurve = curve;
  requestAnimationFrame(() => renderEquityCurve($("#current-equity"), curve, result.trades, result));
  $("#open-current-detail").onclick = () => renderDetail(result);
  if (!result.saved) showToast(result.persistence_error || "回测已完成，但保存失败。");
}

$("#backtest-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("#run-button"), status = $("#backtest-status");
  const values = Object.fromEntries(new FormData(form));
  values.initial_cash = Number(values.initial_cash);
  for (const key of ["commission", "stamp_tax", "slippage", "min_commission"]) values[key] = Number(values[key]);
  button.disabled = true;
  button.textContent = "正在回测…";
  status.textContent = "回测执行中，请勿关闭页面";
  try {
    if (editorDirty) await saveStrategy();
    if (!activeStrategyFile) throw new Error("请先在策略工作区创建或选择策略脚本。");
    values.strategy_file = activeStrategyFile;
    const result = await api("/api/backtest/run", { method: "POST", body: JSON.stringify(values) });
    showCurrentResult(result);
    if (result.saved) showToast("回测完成，结果已保存。");
    await loadHistory();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "开始回测";
    status.textContent = "";
  }
});

$("#refresh-history").addEventListener("click", loadHistory);

$("#new-strategy").addEventListener("click", async () => {
  if (editorDirty && !window.confirm("当前策略有未保存的修改，创建新策略并放弃这些修改吗？")) return;
  const requestedName = window.prompt("请输入新策略文件名（无需填写 .py）：", "my_strategy");
  if (!requestedName) return;
  const filename = requestedName.trim().endsWith(".py") ? requestedName.trim() : `${requestedName.trim()}.py`;
  const strategyCode = `class Strategy:\n    \"\"\"在每日回调中查询股票池与行情，并按代码提交交易委托。\"\"\"\n\n    def __init__(self):\n        self.watchlist = [\"600036\"]\n        self.holdings = set()\n\n    def on_day(self, day, broker):\n        \"\"\"每天收盘后调用；下单将在所选成交模式下执行。\"\"\"\n        quotes = get_history(self.watchlist, 21)\n        for code in self.watchlist:\n            history = quotes.get(code, [])\n            if len(history) < 21:\n                continue\n            closes = [item[\"C\"] for item in reversed(history[:21])]\n            short_ma = sma(closes[-5:], 5)\n            long_ma = sma(closes, 20)\n            if short_ma is None or long_ma is None:\n                continue\n            position = broker.get_position(code)\n            if short_ma > long_ma and position[\"size\"] == 0:\n                broker.buy(code, broker.get_cash() * 0.9)\n            elif short_ma < long_ma and position[\"size\"] > 0:\n                broker.sell(code)\n`;
  try {
    const created = await api("/api/strategies", {
      method: "POST",
      body: JSON.stringify({ filename, code: strategyCode }),
    });
    editorDirty = false;
    activeStrategyFile = created.file;
    await loadStrategies();
    await openStrategy(created.file);
    editor.focus();
    showToast(`已创建策略 ${created.file}。`);
  } catch (error) {
    showToast(error.message);
  }
});

$("#delete-strategy").addEventListener("click", async () => {
  if (!activeStrategyFile) return showToast("请先选择要删除的策略。");
  if (!window.confirm(`确定永久删除策略 ${activeStrategyFile}？已保存的回测记录不受影响。`)) return;
  try {
    await api(`/api/strategies/${encodeURIComponent(activeStrategyFile)}`, { method: "DELETE" });
    activeStrategyFile = null;
    editorDirty = false;
    await loadStrategies();
    showToast("策略文件已删除。");
  } catch (error) {
    showToast(error.message);
  }
});

$("#save-strategy").addEventListener("click", async () => {
  try {
    await saveStrategy();
  } catch (error) {
    updateEditorStatus(error.message, "error");
    showToast(error.message);
  }
});

$("#validate-strategy").addEventListener("click", async () => {
  try {
    await validateStrategy();
  } catch (error) {
    updateEditorStatus(error.message, "error");
    showToast(error.message);
  }
});

$("#run-strategy").addEventListener("click", () => $("#backtest-form").requestSubmit());
$("#toggle-editor").addEventListener("click", event => {
  const button = event.currentTarget;
  const region = $("#editor-region");
  const expanded = button.getAttribute("aria-expanded") !== "true";
  button.setAttribute("aria-expanded", String(expanded));
  button.textContent = expanded ? "收起编辑区" : "展开编辑区";
  region.classList.toggle("hidden", !expanded);
  if (expanded) requestAnimationFrame(() => editor?.view.requestMeasure());
});
$("#reload-strategies").addEventListener("click", () => loadStrategies().catch(error => showToast(error.message)));
$("#strategy-select").addEventListener("change", event => {
  if (event.currentTarget.value && event.currentTarget.value !== activeStrategyFile) {
    openStrategy(event.currentTarget.value);
  }
});
window.addEventListener("strategy:save", () => $("#save-strategy").click());

$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const status = $("#settings-status");
  status.textContent = "正在验证目录…";
  try {
    const config = await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ tdxdir: $("#tdxdir").value.trim() }),
    });
    $("#tdxdir").value = config.tdxdir;
    status.textContent = "目录有效，配置已保存。";
  } catch (error) {
    status.textContent = "";
    showToast(error.message);
  }
});

$("#watch-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(new FormData(form));
  const params = new URLSearchParams({ symbol: values.symbol });
  if (values.start_date) params.set("start_date", values.start_date);
  if (values.end_date) params.set("end_date", values.end_date);
  const displayName = form.elements.symbol.dataset.stockName;
  $("#watch-summary p").textContent = "正在读取通达信日线…";
  try {
    const bars = await api(`/api/kline?${params}`);
    lastKlineBars = bars;
    visibleRange = { start: Math.max(0, bars.length - 180), end: bars.length };
    selectedKlineIndex = -1;
    $("#selected-bar").innerHTML = "<span>点击 K 线查看当日行情；双击打开分时图。</span>";
    $("#watch-summary p").textContent = `${values.symbol}${displayName ? ` ${displayName}` : ""} · ${bars.length} 个交易日 · ${bars[0].date} 至 ${bars.at(-1).date}`;
    requestAnimationFrame(renderKline);
  } catch (error) {
    $("#watch-summary p").textContent = error.message;
  }
});

function visibleKlineBars() {
  return lastKlineBars.slice(visibleRange.start, visibleRange.end);
}

function renderKline() {
  drawCandles(
    $("#kline-chart"),
    visibleKlineBars(),
    selectedKlineIndex - visibleRange.start,
    crosshairKlineIndex - visibleRange.start,
    movingAverageSettings(),
  );
}

const movingAverageColors = ["#d49732", "#6886c2"];

function movingAverageSettings() {
  return [0, 1].map(index => ({
    enabled: $(`[data-ma-toggle="${index}"]`).checked,
    period: Number($(`[data-ma-period="${index}"]`).value),
    color: movingAverageColors[index],
  }));
}

for (const control of $$("[data-ma-toggle]")) {
  control.addEventListener("change", renderKline);
}
for (const input of $$("[data-ma-period]")) {
  input.addEventListener("change", () => {
    const period = Number(input.value);
    if (!Number.isInteger(period) || period < 1 || period > 250) {
      showToast("均线周期需为 1 到 250 之间的整数。");
      input.value = input.dataset.previousValue || "5";
      return;
    }
    input.dataset.previousValue = String(period);
    renderKline();
  });
  input.dataset.previousValue = input.value;
}

async function showIntraday(day) {
  intradayDate = day;
  intradayBars = [];
  selectedIntradayIndex = -1;
  $("#intraday-title").textContent = `${$("#watch-form").elements.symbol.value.trim()} · ${day}`;
  $("#intraday-modal").classList.remove("hidden");
  await loadIntraday();
}

async function loadIntraday() {
  const symbol = $("#watch-form").elements.symbol.value.trim();
  const interval = Number($("#intraday-interval").value);
  $("#intraday-summary").textContent = "正在读取通达信本地分时数据…";
  try {
    intradayBars = await api(`/api/kline/intraday?${new URLSearchParams({
      symbol, date: intradayDate, interval: String(interval),
    })}`);
    $("#intraday-summary").textContent =
      `${intradayBars.length} 根 ${interval} 分钟 K 线 · 本地通达信数据`;
    $("#intraday-selected").innerHTML = "<span>点击分时 K 线查看详细行情。</span>";
    requestAnimationFrame(() => drawCandles($("#intraday-chart"), intradayBars, selectedIntradayIndex));
  } catch (error) {
    $("#intraday-summary").textContent = error.message;
    showToast(error.message);
  }
}

const klineCanvas = $("#kline-chart");
let dragStart = null;
let draggedKline = false;
klineCanvas.addEventListener("pointerdown", event => {
  if (!lastKlineBars.length) return;
  dragStart = { x: event.clientX, start: visibleRange.start, end: visibleRange.end };
  draggedKline = false;
  klineCanvas.setPointerCapture(event.pointerId);
});
klineCanvas.addEventListener("pointermove", event => {
  const bars = visibleKlineBars();
  if (!bars.length) return;
  if (dragStart && event.buttons) {
    const plotWidth = klineCanvas.getBoundingClientRect().width - 74;
    const step = plotWidth / Math.max(1, dragStart.end - dragStart.start);
    const shift = Math.round((dragStart.x - event.clientX) / step);
    if (Math.abs(event.clientX - dragStart.x) > 4) draggedKline = true;
    const span = dragStart.end - dragStart.start;
    const start = Math.max(0, Math.min(lastKlineBars.length - span, dragStart.start + shift));
    visibleRange = { start, end: start + span };
  }
  const index = candleAt(klineCanvas, visibleKlineBars(), event);
  crosshairKlineIndex = index < 0 ? -1 : visibleRange.start + index;
  renderKline();
  const tooltip = $("#kline-tooltip");
  if (index < 0) {
    tooltip.classList.add("hidden");
    return;
  }
  const bar = visibleKlineBars()[index];
  tooltip.innerHTML = `${escapeHtml(bar.date)}<br>开 ${number(bar.open, 3)}　高 ${number(bar.high, 3)}<br>
    低 ${number(bar.low, 3)}　收 ${number(bar.close, 3)}<br>量 ${number(bar.volume, 0)}`;
  const rect = klineCanvas.getBoundingClientRect();
  tooltip.style.left = `${Math.max(58, Math.min(event.clientX - rect.left + 12, rect.width - 178))}px`;
  tooltip.style.top = `${Math.max(8, Math.min(event.clientY - rect.top + 10, rect.height - 98))}px`;
  tooltip.classList.remove("hidden");
});
klineCanvas.addEventListener("pointerup", () => { dragStart = null; });
klineCanvas.addEventListener("pointerleave", () => {
  crosshairKlineIndex = -1;
  $("#kline-tooltip").classList.add("hidden");
  renderKline();
});
klineCanvas.addEventListener("click", event => {
  if (draggedKline) {
    draggedKline = false;
    return;
  }
  const index = candleAt(klineCanvas, visibleKlineBars(), event);
  if (index < 0) return;
  selectedKlineIndex = visibleRange.start + index;
  const bar = lastKlineBars[selectedKlineIndex];
  renderBarDetails($("#selected-bar"), bar, bar.date);
  renderKline();
});
klineCanvas.addEventListener("dblclick", event => {
  const index = candleAt(klineCanvas, visibleKlineBars(), event);
  if (index >= 0) showIntraday(visibleKlineBars()[index].date);
});
klineCanvas.addEventListener("wheel", event => {
  if (!lastKlineBars.length) return;
  event.preventDefault();
  const currentLength = visibleRange.end - visibleRange.start;
  const minLength = Math.min(20, lastKlineBars.length);
  const nextLength = Math.max(
    minLength,
    Math.min(lastKlineBars.length, Math.round(currentLength * (event.deltaY < 0 ? 0.8 : 1.25))),
  );
  const index = candleAt(klineCanvas, visibleKlineBars(), event);
  const anchor = visibleRange.start + Math.max(0, index);
  const ratio = currentLength ? (anchor - visibleRange.start) / currentLength : 0.5;
  let start = Math.round(anchor - ratio * nextLength);
  start = Math.max(0, Math.min(lastKlineBars.length - nextLength, start));
  visibleRange = { start, end: start + nextLength };
  renderKline();
}, { passive: false });

$("#intraday-interval").addEventListener("change", loadIntraday);
$$("[data-close-intraday]").forEach(element => element.addEventListener("click", () => $("#intraday-modal").classList.add("hidden")));
$("#intraday-chart").addEventListener("click", event => {
  const index = candleAt($("#intraday-chart"), intradayBars, event);
  if (index < 0) return;
  selectedIntradayIndex = index;
  renderBarDetails($("#intraday-selected"), intradayBars[index], intradayBars[index].time);
  drawCandles($("#intraday-chart"), intradayBars, selectedIntradayIndex);
});

$$(".nav-button").forEach(button => button.addEventListener("click", () => {
  $$(".nav-button").forEach(item => item.classList.toggle("active", item === button));
  $$(".page").forEach(page => page.classList.toggle("active", page.id === `page-${button.dataset.page}`));
  if (button.dataset.page === "watch" && lastKlineBars.length) requestAnimationFrame(renderKline);
}));

$("#toggle-topbar-pin").addEventListener("click", event => {
  const button = event.currentTarget;
  const pinned = button.getAttribute("aria-pressed") !== "true";
  $("#topbar").classList.toggle("is-pinned", pinned);
  button.setAttribute("aria-pressed", String(pinned));
  button.textContent = pinned ? "取消固定" : "固定顶部";
});

$$("[data-close-detail]").forEach(element => element.addEventListener("click", () => $("#detail-modal").classList.add("hidden")));
$$(".tab").forEach(tab => tab.addEventListener("click", () => {
  $$(".tab").forEach(item => item.classList.toggle("active", item === tab));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === `tab-${tab.dataset.tab}`));
}));

$("#export-trades").addEventListener("click", () => {
  if (!activeResult) return;
  const columns = ["datetime", "symbol", "direction", "price", "size", "amount", "commission", "stamp_tax", "pnl"];
  const rows = [columns.join(","), ...activeResult.trades.map(trade =>
    columns.map(key => `"${String(trade[key] ?? "").replaceAll('"', '""')}"`).join(",")
  )];
  const blob = new Blob(["\ufeff", rows.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${activeResult.meta.id}_trades.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
});

function localDateValue(value) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

const localToday = new Date();
const oneYearAgo = new Date(localToday);
oneYearAgo.setFullYear(oneYearAgo.getFullYear() - 1);
if (oneYearAgo.getMonth() !== localToday.getMonth()) oneYearAgo.setDate(0);
for (const form of [$("#backtest-form"), $("#watch-form")]) {
  form.elements.start_date.value = localDateValue(oneYearAgo);
  form.elements.end_date.value = localDateValue(localToday);
}
$$("[data-stock-search]").forEach(attachStockSearch);
editor = createStrategyEditor($("#code-editor"), "", () => {
  editorDirty = true;
  updateEditorStatus("有未保存的修改", "dirty");
});
Promise.all([loadStrategies(), loadHistory()]).catch(error => showToast(error.message));
api("/api/config").then(config => { $("#tdxdir").value = config.tdxdir || ""; }).catch(error => showToast(error.message));

$("#strategy-help").addEventListener("click", async () => {
  const modal = $("#strategy-help-modal");
  const content = $("#strategy-help-content");
  modal.classList.remove("hidden");
  content.textContent = "正在加载函数说明…";
  try {
    const response = await fetch("/api/strategies/help");
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `读取帮助失败 (${response.status})`);
    }
    content.innerHTML = DOMPurify.sanitize(await marked.parse(await response.text()));
  } catch (error) {
    content.textContent = error.message;
  }
});
$$("[data-close-strategy-help]").forEach(element => element.addEventListener("click", () => $("#strategy-help-modal").classList.add("hidden")));
window.addEventListener("resize", () => {
  if (!$("#current-result").classList.contains("hidden")) {
    if (currentResult) renderEquityCurve($("#current-equity"), currentCurve, currentResult.trades, currentResult);
  }
  if (!$("#detail-modal").classList.contains("hidden") && activeResult) {
    renderEquityCurve($("#detail-equity"), activeResult.equity_curve, activeResult.trades, activeResult);
  }
  if (lastKlineBars.length && $("#page-watch").classList.contains("active")) {
    renderKline();
  }
  if (intradayBars.length && !$("#intraday-modal").classList.contains("hidden")) {
    drawCandles($("#intraday-chart"), intradayBars, selectedIntradayIndex);
  }
});
