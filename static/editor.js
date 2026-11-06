import { acceptCompletion, autocompletion, startCompletion } from "@codemirror/autocomplete";
import { python } from "@codemirror/lang-python";
import { HighlightStyle, indentUnit, syntaxHighlighting } from "@codemirror/language";
import { basicSetup } from "codemirror";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { tags } from "@lezer/highlight";

const strategyHighlighting = HighlightStyle.define([
  { tag: [tags.keyword, tags.controlKeyword, tags.definitionKeyword], color: "#ff9e64", fontWeight: "600" },
  { tag: [tags.typeName, tags.className], color: "#7dcfff" },
  { tag: tags.function(tags.variableName), color: "#7ee787" },
  { tag: tags.propertyName, color: "#c0caf5" },
  { tag: tags.string, color: "#c3e88d" },
  { tag: tags.number, color: "#ffb86c" },
  { tag: [tags.bool, tags.null], color: "#ff79c6" },
  { tag: tags.comment, color: "#9aa7b5", fontStyle: "italic" },
  { tag: tags.operator, color: "#89ddff" },
]);

const apiFunctions = [
  { label: "get_stock_codes()", type: "function", detail: "返回通达信本地股票目录中的证券代码列表" },
  { label: "get_stock_names(codes)", type: "function", detail: "将代码列表转换为含 code 和 name 的证券信息记录列表" },
  { label: "get_history(codes, count)", type: "function", detail: "按代码返回最多 count 条日线，日期从新到旧；每条包含 date、O、C、H、L、V" },
  { label: "sma(values, period)", type: "function", detail: "计算最近 period 个数值的简单平均；数据不足时返回 None" },
  { label: "ema(values, period)", type: "function", detail: "计算指数移动平均，平滑系数为 2/(period+1)；数据不足时返回 None" },
  { label: "cross_up(fast, slow)", type: "function", detail: "检测上穿：序列前值 fast <= slow 且当前 fast > slow；传数值时只比较 fast > slow" },
  { label: "cross_down(fast, slow)", type: "function", detail: "检测下穿：序列前值 fast >= slow 且当前 fast < slow；传数值时只比较 fast < slow" },
  { label: "len(value)", type: "function", detail: "返回字符串、列表、字典等对象的元素个数" },
  { label: "range(stop)", type: "function", detail: "生成从 0 到 stop-1 的整数序列，常用于循环" },
  { label: "enumerate(iterable)", type: "function", detail: "遍历序列并同时获得从 0 开始的索引和值" },
  { label: "min(values)", type: "function", detail: "返回最小值" },
  { label: "max(values)", type: "function", detail: "返回最大值" },
  { label: "sum(values)", type: "function", detail: "返回数值序列之和" },
  { label: "abs(value)", type: "function", detail: "返回绝对值" },
  { label: "all(values)", type: "function", detail: "判断序列元素是否全部为真" },
  { label: "any(values)", type: "function", detail: "判断序列元素是否存在真值" },
  { label: "bool(value)", type: "function", detail: "转换为布尔值" },
  { label: "dict()", type: "function", detail: "创建空字典" },
  { label: "float(value)", type: "function", detail: "转换为浮点数" },
  { label: "int(value)", type: "function", detail: "转换为整数" },
  { label: "list(iterable)", type: "function", detail: "创建列表" },
  { label: "print(value)", type: "function", detail: "输出调试信息" },
  { label: "reversed(sequence)", type: "function", detail: "反向遍历序列" },
  { label: "round(number, ndigits)", type: "function", detail: "将数值舍入到指定小数位数" },
  { label: "sorted(iterable)", type: "function", detail: "返回按升序排列的新列表，不修改原序列" },
  { label: "str(value)", type: "function", detail: "转换为字符串" },
  { label: "set(iterable)", type: "function", detail: "创建集合" },
  { label: "tuple(iterable)", type: "function", detail: "创建元组" },
  { label: "zip(*iterables)", type: "function", detail: "按位置组合多个序列，结果长度取最短序列长度" },
  { label: "class Strategy:", type: "keyword", detail: "策略入口类" },
  { label: "def on_day(self, day, broker):", type: "keyword", detail: "多标的策略每日回调" },
  { label: "def on_bar(self, bar, broker):", type: "keyword", detail: "策略日线回调；订单按成交模式撮合" },
];
const brokerMethods = [
  ["buy(code, funds)", "按指定金额买入证券；金额需足以购买至少 100 股整手"],
  ["sell(code)", "卖出指定股票的全部可卖持仓"],
  ["sell(code, funds)", "按指定金额卖出可卖持仓；省略 funds 时卖出全部可卖数量"],
  ["get_position(code)", "返回单只证券的持仓字典：股数、可卖股数、平均成本、市值及未实现盈亏"],
  ["get_positions()", "返回所有非零持仓，字典键为证券代码，值为单只证券持仓字典"],
  ["get_cash()", "返回可直接参与金额运算的现金信息对象；total 为总权益（现金加持仓市值），available 为可用现金，单位均为元"],
  ["get_equity()", "返回账户总权益：可用现金加已载入行情的持仓市值"],
  ["order_value(value)", "按目标金额买入当前单标的策略的股票"],
  ["order_target_value(value)", "将当前单标的持仓调整到指定金额"],
  ["order_target_percent(percent)", "将当前单标的仓位调整到总权益的指定比例（0 到 1）"],
  ["cancel_all()", "撤销当前所有尚未成交的委托"],
].map(([label, detail]) => ({ label, type: "method", detail }));
const strategyFields = [
  ["date", "交易日期，格式 YYYY-MM-DD"],
  ["日期", "交易日期，格式 YYYY-MM-DD（date 的中文别名）"],
  ["O", "当日开盘价，单位：元"],
  ["C", "当日收盘价，单位：元"],
  ["H", "当日最高价，单位：元"],
  ["L", "当日最低价，单位：元"],
  ["V", "当日成交量，单位与通达信日线文件一致"],
].map(([label, detail]) => ({ label, type: "property", detail }));
const positionFields = [
  ["symbol", "持仓证券代码"],
  ["size", "当前持有股数，单位：股"],
  ["sellable_size", "当前可卖股数，遵循 A 股 T+1 规则"],
  ["average_cost", "当前持仓平均成本，单位：元/股；加仓加权，部分卖出后按已实现盈亏调整"],
  ["market_value", "按最近可用收盘价计算的持仓市值，单位：元"],
  ["unrealized_pnl", "按最近可用收盘价估算的未实现盈亏，单位：元"],
].map(([label, detail]) => ({ label, type: "property", detail }));
const cashFields = [
  ["total", "账户总权益：可用现金加已载入行情的持仓市值，单位：元"],
  ["available", "当前可用现金，单位：元；未成交买单不冻结现金"],
].map(([label, detail]) => ({ label, type: "property", detail }));
const stockFields = [
  ["code", "证券或指数代码，可用于查询行情与下单"],
  ["name", "证券名称，可用于界面展示或日志输出"],
].map(([label, detail]) => ({ label, type: "property", detail }));
const barFields = [
  ["date", "当前 K 线日期，格式 YYYY-MM-DD"],
  ["open", "当前 K 线开盘价，单位：元"],
  ["high", "当前 K 线最高价，单位：元"],
  ["low", "当前 K 线最低价，单位：元"],
  ["close", "当前 K 线收盘价，单位：元"],
  ["volume", "当前 K 线成交量"],
].map(([label, detail]) => ({ label, type: "property", detail }));
const collectionMethods = {
  historyMap: [
    ["clear()", "清空代码到历史行情列表的映射"],
    ["copy()", "复制映射字典"],
    ["get(key)", "按证券代码读取对应的历史行情列表；不存在时返回 None"],
    ["items()", "遍历证券代码与对应的历史行情列表"],
    ["keys()", "获取已查询证券代码"],
    ["values()", "获取所有证券的历史行情列表"],
  ],
  positionsMap: [
    ["clear()", "清空代码到持仓信息的映射副本"],
    ["copy()", "复制持仓映射字典"],
    ["get(key)", "按证券代码读取持仓字典；没有该代码时返回 None"],
    ["items()", "遍历证券代码和对应持仓字典"],
    ["keys()", "获取当前有持仓的证券代码"],
    ["values()", "获取所有持仓字典"],
  ],
  historyList: [
    ["append(value)", "在末尾追加一条行情记录"],
    ["clear()", "清空历史行情列表"],
    ["copy()", "复制历史行情列表"],
    ["count(value)", "统计指定行情记录出现次数"],
    ["extend(values)", "追加多条行情记录"],
    ["index(value)", "查找指定行情记录的位置"],
    ["insert(index, value)", "在指定位置插入行情记录"],
    ["pop()", "移除并返回指定位置的行情记录"],
    ["remove(value)", "移除指定行情记录"],
    ["reverse()", "原地反转行情顺序"],
    ["sort()", "原地排序行情记录"],
  ],
  stockList: [
    ["append(value)", "向股票信息列表追加一项"],
    ["clear()", "清空股票信息列表"],
    ["copy()", "复制股票信息列表"],
    ["count(value)", "统计指定股票记录出现次数"],
    ["extend(values)", "向股票信息列表追加多项"],
    ["index(value)", "查找指定股票记录的位置"],
    ["insert(index, value)", "在指定位置插入股票记录"],
    ["pop()", "移除并返回指定位置的股票记录"],
    ["remove(value)", "移除指定股票记录"],
    ["reverse()", "原地反转股票信息顺序"],
    ["sort()", "原地排序股票信息"],
  ],
  stockMap: [
    ["get(key)", "按字段名读取股票信息"],
    ["items()", "遍历股票信息字段和值"],
    ["keys()", "获取股票信息字段名"],
    ["values()", "获取股票信息字段值"],
  ],
  list: [
    ["append(value)", "在列表末尾追加一个元素"],
    ["clear()", "清空列表"],
    ["copy()", "复制列表"],
    ["count(value)", "统计元素出现次数"],
    ["extend(values)", "将多个元素追加到列表末尾"],
    ["index(value)", "查找元素首次出现的位置"],
    ["insert(index, value)", "在指定位置插入元素"],
    ["pop()", "移除并返回指定位置的元素"],
    ["remove(value)", "移除首个匹配元素"],
    ["reverse()", "原地反转列表"],
    ["sort()", "原地排序列表"],
  ],
  dict: [
    ["clear()", "清空字典"],
    ["copy()", "复制字典"],
    ["get(key)", "按键读取值；键不存在时返回 None"],
    ["items()", "遍历字典的键和值"],
    ["keys()", "获取字典所有键"],
    ["pop(key)", "移除指定键并返回对应值"],
    ["popitem()", "移除并返回最近插入的一组键值"],
    ["setdefault(key)", "读取指定键；不存在时创建并返回默认值"],
    ["update(other)", "用另一个映射更新字典"],
    ["values()", "获取字典所有值"],
  ],
  set: [
    ["add(value)", "向集合添加元素"],
    ["clear()", "清空集合"],
    ["copy()", "复制集合"],
    ["difference(other)", "返回只存在于当前集合的元素"],
    ["discard(value)", "移除元素；元素不存在时不报错"],
    ["intersection(other)", "返回两个集合共有的元素"],
    ["pop()", "移除并返回集合中的一个元素"],
    ["remove(value)", "移除指定元素；元素不存在时抛出 KeyError"],
    ["union(other)", "返回两个集合的并集"],
    ["update(other)", "将另一个集合的元素加入当前集合"],
  ],
  str: [
    ["capitalize()", "返回首字母大写的新字符串"],
    ["casefold()", "返回用于无大小写比较的新字符串"],
    ["endswith(suffix)", "判断字符串是否以指定内容结尾"],
    ["find(sub)", "查找子串位置；未找到时返回 -1"],
    ["format(*args, **kwargs)", "按位置参数或命名参数替换字符串占位符"],
    ["join(iterable)", "用当前字符串连接多个字符串"],
    ["lower()", "返回全部转为小写的新字符串"],
    ["replace(old, new)", "替换字符串中的指定内容"],
    ["split(sep)", "按分隔符拆分并返回列表"],
    ["startswith(prefix)", "判断字符串是否以指定内容开头"],
    ["strip()", "移除字符串两端空白字符"],
    ["upper()", "返回全部转为大写的新字符串"],
  ],
};
const indexedCompletions = [
  { label: "[0]", type: "enum", detail: "最新一条可用行情；通常是当前回测日", apply: "[0]" },
  { label: "[1]", type: "enum", detail: "倒序列表中的第 2 条，即最近一条更早的实际行情（通常为前一交易日）；等同于该列表的 [-1]", apply: "[1]" },
  { label: "[-1]", type: "enum", detail: "负索引按向过去偏移；-1 表示最近一条更早的实际行情（通常为前一交易日），与 [1] 相同", apply: "[-1]" },
];
const toOptions = (items, type = "method") => items.map(item =>
  Array.isArray(item)
    ? { label: item[0], type, detail: item[1] }
    : item
);
const dotMemberOptions = (items, hasPrefix = false) => items.map(item => ({
  ...item,
  apply: item.apply ?? (hasPrefix ? item.label : `.${item.label}`),
}));
const keyFieldOptions = fields => fields.map(field => ({
  ...field,
  label: `["${field.label}"]`,
  apply: `["${field.label}"]`,
}));

function getSymbols(source, cursorPosition) {
  const variables = new Map();
  const attributes = new Map();
  const methods = new Set();
  const lines = source.split("\n");
  const cursorLine = source.slice(0, cursorPosition).split("\n").length - 1;
  const definitions = lines.map((text, index) => {
    const match = text.match(/^([ \t]*)def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*:/);
    return match ? { index, indent: match[1].length, name: match[2], params: match[3] } : null;
  }).filter(Boolean);
  const activeDefinition = [...definitions].reverse().find(definition => definition.index <= cursorLine);
  let scopeLines = [];
  let parameters = "";
  if (activeDefinition) {
    const nextDefinition = definitions.find(definition =>
      definition.index > activeDefinition.index && definition.indent <= activeDefinition.indent
    );
    const scopeEnd = nextDefinition?.index ?? lines.length;
    scopeLines = lines.slice(activeDefinition.index, scopeEnd);
    parameters = activeDefinition.params;
  } else {
    scopeLines = lines.filter(line => !line || !/^[ \t]/.test(line));
  }

  const addParameters = (target, params) => {
    for (const parameter of params.split(",")) {
      const name = parameter.trim().match(/^\*{0,2}([A-Za-z_]\w*)/);
      if (name && name[1] !== "self") target.set(name[1], "unknown");
    }
  };
  const callSuffix = expression => {
    const open = expression.indexOf("(");
    if (open < 0) return "";
    let depth = 0;
    for (let index = open; index < expression.length; index++) {
      if (expression[index] === "(") depth++;
      else if (expression[index] === ")" && --depth === 0) return expression.slice(index + 1);
    }
    return "";
  };
  const assignedType = expression => {
    if (/^get_history\s*\(/.test(expression)) {
      const indices = [...callSuffix(expression).matchAll(/\[[^\]]*\]/g)].length;
      return indices >= 2 ? "historyRecord" : indices === 1 ? "historyList" : "historyMap";
    }
    if (/^get_stock_names\s*\(/.test(expression)) {
      return /\s*\[/.test(callSuffix(expression)) ? "stockMap" : "stockList";
    }
    if (/^get_stock_codes\s*\(/.test(expression)) return "stringList";
    if (/^(?:broker\.)?get_position\s*\(/.test(expression)) return "positionMap";
    if (/^(?:broker\.)?get_positions\s*\(/.test(expression)) return "positionsMap";
    if (/^(?:broker\.)?get_cash\s*\(/.test(expression)) return "cashInfo";
    if (/^(?:broker\.)?get_equity\s*\(/.test(expression)) return "number";
    if (/^\[\s*["']/.test(expression)) return "stringList";
    if (/^(?:\[|list\s*\()/.test(expression)) return "list";
    if (/^(?:\{|dict\s*\()/.test(expression)) return "dict";
    if (/^set\s*\(/.test(expression)) return "set";
    if (/^["']/.test(expression)) return "str";
    if (/^-?\d+(?:\.\d+)?\b/.test(expression)) return "number";
    const selfRef = expression.match(/^self\.([A-Za-z_]\w*)(.*)$/);
    const ref = selfRef || expression.match(/^([A-Za-z_]\w*)(.*)$/);
    if (!ref) return "unknown";
    const sourceType = selfRef ? attributes.get(ref[1]) || "unknown" : variables.get(ref[1]) || attributes.get(ref[1]) || "unknown";
    if (sourceType === "historyMap" && /^\s*\.get\s*\(/.test(ref[2])) return "historyList";
    if (sourceType === "historyMap") {
      const indices = [...ref[2].matchAll(/\[[^\]]*\]/g)].length;
      return indices > 1 ? "historyRecord" : "historyList";
    }
    if (sourceType === "historyList" && /^\s*\[/.test(ref[2])) return "historyRecord";
    if (sourceType === "stockList" && /^\s*\[/.test(ref[2])) return "stockMap";
    if (sourceType === "stockList") return "stockMap";
    if (sourceType === "positionsMap" && (/^\s*\.get\s*\(/.test(ref[2]) || /^\s*\[/.test(ref[2]))) return "positionMap";
    if (sourceType === "dict" && /^\s*\.get\s*\(/.test(ref[2])) return "unknown";
    return sourceType;
  };

  for (const match of source.matchAll(/^[ \t]{4}def\s+([A-Za-z_]\w*)\s*\(/gm)) {
    methods.add(match[1]);
  }
  for (const match of source.matchAll(/^[ \t]*self\.([A-Za-z_]\w*)[ \t]*(?::[^=\n]+)?=[ \t]*([^\n#]+)/gm)) {
    attributes.set(match[1], assignedType(match[2].trim()));
  }

  addParameters(variables, parameters);
  const scopeSource = scopeLines.join("\n");
  for (const match of scopeSource.matchAll(/^[ \t]*(?!self\.)([A-Za-z_]\w*)[ \t]*(?::[^=\n]+)?=[ \t]*([^\n#]+)/gm)) {
    const [, name, expression] = match;
    variables.set(name, assignedType(expression.trim()));
  }
  for (const match of scopeSource.matchAll(/^[ \t]*for\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+in\s+([^\n:]+)/gm)) {
    const iterable = match[2].trim();
    const root = iterable.match(/^[A-Za-z_]\w*/)?.[0];
    let iterableType = iterable.startsWith("self.")
      ? attributes.get(iterable.slice(5).match(/^[A-Za-z_]\w*/)?.[0])
      : variables.get(root);
    let valueTypes = [];
    if (/\.items\(\)\s*$/.test(iterable)) {
      valueTypes = iterableType === "historyMap" ? ["str", "historyList"]
        : iterableType === "positionsMap" ? ["str", "positionMap"]
          : iterableType === "stockMap" ? ["str", "unknown"]
            : ["unknown", "unknown"];
    } else if (iterableType === "historyMap" || iterableType === "positionsMap") iterableType = "stringList";
    else if (iterableType === "historyList") iterableType = "historyRecord";
    else if (iterableType === "stockList") iterableType = "stockMap";
    else if (iterableType === "stringList") iterableType = "str";
    const targets = match[1].split(",").map(name => name.trim());
    targets.forEach((name, index) => variables.set(name, valueTypes[index] || iterableType || "unknown"));
  }

  return { variables, attributes, methods, assignedType };
}

function typeForExpression(expression, symbols) {
  const match = expression.match(/^\s*([A-Za-z_]\w*)/);
  if (!match) return "unknown";
  const [, root] = match;
  const selfAttribute = expression.match(/^self\.([A-Za-z_]\w*)/);
  const brokerCall = expression.match(/^broker\.(get_position|get_positions|get_cash|get_equity)\s*\(/);
  let type = brokerCall ? ({
    get_position: "positionMap",
    get_positions: "positionsMap",
    get_cash: "cashInfo",
    get_equity: "number",
  })[brokerCall[1]]
    : selfAttribute ? symbols.attributes.get(selfAttribute[1]) || "unknown"
    : root === "self" ? "strategy" : root === "broker" ? "broker"
      : root === "bar" ? "bar" : root === "day" ? "str"
        : symbols.variables.get(root) || symbols.attributes.get(root) || "unknown";
  const suffix = brokerCall ? expression.slice(brokerCall[0].length) : expression.slice(match[0].length);
  const indices = [...suffix.matchAll(/\[[^\]]*\]/g)].length;
  const getCall = suffix.match(/^\s*\.get\s*\(/);
  if (getCall) {
    if (type === "historyMap") type = "historyList";
    else if (type === "positionsMap") type = "positionMap";
    else if (type === "stockList") type = "stockMap";
  } else if (type === "historyMap") {
    if (indices >= 2) type = "historyRecord";
    else if (indices === 1) type = "historyList";
  } else if (type === "historyList" && indices >= 1) {
    type = "historyRecord";
  } else if (type === "stockList" && indices >= 1) {
    type = "stockMap";
  } else if (type === "positionsMap" && indices >= 1) {
    type = "positionMap";
  } else if (type === "stockMap" && indices >= 1) {
    type = "str";
  }
  if (
    (type === "positionMap" || type === "historyRecord" || type === "stockMap") &&
    /\[\s*["'][^"']+["']\s*\]\s*$/.test(suffix)
  ) return "scalar";
  return type;
}

function getDynamicHistoryCodes(source, mappingName) {
  const escapedName = mappingName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const assignment = source.match(new RegExp(`(?:^|\\n)[ \\t]*(?:self\\.)?${escapedName}[ \\t]*=[ \\t]*get_history\\s*\\(\\s*([^,]+)`, "m"));
  if (!assignment) return [];
  const expression = assignment[1].trim();
  const values = new Set();
  const visit = (value, seen = new Set()) => {
    const literals = [...value.matchAll(/["']([^"']+)["']/g)].map(match => match[1]);
    literals.forEach(code => values.add(code));
    const names = [...value.matchAll(/\b(?:self\.)?([A-Za-z_]\w*)\b/g)].map(match => match[1]);
    for (const name of names) {
      if (seen.has(name)) continue;
      seen.add(name);
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const definition = new RegExp(`(?:^|\\n)[ \\t]*(?:self\\.)?${escaped}[ \\t]*=[ \\t]*([^\\n#]+)`, "m").exec(source);
      if (definition && !/^get_/.test(definition[1].trim())) visit(definition[1], seen);
    }
  };
  if (/["']/.test(expression) || /^[\w.[\]'" ]+$/.test(expression)) visit(expression);
  return [...values];
}

function describeAttribute(source, name) {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(`^[ \\t]*self\\.${escapedName}[ \\t]*=[ \\t]*([^\\n#]+)`, "m"));
  const expression = match?.[1].trim() || "";
  if (name === "watchlist" || expression.startsWith("[") || expression.startsWith("get_stock_codes(")) {
    return "策略股票池：证券代码列表";
  }
  if (expression.startsWith("get_history(")) return "策略实例保存的多证券历史行情字典";
  if (expression.startsWith("get_stock_names(")) return "策略实例保存的证券代码与名称列表";
  if (expression.startsWith("broker.get_position(") || expression.startsWith("get_position(")) {
    return "策略实例保存的单证券持仓信息";
  }
  if (expression.startsWith("broker.get_positions(") || expression.startsWith("get_positions(")) {
    return "策略实例保存的证券持仓映射";
  }
  if (expression.startsWith("set(")) return "策略自定义集合状态";
  if (expression === "[]") return name === "closes"
    ? "策略累计的收盘价序列"
    : "策略自定义列表状态";
  if (expression === "True" || expression === "False") return "策略布尔状态";
  if (/^-?\d+(?:\.\d+)?$/.test(expression)) return "策略数值状态";
  if (expression.startsWith('"') || expression.startsWith("'")) return "策略字符串状态";
  return "在 __init__ 中初始化的策略实例状态";
}

function defaultVariableName(expression) {
  const member = expression.match(/\.([A-Za-z_]\w*)\s*$/);
  const key = expression.match(/\[\s*["']([A-Za-z_]\w*)["']\s*\]\s*$/);
  const call = expression.match(/([A-Za-z_]\w*)\s*\([^()]*\)\s*$/);
  const identifier = expression.match(/([A-Za-z_]\w*)\s*$/);
  const candidate = member?.[1] || key?.[1] || call?.[1] || identifier?.[1] || "value";
  const keywords = new Set([
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
  ]);
  return keywords.has(candidate) ? "value" : candidate;
}

function isExtractableExpression(expression) {
  return /^[A-Za-z_(]/.test(expression) &&
    !/(?:^|[^=!<>])=(?!=)|[;\n]/.test(expression) &&
    !/^(?:return|yield|raise|assert|if|elif|while|for|not|and|or)\s/.test(expression);
}

function variableExtractionCompletion(expression, expressionFrom, to) {
  const variable = defaultVariableName(expression);
  return {
    label: "var",
    type: "keyword",
    detail: `提取为变量：${variable} = ${expression}`,
    apply(view) {
      view.dispatch({
        changes: { from: expressionFrom, to, insert: `${variable} = ${expression}` },
        selection: { anchor: expressionFrom, head: expressionFrom + variable.length },
        userEvent: "input",
      });
    },
  };
}

function strategyCompletions(context) {
  const source = context.state.doc.toString();
  const before = context.state.sliceDoc(Math.max(0, context.pos - 500), context.pos);
  const symbols = getSymbols(source, context.pos);
  const currentLine = context.state.doc.lineAt(context.pos);
  const linePrefix = context.state.sliceDoc(currentLine.from, context.pos);
  const extraction = linePrefix.match(/^([ \t]*)(.+)\.(var\w*)$/);
  if (extraction) {
    const [, indentation, rawExpression, suffix] = extraction;
    const expression = rawExpression.trim();
    if (isExtractableExpression(expression)) {
      const expressionFrom = currentLine.from + indentation.length;
      return {
        from: context.pos - suffix.length,
        options: [variableExtractionCompletion(expression, expressionFrom, context.pos)],
        filter: false,
      };
    }
  }

  const expressionPattern = String.raw`[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\([^()\n]*\)|\[[^\]\n]*\])*`;
  const keyAccess = before.match(new RegExp(`(${expressionPattern})\\[\\s*["'][^"']*$`));
  if (keyAccess) {
    const expression = keyAccess[1];
    const type = typeForExpression(expression, symbols);
    const fields = type === "historyRecord" ? strategyFields
      : type === "positionMap" ? positionFields
        : type === "stockMap" ? stockFields
          : [];
    if (!fields.length) return null;
    const partial = context.matchBefore(/[\w\u4e00-\u9fff]*$/);
    const options = fields.map(field => ({
      ...field,
      apply: `${field.label}"]`,
    }));
    return partial ? { from: partial.from, options } : null;
  }

  const member = before.match(new RegExp(`(${expressionPattern})\\.([A-Za-z_]\\w*)?$`));
  if (member) {
    const [, expression, prefix = ""] = member;
    const type = typeForExpression(expression, symbols);
    let options = [];
    let from = context.pos - prefix.length;
    let filter = prefix.length > 0;

    if (type === "historyMap") {
      const mappingName = expression.match(/^(?:self\.)?([A-Za-z_]\w*)/)?.[1];
      const keys = mappingName
        ? getDynamicHistoryCodes(source, mappingName).map(code => ({
          label: `["${code}"]`,
          apply: `["${code}"]`,
          type: "enum",
          detail: `历史行情：${code}`,
        }))
        : [];
      const methods = dotMemberOptions(toOptions(collectionMethods.historyMap), Boolean(prefix));
      options = [...keys, ...methods];
      if (!prefix) from = context.pos - 1;
    } else if (type === "historyList") {
      options = [...indexedCompletions, ...dotMemberOptions(toOptions(collectionMethods.historyList), Boolean(prefix))];
      if (!prefix) from = context.pos - 1;
    } else if (type === "broker") {
      options = brokerMethods;
    } else if (type === "strategy") {
      options = [
        ...[...symbols.attributes.keys()].map(label => ({
          label,
          type: "property",
          detail: describeAttribute(source, label),
        })),
        ...[...symbols.methods].map(label => ({
          label: `${label}()`,
          type: "method",
          detail: `调用策略中定义的 ${label} 方法`,
        })),
      ];
    } else if (type === "bar") {
      options = barFields;
    } else if (type === "cashInfo") {
      options = cashFields;
    } else if (type === "historyRecord") {
      options = strategyFields.map(field => ({
        ...field,
        apply: `["${field.label}"]`,
      }));
      from = context.pos - prefix.length - 1;
      filter = false;
    } else if (type === "positionMap") {
      options = keyFieldOptions(positionFields);
      from = context.pos - prefix.length - 1;
      filter = false;
    } else if (type === "stockMap") {
      options = keyFieldOptions(stockFields);
      from = context.pos - prefix.length - 1;
      filter = false;
    } else if (type === "positionsMap") {
      options = dotMemberOptions(toOptions(collectionMethods.positionsMap), Boolean(prefix));
      if (!prefix) from = context.pos - 1;
    } else {
      const methodType = type === "stringList" ? "list" : type;
      options = dotMemberOptions(toOptions(collectionMethods[methodType] || []), Boolean(prefix));
      if (!prefix) from = context.pos - 1;
    }
    return options.length ? { from, options, filter } : null;
  }

  const word = context.matchBefore(/[\w.]+$/);
  if (!context.explicit && (!word || word.from === word.to)) return null;
  const visibleVariables = new Set([...symbols.variables.keys(), ...symbols.attributes.keys()]);
  const names = [...visibleVariables].map(label => {
    const type = symbols.variables.get(label) || symbols.attributes.get(label) || "unknown";
    const detail = symbols.attributes.has(label)
      ? describeAttribute(source, label)
      : ({
        historyMap: "当前作用域中的代码到历史行情映射",
        historyList: "当前作用域中的单证券历史行情列表（新到旧）",
        historyRecord: "当前作用域中的单日 OHLCV 行情记录",
        positionMap: "当前作用域中的单证券持仓信息",
        positionsMap: "当前作用域中的代码到持仓信息映射",
        stockList: "当前作用域中的证券代码与名称列表",
        stockMap: "当前作用域中的单证券代码与名称记录",
        stringList: "当前作用域中的字符串列表",
        list: "当前作用域中的列表",
        set: "当前作用域中的集合",
        dict: "当前作用域中的字典",
        number: "当前作用域中的数值",
        str: "当前作用域中的字符串",
        unknown: "当前方法的参数或尚未推断类型的局部变量",
      })[type];
    return { label, type: "variable", detail };
  });
  return { from: word?.from ?? context.pos, options: [...apiFunctions, ...names] };
}

function confirmSelectedVariableName(view) {
  const { from, to } = view.state.selection.main;
  if (from === to) return false;
  const line = view.state.doc.lineAt(from);
  if (to > line.to) return false;
  const assignment = line.text.match(/^([ \t]*)([A-Za-z_]\w*)[ \t]*=[ \t]*.+$/);
  if (!assignment || from !== line.from + assignment[1].length || to !== from + assignment[2].length) {
    return false;
  }
  view.dispatch({ selection: { anchor: line.to } });
  return true;
}

function acceptCompletionOrIndent(view) {
  if (acceptCompletion(view)) return true;
  if (confirmSelectedVariableName(view)) return true;
  const { from, to } = view.state.selection.main;
  view.dispatch({
    changes: { from, to, insert: "  " },
    selection: { anchor: from + 2 },
    userEvent: "input",
  });
  return true;
}

/*
 * Keep Tab inside the editor: accept an open suggestion, or insert two spaces
 * when there is no active completion menu.
 */
const tabKeymap = keymap.of([
  { key: "Tab", run: acceptCompletionOrIndent },
  {
    key: "Enter",
    run: view => acceptCompletion(view) || confirmSelectedVariableName(view),
  },
]);

export function createStrategyEditor(parent, value, onChange) {
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: value,
      extensions: [
        tabKeymap,
        basicSetup,
        indentUnit.of("    "),
        python(),
        syntaxHighlighting(strategyHighlighting),
        autocompletion({
          activateOnTyping: true,
          selectOnOpen: true,
          override: [strategyCompletions],
        }),
        EditorView.lineWrapping,
        EditorView.updateListener.of(update => {
          if (update.docChanged) {
            onChange(update.state.doc.toString());
            let shouldComplete = false;
            update.changes.iterChanges((fromA, _toA, fromB, _toB, inserted) => {
              if (!inserted.toString().endsWith(".")) return;
              const prefix = update.state.doc.sliceString(Math.max(0, fromB - 2), fromB);
              if (/[\w)\]]$/.test(prefix)) shouldComplete = true;
            });
            if (shouldComplete) queueMicrotask(() => {
              if (view.hasFocus) startCompletion(view);
            });
          }
        }),
        EditorView.theme({
          "&": { height: "100%", fontSize: "13px", backgroundColor: "#17251f", color: "#e5eee8" },
          ".cm-scroller": { overflow: "auto", fontFamily: '"Cascadia Code", Consolas, monospace' },
          ".cm-gutters": { backgroundColor: "#17251f", color: "#718078", border: "none" },
          ".cm-activeLineGutter": { backgroundColor: "#21372d" },
          ".cm-activeLine": { backgroundColor: "#ffffff08" },
          ".cm-cursor": { borderLeftColor: "#8cd2ad" },
          ".cm-selectionBackground": { backgroundColor: "#347b5c88 !important" },
          ".cm-tooltip": { zIndex: "30" },
        }),
        keymap.of([{
          key: "Mod-s",
          run: () => {
            window.dispatchEvent(new CustomEvent("strategy:save"));
            return true;
          },
        }]),
      ],
    }),
  });

  return {
    view,
    getValue: () => view.state.doc.toString(),
    setValue(nextValue) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: nextValue },
      });
    },
    focus: () => view.focus(),
  };
}
