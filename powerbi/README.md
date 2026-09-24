# Power BI layer

Power BI Desktop is a proprietary GUI app — nothing here can generate or edit a
`.pbix` file automatically. This folder gives you everything needed to build
the report yourself in a few minutes: curated views to connect to, and a
starter set of DAX measures.

## 1. Connect

1. Apply `db/views/powerbi_views.sql` on top of the migrated schema (it's not
   part of `db/migrations/` on purpose — it's a Power BI convenience layer,
   not part of the application's own read/write path):
   ```bash
   psql -d marketpulse -f db/views/powerbi_views.sql
   ```
2. In Power BI Desktop: **Get Data → PostgreSQL database**. Server
   `localhost:5432` (or your k8s-exposed host later), database `marketpulse`.
3. Import (recommended for this data volume — well under Import mode limits)
   these views: `vw_DailyPrices`, `vw_DailySentiment`, `vw_PriceAndSentiment`,
   `vw_SectorSentiment`, `vw_PipelineHealth`, `vw_DataQualitySummary`.

## 2. Data model

The views already join `DimAsset`/`DimDate` into the fact tables, so a single
flat table per view is enough for most visuals — you don't need to rebuild the
star schema's relationships inside Power BI for the core dashboards. If you
want native star-schema relationships instead (for cross-filtering across
several visuals), import `DimAsset`, `DimDate`, `FactDailyPrice`,
`FactSentiment` directly and relate them on `AssetKey` / `DateKey`.

## 3. Suggested DAX measures

```dax
Avg Sentiment Score = AVERAGE(vw_DailySentiment[AvgSentimentScore])

Sentiment Trend 7D =
AVERAGEX(
    DATESINPERIOD(vw_DailySentiment[Date], MAX(vw_DailySentiment[Date]), -7, DAY),
    vw_DailySentiment[AvgSentimentScore]
)

Positive Article Share =
DIVIDE(SUM(vw_DailySentiment[PositiveCount]), SUM(vw_DailySentiment[ArticleCount]))

Latest Close = CALCULATE(MAX(vw_DailyPrices[Close]), LASTDATE(vw_DailyPrices[Date]))

Pipeline Success Rate =
DIVIDE(
    CALCULATE(COUNTROWS(vw_PipelineHealth), vw_PipelineHealth[Status] = "Success"),
    COUNTROWS(vw_PipelineHealth)
)

DQ Pass Rate = DIVIDE(SUM(vw_DataQualitySummary[PassedChecks]), SUM(vw_DataQualitySummary[TotalChecks]))
```

## 4. Suggested pages

- **Asset detail**: price line + sentiment line over time for one ticker
  (`vw_PriceAndSentiment`), filterable by ticker.
- **Sector rotation** (the initiation doc's macro use case): `vw_SectorSentiment`
  as a heatmap or small-multiples line chart, sector x date.
- **Pipeline health / ops** (spec §12 KPIs): `vw_PipelineHealth` for run
  duration and status, `vw_DataQualitySummary` for the DQ pass rate — this is
  the dashboard that satisfies the doc's "ניטור תקינות ה-Pipeline" requirement.

## 5. Refresh

For local/dev use, a manual "Refresh" in Power BI Desktop after each pipeline
run is enough. Scheduled refresh against a k8s-hosted Postgres requires either
the on-premises data gateway (if Postgres stays inside your cluster/network)
or exposing Postgres through a stable endpoint — out of scope for this
prototype per the initiation doc's local-only infrastructure constraint.
