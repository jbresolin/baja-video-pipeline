"""The volunteer's "Pausar" / "Retomar" switch (LIVESTACK-38 item 9).

Pausing means: finish the task you are on, then take no new task of any
kind, but keep heartbeating (with `paused: true`) and keep serving held
raws to peers. It needs no administrator rights: the tray icon and the
status page flip it through the localhost JSON API (worker.local_api).

The switch is remembered in worker.json (`paused`), so a paused worker
stays paused after a reboot until someone clicks "Retomar".
"""

import logging
import threading
from pathlib import Path

from worker.config import save_config
from worker.config_problems import read_config

logger = logging.getLogger(__name__)


class PauseSwitch:
    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._lock = threading.Lock()
        # A broken worker.json (shown on the status page) counts as "not paused".
        reading = read_config(config_path)
        self._paused = reading.config.paused if reading.config is not None else False

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._set(True)

    def resume(self) -> None:
        self._set(False)

    def _set(self, paused: bool) -> None:
        with self._lock:
            if self._paused == paused:
                return
            # Read-modify-write so nothing else in worker.json is lost. A file
            # that does not parse is never overwritten (the volunteer is
            # fixing it): the switch then lasts until the next restart.
            reading = read_config(self._config_path)
            if reading.config is not None:
                updated = reading.config.model_copy(update={"paused": paused})
                save_config(self._config_path, updated)
            else:
                logger.warning("worker.json does not parse; pause kept in memory only")
            self._paused = paused
        logger.info("worker %s by the volunteer", "paused" if paused else "resumed")
