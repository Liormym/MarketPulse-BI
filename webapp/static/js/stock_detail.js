const scoreValueEl = document.getElementById("score-value");
const scoreTagEl = document.getElementById("score-tag");
const scoreCardEl = document.getElementById("score-card");
const companyNameEl = document.getElementById("company-name");
const companyTickerEl = document.getElementById("company-ticker");
const searchEl = document.getElementById("ticker-search");

let chart = null;

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

function renderChart(data) {
  const ctx = document.getElementById("price-sentiment-chart").getContext("2d");

  const sentimentColors = data.sentiment.map((s) => {
    if (s === null || s === undefined) return "rgba(139, 149, 171, 0.15)";
    return s >= 0 ? "rgba(47, 212, 137, 0.45)" : "rgba(245, 86, 106, 0.45)";
  });

  const config = {
    data: {
      labels: data.dates,
      datasets: [
        {
          type: "bar",
          label: "Sentiment",
          data: data.sentiment,
          backgroundColor: sentimentColors,
          borderWidth: 0,
          yAxisID: "ySentiment",
          order: 3,
          barPercentage: 0.9,
          categoryPercentage: 1.0,
        },
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
          callbacks: {
            label: (item) => {
              if (item.dataset.label === "Sentiment") {
                const raw = item.raw;
                return raw === null || raw === undefined
                  ? "Sentiment: no articles that day"
                  : `Sentiment: ${item.formattedValue}`;
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
        ySentiment: {
          position: "right",
          min: -1,
          max: 1,
          grid: { display: false },
          ticks: { color: "#8b95ab", stepSize: 0.5 },
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
  const recentExecSale = txns.find((t) => t.is_executive_sale);
  alertEl.innerHTML = recentExecSale
    ? `<div class="alert-badge warn">⚠ Key officer sale: ${recentExecSale.insider_name} (${recentExecSale.position}), ${recentExecSale.transaction_date}</div>`
    : '<div class="alert-badge ok">No recent key-officer sales</div>';

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
  renderChart(data);
  renderTechnicalPanel(data);
  renderGovernancePanel(data.enrichment);
  renderMacroAlignmentPanel(data.sector_flow);
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

// Initial load
loadTicker(searchEl.value.trim().toUpperCase());
