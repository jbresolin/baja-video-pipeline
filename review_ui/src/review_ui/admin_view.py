"""Words and small helpers for the admin pages (pt-BR: the operator and reviewers are Brazilian).

Admin pages are plain HTML forms. After a successful change the browser is
redirected back to the page with `?feito=<key>`, and the page shows the
fixed sentence for that key (never text taken from the URL). When the
orchestrator refuses a change, the page is shown again with the reason.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from review_ui.orchestrator_client import OrchestratorError
from shared.models import Event

# The admin sub-navigation: (path under /review/admin/, label, area for the audit).
SECTIONS = [
    ("users", "Usuários"),
    ("contacts", "Contatos"),
    ("provas", "Provas"),
    ("teams", "Equipes"),
    ("enrollment", "Cadastro de PCs"),
    ("workers", "Workers"),
    ("settings", "Configurações"),
]

ROLE_LABELS = {"reviewer": "Revisor", "admin": "Admin"}
CONTACT_STATUS_LABELS = {
    "allowed": "Permitido",
    "blocked": "Bloqueado",
    "unknown": "Desconhecido",
}
TOKEN_STATUS_LABELS = {
    "unused": "Não usado",
    "used": "Usado",
    "expired": "Expirado",
    "revoked": "Revogado",
}
WORKER_ROLE_LABELS = {"master": "Master", "worker": "Worker", "studio": "Estúdio"}
INTAKE_MODE_LABELS = {
    "open": "Aberto (todos, menos bloqueados)",
    "allowlist": "Só permitidos",
}

# ?feito=<key> → what the page says after a successful change.
DONE_MESSAGES = {
    "user_created": "Usuário criado.",
    "user_updated": "Usuário atualizado.",
    "contact_created": "Contato adicionado.",
    "contact_updated": "Contato atualizado.",
    "prova_created": "Prova adicionada.",
    "prova_updated": "Prova atualizada.",
    "provas_reordered": "Ordem das provas salva.",
    "team_saved": "Equipe salva.",
    "team_deleted": "Equipe removida (ou correção desfeita).",
    "synced": "Sincronizado com o site de resultados.",
    "token_revoked": "Token revogado.",
    "worker_revoked": "Worker revogado. Remova o PC do tailnet no console do Tailscale.",
    "settings_updated": "Configurações salvas.",
}

# Orchestrator error codes in words. The orchestrator's own English
# sentence is added after it, for details (which user, which prova...).
ERROR_TEXTS = {
    "already_exists": "Já existe.",
    "conflict": "Não é possível agora.",
    "not_found": "Não encontrado (talvez alguém removeu agora).",
    "validation_error": "Algum campo está inválido.",
    "forbidden": "Só administradores podem fazer isso.",
}


def done_message(key: str | None) -> str | None:
    return DONE_MESSAGES.get(key or "")


def error_text(error: OrchestratorError) -> str:
    words = ERROR_TEXTS.get(error.code, "O orquestrador recusou a alteração.")
    return f"{words} ({error.message})"


def redirect_done(path: str, key: str) -> RedirectResponse:
    """Back to the page after a change (303: the browser follows with GET)."""
    return RedirectResponse(f"{path}?feito={key}", status_code=303)


def apply_change(
    action: Callable[[], object],
    done_path: str,
    done_key: str,
    show_page: Callable[[str, int], Response],
) -> Response:
    """Run one orchestrator change, then go back to the page.

    Success: redirect to `done_path?feito=<done_key>`. A refusal the admin
    can fix (400, 404, 409, 422): `show_page(reason, status)` shows the page
    again with the reason. 401 (signed out) and 403 (not an admin) are not
    caught: the app's error handler sends the browser to the login page or
    shows "not allowed".
    """
    try:
        action()
    except OrchestratorError as error:
        if error.status_code in (401, 403):
            raise
        return show_page(error_text(error), error.status_code)
    return redirect_done(done_path, done_key)


def form_text(form: dict[str, str], name: str) -> str | None:
    """A text field, stripped; None when left empty."""
    value = form.get(name, "").strip()
    return value or None


def form_int(form: dict[str, str], name: str) -> int | None:
    value = form_text(form, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class AuditLine:
    at: datetime
    who: str
    text: str


def audit_lines(events: list[Event]) -> list[AuditLine]:
    """The "Últimas alterações" box: when, who ("admin:joao" → "joao") and what."""
    lines = []
    for event in events:
        who = event.actor.removeprefix("admin:")
        if event.actor == "admin":
            who = "token de admin"
        lines.append(AuditLine(at=event.at, who=who, text=event.reason or event.kind))
    return lines


def admin_context(request: Request, section: str, **values) -> dict:
    """What every admin page template needs besides its own data."""
    return {
        "active_nav": "admin",
        "sections": SECTIONS,
        "section": section,
        "done": done_message(request.query_params.get("feito")),
        "error": None,
        **values,
    }
