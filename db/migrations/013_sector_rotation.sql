-- MarketPulse BI - Early Sector Rotation Engine.
--
-- One row per sector SPDR ETF per day (written by scripts/compute_sector_flows.py via
-- marketpulse/sector_rotation.py): the sector's 2-, 5- and 20-bar return relative to the
-- S&P 500 Equal Weight ETF, RSP (percentage points; the cap-weighted index would make nearly every non-tech sector look like a leader whenever mega-caps fall), how many of its S&P 1500 stocks rose vs fell that
-- day (breadth), the blended -1..+1 rotation signal and the regime it names
-- (inflow | outflow | balanced). The score (Pillar 3) and the dashboard's Sector Rotation
-- Radar both read this table, so they can never disagree.
CREATE TABLE IF NOT EXISTS "FactSectorRotation" (
    "AssetKey"       INTEGER          NOT NULL REFERENCES "DimAsset"("AssetKey"),  -- the sector SPDR ETF
    "DateKey"        INTEGER          NOT NULL REFERENCES "DimDate"("DateKey"),
    "Rel2DPct"       DOUBLE PRECISION,
    "Rel5DPct"       DOUBLE PRECISION,
    "Rel20DPct"      DOUBLE PRECISION,
    "Advancing"      INTEGER,
    "Declining"      INTEGER,
    "BreadthPct"     DOUBLE PRECISION,   -- advancers / (advancers + decliners) * 100; NULL if too few movers
    "RotationScore"  DOUBLE PRECISION,   -- -1 (aggressive outflow) .. +1 (aggressive inflow)
    "Regime"         VARCHAR(8) CHECK ("Regime" IN ('inflow', 'outflow', 'balanced')),
    "ComputedAt"     TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY ("AssetKey", "DateKey")
);
CREATE INDEX IF NOT EXISTS idx_sector_rotation_date ON "FactSectorRotation" ("DateKey");
