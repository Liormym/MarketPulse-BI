-- MarketPulse BI — initial star schema.
-- Mirrors the data dictionary in "מסמך אפיון מערכת" §6-7 exactly (table/column names,
-- keys, nullability). Applied by scripts/apply_migrations.py, tracked in schema_migrations.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(50) PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- DimAsset
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "DimAsset" (
    "AssetKey"      SERIAL PRIMARY KEY,
    "Ticker"        VARCHAR(10) NOT NULL UNIQUE,
    "CompanyName"   VARCHAR(100) NOT NULL,
    "Sector"        VARCHAR(50)
);

-- ---------------------------------------------------------------------------
-- DimDate
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "DimDate" (
    "DateKey"       INTEGER PRIMARY KEY,        -- YYYYMMDD
    "Date"          DATE NOT NULL,
    "IsTradingDay"  BOOLEAN NOT NULL
);

-- ---------------------------------------------------------------------------
-- FactDailyPrice  (grain: one row per asset per trading day)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "FactDailyPrice" (
    "AssetKey"  INTEGER NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "DateKey"   INTEGER NOT NULL REFERENCES "DimDate"("DateKey"),
    "Close"     DOUBLE PRECISION NOT NULL,
    "Volume"    BIGINT NOT NULL,
    PRIMARY KEY ("AssetKey", "DateKey")          -- idempotency key per spec
);

-- ---------------------------------------------------------------------------
-- NewsArticles  (grain: one row per specific article — raw/processed traceability)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "NewsArticles" (
    "ArticleID"           VARCHAR(64) PRIMARY KEY,   -- sha256 hash of the article URL
    "AssetKey"            INTEGER NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "PublishedAt"         TIMESTAMPTZ NOT NULL,
    "Title"               TEXT NOT NULL,
    "SentimentCategory"   VARCHAR(15),               -- Positive / Negative / Neutral
    "SentimentScore"      DOUBLE PRECISION
);

-- ---------------------------------------------------------------------------
-- FactSentiment  (grain: one row per asset per day — aggregated)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "FactSentiment" (
    "AssetKey"          INTEGER NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "DateKey"           INTEGER NOT NULL REFERENCES "DimDate"("DateKey"),
    "ArticleCount"      INTEGER NOT NULL,
    "PositiveCount"     INTEGER NOT NULL,
    "NegativeCount"     INTEGER NOT NULL,
    "AvgSentimentScore" DOUBLE PRECISION NOT NULL,
    PRIMARY KEY ("AssetKey", "DateKey")               -- idempotency key per spec
);

-- ---------------------------------------------------------------------------
-- PipelineExecutionLog  (operational logging)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "PipelineExecutionLog" (
    "RunID"             UUID NOT NULL,
    "PipelineStage"     VARCHAR(50) NOT NULL,   -- Extract, Transform, Load, AI, ...
    "StartTime"         TIMESTAMPTZ NOT NULL,
    "EndTime"           TIMESTAMPTZ,
    "Status"            VARCHAR(20) NOT NULL,   -- Success / Failed / Running
    "RecordsProcessed"  INTEGER,
    "ErrorMessage"      TEXT,
    PRIMARY KEY ("RunID", "PipelineStage")
);

-- ---------------------------------------------------------------------------
-- DataQualityResults  (data quality monitoring)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "DataQualityResults" (
    "CheckID"       UUID PRIMARY KEY,
    "RunID"         UUID NOT NULL,
    "CheckType"     VARCHAR(50) NOT NULL,       -- MissingTicker, NullPrice, ...
    "Passed"        BOOLEAN NOT NULL,
    "FailedRecords" INTEGER,
    "Details"       TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_asset_date ON "NewsArticles" ("AssetKey", "PublishedAt");
CREATE INDEX IF NOT EXISTS idx_dq_runid ON "DataQualityResults" ("RunID");
