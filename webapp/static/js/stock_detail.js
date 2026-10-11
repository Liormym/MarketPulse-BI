const scoreValueEl = document.getElementById("score-value");
const scoreTagEl = document.getElementById("score-tag");
const scoreCardEl = document.getElementById("score-card");
const companyNameEl = document.getElementById("company-name");
const companyTickerEl = document.getElementById("company-ticker");
const searchEl = document.getElementById("ticker-search");

let chart = null;
let volumeChart = null;
let currentData = null; // full, unfiltered dataset from the API
let currentRange = "1Y";

// Which chart series are shown. Only the price line is visible on first load;
// SMAs stay in the legend (struck through) and toggle on by click.
// Tracked here instead of relying on Chart.js's per-dataset state because
// renderChart() swaps in brand-new dataset objects on every timeframe/ticker
// change, which would otherwise reset the user's toggles.
const DEFAULT_SERIES_VISIBLE = {
  "Price (USD)": true,
  "SMA 20": false,
  "SMA 50": false,
  "SMA 150": false,
  "SMA 200": false,
};
const seriesVisible = { ...DEFAULT_SERIES_VISIBLE };

const RANGE_DAYS = { "1M": 30, "6M": 182, "1Y": 365, "5Y": 365 * 5 };
const SERIES_KEYS = ["dates", "prices", "opens", "volumes", "sma20", "sma50", "sma150", "sma200", "gap_pct"];
const SHORT_TERM_POINT_COUNT = { "5D": 5 };

function filterByRange(data, range) {
  if (range === "MAX" || !data.dates.length) return data;

  if (range in SHORT_TERM_POINT_COUNT) {
    // Daily data, so "5D" means the last 5 daily points, not a 5-calendar-day
    // cutoff (which could land on 3-4 trading days if a weekend intervenes).
    const n = SHORT_TERM_POINT_COUNT[range];
    const sliced = { ...data };
    for (const key of SERIES_KEYS) {
      sliced[key] = data[key].slice(-n);
    }
    return sliced;
  }

  const lastDate = new Date(data.dates[data.dates.length - 1]);
  let cutoff;
  if (range === "YTD") {
    cutoff = new Date(lastDate.getFullYear(), 0, 1);
  } else {
    cutoff = new Date(lastDate);
    cutoff.setDate(cutoff.getDate() - RANGE_DAYS[range]);
  }
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  let from = data.dates.findIndex((d) => d >= cutoffStr);
  if (from === -1) from = 0;

  const sliced = { ...data };
  for (const key of SERIES_KEYS) {
    sliced[key] = data[key].slice(from);
  }
  return sliced;
}

function computePeriodReturnPct(data) {
  const prices = (data.prices || []).filter((p) => p != null);
  if (prices.length < 2) return null;
  const startPrice = prices[0];
  const endPrice = prices[prices.length - 1];
  if (!startPrice) return null;
  return ((endPrice - startPrice) / startPrice) * 100;
}

function renderPeriodReturn(data) {
  const el = document.getElementById("period-return-badge");
  if (!el) return;
  const returnPct = computePeriodReturnPct(data);
  if (returnPct == null) {
    el.textContent = "";
    el.className = "period-return-badge";
    return;
  }
  const cls = returnPct >= 0 ? "positive" : "negative";
  const sign = returnPct >= 0 ? "+" : "";
  el.textContent = `Period Return: ${sign}${returnPct.toFixed(1)}%`;
  el.className = `period-return-badge ${cls}`;
}

function applyTimeframe(range) {
  currentRange = range;
  document.querySelectorAll(".timeframe-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.range === range);
  });
  if (!currentData) return;
  const sliced = filterByRange(currentData, range);
  renderChart(sliced);
  renderVolumeChart(sliced);
  renderPeriodReturn(sliced);
}

document.querySelectorAll(".timeframe-btn").forEach((btn) => {
  btn.addEventListener("click", () => applyTimeframe(btn.dataset.range));
});

function formatVolume(v) {
  if (v >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(v);
}

const SCORE_DISCLAIMER =
  "The score is a diagnostic snapshot of current technical and sector alignment (trend, momentum, sector flow, risk). It is not a forecast and not investment advice.";

function scoreBand(score) {
  if (score >= 70) return { band: "good", tag: "Strong Signal" };
  if (score >= 45) return { band: "warn", tag: "Neutral / Mixed" };
  return { band: "bad", tag: "Weak Signal" };
}

function renderScore(score, detail) {
  scoreCardEl.classList.remove("score-good", "score-warn", "score-bad");

  if (score === null || score === undefined) {
    scoreValueEl.textContent = "—";
    scoreTagEl.textContent = detail && detail.reason ? "Insufficient history" : "Unavailable";
    scoreTagEl.title = (detail && detail.reason) || "";
    return;
  }

  const { band, tag } = scoreBand(score);
  scoreValueEl.textContent = Number(score).toFixed(1);
  scoreTagEl.textContent = tag;
  scoreTagEl.title = SCORE_DISCLAIMER;
  scoreCardEl.classList.add(`score-${band}`);
}

function lastNonNull(arr) {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] !== null && arr[i] !== undefined) return arr[i];
  }
  return null;
}

function smaDataset(label, data, color) {
  return {
    type: "line",
    label,
    data,
    borderColor: color,
    borderWidth: 1.5,
    pointRadius: 0,
    tension: 0.25,
    fill: false,
    yAxisID: "yPrice",
    order: 1,
    spanGaps: true,
  };
}

function renderChart(data) {
  const ctx = document.getElementById("price-chart").getContext("2d");

  const config = {
    data: {
      labels: data.dates,
      datasets: [
        {
          type: "line",
          label: "Price (USD)",
          data: data.prices,
          borderColor: "#2f8cff",
          backgroundColor: "rgba(47, 140, 255, 0.08)",
          borderWidth: 2.5,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: "#2f8cff",
          tension: 0.3,
          fill: true,
          yAxisID: "yPrice",
          order: 1,
        },
        smaDataset("SMA 20", data.sma20, "rgba(255, 176, 32, 0.9)"),
        smaDataset("SMA 50", data.sma50, "rgba(123, 92, 255, 0.9)"),
        smaDataset("SMA 150", data.sma150, "rgba(22, 242, 163, 0.7)"),
        smaDataset("SMA 200", data.sma200, "rgba(255, 77, 106, 0.7)"),
      ].map((ds) => ({ ...ds, hidden: !seriesVisible[ds.label] })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          display: true,
          position: "top",
          align: "end",
          labels: { color: "#8892a3", boxWidth: 10, font: { size: 11, family: "'JetBrains Mono', monospace" } },
          onClick: (event, item, legend) => {
            Chart.defaults.plugins.legend.onClick(event, item, legend);
            seriesVisible[item.text] = legend.chart.isDatasetVisible(item.datasetIndex);
          },
        },
        tooltip: {
          backgroundColor: "#151b27",
          borderColor: "#1e2735",
          borderWidth: 1,
          titleColor: "#e8edf5",
          bodyColor: "#8892a3",
          bodyFont: { family: "'JetBrains Mono', monospace" },
          titleFont: { family: "'JetBrains Mono', monospace" },
          padding: 10,
          callbacks: {
            label: (item) => `${item.dataset.label}: $${item.formattedValue}`,
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#8892a3", font: { family: "'JetBrains Mono', monospace", size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
        },
        yPrice: {
          position: "left",
          grid: { color: "#171e2a" },
          ticks: { color: "#8892a3", font: { family: "'JetBrains Mono', monospace", size: 10 }, callback: (v) => `$${v}` },
        },
      },
    },
  };

  if (chart) {
    chart.data = config.data;
    chart.options = config.options;
    chart.update();
  } else {
    chart = new Chart(ctx, config);
  }
}

function renderVolumeChart(data) {
  const ctx = document.getElementById("volume-chart").getContext("2d");

  const colors = data.volumes.map((_, i) => {
    const open = data.opens[i];
    const close = data.prices[i];
    if (open == null) return "rgba(136, 146, 163, 0.5)";
    return close >= open ? "rgba(22, 242, 163, 0.6)" : "rgba(255, 77, 106, 0.6)";
  });

  const config = {
    type: "bar",
    data: {
      labels: data.dates,
      datasets: [
        {
          label: "Volume",
          data: data.volumes,
          backgroundColor: colors,
          borderWidth: 0,
          barPercentage: 0.9,
          categoryPercentage: 1.0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#151b27",
          borderColor: "#1e2735",
          borderWidth: 1,
          titleColor: "#e8edf5",
          bodyColor: "#8892a3",
          bodyFont: { family: "'JetBrains Mono', monospace" },
          titleFont: { family: "'JetBrains Mono', monospace" },
          padding: 10,
          callbacks: { label: (item) => `Volume: ${Number(item.raw).toLocaleString()}` },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#8892a3", font: { family: "'JetBrains Mono', monospace", size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
        },
        y: {
          position: "left",
          grid: { color: "#171e2a" },
          ticks: { color: "#8892a3", font: { family: "'JetBrains Mono', monospace", size: 10 }, callback: (v) => formatVolume(v), maxTicksLimit: 3 },
        },
      },
    },
  };

  if (volumeChart) {
    volumeChart.data = config.data;
    volumeChart.options = config.options;
    volumeChart.update();
  } else {
    volumeChart = new Chart(ctx, config);
  }
}

function renderTechnicalPanel(data) {
  document.getElementById("metric-sma20").textContent = fmt(lastNonNull(data.sma20));
  document.getElementById("metric-sma50").textContent = fmt(lastNonNull(data.sma50));
  document.getElementById("metric-sma150").textContent = fmt(lastNonNull(data.sma150));
  document.getElementById("metric-sma200").textContent = fmt(lastNonNull(data.sma200));
  document.getElementById("metric-atr").textContent = fmt(data.atr14);
  renderRsi(data);
  document.getElementById("metric-avgvol").textContent =
    data.avg_volume_20d != null ? Math.round(data.avg_volume_20d).toLocaleString() : "—";

  const gapEl = document.getElementById("metric-gap");
  const gap = data.latest_gap;
  if (gap) {
    const lo = Math.min(gap.prev_close, gap.open).toFixed(2);
    const hi = Math.max(gap.prev_close, gap.open).toFixed(2);
    const sign = gap.gap_pct >= 0 ? "+" : "";
    gapEl.textContent = `Gap ${gap.direction}: $${lo} - $${hi} (${sign}${gap.gap_pct.toFixed(2)}%)`;
    gapEl.className = `metric-value ${gap.direction === "Up" ? "positive" : "negative"}`;
  } else {
    gapEl.textContent = "—";
    gapEl.className = "metric-value";
  }
}

const RSI_LABELS = { oversold: "Oversold", overbought: "Overbought", neutral: "Neutral" };

function renderRsi(data) {
  const valueEl = document.getElementById("metric-rsi");
  const badgeEl = document.getElementById("rsi-badge");
  if (data.rsi14 == null) {
    valueEl.textContent = "—";
    valueEl.className = "";
    badgeEl.className = "rsi-badge";
    badgeEl.textContent = "";
    return;
  }
  valueEl.textContent = data.rsi14.toFixed(1);
  valueEl.className = `rsi-value ${data.rsi_state}`;
  badgeEl.className = `rsi-badge ${data.rsi_state}`;
  badgeEl.textContent = RSI_LABELS[data.rsi_state] || "";
}

function formatMarketCap(value) {
  if (value == null) return null;
  const units = [[1e12, "T"], [1e9, "B"], [1e6, "M"]];
  for (const [size, suffix] of units) {
    if (value >= size) return `$${(value / size).toFixed(value / size >= 100 ? 0 : 2)}${suffix}`;
  }
  return `$${Math.round(value).toLocaleString()}`;
}

function renderFundamentalsPanel(fundamentals) {
  const peEl = document.getElementById("metric-pe");
  const mcapEl = document.getElementById("metric-mcap");
  const betaEl = document.getElementById("metric-beta");
  const asOfEl = document.getElementById("fundamentals-asof");

  // "n/a" = fetched fine but the source has no value (loss-making company, ETF,
  // crypto); "—" = nothing fetched at all.
  const fetched = Boolean(fundamentals && fundamentals.as_of);
  const missing = fetched ? "n/a" : "—";
  const f = fundamentals || {};

  peEl.textContent = f.trailing_pe != null ? `${f.trailing_pe.toFixed(1)}x` : missing;
  peEl.title = f.trailing_pe == null && fetched ? "No positive trailing earnings (loss-making company, ETF or crypto)" : "";
  mcapEl.textContent = formatMarketCap(f.market_cap) || missing;
  betaEl.textContent = f.beta != null ? f.beta.toFixed(2) : missing;
  betaEl.title = f.beta == null && fetched ? "Beta not reported for this instrument" : "Sensitivity to the overall market: 1.0 moves with it, above 1 swings harder";
  if (fetched) Freshness.stamp(asOfEl, f.as_of, { label: "Yahoo Finance · updated" });
  else asOfEl.textContent = "";
}

function fmt(value) {
  return value === null || value === undefined ? "—" : `$${value.toFixed(2)}`;
}

function renderGovernancePanel(enrichment) {
  const shortEl = document.getElementById("metric-short");
  const alertEl = document.getElementById("insider-alert");
  const listEl = document.getElementById("insider-list");

  if (!enrichment) {
    shortEl.textContent = "—";
    alertEl.innerHTML = "";
    listEl.innerHTML = '<div class="empty-state">No enrichment data available</div>';
    return;
  }

  shortEl.textContent =
    enrichment.short_percent_of_float != null
      ? `${(enrichment.short_percent_of_float * 100).toFixed(2)}%`
      : "—";

  const txns = enrichment.insider_transactions || [];
  // has_recent_executive_sale is the authoritative (unlimited, date-filtered)
  // signal - the visible txns list below is capped, so a sale within the
  // scoring window can be true even if it doesn't show up in that list.
  if (enrichment.has_recent_executive_sale) {
    const detail = txns.find((t) => t.is_executive_sale);
    alertEl.innerHTML = detail
      ? `<div class="alert-badge warn">⚠ Key officer sale: ${detail.insider_name} (${detail.position}), ${detail.transaction_date}</div>`
      : '<div class="alert-badge warn">⚠ Key officer sale in the last 90 days</div>';
  } else {
    alertEl.innerHTML = '<div class="alert-badge ok">No recent key-officer sales</div>';
  }

  if (!txns.length) {
    listEl.innerHTML = '<div class="empty-state">No insider transactions on file</div>';
    return;
  }
  listEl.innerHTML = txns
    .slice(0, 5)
    .map(
      (t) => `
      <div class="metric-row">
        <span class="metric-label">${t.insider_name} (${t.position || "—"})</span>
        <span class="metric-value">${t.transaction_type}${t.shares ? " · " + t.shares.toLocaleString() + " sh" : ""}</span>
      </div>`
    )
    .join("");
}

function renderMacroAlignmentPanel(sectorFlow) {
  const el = document.getElementById("macro-alignment-panel");
  if (!sectorFlow) {
    el.innerHTML = '<div class="empty-state">No sector mapping for this asset</div>';
    return;
  }
  el.innerHTML = `
    <div class="metric-row"><span class="metric-label">Sector ETF</span><span class="metric-value">${sectorFlow.ticker}</span></div>
    <div class="metric-row"><span class="metric-label">Volume Ratio</span><span class="metric-value">${sectorFlow.volume_ratio != null ? sectorFlow.volume_ratio.toFixed(2) + "x" : "—"}</span></div>
    <span class="flow-tag ${sectorFlow.flow_status || "Neutral"}">${sectorFlow.flow_status || "Insufficient data"}</span>
  `;
}

const FLAG_BADGES = {
  short_squeeze_setup: { label: "🚀 Short Squeeze Setup", cls: "bullish" },
  oversold_recovery: { label: "↗ Oversold Recovery", cls: "bullish" },
  overbought_rsi: { label: "🔥 Overbought RSI", cls: "warn" },
  bearish_conviction: { label: "🐻 Bearish Conviction", cls: "bearish" },
  high_volatility: { label: "⚡ High Volatility", cls: "warn" },
  insider_selling: { label: "⚠ Insider Selling", cls: "bearish" },
};

function renderScoreRationale(scoreDetail) {
  const badgesEl = document.getElementById("score-badges");
  const subscoresEl = document.getElementById("score-subscores");
  const trailEl = document.getElementById("audit-trail");

  if (!scoreDetail || !scoreDetail.pillars) {
    badgesEl.replaceChildren();
    subscoresEl.replaceChildren();
    trailEl.replaceChildren();
    const li = document.createElement("li");
    li.className = "empty-state";
    li.style.cssText = "background:none;border:none;";
    li.textContent = (scoreDetail && scoreDetail.reason) || "No score rationale available";
    trailEl.appendChild(li);
    return;
  }

  const flags = scoreDetail.flags || {};
  badgesEl.replaceChildren(
    ...Object.keys(flags)
      .filter((k) => flags[k])
      .map((k) => {
        const b = FLAG_BADGES[k] || { label: k, cls: "warn" };
        const span = document.createElement("span");
        span.className = `score-badge ${b.cls}`;
        span.textContent = b.label;
        return span;
      })
  );

  // One tile per pillar: points earned out of the pillar's maximum.
  subscoresEl.replaceChildren(
    ...scoreDetail.pillars.map((p) => {
      const tile = document.createElement("div");
      tile.className = "subscore-tile";
      const label = document.createElement("div");
      label.className = "subscore-label";
      label.textContent = p.name;
      const value = document.createElement("div");
      const ratio = p.max ? p.points / p.max : 0;
      value.className = `subscore-value ${ratio >= 0.6 ? "positive" : ratio < 0.4 ? "negative" : ""}`;
      value.textContent = `${p.points}`;
      const max = document.createElement("span");
      max.className = "subscore-max";
      max.textContent = ` / ${p.max}`;
      value.appendChild(max);
      tile.append(label, value);
      return tile;
    })
  );

  // Every rule, grouped by pillar: "earned / possible" and why.
  trailEl.replaceChildren();
  for (const p of scoreDetail.pillars) {
    const head = document.createElement("li");
    head.className = "audit-pillar";
    head.textContent = `${p.name}: ${p.points} / ${p.max} pts`;
    trailEl.appendChild(head);
    for (const item of scoreDetail.items.filter((i) => i.pillar === p.key)) {
      const li = document.createElement("li");
      const ratio = item.max ? item.points / item.max : 0;
      li.className = ratio >= 0.6 ? "positive" : ratio < 0.4 ? "negative" : "";
      li.textContent = `${item.points.toFixed(1)} / ${item.max.toFixed(0)} pts: ${item.text}`;
      trailEl.appendChild(li);
    }
  }
  const total = document.createElement("li");
  total.className = "audit-total";
  total.textContent = `Total: ${Number(scoreDetail.score).toFixed(1)} / 100 → ${scoreDetail.signal}. ${SCORE_DISCLAIMER}`;
  trailEl.appendChild(total);
}

// Headlines come from third-party RSS feeds, so they are untrusted: built with
// DOM APIs (textContent, never innerHTML) and linked only for http(s) URLs.
// Plain news on purpose: no sentiment colour or score, and it is not a score input.
function renderRecentNews(items) {
  const el = document.getElementById("recent-news");
  el.replaceChildren();
  if (!items || !items.length) {
    const li = document.createElement("li");
    li.className = "empty-state";
    li.textContent = "No recent headlines that explicitly mention this company";
    el.appendChild(li);
    return;
  }
  for (const d of items) {
    const li = document.createElement("li");
    li.className = "driver";

    let safeUrl = null;
    let host = "";
    try {
      const u = new URL(d.url);
      if (u.protocol === "https:" || u.protocol === "http:") {
        safeUrl = u.href;
        host = u.hostname.replace(/^www\./, "");
      }
    } catch (e) {
      /* no/invalid URL: render the headline as plain text */
    }

    const title = document.createElement(safeUrl ? "a" : "span");
    title.className = "driver-title";
    title.textContent = d.title;
    if (safeUrl) {
      title.href = safeUrl;
      title.target = "_blank";
      title.rel = "noopener noreferrer nofollow";
    }

    const meta = document.createElement("span");
    meta.className = "driver-when";
    const date = new Date(d.published_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    const source = d.source || host;
    meta.textContent = source ? `${source} \u00b7 ${date}` : date;

    li.append(title, meta);
    el.appendChild(li);
  }
}

// Every timestamp below is a real instant recorded when the data was fetched or
// computed (see migration 009), shown in the viewer's timezone with its age.
function renderFreshness(data) {
  const f = data.freshness;
  if (!f) return;
  Freshness.syncServerClock(f.server_now);

  const barDate = f.latest_bar_date
    ? new Date(`${f.latest_bar_date}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
    : "—";

  // Hero line: the one-glance answer to "as of when is this price?"
  const hero = document.getElementById("hero-freshness");
  hero.replaceChildren();
  const bar = document.createElement("span");
  bar.textContent = `Price bar: ${barDate}${f.market_open && f.last_refresh_kind === "manual" ? " (intraday, may lag ~15 min)" : " close"} ·`;
  const fetched = document.createElement("span");
  Freshness.stamp(fetched, f.prices_fetched_at, { label: "fetched", overdue: f.prices_overdue });
  hero.append(bar, fetched);

  const rows = [
    ["Price bars", f.prices_fetched_at, `latest bar ${barDate}; fetched from Yahoo Finance`, f.prices_overdue],
    ["News feeds", f.news_fetched_at, "Yahoo Finance and Google News, filtered to headlines naming this company", f.prices_overdue],
    ["Technicals (SMA / ATR / RSI)", f.technicals_computed_at, "computed from the stored price bars", f.prices_overdue],
    [
      "Fundamentals (P/E, market cap, beta)",
      data.fundamentals && data.fundamentals.as_of,
      "Yahoo Finance snapshot, cached up to 24 h",
      false,
    ],
    ["Recent news", f.news_panel_computed_at, "latest headlines that name this company, re-ranked on every refresh", false],
    ["Investment Score", f.server_now, "calculated live from the price, RSI and sector data above (news is not an input)", false],
  ];
  const body = document.getElementById("freshness-rows");
  body.replaceChildren();
  for (const [label, iso, note, overdue] of rows) {
    const tr = document.createElement("tr");
    const a = document.createElement("td");
    a.textContent = label;
    const b = document.createElement("td");
    const when = document.createElement("div");
    Freshness.stamp(when, iso, { overdue });
    const n = document.createElement("div");
    n.className = "metric-footnote";
    n.textContent = note;
    b.append(when, n);
    tr.append(a, b);
    body.appendChild(tr);
  }

  const kind = f.last_refresh_kind === "manual" ? "refreshed on demand" : f.last_refresh_kind === "eod" ? "scheduled update" : "";
  const summary = document.getElementById("freshness-summary");
  Freshness.stamp(summary, f.prices_fetched_at, { label: kind ? `${kind}:` : "prices:", overdue: f.prices_overdue });
  document.getElementById("freshness-note").textContent = f.prices_overdue
    ? "A scheduled update has run since this stock was last fetched (or none is recorded). Use Refresh Data for a current quote."
    : "Data refreshes automatically daily at 09:00, 15:00 and 23:00 Israel time. Refresh Data fetches this stock from Yahoo Finance right now.";
}

function renderPatternHints(hints) {
  const el = document.getElementById("pattern-hints");
  if (!hints || !hints.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = hints
    .map(
      (h) => `
      <div class="pattern-hint-badge" title="${h.description.replace(/"/g, "&quot;")}">
        🔍 Technical Observation: ${h.name} — a hint only, not part of the score. Open a real
        charting tool (e.g. TradingView) for a deeper look.
      </div>`
    )
    .join("");
}

// Guards against out-of-order responses: if the user (or the initial
// default-ticker load) triggers a second request before the first one's
// response lands, the stale response must not overwrite the newer one.
let latestRequestedTicker = null;

function renderHeroPrice(data) {
  const priceEl = document.getElementById("hero-price");
  const changeEl = document.getElementById("hero-change");
  priceEl.textContent = data.latest_price != null ? `$${data.latest_price.toFixed(2)}` : "";
  if (data.daily_change_pct == null) {
    changeEl.textContent = "";
    changeEl.className = "hero-change";
    return;
  }
  const cls = data.daily_change_pct >= 0 ? "positive" : "negative";
  const sign = data.daily_change_pct >= 0 ? "+" : "";
  changeEl.textContent = `${sign}${data.daily_change_pct.toFixed(2)}%`;
  changeEl.className = `hero-change ${cls}`;
}

async function loadTicker(ticker) {
  latestRequestedTicker = ticker;
  const res = await fetch(`/api/stock/${encodeURIComponent(ticker)}`);
  if (ticker !== latestRequestedTicker) return; // superseded by a newer request

  if (!res.ok) {
    companyNameEl.textContent = "Ticker not found";
    companyTickerEl.textContent = ticker.toUpperCase();
    document.getElementById("hero-price").textContent = "";
    document.getElementById("hero-change").textContent = "";
    return;
  }
  const data = await res.json();
  if (ticker !== latestRequestedTicker) return; // superseded while awaiting JSON body

  companyNameEl.textContent = data.name;
  companyTickerEl.textContent = data.ticker;
  renderHeroPrice(data);
  renderScore(data.score, data.score_detail);
  currentData = data;
  applyTimeframe(currentRange);
  renderTechnicalPanel(data);
  renderFundamentalsPanel(data.fundamentals);
  renderGovernancePanel(data.enrichment);
  renderMacroAlignmentPanel(data.sector_flow);
  renderScoreRationale(data.score_detail);
  renderRecentNews(data.recent_news);
  renderFreshness(data);
  renderPatternHints(data.pattern_hints);
}

searchEl.addEventListener("change", () => {
  const ticker = searchEl.value.trim().toUpperCase();
  if (ticker) loadTicker(ticker);
});

searchEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const ticker = searchEl.value.trim().toUpperCase();
    if (ticker) loadTicker(ticker);
    searchEl.blur();
  }
});

const refreshBtn = document.getElementById("refresh-btn");
const refreshBtnLabel = document.getElementById("refresh-btn-label");
const refreshMessageEl = document.getElementById("refresh-message");

function showRefreshMessage(text, kind) {
  refreshMessageEl.textContent = text;
  refreshMessageEl.className = `refresh-message visible ${kind}`;
  setTimeout(() => {
    refreshMessageEl.classList.remove("visible");
  }, 6000);
}

refreshBtn.addEventListener("click", async () => {
  const ticker = latestRequestedTicker;
  if (!ticker) return;

  refreshBtn.disabled = true;
  refreshBtnLabel.textContent = "🔄 Refreshing…";

  try {
    const res = await fetch(`/api/stock/${encodeURIComponent(ticker)}/refresh`, { method: "POST" });
    const result = await res.json();

    if (res.status === 429) {
      showRefreshMessage(`⏳ ${result.message}`, "warn");
    } else if (!result.ok) {
      showRefreshMessage(`⚠ Refresh failed: ${result.error || "unknown error"}`, "error");
    } else {
      showRefreshMessage(
        `✓ Refreshed at ${Freshness.fmtDateTime(result.refreshed_at)}: ${result.price_rows} price row(s), ${result.articles_fetched} article(s) checked. ` +
          `${result.remaining_refreshes} refresh(es) left this hour.`,
        "ok"
      );
      await loadTicker(ticker);
    }
  } catch (err) {
    showRefreshMessage("⚠ Refresh failed: network error", "error");
  } finally {
    refreshBtn.disabled = false;
    refreshBtnLabel.textContent = "🔄 Refresh Data";
  }
});

// Initial load
loadTicker(searchEl.value.trim().toUpperCase());
