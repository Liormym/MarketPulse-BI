-- MarketPulse BI - Investment Score v2 (pure quantitative) and Recent Company News.
--
-- 1. FactInvestmentScoreHistory gets the v2 pillar columns and a ScoreVersion.
--    v1 rows (sentiment-based) keep their old columns and ScoreVersion = 1; they
--    are NOT comparable with v2 and the IC evaluation only reads v2 rows. v2 rows
--    leave the v1-only columns NULL (HasSentiment made nullable for that reason).
-- 2. StockRecentNews replaces StockSentimentDrivers: the latest headlines that
--    explicitly name a stock, ranked by recency. No sentiment is stored or used.
ALTER TABLE "FactInvestmentScoreHistory" ADD COLUMN IF NOT EXISTS "ScoreVersion"   SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE "FactInvestmentScoreHistory" ADD COLUMN IF NOT EXISTS "TrendPoints"    DOUBLE PRECISION;
ALTER TABLE "FactInvestmentScoreHistory" ADD COLUMN IF NOT EXISTS "MomentumPoints" DOUBLE PRECISION;
ALTER TABLE "FactInvestmentScoreHistory" ADD COLUMN IF NOT EXISTS "SectorPoints"   DOUBLE PRECISION;
ALTER TABLE "FactInvestmentScoreHistory" ADD COLUMN IF NOT EXISTS "RiskPoints"     DOUBLE PRECISION;
ALTER TABLE "FactInvestmentScoreHistory" ADD COLUMN IF NOT EXISTS "RSI14"          DOUBLE PRECISION;
ALTER TABLE "FactInvestmentScoreHistory" ALTER COLUMN "HasSentiment" DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_score_history_version_date ON "FactInvestmentScoreHistory" ("ScoreVersion", "DateKey");

CREATE TABLE IF NOT EXISTS "StockRecentNews" (
    "AssetKey"    INTEGER     NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "Rank"        SMALLINT    NOT NULL CHECK ("Rank" BETWEEN 1 AND 10),
    "ArticleID"   VARCHAR(64) NOT NULL REFERENCES "NewsArticles"("ArticleID"),
    "ComputedAt"  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY ("AssetKey", "Rank")
);

DROP TABLE IF EXISTS "StockSentimentDrivers";
