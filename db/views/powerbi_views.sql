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
