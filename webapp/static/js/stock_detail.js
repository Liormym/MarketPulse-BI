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

const RANGE_DAYS = { "1M": 30, "6M": 182, "1Y": 365, "5Y": 365 * 5 };
const SERIES_KEYS = ["dates", "prices", "opens", "volumes", "sentiment", "sma20", "sma50", "sma150", "sma200", "gap_pct"];

function filterByRange(data, range) {
  if (range === "MAX" || !data.dates.length) return data;

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

function applyTimeframe(range) {
  currentRange = range;
  document.querySelectorAll(".timeframe-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.range === range);
  });
  if (!currentData) return;
  const sliced = filterByRange(currentData, range);
  renderChart(sliced);
  renderVolumeChart(sliced);
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

function scoreBand(score) {
  if (score >= 70) return { band: "good", tag: "Strong Buy Signal" };
  if (score >= 45) return { band: "warn", tag: "Neutral / Watch" };
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
  scoreValueEl.textContent = score;
  scoreTagEl.textContent = tag;
  scoreTagEl.title = "";
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

function sentimentMarkerDataset(data) {
  // Sentiment is sporadic (news doesn't happen every day), so instead of a
  // bar sub-axis where most days are just empty space, plot it as colored
  // dot markers sitting directly on the price line for the days it exists -
  // prominent regardless of how sparse the underlying data is.
  const points = data.prices.map((p, i) => (data.sentiment[i] != null ? p : null));
  const colors = data.sentiment.map((s) => (s == null ? "transparent" : s >= 0 ? "#2fd489" : "#f5566a"));
  const radii = data.sentiment.map((s) => (s == null ? 0 : 6));
  return {
    type: "line",
    label: "Sentiment",
    data: points,
    showLine: false,
    pointRadius: radii,
    pointHoverRadius: radii.map((r) => (r ? r + 2 : 0)),
    pointBackgroundColor: colors,
    pointBorderColor: "#0b0f17",
    pointBorderWidth: 1.5,
    yAxisID: "yPrice",
    order: 0,
  };
}

function renderChart(data) {
  const ctx = document.getElementById("price-sentiment-chart").getContext("2d");

  const config = {
    data: {
      labels: data.dates,
      datasets: [
        {
          type: "line",
          label: "Price (USD)",
          data: data.prices,
          borderColor: "#5b8cff",
          backgroundColor: "rgba(91, 140, 255, 0.08)",
          borderWidth: 2.5,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: "#5b8cff",
          tension: 0.3,
          fill: true,
          yAxisID: "yPrice",
          order: 1,
        },
        smaDataset("SMA 20", data.sma20, "rgba(245, 185, 66, 0.9)"),
        smaDataset("SMA 50", data.sma50, "rgba(143, 107, 255, 0.9)"),
        smaDataset("SMA 150", data.sma150, "rgba(47, 212, 137, 0.7)"),
        smaDataset("SMA 200", data.sma200, "rgba(245, 86, 106, 0.7)"),
        sentimentMarkerDataset(data),
      ],
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
          labels: { color: "#8b95ab", boxWidth: 10, font: { size: 11 } },
        },
        tooltip: {
          backgroundColor: "#161d2e",
          borderColor: "#232c40",
          borderWidth: 1,
          titleColor: "#e7ebf3",
          bodyColor: "#8b95ab",
          padding: 10,
          filter: (item) => !(item.dataset.label === "Sentiment" && data.sentiment[item.dataIndex] == null),
          callbacks: {
            label: (item) => {
              if (item.dataset.label === "Sentiment") {
                return `Sentiment: ${data.sentiment[item.dataIndex].toFixed(2)}`;
              }
              return `${item.dataset.label}: $${item.formattedValue}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#8b95ab", maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
        },
        yPrice: {
          position: "left",
          grid: { color: "#1c2437" },
          ticks: { color: "#8b95ab", callback: (v) => `$${v}` },
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
    if (open == null) return "rgba(139, 149, 171, 0.5)";
    return close >= open ? "rgba(47, 212, 137, 0.6)" : "rgba(245, 86, 106, 0.6)";
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
          backgroundColor: "#161d2e",
          borderColor: "#232c40",
          borderWidth: 1,
          titleColor: "#e7ebf3",
          bodyColor: "#8b95ab",
          padding: 10,
          callbacks: { label: (item) => `Volume: ${Number(item.raw).toLocaleString()}` },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#8b95ab", maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
        },
        y: {
          position: "left",
          grid: { color: "#1c2437" },
          ticks: { color: "#8b95ab", callback: (v) => formatVolume(v), maxTicksLimit: 3 },
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
  document.getElementById("metric-avgvol").textContent =
    data.avg_volume_20d != null ? Math.round(data.avg_volume_20d).toLocaleString() : "—";
  const latestGap = lastNonNull(data.gap_pct);
  document.getElementById("metric-gap").textContent = latestGap != null ? `${latestGap.toFixed(2)}%` : "—";
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
  seller_exhaustion: { label: "💪 Seller Exhaustion", cls: "bullish" },
  bearish_conviction: { label: "🐻 Bearish Conviction", cls: "bearish" },
  high_volatility: { label: "⚡ High Volatility", cls: "warn" },
  insider_selling: { label: "⚠ Insider Selling", cls: "bearish" },
};

function renderScoreRationale(scoreDetail) {
  const badgesEl = document.getElementById("score-badges");
  const subscoresEl = document.getElementById("score-subscores");
  const trailEl = document.getElementById("audit-trail");

  if (!scoreDetail || scoreDetail.audit_trail === undefined) {
    badgesEl.innerHTML = "";
    subscoresEl.innerHTML = "";
    trailEl.innerHTML = '<li class="empty-state" style="background:none;border:none;">No score rationale available</li>';
    return;
  }

  const flags = scoreDetail.flags || {};
  const badgeKeys = Object.keys(flags).filter((k) => flags[k]);
  badgesEl.innerHTML = badgeKeys.length
    ? badgeKeys
        .map((k) => {
          const b = FLAG_BADGES[k] || { label: k, cls: "warn" };
          return `<span class="score-badge ${b.cls}">${b.label}</span>`;
        })
        .join("")
    : "";

  const subscores = [
    ["Sentiment", scoreDetail.sentiment_points],
    ["Technical", scoreDetail.technical_points],
    ["Positioning & Macro", scoreDetail.positioning_points],
    ["Risk Modifiers", scoreDetail.risk_modifier_points],
  ];
  subscoresEl.innerHTML = subscores
    .map(([label, value]) => {
      if (value === null || value === undefined) return "";
      const cls = value > 0 ? "positive" : value < 0 ? "negative" : "";
      const sign = value > 0 ? "+" : "";
      return `
      <div class="subscore-tile">
        <div class="subscore-label">${label}</div>
        <div class="subscore-value ${cls}">${sign}${value}</div>
      </div>`;
    })
    .join("");

  const trail = scoreDetail.audit_trail || [];
  trailEl.innerHTML = trail.length
    ? trail.map((line) => `<li class="${line.trim().startsWith("-") ? "negative" : "positive"}">${line}</li>`).join("")
    : `<li class="empty-state" style="background:none;border:none;">${scoreDetail.reason || "No rules applied"}</li>`;
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

async function loadTicker(ticker) {
  latestRequestedTicker = ticker;
  const res = await fetch(`/api/stock/${encodeURIComponent(ticker)}`);
  if (ticker !== latestRequestedTicker) return; // superseded by a newer request

  if (!res.ok) {
    companyNameEl.textContent = "Ticker not found";
    companyTickerEl.textContent = ticker.toUpperCase();
    return;
  }
  const data = await res.json();
  if (ticker !== latestRequestedTicker) return; // superseded while awaiting JSON body

  companyNameEl.textContent = data.name;
  companyTickerEl.textContent = data.ticker;
  renderScore(data.score, data.score_detail);
  currentData = data;
  applyTimeframe(currentRange);
  renderTechnicalPanel(data);
  renderGovernancePanel(data.enrichment);
  renderMacroAlignmentPanel(data.sector_flow);
  renderScoreRationale(data.score_detail);
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
        `✓ Refreshed: ${result.price_rows} price row(s), ${result.articles_fetched} article(s) checked. ` +
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
