# Monitoring the pipeline from outside the VPS

The 4 GB VPS runs no Prometheus or Grafana (design §10: Evolution, Postgres
and Redis already use most of the RAM). Instead the orchestrator exposes
**`GET /metrics`** in the Prometheus text format, and any Prometheus on the
tailnet can scrape it. Nothing in `docker-compose.yml` changes for this.

The review UI's fleet page (`/review/fleet`) stays the live dashboard
during the event; `/metrics` is for history ("how full did the inbox get at
14:00?") and alerts.

## What is exported

Full list and meaning: `docs/api.md` §7.1, "Metrics". In short:

| Metric | Type | Use it for |
| --- | --- | --- |
| `bvp_jobs{state}` | gauge | queue depth per state; `awaiting_review` piling up = reviewers needed |
| `bvp_workers_online{role}`, `bvp_workers_enrolled{role}` | gauge | fleet size; `role="master"` at 0 = João's desktop is off |
| `bvp_storage_bytes{area}` | gauge | inbox / proxies / outbox bytes on the VPS |
| `bvp_disk_free_bytes` | gauge | free space on `/srv/pipeline`'s filesystem |
| `bvp_inbox_hold_bytes`, `bvp_inbox_cap_bytes` | gauge | the configured soft and hard inbox caps, to draw as lines |
| `bvp_task_offers_total{kind}` | counter | how often workers are offered work |
| `bvp_task_claims_total{kind}` | counter | throughput per stage |
| `bvp_task_failures_total{kind,scope}` | counter | failures; `scope="job"` = bad input, `scope="worker"` = a PC's problem |

Counters restart at 0 when the orchestrator restarts; always graph them
with `rate()` or `increase()`, which handle that.

## Where to scrape

The orchestrator is never published (design §8). Prometheus reaches it the
same way workers do, through Caddy's **tailnet listener**:

```
http://<VPS Tailscale IP>:8080/metrics      Authorization: Bearer <BVP_ADMIN_TOKEN>
```

Test it from any machine allowed to reach `tag:server:8080` (an admin's own
device, or a worker PC):

```sh
curl -s -H "Authorization: Bearer $BVP_ADMIN_TOKEN" http://100.x.y.z:8080/metrics | head
```

`infra/vps/compose-smoke.sh` also checks this endpoint answers.

## Pointing a Prometheus at it (later, not part of the VPS)

Run Prometheus on a machine that is already on the tailnet with access to
`tag:server:8080`. Today's ACL (`infra/tailnet/acl.hujson`) allows that for
admins' own untagged devices (e.g. João's laptop or a home server joined
as an admin device). A dedicated tagged monitoring node would need a new
tag and a grant `tag:monitor → tag:server tcp:8080`; that is an ACL change,
not covered here.

Put the admin token in a file only Prometheus can read, then:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: baja-orchestrator
    scrape_interval: 30s
    metrics_path: /metrics
    scheme: http                      # WireGuard already encrypts the tailnet
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/baja_admin_token
    static_configs:
      - targets: ["100.x.y.z:8080"]   # the VPS Tailscale IP (tailscale ip -4 on the VPS)
```

30 s is plenty: every scrape reads the DB and sums three folders, which is
cheap but not free on the small VPS. Don't go below 15 s.

## Grafana

Add the Prometheus above as a data source. Useful panels:

| Panel | PromQL |
| --- | --- |
| Jobs waiting for review | `bvp_jobs{state="awaiting_review"}` |
| Jobs by state (stacked) | `bvp_jobs{state!~"done\|discarded"}` |
| Inbox vs caps | `bvp_storage_bytes{area="inbox"}`, `bvp_inbox_hold_bytes`, `bvp_inbox_cap_bytes` |
| Free disk | `bvp_disk_free_bytes` |
| Workers online | `sum by (role) (bvp_workers_online)` |
| Clips rendered per 10 min | `increase(bvp_task_claims_total{kind="process"}[10m])` |
| Failures per 10 min | `sum by (kind, scope) (increase(bvp_task_failures_total[10m]))` |

Alert ideas (Alertmanager or Grafana alerts):

* `bvp_workers_online{role="master"} == 0 for 10m` — the master is off; raws
  are not being backed up to it.
* `bvp_storage_bytes{area="inbox"} > bvp_inbox_hold_bytes` — the bot is
  telling volunteers to hold; check the fleet (EVENT_DAY.md, "Inbox filling up").
* `bvp_disk_free_bytes < 10e9` — proxy eviction is running; check `df -h`.
* `bvp_jobs{state="awaiting_review"} > 20 for 15m` — reviewers are behind.
