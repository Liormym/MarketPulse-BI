-- MarketPulse BI - "AI Sentiment Drivers" and the Daily Macro Brief.
--
-- 1. NewsArticles.SourceUrl: until now only sha256(url) was kept (ArticleID),
--    so a headline could not be linked back to its source. Nullable: rows
--    ingested before this migration have no URL and are simply not eligible
--    to be shown as a driver until the pipeline re-sees them in its RSS window.
-- 2. StockSentimentDrivers: the top headlines per asset, ranked by FinBERT
--    impact (|score|, Positive/Negative only). A small ranked snapshot that
--    points at NewsArticles instead of copying title/url/score, so there is
--    one source of truth for article text. Rewritten per asset on every
--    pipeline / live-refresh run (delete + insert in one transaction).
-- 3. DailyMarketBrief: the rule-based 1-2 sentence market summary, one row
--    per market date (idempotent re-runs), with the facts it was built from
--    so every sentence can be audited later.
ALTER TABLE "NewsArticles" ADD COLUMN IF NOT EXISTS "SourceUrl" TEXT;

CREATE TABLE IF NOT EXISTS "StockSentimentDrivers" (
    "AssetKey"    INTEGER     NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "Rank"        SMALLINT    NOT NULL CHECK ("Rank" BETWEEN 1 AND 10),
    "ArticleID"   VARCHAR(64) NOT NULL REFERENCES "NewsArticles"("ArticleID"),
    "ComputedAt"  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY ("AssetKey", "Rank")
);

CREATE TABLE IF NOT EXISTS "DailyMarketBrief" (
    "DateKey"      INTEGER     PRIMARY KEY REFERENCES "DimDate"("DateKey"),
    "Brief"        TEXT        NOT NULL,
    "Facts"        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    "GeneratedAt"  TIMESTAMPTZ NOT NULL
);
