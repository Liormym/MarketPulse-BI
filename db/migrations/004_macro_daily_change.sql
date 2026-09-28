-- MarketPulse BI — 1-day % change for each macro indicator, computed and
-- stored by scripts/fetch_macro_indicators.py (pandas .pct_change() against
-- that series' own previous reading, not a fixed calendar day - yields,
-- oil, and KOSPI don't all trade on the same calendar, and Bitcoin trades
-- every day). Applied by scripts/apply_migrations.py.
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "TenYearYieldChangePct" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "TwoYearYieldChangePct" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "CrudeOilChangePct" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "BitcoinChangePct" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "KospiChangePct" DOUBLE PRECISION;
