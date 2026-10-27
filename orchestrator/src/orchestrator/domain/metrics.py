"""GET /metrics: the fleet and the VPS in Prometheus text format (docs/api.md §7.5).

Built for an external Prometheus to scrape (infra/vps/MONITORING.md); no
client library, the text format is a few lines per metric:

    # HELP bvp_jobs Jobs per state.
    # TYPE bvp_jobs gauge
    bvp_jobs{state="received"} 3

Gauges are read fresh on every scrape (DB rows, folder sizes). Counters
come from context.task_counters and restart at zero with the process.
Every label combination is always present, zeros included, so a graph
shows 0 instead of a gap.
"""

from dataclasses import dataclass, field

from orchestrator.context import OrchestratorContext
from orchestrator.domain.fleet_snapshot import is_online
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import workers as workers_repo
from shared.models import FailureScope, JobState, TaskKind, WorkerRole

# The content type Prometheus expects for the text exposition format.
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@dataclass
class Metric:
    name: str
    kind: str  # "gauge" or "counter"
    help_text: str
    # (labels, value) pairs; labels is a tuple of (name, value) pairs.
    samples: list[tuple[tuple[tuple[str, str], ...], float]] = field(default_factory=list)

    def add(self, value: float, **labels: str) -> None:
        self.samples.append((tuple(labels.items()), value))

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} {self.kind}"]
        for labels, value in self.samples:
            label_text = ",".join(f'{name}="{_escape(text)}"' for name, text in labels)
            suffix = f"{{{label_text}}}" if label_text else ""
            lines.append(f"{self.name}{suffix} {_format_number(value)}")
        return "\n".join(lines)


def render_metrics(context: OrchestratorContext) -> str:
    """The whole /metrics body."""
    metrics = [
        *_job_metrics(context),
        *_worker_metrics(context),
        *_disk_metrics(context),
        *_task_counter_metrics(context),
    ]
    return "\n".join(metric.render() for metric in metrics) + "\n"


def _job_metrics(context: OrchestratorContext) -> list[Metric]:
    with context.database.transaction() as connection:
        counts = jobs_repo.count_jobs_by_state(connection)
    jobs = Metric("bvp_jobs", "gauge", "Jobs per state.")
    for state in JobState:
        jobs.add(counts.get(str(state), 0), state=str(state))
    return [jobs]


def _worker_metrics(context: OrchestratorContext) -> list[Metric]:
    now = context.clock.now()
    with context.database.transaction() as connection:
        rows = workers_repo.list_worker_rows(connection)
    enrolled = Metric("bvp_workers_enrolled", "gauge", "Enrolled workers per role.")
    online = Metric(
        "bvp_workers_online", "gauge", "Workers per role with a heartbeat in the last 45 s."
    )
    for role in WorkerRole:
        of_role = [row for row in rows if row["role"] == role]
        enrolled.add(len(of_role), role=str(role))
        online.add(sum(1 for row in of_role if is_online(row, now)), role=str(role))
    return [enrolled, online]


def _disk_metrics(context: OrchestratorContext) -> list[Metric]:
    layout, settings = context.layout, context.settings
    used = Metric(
        "bvp_storage_bytes",
        "gauge",
        "Bytes in each BVP_DATA_DIR area (proxies includes thumbnails), partial files included.",
    )
    used.add(layout.inbox_bytes(), area="inbox")
    used.add(layout.proxy_bytes(), area="proxies")
    used.add(layout.outbox_bytes(), area="outbox")
    free = Metric("bvp_disk_free_bytes", "gauge", "Free bytes on the BVP_DATA_DIR filesystem.")
    free.add(layout.free_bytes())
    hold = Metric(
        "bvp_inbox_hold_bytes", "gauge", "Soft inbox cap: above it the bot asks senders to hold."
    )
    hold.add(settings.inbox_hold_bytes)
    cap = Metric("bvp_inbox_cap_bytes", "gauge", "Hard inbox cap: above it intake answers 413.")
    cap.add(settings.inbox_cap_bytes)
    return [used, free, hold, cap]


def _task_counter_metrics(context: OrchestratorContext) -> list[Metric]:
    offers, claims, failures = context.task_counters.snapshot()
    offered = Metric(
        "bvp_task_offers_total", "counter", "Tasks offered by /workers/{id}/next since start."
    )
    claimed = Metric("bvp_task_claims_total", "counter", "New leases from /claim since start.")
    for kind in TaskKind:
        offered.add(offers.get(kind, 0), kind=str(kind))
        claimed.add(claims.get(kind, 0), kind=str(kind))
    failed = Metric(
        "bvp_task_failures_total", "counter", "Failure reports accepted by /failed since start."
    )
    for kind in TaskKind:
        for scope in FailureScope:
            failed.add(failures.get((kind, scope), 0), kind=str(kind), scope=str(scope))
    return [offered, claimed, failed]


def _escape(label_value: str) -> str:
    """Label values escape backslash, double quote and newline (Prometheus text format)."""
    return label_value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_number(value: float) -> str:
    # Whole numbers without ".0", so byte counts read naturally.
    return str(int(value)) if float(value).is_integer() else repr(float(value))
