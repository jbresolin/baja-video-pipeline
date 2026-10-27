"""One handler per task kind (docs/api.md §5). See tasks.environment for the contract."""

from shared.models import TaskKind
from worker.tasks.deliver import run_deliver
from worker.tasks.download_raw import run_download_raw
from worker.tasks.make_proxy import run_make_proxy
from worker.tasks.process import run_process

TASK_HANDLERS = {
    TaskKind.DOWNLOAD_RAW: run_download_raw,
    TaskKind.MAKE_PROXY: run_make_proxy,
    TaskKind.PROCESS: run_process,
    TaskKind.DELIVER: run_deliver,
}
