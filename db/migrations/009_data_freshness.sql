-- MarketPulse BI - when was each piece of data actually refreshed?
--
-- Until now the app only knew DATES (a price bar's trading day), never the
-- moment a value was fetched or computed, so the UI could not say how stale
-- anything was. These two tables record real UTC timestamps, written by the
-- jobs that produce the data:
--
-- DataFreshness       one row per GLOBAL source (macro, prices + news = the nightly
--                     batch only, sector_flows, scores,
--                     brief, eod_update = the last fully successful EOD run).
-- StockDataFreshness  one row per asset: when its prices / news were last
--                     fetched from the upstream feeds (only counted if the
--                     fetch actually succeeded), when its technicals were last
--                     computed, and whether the last refresh was the nightly
--                     batch ('eod') or a user's Refresh click ('manual').
CREATE TABLE IF NOT EXISTS "DataFreshness" (
    "Source"       VARCHAR(40)  PRIMARY KEY,
    "RefreshedAt"  TIMESTAMPTZ  NOT NULL
);

CREATE TABLE IF NOT EXISTS "StockDataFreshness" (
    "AssetKey"              INTEGER      PRIMARY KEY REFERENCES "DimAsset"("AssetKey"),
    "PricesFetchedAt"       TIMESTAMPTZ,
    "NewsFetchedAt"         TIMESTAMPTZ,
    "TechnicalsComputedAt"  TIMESTAMPTZ,
    "LastRefreshKind"       VARCHAR(10)
);

-- One-off seed from records that already exist, so the UI is not blank until the
-- next run. Only real timestamps are used; anything unknown stays NULL.
INSERT INTO "DataFreshness" ("Source", "RefreshedAt")
SELECT 'scores', MAX("ComputedAt") FROM "FactInvestmentScore" HAVING MAX("ComputedAt") IS NOT NULL
ON CONFLICT ("Source") DO NOTHING;

INSERT INTO "DataFreshness" ("Source", "RefreshedAt")
SELECT 'brief', MAX("GeneratedAt") FROM "DailyMarketBrief" HAVING MAX("GeneratedAt") IS NOT NULL
ON CONFLICT ("Source") DO NOTHING;

INSERT INTO "DataFreshness" ("Source", "RefreshedAt")
SELECT s.src, l."EndTime"
FROM (VALUES ('prices'), ('news')) AS s(src)
CROSS JOIN (
    SELECT "EndTime" FROM "PipelineExecutionLog"
    WHERE "PipelineStage" = 'Load' AND "Status" = 'Success'
    ORDER BY "EndTime" DESC LIMIT 1
) l
ON CONFLICT ("Source") DO NOTHING;

INSERT INTO "StockDataFreshness" ("AssetKey", "PricesFetchedAt", "NewsFetchedAt", "LastRefreshKind")
SELECT a."AssetKey", l."EndTime", l."EndTime", 'eod'
FROM "DimAsset" a
CROSS JOIN (
    SELECT "EndTime" FROM "PipelineExecutionLog"
    WHERE "PipelineStage" = 'Load' AND "Status" = 'Success'
    ORDER BY "EndTime" DESC LIMIT 1
) l
ON CONFLICT ("AssetKey") DO NOTHING;
