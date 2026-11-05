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

from worker.config import load_config, save_config

logger = logging.getLogger(__name__)


class PauseSwitch:
    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._lock = threading.Lock()
        self._paused = load_config(config_path).paused

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
            # Read-modify-write so nothing else in worker.json is lost.
            config = load_config(self._config_path)
            save_config(self._config_path, config.model_copy(update={"paused": paused}))
            self._paused = paused
        logger.info("worker %s by the volunteer", "paused" if paused else "resumed")
