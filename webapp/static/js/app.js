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
          order: 2,
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
          callbacks: {
            label: (item) => {
              if (item.dataset.label === "Price (USD)") {
                return `Price: $${item.formattedValue}`;
              }
              const raw = item.raw;
              return raw === null || raw === undefined
                ? "Sentiment: no articles that day"
                : `Sentiment: ${item.formattedValue}`;
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
