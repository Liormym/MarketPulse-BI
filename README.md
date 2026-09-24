# MarketPulse BI

Zero-cost, end-to-end financial market-intelligence pipeline: pulls stock
prices (yfinance) and news headlines (free RSS), scores headlines with
FinBERT sentiment analysis, and loads everything into a PostgreSQL star
schema — idempotent, data-quality-checked, and logged — ready for a Power BI
dashboard on top. Built from `docs/מסמך ייזום מפורט - MarketPulse BI.pdf`
(initiation) and `docs/מסמך אפיון מערכת (System Specification) מעודכן - MarketPulse BI.pdf`
(spec).

## Architecture

```
CronJob (k8s) ──▶ Job ──▶ Pod ──▶ Python container
                                     │
                    ┌────────────────┴─────────────────┐
                    ▼                                   ▼
              yfinance (prices)                  RSS feeds (news)
                    │                                   │
                    ▼                                   ▼
              raw storage (./data/raw, replay/debug copy)
                    │
                    ▼
         validate → transform → FinBERT sentiment → aggregate
                    │
                    ▼
     idempotent upsert (ON CONFLICT) ──▶ PostgreSQL star schema
                    │                         (StatefulSet + PVC in k8s)
                    ▼
      PipelineExecutionLog / DataQualityResults
                    │
                    ▼
                Power BI (manual connection, see powerbi/README.md)
```

Star schema: `DimAsset`, `DimDate`, `FactDailyPrice`, `NewsArticles`,
`FactSentiment`, plus the operational tables `PipelineExecutionLog` and
`DataQualityResults`. Full column-level definitions in
`db/migrations/001_init_schema.sql`.

## Repo layout

- `src/marketpulse/` — the pipeline: `extract/` (prices, news), `sentiment/`
  (FinBERT), `quality/` (the 7 data-quality rules), `load/` (idempotent
  upserts), `pipeline.py` (orchestrator), `logging_utils.py` (run/DQ logging).
- `db/migrations/` — versioned SQL DDL, tracked in `schema_migrations`.
- `db/views/` — Power BI-facing views (not used by the app itself).
- `config/watchlist.yaml` — the ticker universe; add rows to grow past ~18
  toward the doc's ~50-ticker target, no code changes needed.
- `docker/`, `docker-compose.yml`, `k8s/` — containerization and orchestration
  (see "Beyond local" below).
- `powerbi/README.md` — how to wire the warehouse into Power BI Desktop.
- `tests/` — pytest: transform, the 7 DQ rules, sentiment (mocked), and
  idempotent-upsert integration tests against a real local Postgres.

## Local setup (what's running now)

Requires Python 3.11+ (newer `yfinance` needs `curl_cffi`, which has no
wheels for Python 3.9 — installed here via `brew install python@3.11`) and a
local PostgreSQL (installed here via `brew install postgresql@16`).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # edit if your local Postgres creds differ

# one-time setup
python scripts/apply_migrations.py
python scripts/seed_dimensions.py

# run the pipeline (fetches prices + news, scores sentiment, loads, logs)
PYTHONPATH=src python -m marketpulse.pipeline

# tests (spins up nothing extra — reuses the same local DB, rolls back after each test)
PYTHONPATH=src pytest
```

First run downloads the FinBERT model (~440MB) from HuggingFace; later runs
reuse the cached weights.

## Verifying it actually works

```bash
psql -d marketpulse -c 'SELECT "PipelineStage", "Status", "RecordsProcessed" FROM "PipelineExecutionLog" ORDER BY "StartTime" DESC LIMIT 10;'
psql -d marketpulse -c 'SELECT "CheckType", "Passed", "FailedRecords" FROM "DataQualityResults" ORDER BY "CheckID" DESC LIMIT 10;'
psql -d marketpulse -c 'SELECT COUNT(*) FROM "FactDailyPrice";'
```

Re-run the pipeline a second time and re-check the row counts — per spec §12
("Confirmed Idempotency"), they should not grow from re-processing the same
day's data.

## Beyond local: Docker and Kubernetes

Docker Desktop/kubectl/minikube aren't installed on this machine, so the
following are written and reviewed but **not yet run**:

```bash
# once Docker Desktop is installed:
docker compose up --build          # postgres + pipeline in containers

# once minikube/kubectl are set up:
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/secrets.yaml        # copy from k8s/secrets.example.yaml first
kubectl apply -f k8s/postgres-statefulset.yaml
# build & load the pipeline image into the cluster, then:
kubectl apply -f k8s/pipeline-cronjob.yaml
```

## Out of scope (per the initiation/spec docs)

No algorithmic trading, no real-time streaming (batch only), no Big Data
stack (Kafka/Spark), no FinBERT fine-tuning, no cloud spend — everything runs
locally at $0.

## Deviations / improvements over the source docs

- **Local-first dev loop**: build and verify the pipeline against a native
  Postgres before containers/Kubernetes, for a much faster feedback loop.
- **Versioned SQL migrations** with a `schema_migrations` tracking table
  instead of ad-hoc DDL.
- **`tenacity`** for retry/backoff (spec §10) instead of hand-rolled loops.
- **pytest coverage** for the DQ rules, idempotency, and transforms — the
  docs define these as acceptance KPIs (§12) but don't specify how they're
  proven; the test suite makes that concrete.
- **Config-driven watchlist** (`config/watchlist.yaml`) instead of a
  hardcoded ticker list.
- No `.pbix` file is generated — Power BI Desktop has no automation surface
  available here. `powerbi/README.md` gives you views + DAX to wire up
  yourself in a few minutes.
