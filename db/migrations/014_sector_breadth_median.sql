-- Breadth is measured relative to the cross-sector median (a rotation is a relative move:
-- on a day 90% of stocks rise, "more advancers than decliners" is true of almost every
-- sector). The median advancing share across the sectors that day is stored on every
-- row of that day so the score (Pillar 3) and the Radar rebuild the same signal.
ALTER TABLE "FactSectorRotation" ADD COLUMN IF NOT EXISTS "BreadthMedianPct" DOUBLE PRECISION;
