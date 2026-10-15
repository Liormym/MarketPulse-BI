-- MarketPulse BI - expanded universe (S&P 1500 + TASE), native currency per asset, FX rates.
--
-- DimAsset gets:
--   Currency      ISO code the price is stored in. USD for US names, ILS for TASE.
--   PriceDivisor  1 or 100. Yahoo quotes most TASE shares in AGOROT (currency "ILA",
--                 1/100 of a shekel); the pipeline divides OHLC by this at load time, so
--                 every stored price is in whole shekels and Currency is "ILS".
--   Exchange      listing venue when known (e.g. TLV).
--   Universe      sp500 | sp400 | sp600 | tase | personal | etf | index | crypto.
--                 Provisional values for the rows that exist today; scripts/build_universe.py
--                 refines them from the real index membership.
-- Ticker is widened to 16 chars (TASE symbols carry a ".TA" suffix). Postgres will not
-- alter a column that views depend on, so the six BI views are dropped and recreated
-- below, unchanged (their canonical definitions stay in db/views/powerbi_views.sql).
--
-- FxRate: one row per day per currency pair. USD -> ILS comes from yfinance "ILS=X"
-- (Rate = ILS per 1 USD). Written by scripts/fetch_macro_indicators.py.
DROP VIEW IF EXISTS "vw_SectorSentiment", "vw_PriceAndSentiment", "vw_DailySentiment",
                    "vw_DailyPrices", "vw_PipelineHealth", "vw_DataQualitySummary";

ALTER TABLE "DimAsset" ALTER COLUMN "Ticker" TYPE VARCHAR(16);
ALTER TABLE "DimAsset" ADD COLUMN IF NOT EXISTS "Currency"     CHAR(3)     NOT NULL DEFAULT 'USD';
ALTER TABLE "DimAsset" ADD COLUMN IF NOT EXISTS "PriceDivisor" SMALLINT    NOT NULL DEFAULT 1
    CHECK ("PriceDivisor" IN (1, 100));
ALTER TABLE "DimAsset" ADD COLUMN IF NOT EXISTS "Exchange"     VARCHAR(12);
ALTER TABLE "DimAsset" ADD COLUMN IF NOT EXISTS "Universe"     VARCHAR(12) NOT NULL DEFAULT 'sp500'
    CHECK ("Universe" IN ('sp500', 'sp400', 'sp600', 'tase', 'personal', 'etf', 'index', 'crypto'));

UPDATE "DimAsset" SET "Universe" = 'etf'    WHERE "Sector" = 'ETF';
UPDATE "DimAsset" SET "Universe" = 'index'  WHERE "Sector" = 'Index';
UPDATE "DimAsset" SET "Universe" = 'crypto' WHERE "Sector" = 'Crypto';
UPDATE "DimAsset" SET "Universe" = 'tase', "Currency" = 'ILS', "Exchange" = 'TLV' WHERE "Ticker" LIKE '%.TA';
CREATE INDEX IF NOT EXISTS idx_dimasset_universe ON "DimAsset" ("Universe");

CREATE TABLE IF NOT EXISTS "FxRate" (
    "DateKey"    INTEGER          NOT NULL REFERENCES "DimDate"("DateKey"),
    "BaseCcy"    CHAR(3)          NOT NULL,
    "QuoteCcy"   CHAR(3)          NOT NULL,
    "Rate"       DOUBLE PRECISION NOT NULL CHECK ("Rate" > 0),
    "FetchedAt"  TIMESTAMPTZ      NOT NULL,
    PRIMARY KEY ("DateKey", "BaseCcy", "QuoteCcy")
);

-- ---- BI views, recreated unchanged (copy of db/views/powerbi_views.sql) ----
-- Curated views for Power BI to connect to (Import or DirectQuery), so DAX
-- measures don't need to re-derive basic joins/labels every time.
-- Apply after 001_init_schema.sql: psql -d marketpulse -f db/views/powerbi_views.sql

CREATE OR REPLACE VIEW "vw_DailyPrices" AS
SELECT
    a."Ticker",
    a."CompanyName",
    a."Sector",
    d."Date",
    d."IsTradingDay",
    f."Close",
    f."Volume",
    f."Close" - LAG(f."Close") OVER (PARTITION BY f."AssetKey" ORDER BY d."Date") AS "DailyChange",
    ROUND(
        (
            (f."Close" - LAG(f."Close") OVER (PARTITION BY f."AssetKey" ORDER BY d."Date"))
            / NULLIF(LAG(f."Close") OVER (PARTITION BY f."AssetKey" ORDER BY d."Date"), 0) * 100
        )::numeric,
        2
    ) AS "DailyReturnPct"
FROM "FactDailyPrice" f
JOIN "DimAsset" a ON a."AssetKey" = f."AssetKey"
JOIN "DimDate" d ON d."DateKey" = f."DateKey";

CREATE OR REPLACE VIEW "vw_DailySentiment" AS
SELECT
    a."Ticker",
    a."CompanyName",
    a."Sector",
    d."Date",
    s."ArticleCount",
    s."PositiveCount",
    s."NegativeCount",
    s."ArticleCount" - s."PositiveCount" - s."NegativeCount" AS "NeutralCount",
    s."AvgSentimentScore"
FROM "FactSentiment" s
JOIN "DimAsset" a ON a."AssetKey" = s."AssetKey"
JOIN "DimDate" d ON d."DateKey" = s."DateKey";

-- Price + sentiment combined, for the "does sentiment lead price" style visuals.
CREATE OR REPLACE VIEW "vw_PriceAndSentiment" AS
SELECT
    p."Ticker", p."CompanyName", p."Sector", p."Date",
    p."Close", p."Volume", p."DailyChange", p."DailyReturnPct",
    s."ArticleCount", s."PositiveCount", s."NegativeCount", s."AvgSentimentScore"
FROM "vw_DailyPrices" p
LEFT JOIN "vw_DailySentiment" s ON s."Ticker" = p."Ticker" AND s."Date" = p."Date";

-- Sector-level rollup, for the initiation doc's "macro insight: capital
-- flowing into a whole sector" use case.
CREATE OR REPLACE VIEW "vw_SectorSentiment" AS
SELECT
    a."Sector",
    d."Date",
    SUM(s."ArticleCount") AS "TotalArticles",
    AVG(s."AvgSentimentScore") AS "AvgSectorSentiment"
FROM "FactSentiment" s
JOIN "DimAsset" a ON a."AssetKey" = s."AssetKey"
JOIN "DimDate" d ON d."DateKey" = s."DateKey"
WHERE a."Sector" IS NOT NULL
GROUP BY a."Sector", d."Date";

-- Operational monitoring: latest run per stage + today's DQ pass rate.
CREATE OR REPLACE VIEW "vw_PipelineHealth" AS
SELECT
    "RunID", "PipelineStage", "StartTime", "EndTime", "Status",
    "RecordsProcessed", "ErrorMessage",
    EXTRACT(EPOCH FROM ("EndTime" - "StartTime")) AS "DurationSeconds"
FROM "PipelineExecutionLog"
ORDER BY "StartTime" DESC;

CREATE OR REPLACE VIEW "vw_DataQualitySummary" AS
SELECT
    "RunID",
    COUNT(*) AS "TotalChecks",
    SUM(CASE WHEN "Passed" THEN 1 ELSE 0 END) AS "PassedChecks",
    SUM(CASE WHEN NOT "Passed" THEN 1 ELSE 0 END) AS "FailedChecks",
    SUM(COALESCE("FailedRecords", 0)) AS "TotalFailedRecords"
FROM "DataQualityResults"
GROUP BY "RunID";
