"""BajaWorkerTray.exe: the notification-area icon. The thin GUI layer only.

Every few seconds: ask the worker (GET http://127.0.0.1:8700/api/status)
and Windows (`sc query BajaWorker`), let worker.tray.state decide colour,
tooltip, menu and notifications, and show them. Menu actions call the
localhost API (pause/resume), sc.exe (start/stop) or BajaWorker.exe
--first-run. Nothing here is unit-tested on Linux beyond being importable;
pystray is imported inside run_tray() for that reason (it is a Windows-only
dependency).
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw

from worker.tray.service import ServiceController, run_sc
from worker.tray.state import (
    EXIT_BALLOON_TEXT,
    TOAST_TEXT,
    Colour,
    ServiceState,
    ToastTracker,
    TrayView,
    view_for,
)

logger = logging.getLogger(__name__)

PANEL_URL = "http://localhost:8700"
API_URL = "http://127.0.0.1:8700/api"
POLL_SECONDS = 3.0
HTTP_TIMEOUT_SECONDS = 2.0
ICON_SIZE = 64
ICON_RGB = {
    Colour.GREEN: (46, 160, 67),
    Colour.BLUE: (56, 109, 180),
    Colour.YELLOW: (230, 180, 30),
    Colour.RED: (205, 50, 50),
    Colour.GREY: (130, 130, 130),
}
# Tooltips longer than this are cut by Windows.
MAX_TOOLTIP_CHARS = 127


def draw_icon(colour: Colour) -> Image.Image:
    """A filled circle with a white "B": readable at 16 px, colour says the state."""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, ICON_SIZE - 2, ICON_SIZE - 2), fill=ICON_RGB[colour])
    draw.text((ICON_SIZE * 0.36, ICON_SIZE * 0.22), "B", fill="white", font_size=36)
    return image


def fetch_status() -> dict | None:
    try:
        with urllib.request.urlopen(f"{API_URL}/status", timeout=HTTP_TIMEOUT_SECONDS) as answer:
            return json.loads(answer.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def post_action(name: str) -> bool:
    request = urllib.request.Request(
        f"{API_URL}/{name}",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS):
            return True
    except (OSError, urllib.error.HTTPError):
        return False


def run_sc_elevated(arguments: list[str]) -> bool:
    """Fallback when Users lack the service right: sc.exe behind a UAC prompt."""
    import ctypes

    parameters = subprocess.list2cmdline(arguments)
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "sc.exe", parameters, None, 0)
    return result > 32


def worker_exe() -> Path:
    return Path(sys.executable).resolve().parent / "BajaWorker.exe"


def default_config_path() -> Path:
    return Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\") / "BajaWorker" / "worker.json"


class TrayApp:
    def __init__(self, controller: ServiceController) -> None:
        self.controller = controller
        self.toasts = ToastTracker()
        self.view = TrayView(Colour.GREY, "iniciando…")
        self.stopped_by_us = False
        self.was_running = False
        self.icon = None
        self._stop = threading.Event()

    # --- polling ---

    def refresh(self) -> None:
        service = self.controller.state()
        if service == ServiceState.RUNNING:
            self.was_running = True
            self.stopped_by_us = False
        page_may_answer = service in (ServiceState.RUNNING, ServiceState.STARTING)
        status = fetch_status() if page_may_answer else None
        self.view = view_for(
            status, service, stopped_by_us=self.stopped_by_us, was_running=self.was_running
        )
        if self.icon is not None:
            self.icon.icon = draw_icon(self.view.colour)
            self.icon.title = self.view.tooltip[:MAX_TOOLTIP_CHARS]
            self.icon.update_menu()
            for toast in self.toasts.new_toasts(self.view):
                self.icon.notify(TOAST_TEXT[toast], "Baja Worker")

    def poll_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                logger.exception("tray refresh failed")
            self._stop.wait(POLL_SECONDS)

    # --- menu actions ---

    def open_panel(self, *_):
        webbrowser.open(PANEL_URL)

    def pause(self, *_):
        post_action("pause")
        self.refresh()

    def resume(self, *_):
        post_action("resume")
        self.refresh()

    def stop_service(self, *_):
        self.stopped_by_us = True
        self.controller.stop()
        self.refresh()

    def start_service(self, *_):
        self.controller.start()
        self.refresh()

    def finish_connection(self, *_):
        # BajaWorker.exe asks for administrator rights itself (cli.py).
        subprocess.Popen([str(worker_exe()), "--first-run", "--config", str(default_config_path())])

    def exit_icon(self, *_):
        if self.icon is not None:
            self.icon.notify(EXIT_BALLOON_TEXT, "Baja Worker")
            time.sleep(3)  # let the balloon show before the icon disappears
            self._stop.set()
            self.icon.stop()

    def build_menu(self, pystray):
        def shown(name: str):
            return lambda _item: getattr(self.view.menu, name)

        item = pystray.MenuItem
        return pystray.Menu(
            item("Abrir painel", self.open_panel, default=True, visible=shown("open_panel")),
            item("Pausar", self.pause, visible=shown("pause")),
            item("Retomar", self.resume, visible=shown("resume")),
            item("Desligar o worker", self.stop_service, visible=shown("stop")),
            item("Ligar o worker", self.start_service, visible=shown("start")),
            item("Concluir conexão", self.finish_connection, visible=shown("finish_connection")),
            pystray.Menu.SEPARATOR,
            item("Sair do ícone", self.exit_icon, visible=shown("exit_icon")),
        )


def run_tray() -> int:
    import pystray  # Windows-only dependency (worker/pyproject.toml)

    app = TrayApp(ServiceController(run=run_sc, run_elevated=run_sc_elevated))
    app.icon = pystray.Icon(
        "BajaWorker", draw_icon(Colour.GREY), app.view.tooltip, menu=app.build_menu(pystray)
    )
    threading.Thread(target=app.poll_forever, name="tray-poll", daemon=True).start()
    app.icon.run()
    return 0


if __name__ == "__main__":
    sys.exit(run_tray())
