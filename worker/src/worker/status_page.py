"""The status page at http://localhost:8700 once the worker is enrolled (pt-BR, for novices).

LIVESTACK-38 item 9. From top to bottom:
  * one big state line, in the same colour as the tray icon (the colour
    and sentence come from worker.tray.state, so the two always agree);
  * what it is doing now (video, prova, car, % when known);
  * space used vs. the cap, held raws (and archived clips, when known),
    last contact with the server;
  * the same "Pausar"/"Retomar" and "Desligar o worker" buttons as the
    tray (they call the localhost JSON API, worker.local_api);
  * technical details (worker id, URLs, version, last error) in a
    collapsed "Detalhes" section.
"""

import html
from datetime import datetime

from worker.tray.state import Colour, ServiceState, view_for

GIGABYTE = 1024**3
COLOUR_CSS = {
    Colour.GREEN: "#2ea043",
    Colour.BLUE: "#386db4",
    Colour.YELLOW: "#d9a400",
    Colour.RED: "#cd3232",
    Colour.GREY: "#828282",
}
TASK_WORDS = {
    "download_raw": "Baixando vídeo",
    "make_proxy": "Preparando a prévia",
    "process": "Editando o clipe",
    "deliver": "Recebendo clipe",
}
STATUS_STYLE = (
    ".state{font-size:1.6em;font-weight:700;padding:.6em .8em;border-radius:8px;color:#fff}"
    ".facts td:first-child{color:#555;padding-right:1.5em}"
    ".meter{background:#ddd;border-radius:4px;height:.8em;width:16em}"
    ".meter span{display:block;height:100%;border-radius:4px;background:#386db4}"
    "details{margin-top:2em}summary{cursor:pointer}"
    ".actions button{margin-right:1em}"
)
# Buttons POST JSON to the local API (a plain form could not send JSON,
# which is the point: see worker.local_api), then reload the page.
STATUS_SCRIPT = """
async function act(name, question) {
  if (question && !confirm(question)) return;
  await fetch('/api/' + name, {method: 'POST', headers: {'Content-Type': 'application/json'},
                               body: '{}'}).catch(() => null);
  setTimeout(() => location.reload(), 500);
}
"""


def _gb(byte_count: int | None) -> str:
    if byte_count is None:
        return "?"
    return f"{byte_count / GIGABYTE:.1f} GB".replace(".", ",")


def _ago(iso_time: str | None, now: datetime) -> str:
    if not iso_time:
        return "ainda não houve contato"
    moment = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return f"há {seconds} s"
    if seconds < 3600:
        return f"há {seconds // 60} min"
    return f"há {seconds // 3600} h"


def describe_task(task: dict) -> str:
    words = TASK_WORDS.get(task.get("kind") or "", task.get("kind") or "Tarefa")
    parts = []
    if task.get("prova"):
        parts.append(str(task["prova"]))
    if task.get("car_number") is not None:
        parts.append(f"carro {task['car_number']}")
    if not parts and task.get("job_id"):
        parts.append(f"vídeo {str(task['job_id'])[:8]}")
    text = words + (" — " + ", ".join(parts) if parts else "")
    if task.get("percent") is not None:
        text += f" ({task['percent']:.0f}%)"
    return text


def render_status_body(document: dict, now: datetime) -> str:
    """The page body (inside <body>), from GET /api/status's document."""
    view = view_for(document, ServiceState.RUNNING)
    escape = html.escape
    tasks = document.get("tasks") or []
    doing = "".join(f"<li>{escape(describe_task(task))}</li>" for task in tasks)
    doing_html = f"<ul>{doing}</ul>" if doing else "<p>Nenhuma tarefa agora.</p>"

    storage = document.get("storage") or {}
    used, cap = storage.get("used_bytes"), storage.get("cap_bytes")
    percent = min(100, round(100 * used / cap)) if used is not None and cap else 0
    rows = [
        (
            "Espaço usado",
            f"{_gb(used)} de {_gb(cap)}"
            f"<div class='meter'><span style='width:{percent}%'></span></div>",
        ),
        ("Vídeos guardados", escape(str(document.get("held_raws") or 0))),
    ]
    # Slot for the clip archive (LIVESTACK-38 item 7): shown only once the
    # worker reports these numbers.
    if document.get("clips_held") is not None:
        archived = document.get("clips_archived")
        text = str(document["clips_held"])
        if archived is not None:
            text += f" (arquivados pelo master: {archived})"
        rows.append(("Clipes guardados", escape(text)))
    last_contact = _ago(document.get("last_contact_at"), now)
    rows.append(("Último contato com o servidor", escape(last_contact)))
    facts = "".join(f"<tr><td>{label}</td><td>{value}</td></tr>" for label, value in rows)

    pause_button = (
        "<button onclick=\"act('resume')\">Retomar</button>"
        if document.get("paused")
        else "<button onclick=\"act('pause')\">Pausar</button>"
    )
    stop_question = (
        "Desligar o Baja Worker? Para ligar de novo use o ícone perto do relógio ou reinicie o PC."
    )
    actions = (
        f"<p class='actions'>{pause_button}"
        f"<button onclick=\"act('stop', '{stop_question}')\">Desligar o worker</button></p>"
    )
    return (
        f"<div class='state' style='background:{COLOUR_CSS[view.colour]}'>"
        f"{escape(view.headline[:1].upper() + view.headline[1:])}</div>"
        f"<h2>Agora</h2>{doing_html}<table class='facts'>{facts}</table>{actions}"
        f"{_details(document)}<script>{STATUS_SCRIPT}</script>"
    )


def _details(document: dict) -> str:
    details = document.get("details") or {}
    rows = {
        "Worker": f"{details.get('worker_name')} ({details.get('worker_id')})",
        "Papel": details.get("role"),
        "Servidor": details.get("orchestrator_url"),
        "Rede Baja (Caddy)": details.get("tailnet_base_url"),
        "Versão": details.get("version"),
        "Último erro": details.get("last_error") or "—",
        "Pausado": "sim" if document.get("paused") else "não",
        **(details.get("notes") or {}),
    }
    table = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>"
        for key, value in rows.items()
    )
    return f"<details><summary>Detalhes</summary><table>{table}</table></details>"
