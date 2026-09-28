-- MarketPulse BI — S&P 500, NASDAQ Composite, and the S&P 500 Equal Weight
-- ETF (RSP), tracked as a cap-weighted-vs-equal-weighted breadth signal:
-- when RSP lags SPX, gains are concentrated in a handful of mega-caps
-- rather than broad-based. Fetched/stored by scripts/fetch_macro_indicators.py
-- alongside the other macro indicators. Applied by scripts/apply_migrations.py.
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "SP500Index" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "SP500ChangePct" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "NasdaqIndex" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "NasdaqChangePct" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "RSPPrice" DOUBLE PRECISION;
ALTER TABLE "MacroIndicators" ADD COLUMN IF NOT EXISTS "RSPChangePct" DOUBLE PRECISION;
