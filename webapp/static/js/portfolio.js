/* My Portfolio.
 *
 * Holdings ({ticker, avg_price, quantity}) live ONLY in this browser's localStorage. The server is
 * asked for today's price / previous close / score of bare tickers (POST /api/portfolio/quotes) and
 * for the USD/ILS rate (GET /api/fx); it never receives quantities or buy prices. Everything about
 * value, daily change and total return is computed here.
 *
 * Money rules:
 *  - Avg buy price and current price are in the asset's NATIVE currency (USD for US names, ILS for TASE).
 *  - Values and amounts are shown in the selected display currency, converted at TODAY's rate
 *    (so Total Return for a foreign asset ignores the FX move since purchase; the page says so).
 *  - Daily change and Total return always show the percentage AND the absolute amount side by side.
 */
"use strict";

const PortfolioMath = (() => {
  const SYMBOL = { USD: "$", ILS: "₪" };

  /** rate = ILS per 1 USD. null when the conversion is impossible (unknown pair or no rate). */
  function convert(amount, from, to, rate) {
    if (amount == null || !Number.isFinite(amount)) return null;
    if (from === to) return amount;
    if (!rate || !Number.isFinite(rate) || rate <= 0) return null;
    if (from === "USD" && to === "ILS") return amount * rate;
    if (from === "ILS" && to === "USD") return amount / rate;
    return null;
  }

  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

  /** One holding + its quote -> metrics in the display currency (any piece may be null). */
  function positionMetrics(holding, quote, display, rate) {
    const empty = { value: null, prevValue: null, cost: null, dailyAmt: null, dailyPct: null, retAmt: null, retPct: null };
    if (!quote) return empty;
    const ccy = quote.currency || "USD";
    const qty = holding.quantity;
    const price = num(quote.price);
    const prev = num(quote.previous_close);
    const avg = num(holding.avg_price);
    const conv = (v) => convert(v, ccy, display, rate);

    const value = price == null ? null : conv(qty * price);
    const prevValue = prev == null ? null : conv(qty * prev);
    const cost = avg == null ? null : conv(qty * avg);
    return {
      value,
      prevValue,
      cost,
      dailyAmt: value != null && prevValue != null ? value - prevValue : null,
      dailyPct: price != null && prev ? (price / prev - 1) * 100 : null,
      retAmt: value != null && cost != null ? value - cost : null,
      retPct: price != null && avg ? (price / avg - 1) * 100 : null,
    };
  }

  /** Sum the per-position metrics. A position missing a piece is left out of THAT total only. */
  function portfolioTotals(metricsList) {
    let value = 0, valueN = 0;
    let daily = 0, prevBase = 0, dailyN = 0;
    let ret = 0, costBase = 0, retN = 0;
    for (const m of metricsList) {
      if (m.value != null) { value += m.value; valueN++; }
      if (m.dailyAmt != null && m.prevValue != null) { daily += m.dailyAmt; prevBase += m.prevValue; dailyN++; }
      if (m.retAmt != null && m.cost != null) { ret += m.retAmt; costBase += m.cost; retN++; }
    }
    return {
      value: valueN ? value : null,
      dailyAmt: dailyN ? daily : null,
      dailyPct: dailyN && prevBase ? (daily / prevBase) * 100 : null,
      retAmt: retN ? ret : null,
      retPct: retN && costBase ? (ret / costBase) * 100 : null,
      counted: valueN,
    };
  }

  const group = (v, min, max) => v.toLocaleString("en-US", { minimumFractionDigits: min, maximumFractionDigits: max });
  const sym = (ccy) => SYMBOL[ccy] || `${ccy} `;

  function money(amount, ccy) {
    if (amount == null) return "—";
    return `${amount < 0 ? "−" : ""}${sym(ccy)}${group(Math.abs(amount), 2, 2)}`;
  }
  function signedMoney(amount, ccy) {
    if (amount == null) return "";
    if (Math.abs(amount) < 0.005) return `${sym(ccy)}0.00`;
    return `${amount < 0 ? "−" : "+"}${sym(ccy)}${group(Math.abs(amount), 2, 2)}`;
  }
  function signedPct(p) {
    if (p == null) return "—";
    if (Math.abs(p) < 0.005) return "0.00%";
    return `${p < 0 ? "−" : "+"}${group(Math.abs(p), 2, 2)}%`;
  }
  function price(v) {
    return v == null ? "—" : group(v, 2, 4);
  }
  function quantity(v) {
    return group(v, 0, 6);
  }
  const tone = (v) => (v == null || Math.abs(v) < 0.005 ? "flat" : v > 0 ? "pos" : "neg");

  /** Parse whatever localStorage returned into clean holdings; drops anything invalid. */
  function sanitizeHoldings(raw) {
    if (!Array.isArray(raw)) return [];
    const seen = new Set();
    const out = [];
    for (const h of raw) {
      if (!h || typeof h.ticker !== "string") continue;
      const ticker = h.ticker.trim().toUpperCase();
      const qty = Number(h.quantity);
      const avg = Number(h.avg_price);
      if (!ticker || seen.has(ticker) || !Number.isFinite(qty) || qty <= 0 || !Number.isFinite(avg) || avg < 0) continue;
      seen.add(ticker);
      out.push({ ticker, avg_price: avg, quantity: qty });
    }
    return out;
  }

  const scoreBand = (s) => (s == null ? "none" : s >= 70 ? "strong" : s >= 45 ? "mixed" : "weak");

  return { convert, positionMetrics, portfolioTotals, money, signedMoney, signedPct, price, quantity, tone, sanitizeHoldings, scoreBand, SYMBOL };
})();

if (typeof module !== "undefined" && module.exports) module.exports = PortfolioMath;

(function initPortfolioPage() {
  if (typeof document === "undefined" || !document.getElementById("pf-list")) return;
  const M = PortfolioMath;

  const STORAGE_KEY = "marketpulse.portfolio.v1";
  const CCY_KEY = "marketpulse.portfolio.ccy.v1";

  const $ = (id) => document.getElementById(id);
  const listEl = $("pf-list");
  const dialog = $("pf-dialog");
  const form = $("pf-form");
  const fTicker = $("pf-f-ticker");
  const fQty = $("pf-f-qty");
  const fPrice = $("pf-f-price");

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---- storage: every access is guarded; the page still works (in memory) without it ----
  let storageWorks = true;
  let memoryHoldings = [];

  function loadHoldings() {
    let raw;
    try {
      raw = window.localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      storageWorks = false;
      return memoryHoldings;
    }
    if (raw == null) return [];
    try {
      return M.sanitizeHoldings(JSON.parse(raw));
    } catch (_) {
      // Corrupt JSON: keep a copy for recovery instead of silently destroying it.
      try { window.localStorage.setItem(`${STORAGE_KEY}.corrupt`, raw); } catch (__) { /* ignore */ }
      showStatus("Your saved portfolio could not be read, so it was reset. A raw copy was kept under 'marketpulse.portfolio.v1.corrupt'.", "warn");
      return [];
    }
  }

  function saveHoldings(list) {
    memoryHoldings = list;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
      storageWorks = true;
    } catch (_) {
      storageWorks = false;
    }
    $("pf-storage-warning").hidden = storageWorks;
  }

  function readCcyPref() {
    try {
      const v = window.localStorage.getItem(CCY_KEY);
      return v === "ILS" ? "ILS" : "USD";
    } catch (_) {
      return "USD";
    }
  }
  function writeCcyPref(v) {
    try { window.localStorage.setItem(CCY_KEY, v); } catch (_) { /* preference is a nicety */ }
  }

  // ---- state ----
  let holdings = loadHoldings();
  memoryHoldings = holdings;
  $("pf-storage-warning").hidden = storageWorks;
  let display = readCcyPref();
  let quotes = {}; // ticker -> quote from the API
  let unknown = new Set();
  let fx = null; // { rate, as_of, stale, age_days }
  let quotesLoaded = false;
  let requestSeq = 0;

  const known = new Map(); // ticker -> { name, ccy }, from the same <datalist> the stock search uses
  for (const opt of document.querySelectorAll("#pf-ticker-list option")) {
    known.set(opt.value.toUpperCase(), { name: opt.textContent.trim(), ccy: opt.dataset.ccy || "USD" });
  }

  function showStatus(msg, kind) {
    const el = $("pf-status");
    if (!msg) { el.hidden = true; return; }
    el.textContent = msg;
    el.className = `pf-banner${kind === "warn" ? " pf-banner-warn" : ""}`;
    el.hidden = false;
  }

  // ---- data ----
  async function refresh() {
    const seq = ++requestSeq;
    const tickers = holdings.map((h) => h.ticker);
    $("pf-refresh").disabled = true;
    try {
      const [fxRes, qRes] = await Promise.allSettled([
        fetch("/api/fx").then((r) => (r.ok ? r.json() : Promise.reject(new Error(`FX HTTP ${r.status}`)))),
        tickers.length
          ? fetch("/api/portfolio/quotes", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ tickers }),
            }).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`quotes HTTP ${r.status}`))))
          : Promise.resolve({ quotes: {}, unknown: [] }),
      ]);
      if (seq !== requestSeq) return; // a newer refresh superseded this one

      fx = fxRes.status === "fulfilled" ? fxRes.value : fx;
      const problems = [];
      if (fxRes.status === "rejected") problems.push("the USD/ILS exchange rate");
      if (qRes.status === "fulfilled") {
        quotes = qRes.value.quotes || {};
        unknown = new Set(qRes.value.unknown || []);
        quotesLoaded = true;
      } else {
        problems.push("prices");
      }
      showStatus(problems.length ? `Could not load ${problems.join(" and ")}. ${quotesLoaded ? "Showing the last values that loaded." : ""}` : "", "warn");
    } finally {
      if (seq === requestSeq) $("pf-refresh").disabled = false;
    }
    render();
  }

  // ---- rendering ----
  const shortDate = (iso) => {
    const d = new Date(`${iso}T00:00:00`);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };
  const toneClass = (v) => `pf-${M.tone(v)}`;

  function dualHtml(pct, amt, ccy) {
    // Percentage and absolute amount are ALWAYS rendered together.
    const t = toneClass(pct ?? amt);
    return `<span class="pf-pct ${t}">${M.signedPct(pct)}</span><span class="pf-amt ${t}">${amt == null ? "" : M.signedMoney(amt, ccy)}</span>`;
  }

  function rowMetrics() {
    const rate = fx ? fx.rate : null;
    return holdings.map((h) => ({ h, q: quotes[h.ticker], m: M.positionMetrics(h, quotes[h.ticker], display, rate) }));
  }

  function renderSummary(rows) {
    const rate = fx ? fx.rate : null;
    const totals = M.portfolioTotals(rows.map((r) => r.m));
    $("pf-total-value").textContent = totals.value == null ? "—" : M.money(totals.value, display);
    $("pf-total-daily").innerHTML = totals.dailyPct == null && totals.dailyAmt == null ? '<span class="pf-pct pf-flat">—</span><span class="pf-amt"></span>' : dualHtml(totals.dailyPct, totals.dailyAmt, display);
    $("pf-total-return").innerHTML = totals.retPct == null && totals.retAmt == null ? '<span class="pf-pct pf-flat">—</span><span class="pf-amt"></span>' : dualHtml(totals.retPct, totals.retAmt, display);
    $("pf-count").textContent = String(holdings.length);

    const fxNote = $("pf-fx-note");
    if (fx) {
      const age = fx.stale ? ` · <span class="pf-warn-text">${fx.age_days} days old</span>` : "";
      fxNote.innerHTML = `USD/ILS ${fx.rate.toFixed(4)} · as of ${escapeHtml(fx.as_of)}${age}`;
    } else {
      fxNote.innerHTML = '<span class="pf-warn-text">Exchange rate unavailable</span>';
    }

    const dates = rows.map((r) => r.q && r.q.price_date).filter(Boolean).sort();
    $("pf-asof").textContent = dates.length ? `Prices as of ${dates[0]}${dates[dates.length - 1] !== dates[0] ? " – " + dates[dates.length - 1] : ""}` : "";

    // Holdings in a currency we cannot convert drop out of the totals; say so rather than hide it.
    const unconverted = rows.filter((r) => r.q && r.m.value == null && r.q.price != null);
    if (unconverted.length && !rate) {
      showStatus(`The exchange rate is unavailable, so ${unconverted.map((r) => r.h.ticker).join(", ")} (${unconverted[0].q.currency}) cannot be converted to ${display} and is left out of the totals.`, "warn");
    }
  }

  function rowHtml({ h, q, m }) {
    const href = `/stock/${encodeURIComponent(h.ticker)}`;
    const nativeCcy = q ? q.currency : (known.get(h.ticker) || {}).ccy || "USD";
    const name = q ? q.name : (known.get(h.ticker) || {}).name || "";
    const fresh = q && q.freshness;
    const stale = fresh && fresh.price_stale;
    const noData = quotesLoaded && !q;
    const scoreVal = q ? q.score : null;
    const band = M.scoreBand(scoreVal);

    let priceCell;
    if (!quotesLoaded) priceCell = '<span class="pf-muted">…</span>';
    else if (noData || q.price == null) priceCell = '<span class="pf-muted">No data</span>';
    else priceCell = `${escapeHtml(M.SYMBOL[nativeCcy] || nativeCcy + " ")}${M.price(q.price)}<small class="pf-asof ${stale ? "pf-warn-text" : ""}" title="${stale ? "Stale: " : ""}date of the latest daily bar (${escapeHtml(q.price_date)})">${stale ? "⚠ " : ""}${escapeHtml(shortDate(q.price_date))}</small>`;

    const scoreTitle = q && q.score_exact != null ? `Quant Score v2: ${q.score_exact} (${q.signal})` : "No score yet";
    return `
      <article class="pf-row" data-ticker="${escapeHtml(h.ticker)}" data-href="${href}">
        <div class="pf-cell pf-cell-name">
          <a class="pf-ticker" href="${href}">${escapeHtml(h.ticker)}</a>
          <span class="pf-name">${escapeHtml(name)}</span>
          ${nativeCcy !== "USD" ? `<span class="pf-ccy-chip">${escapeHtml(nativeCcy)}</span>` : ""}
        </div>
        <div class="pf-cell r" data-label="Quantity">${M.quantity(h.quantity)}</div>
        <div class="pf-cell r" data-label="Avg buy">${escapeHtml(M.SYMBOL[nativeCcy] || nativeCcy + " ")}${M.price(h.avg_price)}</div>
        <div class="pf-cell r pf-price" data-label="Price">${priceCell}</div>
        <div class="pf-cell r" data-label="Value">${m.value == null ? "—" : M.money(m.value, display)}</div>
        <div class="pf-cell r pf-dual" data-label="Daily change">${m.dailyPct == null && m.dailyAmt == null ? '<span class="pf-pct pf-flat">—</span><span class="pf-amt"></span>' : dualHtml(m.dailyPct, m.dailyAmt, display)}</div>
        <div class="pf-cell r pf-dual" data-label="Total return">${m.retPct == null && m.retAmt == null ? '<span class="pf-pct pf-flat">—</span><span class="pf-amt"></span>' : dualHtml(m.retPct, m.retAmt, display)}</div>
        <div class="pf-cell r" data-label="Quant Score"><span class="pf-score pf-score-${band}" title="${escapeHtml(scoreTitle)}">${scoreVal == null ? "—" : scoreVal.toFixed(1)}</span></div>
        <div class="pf-cell pf-actions">
          <button type="button" class="pf-icon-btn" data-action="edit" aria-label="Edit ${escapeHtml(h.ticker)}" title="Edit">✎</button>
          <button type="button" class="pf-icon-btn pf-icon-danger" data-action="remove" aria-label="Remove ${escapeHtml(h.ticker)}" title="Remove">✕</button>
        </div>
      </article>`;
  }

  function render() {
    document.querySelectorAll(".pf-ccy-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.ccy === display)));
    const rows = rowMetrics();
    renderSummary(rows);
    if (!holdings.length) {
      listEl.innerHTML = '<div class="empty-state pf-empty">No holdings yet. Use <strong>+ Add holding</strong> to enter a ticker, the quantity and your average buy price.</div>';
      return;
    }
    // Largest position first (value in the display currency); positions without a value sink to the bottom.
    rows.sort((a, b) => (b.m.value ?? -1) - (a.m.value ?? -1));
    listEl.innerHTML = rows.map(rowHtml).join("");
  }

  // ---- list interactions ----
  listEl.addEventListener("click", (e) => {
    const row = e.target.closest(".pf-row");
    if (!row) return;
    const btn = e.target.closest("button[data-action]");
    if (btn) {
      const ticker = row.dataset.ticker;
      if (btn.dataset.action === "edit") openDialog(ticker);
      else if (btn.dataset.action === "remove") removeHolding(ticker, true);
      return;
    }
    if (e.target.closest("a")) return; // the ticker link navigates by itself
    window.location.href = row.dataset.href;
  });

  document.querySelectorAll(".pf-ccy-btn").forEach((b) =>
    b.addEventListener("click", () => {
      display = b.dataset.ccy;
      writeCcyPref(display);
      showStatus("");
      render();
    })
  );
  $("pf-refresh").addEventListener("click", refresh);
  $("pf-add").addEventListener("click", () => openDialog(null));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && holdings.length) refresh();
  });

  // ---- add / edit / remove ----
  let editing = null; // ticker being edited, or null when adding

  function lookup(raw) {
    return known.get(String(raw || "").trim().toUpperCase()) || null;
  }

  function updateTickerHint() {
    const info = lookup(fTicker.value);
    $("pf-f-ticker-hint").textContent = info ? `${info.name} · prices in ${info.ccy}` : "Pick a ticker from the list.";
    $("pf-f-price-label").textContent = info ? `Average buy price (${info.ccy})` : "Average buy price";
  }

  function formError(msg) {
    const el = $("pf-form-error");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function openDialog(ticker) {
    editing = ticker;
    formError("");
    const h = ticker ? holdings.find((x) => x.ticker === ticker) : null;
    $("pf-dialog-title").textContent = h ? `Edit ${h.ticker}` : "Add holding";
    fTicker.value = h ? h.ticker : "";
    fTicker.disabled = Boolean(h); // changing the ticker means a different holding: remove and add instead
    fQty.value = h ? String(h.quantity) : "";
    fPrice.value = h ? String(h.avg_price) : "";
    $("pf-f-remove").hidden = !h;
    updateTickerHint();
    dialog.showModal();
    (h ? fQty : fTicker).focus();
  }

  fTicker.addEventListener("input", () => { updateTickerHint(); formError(""); });
  $("pf-f-cancel").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); }); // backdrop click
  $("pf-f-remove").addEventListener("click", () => {
    if (editing && removeHolding(editing, true)) dialog.close();
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const ticker = (editing || fTicker.value).trim().toUpperCase();
    const qty = Number(fQty.value);
    const avg = Number(fPrice.value);
    if (!lookup(ticker)) return formError("Unknown ticker. Pick one from the suggestions list.");
    if (!fQty.value.trim() || !Number.isFinite(qty) || qty <= 0) return formError("Quantity must be a number greater than 0.");
    if (!fPrice.value.trim() || !Number.isFinite(avg) || avg <= 0) return formError("Average buy price must be a number greater than 0.");
    if (!editing && holdings.some((h) => h.ticker === ticker)) return formError(`${ticker} is already in your portfolio. Edit it from the list instead.`);

    const next = editing
      ? holdings.map((h) => (h.ticker === ticker ? { ticker, avg_price: avg, quantity: qty } : h))
      : [...holdings, { ticker, avg_price: avg, quantity: qty }];
    holdings = next;
    saveHoldings(holdings);
    dialog.close();
    // Only a new ticker needs a server round trip; an edit changes nothing the server knows.
    if (editing) render();
    else refresh();
  });

  function removeHolding(ticker, ask) {
    if (ask && !window.confirm(`Remove ${ticker} from your portfolio?`)) return false;
    holdings = holdings.filter((h) => h.ticker !== ticker);
    saveHoldings(holdings);
    delete quotes[ticker];
    render();
    return true;
  }

  // ---- go ----
  render();
  refresh();
})();
