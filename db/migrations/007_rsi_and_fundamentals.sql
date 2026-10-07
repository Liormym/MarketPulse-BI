-- MarketPulse BI - RSI(14) and fundamentals for the stock deep-dive page.
--
-- RSI14 is a per-day technical, computed in marketpulse.technicals from
-- FactDailyPrice alongside the SMAs/ATR, so it lives next to them. It is
-- informational only and never feeds the Investment Score.
--
-- Trailing P/E, market cap and beta are point-in-time snapshots that yfinance
-- returns in the same Ticker.info call already used for short interest, so
-- they live in StockEnrichmentCache with it (same 24h TTL), not in a daily
-- fact table. FundamentalsFetchedAt distinguishes "fetched, and the source
-- genuinely has no P/E (loss-making company, ETF, crypto)" from "row written
-- before these columns existed" - without it, a legitimate NULL would either
-- be refetched on every page view or never be fetched for pre-existing rows.
ALTER TABLE "FactStockTechnicals"   ADD COLUMN IF NOT EXISTS "RSI14" DOUBLE PRECISION;

ALTER TABLE "StockEnrichmentCache"  ADD COLUMN IF NOT EXISTS "TrailingPE" DOUBLE PRECISION;
ALTER TABLE "StockEnrichmentCache"  ADD COLUMN IF NOT EXISTS "MarketCap" BIGINT;
ALTER TABLE "StockEnrichmentCache"  ADD COLUMN IF NOT EXISTS "Beta" DOUBLE PRECISION;
ALTER TABLE "StockEnrichmentCache"  ADD COLUMN IF NOT EXISTS "FundamentalsFetchedAt" TIMESTAMPTZ;
