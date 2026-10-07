-- MarketPulse BI - Investment Score history.
--
-- FactInvestmentScore holds ONE row per asset (the latest score, overwritten
-- on every run) so the dashboard's Top 5 widget is a fast indexed SELECT. It
-- cannot answer "did high scores actually precede gains?" because the past is
-- gone. This table keeps every day's score, so that question becomes
-- measurable going forward without having to reconstruct scores after the fact.
--
-- Grain: one row per asset per PRICE BAR the score was computed on (DateKey is
-- the date of the last price used, not the date the job happened to run), so
-- re-running on a weekend or holiday rewrites the same row instead of adding a
-- duplicate. Written by scripts/compute_investment_scores.py (idempotent upsert).
--
-- ClosePrice is the close of that bar, stored so forward returns can be
-- computed from this table alone. HasSentiment records whether the score used
-- news sentiment or took the "no recent news" normalized path, because those
-- two populations should be evaluated separately.
CREATE TABLE IF NOT EXISTS "FactInvestmentScoreHistory" (
    "AssetKey"            INTEGER          NOT NULL REFERENCES "DimAsset"("AssetKey"),
    "DateKey"             INTEGER          NOT NULL REFERENCES "DimDate"("DateKey"),
    "Score"               DOUBLE PRECISION NOT NULL,
    "SentimentPoints"     DOUBLE PRECISION,
    "TechnicalPoints"     DOUBLE PRECISION,
    "PositioningPoints"   DOUBLE PRECISION,
    "RiskModifierPoints"  DOUBLE PRECISION,
    "ClosePrice"          DOUBLE PRECISION,
    "ShortPercentOfFloat" DOUBLE PRECISION,
    "HasSentiment"        BOOLEAN          NOT NULL,
    "Flags"               JSONB            NOT NULL DEFAULT '{}'::jsonb,
    "ComputedAt"          TIMESTAMPTZ      NOT NULL,
    PRIMARY KEY ("AssetKey", "DateKey")
);
CREATE INDEX IF NOT EXISTS idx_score_history_date ON "FactInvestmentScoreHistory" ("DateKey");
