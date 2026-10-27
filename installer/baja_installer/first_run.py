"""`BajaWorker.exe --first-run`: put a freshly enrolled PC on the Baja tailnet.

The installer starts this right after installing (already elevated); the
Start-menu entry "Baja Worker - concluir conexão" starts it again later.
It talks to the volunteer in Portuguese in its console window.

 1. open the setup page (http://localhost:8700) in the volunteer's browser;
 2. wait until the service has enrolled: worker.json holds a worker id and
    tailscale-authkey.txt sits next to it (worker/enrollment.py writes both);
 3. if this PC is on another tailnet, ask before switching (worker_join.md);
 4. `tailscale up --auth-key=file:… --hostname=… --unattended`, then delete
    the key file whatever the outcome (the key is single-use either way);
 5. store the Tailscale IPv4 as `tailnet_address` and Caddy's tailnet
    listener (`tailnet_base_url` from /enroll) as `orchestrator_url`;
 6. restart the service so it starts again with both.

Why not a PowerShell script: this is the same flow, but in Python it is
frozen into the exe that is already installed, and tests/installer runs
every branch of it on Linux with a fake `tailscale`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from baja_installer.tailscale_cli import TailscaleCli, TailscaleStatus
from shared.models import WorkerRole
from worker.config import WorkerConfig, load_config, save_config
from worker.enrollment import TAILSCALE_AUTH_KEY_FILE_NAME

EXIT_JOINED = 0
EXIT_JOIN_FAILED = 3
EXIT_CANCELLED = 4
POLL_SECONDS = 2.0
STUDIO_HOSTNAME = "baja-studio"
# Workers get "baja-w-" plus the start of their orchestrator-made id: unique
# enough for a fleet of dozens, and never the volunteer's or the PC's name,
# which would end up in public certificate logs (infra/tailnet/README.md §2).
WORKER_HOSTNAME_PREFIX = "baja-w-"
WORKER_ID_CHARACTERS_IN_HOSTNAME = 8
LOCAL_ORCHESTRATOR_URL = WorkerConfig.model_fields["orchestrator_url"].default


def tailscale_hostname(config: WorkerConfig) -> str:
    if config.role == WorkerRole.STUDIO:
        return STUDIO_HOSTNAME
    worker_id = re.sub(r"[^a-z0-9]", "", (config.worker_id or "").lower())
    return WORKER_HOSTNAME_PREFIX + worker_id[:WORKER_ID_CHARACTERS_IN_HOSTNAME]


def config_after_join(config: WorkerConfig, status: TailscaleStatus) -> WorkerConfig:
    """What the service needs to know once Tailscale is up.

    `orchestrator_url` is replaced only while it still has the built-in
    local default; a URL someone typed into worker.json stays.
    """
    updates: dict[str, object] = {"tailnet_address": status.ipv4}
    if config.orchestrator_url == LOCAL_ORCHESTRATOR_URL and config.tailnet_base_url:
        updates["orchestrator_url"] = config.tailnet_base_url
    return config.model_copy(update=updates)


def config_without_enrollment(config: WorkerConfig) -> WorkerConfig:
    """Forget the identity so the setup page asks for a new token."""
    return config.model_copy(
        update={
            "worker_id": None,
            "bearer_token": None,
            "role": None,
            "tailnet_base_url": None,
            "enrollment_token": None,
        }
    )


@dataclass
class FirstRunEnvironment:
    """Everything the flow touches outside worker.json; tests pass fakes."""

    config_path: Path
    tailscale: TailscaleCli
    open_setup_page: Callable[[], None]
    restart_service: Callable[[], None]
    ask_yes_no: Callable[[str], bool]
    say: Callable[[str], None]
    sleep: Callable[[float], None]
    poll_seconds: float = POLL_SECONDS

    @property
    def key_path(self) -> Path:
        return self.config_path.parent / TAILSCALE_AUTH_KEY_FILE_NAME


def run_first_run(env: FirstRunEnvironment) -> int:
    env.say("Abrindo a página de configuração do Baja Worker no navegador…")
    env.open_setup_page()
    config = wait_for_enrollment(env)
    if env.key_path.exists():
        return join_tailnet(env, config)
    return check_existing_join(env, config)


def wait_for_enrollment(env: FirstRunEnvironment) -> WorkerConfig:
    """Block until worker.json says "enrolled". Closing the window stops it."""
    told_the_volunteer = False
    while True:
        config = load_config(env.config_path)
        if config.is_enrolled:
            return config
        if not told_the_volunteer:
            env.say(
                "Aguardando: cole o token de inscrição na página de configuração "
                'e clique em "Save and connect". Pode deixar esta janela aberta.'
            )
            told_the_volunteer = True
        env.sleep(env.poll_seconds)


def join_tailnet(env: FirstRunEnvironment, config: WorkerConfig) -> int:
    status = env.tailscale.status()
    if status is not None and status.on_baja_tailnet:
        env.say("Este PC já está na rede Baja.")
        env.key_path.unlink(missing_ok=True)  # unused, and useless now
        return finish(env, config, status)

    switching_tailnet = status is not None and status.is_running
    if switching_tailnet and not env.ask_yes_no(
        "Este PC está conectado a outra rede Tailscale. Entrar na rede Baja vai "
        "desconectá-lo dela. Continuar?"
    ):
        # The key stays: running this again within the hour can still use it.
        env.say('Cancelado. Nada foi alterado. Rode "Baja Worker - concluir conexão" depois.')
        return EXIT_CANCELLED

    hostname = tailscale_hostname(config)
    env.say(f"Conectando à rede Baja como {hostname}…")
    try:
        finished = env.tailscale.up(env.key_path, hostname, switching_tailnet=switching_tailnet)
    finally:
        env.key_path.unlink(missing_ok=True)  # single-use: spent or not, it goes

    status = env.tailscale.status() if finished.returncode == 0 else None
    if status is None or not status.on_baja_tailnet or not status.ipv4:
        return report_join_failure(env, config, finished.stderr or finished.stdout or "")
    return finish(env, config, status)


def check_existing_join(env: FirstRunEnvironment, config: WorkerConfig) -> int:
    """Enrolled, but the key file is gone: joined earlier, or the join failed."""
    status = env.tailscale.status()
    if status is not None and status.on_baja_tailnet and status.ipv4:
        env.say("Este PC já está na rede Baja.")
        return finish(env, config, status)
    return report_join_failure(env, config, "a chave de acesso já foi usada")


def finish(env: FirstRunEnvironment, config: WorkerConfig, status: TailscaleStatus) -> int:
    updated = config_after_join(config, status)
    if updated != config:
        save_config(env.config_path, updated)
    env.say("Reiniciando o serviço BajaWorker…")
    env.restart_service()
    env.say(
        f"Pronto! Conectado à rede Baja ({status.dns_name or status.ipv4}). "
        "O status aparece em http://localhost:8700."
    )
    return EXIT_JOINED


def report_join_failure(env: FirstRunEnvironment, config: WorkerConfig, detail: str) -> int:
    env.say("Não foi possível entrar na rede Baja.")
    if detail.strip():
        env.say(f"Detalhe: {detail.strip()}")
    env.say("A chave de acesso só vale uma vez, então é preciso um novo token de inscrição.")
    if env.ask_yes_no("Apagar o registro deste PC para usar um novo token?"):
        save_config(env.config_path, config_without_enrollment(load_config(env.config_path)))
        env.restart_service()
        env.say("Registro apagado. Peça um novo token à equipe Baja e rode isto de novo.")
    return EXIT_JOIN_FAILED
