function fmtPct(value, digits = 2) {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}%`;
}

function fmtUsd(value, digits = 2) {
  return value === null || value === undefined ? "—" : `$${value.toFixed(digits)}`;
}

function fmtNum(value, digits = 0) {
  return value === null || value === undefined ? "—" : value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// Daily % change badge for a macro tile. Standard positive=green/negative=red
// rule applies even to yields - a rising 10Y is bearish for equities, but
// the badge itself just reports the number's own sign, consistently with
// every other neon green/red indicator in the UI.
function renderMacroChange(id, changePct) {
  const el = document.getElementById(id);
  if (!el) return;
  if (changePct == null) {
    el.textContent = "";
    el.className = "macro-tile-change";
    return;
  }
  const cls = changePct >= 0 ? "positive" : "negative";
  const sign = changePct >= 0 ? "+" : "";
  el.textContent = `${sign}${changePct.toFixed(2)}%`;
  el.className = `macro-tile-change ${cls}`;
}

async function loadMacro() {
  const res = await fetch("/api/macro");
  const data = await res.json();

  const asOfEl = document.getElementById("macro-asof");
  if (asOfEl) {
    asOfEl.textContent = data.as_of ? `Live as of: ${data.as_of}` : "";
  }

  document.getElementById("macro-10y").textContent = fmtPct(data.ten_year_yield);
  renderMacroChange("macro-10y-change", data.ten_year_change_pct);
  document.getElementById("macro-2y").textContent = fmtPct(data.two_year_yield);
  renderMacroChange("macro-2y-change", data.two_year_change_pct);
  if (data.ten_year_yield != null && data.two_year_yield != null) {
    const spread = data.ten_year_yield - data.two_year_yield;
    const spreadEl = document.getElementById("macro-spread");
    spreadEl.textContent = fmtPct(spread);
    spreadEl.style.color = spread < 0 ? "var(--neg)" : "var(--text)"; // inverted curve is a classic recession signal
  }
  document.getElementById("macro-oil").textContent = fmtUsd(data.crude_oil_price);
  renderMacroChange("macro-oil-change", data.crude_oil_change_pct);
  document.getElementById("macro-btc").textContent = fmtUsd(data.bitcoin_price, 0);
  renderMacroChange("macro-btc-change", data.bitcoin_change_pct);
  document.getElementById("macro-kospi").textContent = fmtNum(data.kospi_index, 2);
  renderMacroChange("macro-kospi-change", data.kospi_change_pct);
  document.getElementById("macro-sp500").textContent = fmtNum(data.sp500_index, 2);
  renderMacroChange("macro-sp500-change", data.sp500_change_pct);
  document.getElementById("macro-nasdaq").textContent = fmtNum(data.nasdaq_index, 2);
  renderMacroChange("macro-nasdaq-change", data.nasdaq_change_pct);
  document.getElementById("macro-rsp").textContent = fmtUsd(data.rsp_price);
  renderMacroChange("macro-rsp-change", data.rsp_change_pct);

  const bannerEl = document.getElementById("macro-risk-banner");
  if (bannerEl) {
    bannerEl.classList.toggle("hidden", !data.macro_risk_alert);
  }
}

const SECTOR_SURGE_THRESHOLD_PCT = 1.0;

function renderSectorLeaderBanner(rows) {
  const el = document.getElementById("sector-leader-banner");
  if (!el) return;

  const candidates = rows.filter((r) => r.daily_change_pct != null && r.daily_change_pct > 0);
  if (!candidates.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }

  const leader = candidates.reduce((best, r) => (r.daily_change_pct > best.daily_change_pct ? r : best));
  const sectorName = leader.name.replace(/ Select Sector SPDR Fund$/, "");

  el.innerHTML = `🏆 <strong>Market Leader:</strong> ${sectorName} (${leader.ticker}) surged +${leader.daily_change_pct.toFixed(2)}%.`;
  el.classList.remove("hidden");
}

async function loadSectorFlows() {
  const res = await fetch("/api/sector-flows");
  const rows = await res.json();
  const tbody = document.getElementById("sector-table-body");

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No sector volume data yet — run scripts/compute_sector_flows.py</td></tr>';
    return;
  }

  document.getElementById("sector-asof").textContent = `as of ${rows[0].as_of}`;
  renderSectorLeaderBanner(rows);

  tbody.innerHTML = rows
    .map((r) => {
      const change = r.daily_change_pct;
      const changeCls = change == null ? "" : change >= 0 ? "positive" : "negative";
      const changeSign = change == null ? "" : change >= 0 ? "+" : "";
      const changeText = change == null ? "—" : `${changeSign}${change.toFixed(2)}%`;
      const surgeCls =
        change == null ? "" : change > SECTOR_SURGE_THRESHOLD_PCT ? "row-surge-up" : change < -SECTOR_SURGE_THRESHOLD_PCT ? "row-surge-down" : "";
      return `
      <tr class="clickable ${surgeCls}" onclick="window.location.href='/stock/${r.ticker}'">
        <td><strong>${r.ticker}</strong></td>
        <td>${r.name}</td>
        <td class="change-pct ${changeCls}">${changeText}</td>
        <td>${r.volume_ratio != null ? r.volume_ratio.toFixed(2) + "x" : "—"}</td>
        <td><span class="flow-tag ${r.flow_status || "Neutral"}">${r.flow_status || "Insufficient data"}</span></td>
      </tr>`;
    })
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
