-- MarketPulse BI — macro/regime tracking, sector money-flow, and per-stock
-- technical/risk/governance enrichment for the two-screen dashboard.
-- Applied by scripts/apply_migrations.py, tracked in schema_migrations.

-- FactDailyPrice only ever captured Close/Volume; ATR-14 and gap % both need
-- Open/High/Low, which yfinance already returns but extract/prices.py wasn't
-- capturing. Nullable so existing rows don't need a forced backfill to stay
-- valid; scripts/backfill_historical_prices.py re-populates them going forward.
ALTER TABLE "FactDailyPrice" ADD COLUMN IF NOT EXISTS "Open" DOUBLE PRECISION;
ALTER TABLE "FactDailyPrice" ADD COLUMN IF NOT EXISTS "High" DOUBLE PRECISION;
ALTER TABLE "FactDailyPrice" ADD COLUMN IF NOT EXISTS "Low" DOUBLE PRECISION;

-- ---------------------------------------------------------------------------
-- MacroIndicators  (grain: one row per calendar day)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "MacroIndicators" (
    "DateKey"        INTEGER PRIMARY KEY REFERENCES "DimDate"("DateKey"),
    "TenYearYield"   DOUBLE PRECISION,   -- FRED DGS10, percent
    "TwoYearYield"   DOUBLE PRECISION,   -- FRED DGS2, percent
    "CrudeOilPrice"  DOUBLE PRECISION,   -- yfinance CL=F close, USD/barrel
    "MarketBreadth"  DOUBLE PRECISION,   -- placeholder (e.g. S5FI) - not fetched yet
    "AAIISentiment"  DOUBLE PRECISION    -- placeholder (AAII bull/bear ratio) - not fetched yet
);

-- ---------------------------------------------------------------------------
-- FactSectorVolume  (grain: one row per sector SPDR ETF per trading day)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "FactSectorVolume" (
    "AssetKey"      INTEGER NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "DateKey"       INTEGER NOT NULL REFERENCES "DimDate"("DateKey"),
    "Volume"        BIGINT NOT NULL,
    "AvgVolume20D"  DOUBLE PRECISION,
    "DollarVolume"  DOUBLE PRECISION,
    "VolumeRatio"   DOUBLE PRECISION,             -- Volume / AvgVolume20D
    "FlowStatus"    VARCHAR(15),                  -- Accumulation / Distribution / Neutral
    PRIMARY KEY ("AssetKey", "DateKey")
);

-- ---------------------------------------------------------------------------
-- FactStockTechnicals  (grain: one row per asset per trading day)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "FactStockTechnicals" (
    "AssetKey"      INTEGER NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "DateKey"       INTEGER NOT NULL REFERENCES "DimDate"("DateKey"),
    "SMA20"         DOUBLE PRECISION,
    "SMA50"         DOUBLE PRECISION,
    "SMA150"        DOUBLE PRECISION,
    "SMA200"        DOUBLE PRECISION,
    "ATR14"         DOUBLE PRECISION,
    "AvgVolume20D"  DOUBLE PRECISION,
    "GapPct"        DOUBLE PRECISION,              -- today's Open vs. prior Close, %
    PRIMARY KEY ("AssetKey", "DateKey")
);

-- ---------------------------------------------------------------------------
-- StockEnrichmentCache  (grain: one row per asset - latest known snapshot,
-- not a time series; short interest is only reported ~biweekly by NASDAQ)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "StockEnrichmentCache" (
    "AssetKey"             INTEGER PRIMARY KEY REFERENCES "DimAsset"("AssetKey"),
    "ShortPercentOfFloat"  DOUBLE PRECISION,
    "FetchedAt"            TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- InsiderTransactions  (grain: one row per filed SEC Form 4 transaction)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "InsiderTransactions" (
    "TransactionID"    VARCHAR(64) PRIMARY KEY,   -- sha256 of ticker+insider+date+shares+value
    "AssetKey"         INTEGER NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "InsiderName"      VARCHAR(200),
    "Position"         VARCHAR(200),
    "TransactionType"  VARCHAR(50),
    "Shares"           BIGINT,
    "Value"            DOUBLE PRECISION,
    "TransactionDate"  DATE,
    "IsExecutiveSale"  BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_sector_volume_date ON "FactSectorVolume" ("DateKey");
CREATE INDEX IF NOT EXISTS idx_technicals_date ON "FactStockTechnicals" ("DateKey");
CREATE INDEX IF NOT EXISTS idx_insider_asset ON "InsiderTransactions" ("AssetKey", "TransactionDate");
