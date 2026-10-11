-- MarketPulse BI - CEO/CFO insider activity as an explicit +/-5 score modifier.
--
-- Until now only SALES were captured (a purchase was stored as "Other" with its
-- text discarded) and "key officer" meant any "Chief ..." title (COO, CTO, ...).
-- The score now needs net buying vs net selling by the CEO/CFO over 90 days:
--   * InsiderTransactions.IsExecutiveBuy: a CEO/CFO open-market purchase.
--     IsExecutiveSale is redefined to CEO/CFO sales only (re-derived below).
--   * StockEnrichmentCache.InsiderFetchedAt: insider filings used to be fetched
--     once, ever, so a new Form 4 never reached the score. They are now
--     refreshed on the same 24h cycle as the rest of the enrichment; NULL forces
--     a full refresh (and re-classification of every stored row) on the next run.
--   * FactInvestmentScoreHistory.InsiderPoints: the modifier that was applied.
ALTER TABLE "InsiderTransactions" ADD COLUMN IF NOT EXISTS "IsExecutiveBuy" BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE "StockEnrichmentCache" ADD COLUMN IF NOT EXISTS "InsiderFetchedAt" TIMESTAMPTZ;
ALTER TABLE "FactInvestmentScoreHistory" ADD COLUMN IF NOT EXISTS "InsiderPoints" DOUBLE PRECISION;

UPDATE "InsiderTransactions"
SET "IsExecutiveSale" = ("TransactionType" = 'Sale'
    AND "Position" ~* '\m(ceo|cfo|chief executive|chief financial)\M');
CREATE INDEX IF NOT EXISTS idx_insider_exec ON "InsiderTransactions" ("AssetKey", "TransactionDate")
    WHERE "IsExecutiveSale" OR "IsExecutiveBuy";
