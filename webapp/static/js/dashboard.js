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
    // as_of is the market date of the newest reading (FRED yields trail by a day);
    // the fetch time comes from loadFreshness().
    asOfEl.dataset.reading = data.as_of || "";
    paintMacroAsOf();
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

  sectorBarDate = rows[0].as_of;
  paintSectorAsOf();
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

async function loadStrongBuys(market = "us", gridId = "strong-buys-grid") {
  const grid = document.getElementById(gridId);
  if (!grid) return;

  const res = await fetch(`/api/top-scores?market=${market}`);
  const rows = await res.json();

  if (!rows.length) {
    grid.innerHTML = '<div class="empty-state">No scored tickers yet — run scripts/compute_investment_scores.py</div>';
    return;
  }

  grid.innerHTML = rows
    .map(
      (r, i) => `
      <a class="strong-buy-card" href="/stock/${encodeURIComponent(r.ticker)}">
        <div class="strong-buy-rank">#${i + 1}</div>
        <div class="strong-buy-ticker">${escapeHtml(r.ticker)}</div>
        <div class="strong-buy-name">${escapeHtml(r.name)}</div>
        <div class="strong-buy-score" title="Exact score ${r.score_exact}">${r.score.toFixed(1)}</div>
      </a>`
    )
    .join("");
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// ---- Sector Rotation Radar ----
const REGIME_CLASS = { inflow: "inflow", outflow: "outflow", balanced: "balanced" };

function ppText(value) {
  return value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function signCls(value) {
  return value == null ? "" : value >= 0 ? "positive" : "negative";
}

async function loadSectorRotation() {
  const list = document.getElementById("rotation-list");
  if (!list) return;
  const res = await fetch("/api/sector-rotation");
  const data = await res.json();

  if (!data.sectors || !data.sectors.length) {
    list.innerHTML = '<div class="empty-state">No rotation data yet — run scripts/compute_sector_flows.py</div>';
    return;
  }

  rotationBarDate = data.as_of;
  paintRotationAsOf();

  const summary = document.getElementById("rotation-summary");
  const chips = (label, cls, tickers) =>
    tickers.length ? `<span class="rotation-chip ${cls}"><strong>${label}</strong> ${tickers.map(escapeHtml).join(", ")}</span>` : "";
  summary.innerHTML =
    chips("Inflow / Leading", "inflow", data.leading) + chips("Outflow / Lagging", "outflow", data.lagging) ||
    '<span class="rotation-chip balanced">No sector is in a confirmed inflow or outflow today</span>';

  list.replaceChildren(
    ...data.sectors.map((s, i) => {
      const row = document.createElement("a");
      row.className = `rotation-row ${REGIME_CLASS[s.regime] || "balanced"}`;
      row.href = `/stock/${encodeURIComponent(s.ticker)}`;
      const name = s.name.replace(/ Select Sector SPDR Fund$/, "");
      const half = Math.min(Math.abs(s.rotation_score), 1) * 50; // % of the track, from the centre line
      const breadth = s.breadth_pct == null ? "—" : `${s.breadth_pct.toFixed(0)}%`;
      const movers = s.advancing == null ? "" : `${s.advancing} up / ${s.declining} down`;
      row.innerHTML = `
        <span class="rot-rank">${i + 1}</span>
        <span class="rot-name"><strong>${escapeHtml(s.ticker)}</strong> ${escapeHtml(name)}</span>
        <span class="rot-bar" title="Rotation signal ${s.rotation_score.toFixed(2)} (-1 outflow … +1 inflow)">
          <span class="rot-bar-fill ${s.rotation_score >= 0 ? "positive" : "negative"}"
                style="${s.rotation_score >= 0 ? "left:50%" : `right:50%`};width:${half}%"></span>
        </span>
        <span class="rot-metric rot-m2"><em>2d</em><b class="change-pct ${signCls(s.rel_2d_pct)}">${ppText(s.rel_2d_pct)}</b></span>
        <span class="rot-metric rot-m5"><em>5d</em><b class="change-pct ${signCls(s.rel_5d_pct)}">${ppText(s.rel_5d_pct)}</b></span>
        <span class="rot-metric rot-mb" title="${movers}"><em>Breadth</em><b>${breadth}</b></span>
        <span class="rot-regime ${REGIME_CLASS[s.regime] || "balanced"}">${escapeHtml(s.regime_label || "Balanced")}</span>`;
      return row;
    })
  );
}

async function loadMarketBrief() {
  const el = document.getElementById("market-brief");
  if (!el) return;
  try {
    const res = await fetch("/api/market-brief");
    const data = await res.json();
    if (!data.brief) return; // nothing generated yet: keep the card hidden
    document.getElementById("market-brief-text").textContent = data.brief;
    const asOf = document.getElementById("market-brief-asof");
    asOf.dataset.marketDate = data.as_of;
    Freshness.stamp(asOf, data.generated_at, { label: `market date ${data.as_of} · generated` });
    el.hidden = false;
  } catch (err) {
    console.warn("market brief unavailable", err);
  }
}

let freshnessData = null;
let sectorBarDate = "";
let rotationBarDate = "";

function paintMacroAsOf() {
  const el = document.getElementById("macro-asof");
  if (!el || !freshnessData) return;
  const f = freshnessData.sources.macro;
  Freshness.stamp(el, f.refreshed_at, { label: `latest reading ${el.dataset.reading || "—"} · fetched`, overdue: f.overdue });
}

function paintSectorAsOf() {
  const el = document.getElementById("sector-asof");
  if (!el || !freshnessData) return;
  const f = freshnessData.sources.sector_flows;
  Freshness.stamp(el, f.refreshed_at, { label: `bar ${sectorBarDate || "—"} · computed`, overdue: f.overdue });
}

function paintRotationAsOf() {
  const el = document.getElementById("rotation-asof");
  if (!el || !freshnessData) return;
  const f = freshnessData.sources.sector_flows;
  Freshness.stamp(el, f.refreshed_at, { label: `bar ${rotationBarDate || "—"} · computed`, overdue: f.overdue });
}

const SOURCE_LABELS = [
  ["eod_update", "Last full update (all steps succeeded)"],
  ["prices", "Prices (scheduled runs)"],
  ["news", "News headlines (scheduled runs)"],
  ["macro", "Macro indicators"],
  ["sector_flows", "Sector flows"],
  ["scores", "Investment Scores"],
  ["brief", "Daily Macro Brief"],
];

async function loadFreshness() {
  try {
    const res = await fetch("/api/freshness");
    const f = await res.json();
    Freshness.syncServerClock(f.server_now);
    freshnessData = f;

    const eod = f.sources.eod_update;
    const dot = document.getElementById("freshness-dot");
    dot.className = `freshness-dot ${eod.refreshed_at && !eod.overdue ? "ok" : "stale"}`;
    Freshness.stamp(document.getElementById("freshness-eod"), eod.refreshed_at, {
      label: "Last full update:",
      overdue: eod.overdue,
    });
    const next = document.getElementById("freshness-next");
    next.dataset.ts = "";
    next.textContent = `· Next scheduled: ${Freshness.fmtDateTime(f.schedule.next_scheduled_run)} (${f.schedule.description})`
      + (eod.overdue ? " · data is OVERDUE for the last scheduled run" : "")
      + (f.market_open ? " · US market is open: scheduled data is the previous close until the 23:00 run, use Refresh on a stock page for a live quote" : "");

    const list = document.getElementById("freshness-list");
    list.replaceChildren();
    for (const [key, label] of SOURCE_LABELS) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = label;
      const when = document.createElement("span");
      when.className = "freshness-when";
      Freshness.stamp(when, f.sources[key].refreshed_at, { overdue: f.sources[key].overdue });
      li.append(name, when);
      list.appendChild(li);
    }
    paintMacroAsOf();
    paintSectorAsOf();
    paintRotationAsOf();
    paintMoversAsOf();
    paintScoresAsOf();
  } catch (err) {
    document.getElementById("freshness-eod").textContent = "Data freshness unavailable";
    console.warn("freshness unavailable", err);
  }
}

function paintMoversAsOf() {
  const el = document.getElementById("movers-asof");
  if (!el || !freshnessData) return;
  const f = freshnessData.sources.prices;
  Freshness.stamp(el, f.refreshed_at, { label: "prices fetched", overdue: f.overdue });
}

function paintScoresAsOf() {
  const el = document.getElementById("scores-asof");
  if (!el || !freshnessData) return;
  const f = freshnessData.sources.scores;
  Freshness.stamp(el, f.refreshed_at, { label: "scores computed", overdue: f.overdue });
}

loadFreshness();
loadMarketBrief();
loadMacro();
loadSectorFlows();
loadMovers();
loadStrongBuys("us", "strong-buys-grid");
loadStrongBuys("tase", "strong-buys-grid-tase");
loadSectorRotation();
