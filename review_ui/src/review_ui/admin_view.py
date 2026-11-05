"""Labels and small helpers for the admin pages (words from the pt-BR catalog).

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
from markupsafe import Markup

from review_ui.catalog import text
from review_ui.errors import error_markup
from review_ui.event_log_view import kind_text
from review_ui.orchestrator_client import OrchestratorError
from shared.models import Event, UserRole, WorkerRole

# The admin sub-navigation: (path under /review/admin/, label).
SECTION_PATHS = ["users", "contacts", "provas", "teams", "enrollment", "workers", "settings"]
SECTIONS = [(path, text(f"admin.section.{path}")) for path in SECTION_PATHS]


def _labels(prefix: str, values) -> dict[str, str]:
    """{value: catalog text of "<prefix>.<value>"} for each value, in order."""
    return {str(value): text(f"{prefix}.{value}") for value in values}


ROLE_LABELS = _labels("role", [role.value for role in UserRole])
CONTACT_STATUS_LABELS = _labels("contact_status", ["allowed", "blocked", "unknown"])
TOKEN_STATUS_LABELS = _labels("token_status", ["unused", "used", "expired", "revoked"])
WORKER_ROLE_LABELS = _labels("worker_role", [role.value for role in WorkerRole])
INTAKE_MODE_LABELS = _labels("intake_mode", ["open", "allowlist"])

# ?feito=<key> → what the page says after a successful change (catalog "done.<key>").
DONE_KEYS = {
    "user_created",
    "user_updated",
    "contact_created",
    "contact_updated",
    "prova_created",
    "prova_updated",
    "provas_reordered",
    "team_saved",
    "team_deleted",
    "synced",
    "token_revoked",
    "worker_revoked",
    "settings_updated",
}


def done_message(key: str | None) -> str | None:
    """The fixed sentence for a ?feito= key; never text taken from the URL."""
    return text(f"done.{key}") if key in DONE_KEYS else None


def orchestrator_error_text(error: OrchestratorError) -> Markup:
    """The refusal in words, with the orchestrator's sentence (which user, which prova...)."""
    return error_markup(error.code, error.message)


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
        return show_page(orchestrator_error_text(error), error.status_code)
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
    # The kind of change in words ("usuário alterado"), or the kind's own
    # name when the catalog has no words for it.
    what: str
    what_is_kind_name: bool
    # The orchestrator's description of the change (English), or None.
    detail: str | None


def audit_lines(events: list[Event]) -> list[AuditLine]:
    """The "Últimas alterações" box: when, who ("admin:joao" → "joao") and what."""
    lines = []
    for event in events:
        who = event.actor.removeprefix("admin:")
        if event.actor == "admin":
            who = text("actor.admin_token")
        words = kind_text(event.kind)
        lines.append(
            AuditLine(
                at=event.at,
                who=who,
                what=words or event.kind,
                what_is_kind_name=words is None,
                detail=event.reason,
            )
        )
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
