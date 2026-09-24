# Kubernetes manifests

Written per the spec's CronJob → Job → Pod → Python container hierarchy
(§4) and the StatefulSet+PersistentVolume requirement for Postgres
durability (§4, and initiation-doc risk #2). **Not applied or tested in this
session** — minikube/kubectl aren't installed on this machine yet.

## Apply order (once you have a local cluster)

```bash
kubectl apply -f k8s/00-namespace.yaml
cp k8s/secrets.example.yaml k8s/secrets.yaml   # edit real values, never commit this file
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/postgres-statefulset.yaml
kubectl wait --for=condition=ready pod -l app=postgres -n marketpulse --timeout=120s

# run migrations + seed once (from your machine, port-forwarded, or as a one-off Job)
kubectl port-forward -n marketpulse svc/postgres 5432:5432 &
DB_HOST=localhost python scripts/apply_migrations.py
DB_HOST=localhost python scripts/seed_dimensions.py

# build the pipeline image and load it into the cluster (minikube example)
docker build -t marketpulse-pipeline:latest -f docker/pipeline.Dockerfile .
minikube image load marketpulse-pipeline:latest

kubectl apply -f k8s/pipeline-cronjob.yaml
kubectl create job --from=cronjob/marketpulse-pipeline marketpulse-pipeline-manual -n marketpulse  # trigger once, don't wait for the schedule
```

## Files

- `00-namespace.yaml` — the `marketpulse` namespace everything else lives in.
- `secrets.example.yaml` — template for DB credentials as a k8s Secret (spec §11: no hardcoded credentials).
- `postgres-statefulset.yaml` — Postgres StatefulSet + headless Service + PVC (2Gi, `ReadWriteOnce`).
- `pipeline-cronjob.yaml` — the scheduled pipeline run, default weekdays 21:30 UTC, 15-minute deadline matching the spec's performance KPI.
