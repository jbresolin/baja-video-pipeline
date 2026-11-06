"""The display name field on the token form (LIVESTACK-42 item 7).

Host names often mean nothing ("DESKTOP-8F3K2LQ"), so the volunteer may
name the PC when entering the token. The field is prefilled with the
config's `worker_name` (the host name until someone changes it) and sent
to POST /enroll as `worker_name`; the orchestrator shows it on every page
and an admin may change it later on Admin → Workers. The host name itself
is always sent separately (`hostname`).

Kept apart from setup_page.py on purpose: that page is being rewritten
(LIVESTACK-41), and this is the whole of the display-name change on the
worker, so it moves over in one piece.
"""

import html

from worker.config import WorkerConfig

FORM_FIELD_NAME = "worker_name"
MAX_LENGTH = 100


def display_name_input(config: WorkerConfig) -> str:
    """The form field, prefilled with the current name (the host name by default)."""
    return (
        "<label>Nome desta máquina (como a equipe Baja vai vê-la)"
        f"<input name='{FORM_FIELD_NAME}' maxlength='{MAX_LENGTH}' required"
        f" value='{html.escape(config.worker_name, quote=True)}'></label>"
    )


def display_name_change(fields: dict[str, str]) -> dict[str, str]:
    """The config change the submitted form asks for: {"worker_name": ...} or nothing.

    Runs of spaces become one. A blank or missing value keeps the current
    name, so a form without the field (an older page) changes nothing.
    """
    typed = " ".join(fields.get(FORM_FIELD_NAME, "").split())[:MAX_LENGTH]
    return {"worker_name": typed} if typed else {}
