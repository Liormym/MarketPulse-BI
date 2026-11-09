// Run with: node --test tests/js/   (also wrapped by tests/test_portfolio_math.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const M = require("../../webapp/static/js/portfolio.js");

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} !~ ${b}`);
const RATE = 3.1556; // ILS per USD

const aapl = { ticker: "AAPL", quantity: 10, avg_price: 150 };
const aaplQ = { currency: "USD", price: 353.72, previous_close: 331.9 };
const bezq = { ticker: "BEZQ.TA", quantity: 500, avg_price: 6.2 };
const bezqQ = { currency: "ILS", price: 6.754, previous_close: 6.805 };

test("convert: same currency is identity, USD<->ILS use the rate, unknown pairs and missing rate are null", () => {
  assert.equal(M.convert(100, "USD", "USD", null), 100);
  near(M.convert(100, "USD", "ILS", RATE), 315.56);
  near(M.convert(315.56, "ILS", "USD", RATE), 100);
  assert.equal(M.convert(100, "USD", "ILS", null), null);
  assert.equal(M.convert(100, "EUR", "ILS", RATE), null);
  assert.equal(M.convert(null, "USD", "ILS", RATE), null);
});

test("a USD holding shown in USD: percent and amounts line up", () => {
  const m = M.positionMetrics(aapl, aaplQ, "USD", RATE);
  near(m.value, 3537.2);
  near(m.dailyAmt, 10 * (353.72 - 331.9));
  near(m.dailyPct, (353.72 / 331.9 - 1) * 100);
  near(m.retAmt, 10 * (353.72 - 150));
  near(m.retPct, (353.72 / 150 - 1) * 100);
});

test("converting to ILS scales the amounts by today's rate and leaves the percentages alone", () => {
  const usd = M.positionMetrics(aapl, aaplQ, "USD", RATE);
  const ils = M.positionMetrics(aapl, aaplQ, "ILS", RATE);
  near(ils.value, usd.value * RATE);
  near(ils.dailyAmt, usd.dailyAmt * RATE);
  near(ils.retAmt, usd.retAmt * RATE);
  near(ils.dailyPct, usd.dailyPct);
  near(ils.retPct, usd.retPct);
});

test("an ILS (TASE) holding is converted to USD at today's rate, cost basis included", () => {
  const m = M.positionMetrics(bezq, bezqQ, "USD", RATE);
  near(m.value, (500 * 6.754) / RATE);
  near(m.cost, (500 * 6.2) / RATE);
  near(m.retAmt, (500 * (6.754 - 6.2)) / RATE);
  const native = M.positionMetrics(bezq, bezqQ, "ILS", RATE);
  near(native.value, 500 * 6.754);
});

test("missing pieces stay null instead of becoming zero", () => {
  const noPrev = M.positionMetrics(aapl, { currency: "USD", price: 353.72, previous_close: null }, "USD", RATE);
  assert.equal(noPrev.dailyAmt, null);
  assert.equal(noPrev.dailyPct, null);
  assert.ok(noPrev.value > 0);
  const noQuote = M.positionMetrics(aapl, undefined, "USD", RATE);
  assert.equal(noQuote.value, null);
  const noRate = M.positionMetrics(bezq, bezqQ, "USD", null);
  assert.equal(noRate.value, null);
});

test("totals: percentages are weighted by position size, not averaged", () => {
  const rows = [M.positionMetrics(aapl, aaplQ, "USD", RATE), M.positionMetrics(bezq, bezqQ, "USD", RATE)];
  const t = M.portfolioTotals(rows);
  near(t.value, rows[0].value + rows[1].value);
  near(t.dailyAmt, rows[0].dailyAmt + rows[1].dailyAmt);
  near(t.dailyPct, (t.dailyAmt / (rows[0].prevValue + rows[1].prevValue)) * 100);
  near(t.retPct, (t.retAmt / (rows[0].cost + rows[1].cost)) * 100);
});

test("a position that cannot be converted drops out of the totals (and only those)", () => {
  const rows = [M.positionMetrics(aapl, aaplQ, "USD", null), M.positionMetrics(bezq, bezqQ, "USD", null)];
  const t = M.portfolioTotals(rows);
  near(t.value, 3537.2);
  assert.equal(t.counted, 1);
  assert.equal(M.portfolioTotals([]).value, null);
});

test("formatting: signs, symbols and grouping", () => {
  assert.equal(M.money(10868.1399, "USD"), "$10,868.14");
  assert.equal(M.money(-12.5, "ILS"), "−₪12.50");
  assert.equal(M.signedMoney(253.14, "USD"), "+$253.14");
  assert.equal(M.signedMoney(-84.4, "ILS"), "−₪84.40");
  assert.equal(M.signedMoney(0, "USD"), "$0.00");
  assert.equal(M.signedPct(2.384), "+2.38%");
  assert.equal(M.signedPct(-7.857), "−7.86%");
  assert.equal(M.signedPct(null), "—");
  assert.equal(M.price(353.72), "353.72");
  assert.equal(M.price(6.754), "6.754");
  assert.equal(M.quantity(1234.5), "1,234.5");
});

test("sanitizeHoldings keeps valid rows, uppercases and de-duplicates tickers, drops junk", () => {
  const clean = M.sanitizeHoldings([
    { ticker: " aapl ", avg_price: "150", quantity: 10 },
    { ticker: "AAPL", avg_price: 1, quantity: 1 },
    { ticker: "MSFT", avg_price: 0, quantity: 2 },
    { ticker: "BAD", avg_price: 10, quantity: 0 },
    { ticker: "NEG", avg_price: -1, quantity: 1 },
    { ticker: 5, avg_price: 1, quantity: 1 },
    null,
    { ticker: "NAN", avg_price: "x", quantity: 1 },
  ]);
  assert.deepEqual(clean, [
    { ticker: "AAPL", avg_price: 150, quantity: 10 },
    { ticker: "MSFT", avg_price: 0, quantity: 2 },
  ]);
  assert.deepEqual(M.sanitizeHoldings("nope"), []);
  assert.deepEqual(M.sanitizeHoldings(null), []);
});

test("score bands follow the 70 / 45 thresholds", () => {
  assert.equal(M.scoreBand(92.4), "strong");
  assert.equal(M.scoreBand(70), "strong");
  assert.equal(M.scoreBand(69.9), "mixed");
  assert.equal(M.scoreBand(44.9), "weak");
  assert.equal(M.scoreBand(null), "none");
});
