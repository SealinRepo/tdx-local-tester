export function attachStockSearch(input) {
  const picker = input.closest(".stock-picker");
  const results = picker.querySelector(".stock-options");
  let matches = [];
  let activeIndex = -1;
  let timer;
  let searchVersion = 0;

  function close() {
    searchVersion += 1;
    results.classList.add("hidden");
    input.setAttribute("aria-expanded", "false");
    activeIndex = -1;
  }

  function render(items) {
    matches = items;
    activeIndex = -1;
    results.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "stock-option empty";
      empty.textContent = "没有匹配的股票";
      results.append(empty);
    } else {
      for (const [index, stock] of items.entries()) {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "stock-option";
        option.setAttribute("role", "option");
        option.dataset.index = String(index);
        const code = document.createElement("strong");
        code.textContent = stock.code;
        const name = document.createElement("span");
        name.textContent = stock.name;
        option.append(code, name);
        option.addEventListener("pointerdown", event => event.preventDefault());
        option.addEventListener("click", () => select(index));
        results.append(option);
      }
    }
    results.classList.remove("hidden");
    input.setAttribute("aria-expanded", "true");
  }

  function select(index) {
    const stock = matches[index];
    if (!stock) return;
    input.value = stock.code;
    input.dataset.stockName = stock.name;
    input.setCustomValidity("");
    input.dispatchEvent(new Event("change", { bubbles: true }));
    close();
  }

  function updateSelectionValidity() {
    const value = input.value.trim();
    const validSelection = !value || Boolean(input.dataset.stockName) || /^\d{6}$/.test(value);
    input.setCustomValidity(
      validSelection ? "" : "请选择搜索结果，或直接输入 6 位股票代码。",
    );
    return validSelection;
  }

  function updateActiveOption() {
    const options = [...results.querySelectorAll("button.stock-option")];
    options.forEach((option, index) => {
      option.classList.toggle("active", index === activeIndex);
      option.setAttribute("aria-selected", String(index === activeIndex));
    });
    options[activeIndex]?.scrollIntoView({ block: "nearest" });
  }

  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.addEventListener("input", () => {
    delete input.dataset.stockName;
    updateSelectionValidity();
    clearTimeout(timer);
    const query = input.value.trim();
    if (!query) {
      close();
      return;
    }
    const version = ++searchVersion;
    timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `搜索失败 (${response.status})`);
        if (version !== searchVersion) return;
        render(payload);
      } catch (error) {
        if (version !== searchVersion) return;
        matches = [];
        results.replaceChildren();
        const message = document.createElement("div");
        message.className = "stock-option stock-error";
        message.textContent = error.message;
        results.append(message);
        results.classList.remove("hidden");
        input.setAttribute("aria-expanded", "true");
      }
    }, 500);
  });
  input.form.addEventListener("submit", event => {
    if (!updateSelectionValidity()) {
      event.preventDefault();
      input.reportValidity();
    }
  });
  input.addEventListener("keydown", event => {
    if (results.classList.contains("hidden")) return;
    if (event.key === "ArrowDown" && matches.length) {
      event.preventDefault();
      activeIndex = (activeIndex + 1) % matches.length;
      updateActiveOption();
    } else if (event.key === "ArrowUp" && matches.length) {
      event.preventDefault();
      activeIndex = (activeIndex - 1 + matches.length) % matches.length;
      updateActiveOption();
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      select(activeIndex);
    } else if (event.key === "Escape") {
      close();
    }
  });
  document.addEventListener("pointerdown", event => {
    if (!picker.contains(event.target)) close();
  });
}
