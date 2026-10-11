# Kubernetes manifests (k3s, single VM)

Validated offline only (`kustomize build` + `kubeconform --strict` against the
Kubernetes 1.31 and External Secrets / cert-manager CRD schemas). **Never
applied to a real cluster yet** - expect to fix small things on first contact.

## Layout

| File | What |
|---|---|
| `00-namespace.yaml` | `marketpulse` namespace, Pod Security `baseline` enforced |
| `serviceaccounts.yaml` | one SA per workload class, no API token mounted |
| `configmap.yaml` | non-secret runtime config |
| `externalsecrets.yaml` | `SecretStore` + 3 `ExternalSecret`s (AWS Secrets Manager); replaces `secrets.example.yaml` |
| `networkpolicies.yaml` | default-deny, DNS, Postgres :5432 only from `marketpulse/db-client` pods, ingress-nginx -> webapp, HTTPS-only public egress, ACME solver |
| `postgres-statefulset.yaml` | Postgres 16 (digest-pinned), **15Gi `local-path` PVC**, probes, non-root |
| `migrate-job.yaml` | migrations + dimension seed (re-create on every image bump) |
| `webapp-deployment.yaml` | 2 replicas, PDB, GHCR image, probes, limits |
| `eod-update-cronjob.yaml` | full refresh 09:00 / 15:00 / 23:00 Israel time, daily |
| `backup-cronjob.yaml` | nightly `pg_dump` (02:00 Israel) -> S3-compatible bucket (digest-pinned images) |
| `clusterissuer.yaml` | Let's Encrypt staging + prod |
| `ingress.yaml` | main Ingress (TLS) + rate-limited refresh Ingress |

Images: `ghcr.io/liormym/marketpulse-{webapp,pipeline}:sha-339e49d-amd64` (CI run 38129077609).

## Cluster prerequisites (one-time, not managed here)

1. **VM**: amd64 (the images are amd64 only), 4 GB RAM is enough. Requests add up to ~1.2 GiB
   (2x webapp 192Mi, Postgres 512Mi, EOD 256Mi); the full update measured a 196 MB peak RSS locally.
2. **k3s without Traefik**: install with `--disable=traefik`. Keep the embedded network-policy controller enabled (the default).
3. **ingress-nginx** with real client IPs and a proper 429:
   ```yaml
   controller:
     service: {externalTrafficPolicy: Local}
     config: {limit-req-status-code: "429", limit-conn-status-code: "429"}
   ```
   It must run in the `ingress-nginx` namespace (the NetworkPolicies match that name).
4. **cert-manager** (with CRDs) and the **External Secrets Operator** (`external-secrets.io/v1`, ESO >= 0.17).
5. DNS `A` record for your host -> the VM's public IP; ports 80/443 open (80 is needed for HTTP-01).

## Secrets

Create ONE Secrets Manager secret `marketpulse/prod` (JSON keys documented at the top of `externalsecrets.yaml`),
plus an IAM user limited to `secretsmanager:GetSecretValue` on it (about $0.40/month). Then the only
credential created by hand - the key to the vault:

```bash
kubectl create namespace marketpulse --dry-run=client -o yaml | kubectl apply -f -
kubectl -n marketpulse create secret generic aws-secrets-manager-credentials \
  --from-literal=access-key=AKIA... --from-literal=secret-access-key=...
```

Do not put real values in Git. `DB_PASSWORD` only takes effect when Postgres first initialises its
data directory; rotating it later also needs `ALTER USER`.

## Deploy

Edit the TODOs first: host (`ingress.yaml`, 3 places), email (`clusterissuer.yaml`), AWS region
(`externalsecrets.yaml`, `backup-cronjob.yaml`).

```bash
kubectl -n marketpulse delete job marketpulse-migrate --ignore-not-found
kubectl apply -k k8s/
kubectl -n marketpulse get externalsecret          # all READY=True
kubectl -n marketpulse wait --for=condition=complete job/marketpulse-migrate --timeout=10m
kubectl -n marketpulse get certificate             # staging cert issues, then:
# set cert-manager.io/cluster-issuer: letsencrypt-prod in ingress.yaml, delete secret marketpulse-tls, re-apply
kubectl create job --from=cronjob/marketpulse-eod-update eod-manual -n marketpulse   # first data load
```

Bump images: `sed -i 's/sha-OLD-amd64/sha-NEW-amd64/' k8s/*.yaml`, then repeat the first two commands.

## Storage: what `local-path` means

The 15Gi request is **not enforced** and cannot be expanded (`local-path` ignores both); the data is a
directory on the VM disk. It survives pod restarts but not loss of the VM, so the S3 backup is the only
disaster-recovery copy. Monitor node disk usage. Moving to a replicated or expandable class later means
a dump/restore into a new StatefulSet.

## Rate limiting

`POST /api/stock/<ticker>/refresh` is limited per client IP by `marketpulse-refresh` (6/min, burst x2, 2 concurrent).
Verify after go-live: `for i in $(seq 20); do curl -s -o /dev/null -w '%{http_code}\n' -X POST https://HOST/api/stock/AAPL/refresh; done`
should turn to `429`. If every client shares one IP in the logs, `externalTrafficPolicy: Local` is not in effect.

## Backup and restore

Dumps land in `s3://$S3_BUCKET/postgres/marketpulse-<UTC timestamp>.dump`; add a bucket lifecycle rule for retention.
Test a manual run: `kubectl create job --from=cronjob/marketpulse-pg-backup backup-test -n marketpulse`.

Restore into a fresh cluster (after Postgres is up, before the webapp matters):
```bash
aws s3 cp s3://BUCKET/postgres/marketpulse-XXXX.dump .
kubectl -n marketpulse port-forward svc/postgres 5432:5432 &     # allowed: kubectl port-forward bypasses NetworkPolicy
pg_restore -h localhost -U marketpulse -d marketpulse --clean --if-exists --no-owner marketpulse-XXXX.dump
```
**Do a restore drill once** - an untested backup is not a backup.

## Hardening backlog

- `readOnlyRootFilesystem` is on only for the backup uploader. The app images are not yet proven to run read-only
  (pandas/yfinance temp and cache paths; there is no ML stack any more); add `--read-only --tmpfs /tmp` to the CI smoke tests first, then enable it.
- Add ephemeral-storage requests/limits; image-pull policy is `IfNotPresent` (tags are immutable per commit).
- Trivy scan in CI; Argo CD/Flux instead of manual `kubectl apply -k`.
- Yahoo Finance may throttle a cloud VM's egress IP; watch the first EOD runs.
