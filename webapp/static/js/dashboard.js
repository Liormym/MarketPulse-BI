function fmtPct(value, digits = 2) {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}%`;
}

function fmtUsd(value, digits = 2) {
  return value === null || value === undefined ? "—" : `$${value.toFixed(digits)}`;
}

function fmtNum(value, digits = 0) {
  return value === null || value === undefined ? "—" : value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

async function loadMacro() {
  const res = await fetch("/api/macro");
  const data = await res.json();

  document.getElementById("macro-10y").textContent = fmtPct(data.ten_year_yield);
  document.getElementById("macro-2y").textContent = fmtPct(data.two_year_yield);
  if (data.ten_year_yield != null && data.two_year_yield != null) {
    const spread = data.ten_year_yield - data.two_year_yield;
    const spreadEl = document.getElementById("macro-spread");
    spreadEl.textContent = fmtPct(spread);
    spreadEl.style.color = spread < 0 ? "var(--neg)" : "var(--text)"; // inverted curve is a classic recession signal
  }
  document.getElementById("macro-oil").textContent = fmtUsd(data.crude_oil_price);
  document.getElementById("macro-btc").textContent = fmtUsd(data.bitcoin_price, 0);
  document.getElementById("macro-kospi").textContent = fmtNum(data.kospi_index, 2);

  const bannerEl = document.getElementById("macro-risk-banner");
  if (bannerEl) {
    bannerEl.classList.toggle("hidden", !data.macro_risk_alert);
  }

  if (data.market_breadth != null) {
    document.getElementById("macro-breadth").textContent = data.market_breadth;
    document.getElementById("macro-breadth").classList.remove("placeholder");
  }
  if (data.aaii_sentiment != null) {
    document.getElementById("macro-aaii").textContent = data.aaii_sentiment;
    document.getElementById("macro-aaii").classList.remove("placeholder");
  }
}

async function loadSectorFlows() {
  const res = await fetch("/api/sector-flows");
  const rows = await res.json();
  const tbody = document.getElementById("sector-table-body");

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No sector volume data yet — run scripts/compute_sector_flows.py</td></tr>';
    return;
  }

  document.getElementById("sector-asof").textContent = `as of ${rows[0].as_of}`;
  tbody.innerHTML = rows
    .map(
      (r) => `
      <tr class="clickable" onclick="window.location.href='/stock/${r.ticker}'">
        <td><strong>${r.ticker}</strong></td>
        <td>${r.name}</td>
        <td>${r.volume_ratio != null ? r.volume_ratio.toFixed(2) + "x" : "—"}</td>
        <td><span class="flow-tag ${r.flow_status || "Neutral"}">${r.flow_status || "Insufficient data"}</span></td>
      </tr>`
    )
    .join("");
}

async function loadMovers() {
  const res = await fetch("/api/movers");
  const movers = await res.json();
  const grid = document.getElementById("movers-grid");

  if (!movers.length) {
    grid.innerHTML = '<div class="empty-state">No price history yet</div>';
    return;
  }

  grid.innerHTML = movers
    .map((m) => {
      const cls = m.change_pct >= 0 ? "positive" : "negative";
      const sign = m.change_pct >= 0 ? "+" : "";
      return `
      <div class="mover-card ${cls}" onclick="window.location.href='/stock/${m.ticker}'">
        <div class="mover-ticker">${m.ticker}</div>
        <div class="mover-price">$${m.price.toFixed(2)}</div>
        <div class="mover-change change-pct ${cls}">${sign}${m.change_pct.toFixed(2)}%</div>
      </div>`;
    })
    .join("");

  renderTickerTape(movers);
}

function renderTickerTape(movers) {
  const tape = document.getElementById("ticker-tape");
  if (!tape) return; // not present on every screen

  if (!movers.length) {
    tape.innerHTML = "";
    return;
  }

  const itemsHtml = movers
    .map((m) => {
      const cls = m.change_pct >= 0 ? "positive" : "negative";
      const sign = m.change_pct >= 0 ? "+" : "";
      return `
      <div class="ticker-tape-item" onclick="window.location.href='/stock/${m.ticker}'">
        <span class="tt-ticker">${m.ticker}</span>
        <span class="tt-price">$${m.price.toFixed(2)}</span>
        <span class="tt-change ${cls}">${sign}${m.change_pct.toFixed(2)}%</span>
      </div>`;
    })
    .join("");

  // The track is rendered twice back-to-back so the CSS animation (which
  // slides it exactly -50%) loops seamlessly instead of snapping.
  tape.innerHTML = `<div class="ticker-tape-track">${itemsHtml}${itemsHtml}</div>`;
}

async function loadStrongBuys() {
  const grid = document.getElementById("strong-buys-grid");
  if (!grid) return;

  const res = await fetch("/api/top-scores");
  const rows = await res.json();

  if (!rows.length) {
    grid.innerHTML = '<div class="empty-state">No scored tickers yet — run scripts/compute_investment_scores.py</div>';
    return;
  }

  grid.innerHTML = rows
    .map(
      (r, i) => `
      <a class="strong-buy-card" href="/stock/${r.ticker}">
        <div class="strong-buy-rank">#${i + 1}</div>
        <div class="strong-buy-ticker">${r.ticker}</div>
        <div class="strong-buy-name">${r.name}</div>
        <div class="strong-buy-score">${r.score}</div>
      </a>`
    )
    .join("");
}

loadMacro();
loadSectorFlows();
loadMovers();
loadStrongBuys();
