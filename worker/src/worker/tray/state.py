"""What the tray icon (and the status page) should say, from the facts alone.

No GUI here: give it the status document from GET /api/status (or None
when the page did not answer) and the Windows service state, and it says
the colour, the one-line pt-BR tooltip, which menu items to show, and
(through ToastTracker) which notifications to raise, each only once.

Colours, most urgent first (the first rule that matches wins):
  grey    the service is not running (and nobody needs to act)
  red     a problem the volunteer must act on: the service stopped by
          itself, the server unreachable for over a minute, disk near the
          storage cap, enrollment token refused
  yellow  paused, or waiting: connection step not finished, waiting for
          the enrollment token, the service still starting
  green   working: at least one task running
  blue    idle and connected
"""

from dataclasses import dataclass, field
from enum import StrEnum

# The server counts as "unreachable" (red) after this long without contact.
UNREACHABLE_ALERT_SECONDS = 60.0
TOOLTIP_PREFIX = "Baja Worker: "


class Colour(StrEnum):
    GREEN = "green"
    BLUE = "blue"
    YELLOW = "yellow"
    RED = "red"
    GREY = "grey"


class ServiceState(StrEnum):
    """The Windows service, as `sc query` reports it (see tray.service)."""

    RUNNING = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    # Not installed, or Windows would not tell us.
    UNKNOWN = "unknown"


class Toast(StrEnum):
    """Things a volunteer must act on: the only notifications ever shown."""

    CONNECTION_STEP_PENDING = "connection_step_pending"
    DISK_NEAR_CAP = "disk_near_cap"
    ENROLLMENT_REFUSED = "enrollment_refused"
    SERVICE_STOPPED_UNEXPECTEDLY = "service_stopped_unexpectedly"


TOAST_TEXT = {
    Toast.CONNECTION_STEP_PENDING: (
        'Falta concluir a conexão: clique no ícone e escolha "Concluir conexão".'
    ),
    Toast.DISK_NEAR_CAP: (
        "O espaço reservado para o Baja Worker está quase cheio. Ele não vai "
        "receber novos vídeos; fale com a equipe Baja."
    ),
    Toast.ENROLLMENT_REFUSED: (
        "O token de inscrição foi recusado. Peça um novo à equipe Baja e cole-o no painel."
    ),
    Toast.SERVICE_STOPPED_UNEXPECTEDLY: (
        'O Baja Worker parou sozinho. Clique no ícone e escolha "Ligar o worker".'
    ),
}
EXIT_BALLOON_TEXT = (
    "O ícone foi fechado, mas o Baja Worker continua funcionando. Ele volta "
    "no próximo login, ou pelo menu Iniciar."
)


@dataclass(frozen=True)
class MenuState:
    """Which tray menu items to show (hidden ones are simply not listed)."""

    open_panel: bool = True
    pause: bool = False  # "Pausar"
    resume: bool = False  # "Retomar"
    stop: bool = False  # "Desligar o worker"
    start: bool = False  # "Ligar o worker"
    finish_connection: bool = False  # "Concluir conexão"
    exit_icon: bool = True  # "Sair do ícone"


@dataclass(frozen=True)
class TrayView:
    colour: Colour
    # The pt-BR state line without the "Baja Worker: " prefix.
    headline: str
    menu: MenuState = field(default_factory=MenuState)
    # Conditions that are true right now and deserve a notification.
    alerts: frozenset[Toast] = frozenset()

    @property
    def tooltip(self) -> str:
        return TOOLTIP_PREFIX + self.headline


def _videos(count: int) -> str:
    return "1 vídeo" if count == 1 else f"{count} vídeos"


def _busy_job_count(status: dict) -> int:
    """How many different videos the running tasks are about."""
    tasks = status.get("tasks") or []
    return len({task.get("job_id") or task.get("kind") for task in tasks})


def current_alerts(status: dict | None, service: ServiceState, stopped_by_us: bool) -> set[Toast]:
    alerts: set[Toast] = set()
    if service == ServiceState.STOPPED and not stopped_by_us:
        alerts.add(Toast.SERVICE_STOPPED_UNEXPECTEDLY)
    if status is None:
        return alerts
    if status.get("connection_step_pending"):
        alerts.add(Toast.CONNECTION_STEP_PENDING)
    if (status.get("storage") or {}).get("near_cap"):
        alerts.add(Toast.DISK_NEAR_CAP)
    if status.get("enrollment_refused"):
        alerts.add(Toast.ENROLLMENT_REFUSED)
    return alerts


def view_for(
    status: dict | None,
    service: ServiceState,
    *,
    stopped_by_us: bool = False,
    was_running: bool = False,
) -> TrayView:
    """Colour, headline and menu for the current facts.

    `status`: GET /api/status's JSON, or None when the page did not answer.
    `stopped_by_us`: the volunteer chose "Desligar" in this tray session.
    `was_running`: this tray saw the service running earlier, so a stop it
    did not ask for is "stopped by itself" (red), not just "off" (grey).
    """
    alerts = frozenset(current_alerts(status, service, stopped_by_us or not was_running))
    if service in (ServiceState.STOPPED, ServiceState.UNKNOWN, ServiceState.STOPPING):
        menu = MenuState(open_panel=False, start=service == ServiceState.STOPPED)
        if Toast.SERVICE_STOPPED_UNEXPECTEDLY in alerts:
            return TrayView(Colour.RED, "o serviço parou inesperadamente", menu, alerts)
        if service == ServiceState.UNKNOWN:
            return TrayView(Colour.GREY, "serviço não encontrado", menu, alerts)
        return TrayView(Colour.GREY, "desligado", menu, alerts)

    if status is None:
        # Service running (or starting) but its page is not up yet.
        return TrayView(Colour.YELLOW, "iniciando…", MenuState(stop=True), alerts)

    paused = bool(status.get("paused"))
    menu = MenuState(
        pause=not paused,
        resume=paused,
        stop=True,
        finish_connection=bool(status.get("connection_step_pending")),
    )
    return TrayView(*_colour_and_headline(status, paused), menu, alerts)


def _colour_and_headline(status: dict, paused: bool) -> tuple[Colour, str]:
    unreachable_for = status.get("unreachable_for_s")
    if status.get("enrollment_refused"):
        return Colour.RED, "token de inscrição recusado"
    if (status.get("storage") or {}).get("near_cap"):
        return Colour.RED, "disco quase cheio (limite de armazenamento)"
    if status.get("connection_step_pending"):
        return Colour.YELLOW, "conexão não concluída"
    if (
        status.get("enrolled")
        and unreachable_for is not None
        and unreachable_for > UNREACHABLE_ALERT_SECONDS
    ):
        return Colour.RED, "sem conexão com o servidor"
    if not status.get("enrolled"):
        return Colour.YELLOW, "aguardando o token de inscrição"
    busy = _busy_job_count(status)
    if paused:
        if busy:
            return Colour.YELLOW, f"pausando (terminando {_videos(busy)})"
        return Colour.YELLOW, "pausado"
    if busy:
        return Colour.GREEN, f"processando {_videos(busy)}"
    if status.get("last_contact_at") is None:
        return Colour.YELLOW, "conectando ao servidor…"
    return Colour.BLUE, "conectado, aguardando vídeos"


class ToastTracker:
    """Fires each notification once, when its condition becomes true.

    A condition that stays true does not fire again; one that goes away and
    comes back fires again (e.g. the disk filled up a second time).
    """

    def __init__(self) -> None:
        self._active: set[Toast] = set()

    def new_toasts(self, view: TrayView) -> list[Toast]:
        fresh = sorted(view.alerts - self._active)
        self._active = set(view.alerts)
        return fresh
