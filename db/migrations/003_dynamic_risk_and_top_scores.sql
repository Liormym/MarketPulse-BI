-- MarketPulse BI — relative (z-score) volatility inputs, a cached Top-5
-- Investment Score table for the dashboard widget, and two new macro
-- indicators (Bitcoin, KOSPI). Applied by scripts/apply_migrations.py.

-- Rolling 90-day mean/stdev of ATR14, computed alongside it in
-- technicals.compute_technicals() - lets scoring.py penalize a stock only
-- when ITS OWN volatility is a genuine outlier relative to its own history,
-- instead of a flat percent-of-price threshold that unfairly punishes
-- naturally volatile names. NULL until 90 ATR14 readings exist.
ALTER TABLE "FactStockTechnicals" ADD COLUMN IF NOT EXISTS "ATR90Avg" DOUBLE PRECISION;
ALTER TABLE "FactStockTechnicals" ADD COLUMN IF NOT EXISTS "ATR90Std" DOUBLE PRECISION;

-- ---------------------------------------------------------------------------
-- FactInvestmentScore  (grain: one row per asset - latest known score, not a
-- time series). Recomputing the full Investment Score for every watchlist
-- ticker on every dashboard load would be far too slow (each score needs
-- price/technicals/sentiment/sector-flow joins) - scripts/
-- compute_investment_scores.py refreshes this on a schedule instead, the
-- same pattern StockEnrichmentCache already uses for short interest.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "FactInvestmentScore" (
    "AssetKey"    INTEGER PRIMARY KEY REFERENCES "DimAsset"("AssetKey"),
    "Score"       DOUBLE PRECISION NOT NULL,
    "ComputedAt"  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_investment_score_value ON "FactInvestmentScore" ("Score" DESC);

-- Global liquidity (Bitcoin) and Asian manufacturing/tech flow (KOSPI),
-- fetched via yfinance in scripts/fetch_macro_indicators.py alongside oil.
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "BitcoinPrice" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "KospiIndex" DOUBLE PRECISION;
