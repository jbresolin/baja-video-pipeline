"""The status part of http://localhost:8700 once the worker is enrolled (pt-BR, for novices).

LIVESTACK-38 item 9, extended in LIVESTACK-41. From top to bottom:
  * one big state line, in the same colour as the tray icon (the colour
    and sentence come from worker.tray.state, so the two always agree);
  * when something needs the volunteer: not on the Baja tailnet (with what
    fixes it), or this PC removed from the team ("Registrar de novo");
  * what it is doing now (video, prova, car, % when known);
  * space used vs. the cap, held raws and clips, last contact;
  * studio: the clip folder and the full path of latest.m3u for OBS, with
    a copy button;
  * the same "Pausar"/"Retomar" and "Desligar o worker" buttons as the
    tray (they call the localhost JSON API, worker.local_api);
  * technical details (worker id, URLs, version, working folder, last
    error, all in pt-BR; the board's internal notes are not shown) and
    "Registrar de novo" in a collapsed "Detalhes" section.

Everything here is the page's *live* region: worker.setup_page swaps it in
place every few seconds (item 14). It must never contain a form field.
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
    "archive_clip": "Arquivando clipe",
    "upload_original": "Enviando o original",
}
STATUS_STYLE = (
    ".state{font-size:1.6em;font-weight:700;padding:.6em .8em;border-radius:8px;color:#fff}"
    ".facts td:first-child{color:#555;padding-right:1.5em}"
    ".meter{background:#ddd;border-radius:4px;height:.8em;width:16em}"
    ".meter span{display:block;height:100%;border-radius:4px;background:#386db4}"
    "details{margin-top:2em}summary{cursor:pointer}"
    ".actions button{margin-right:1em}"
    ".notice{border-left:6px solid #d9a400;background:#fff8e1;padding:.6em 1em;margin:1em 0}"
    ".notice.red{border-color:#cd3232;background:#fdecea}"
    "code.path{background:#eee;padding:.1em .3em;word-break:break-all}"
)
RE_REGISTER_QUESTION = (
    "Registrar este PC de novo? Ele esquece a identidade atual na equipe Baja e "
    "volta para a página do token (peça um token novo à equipe). Os vídeos "
    "guardados e as configurações ficam."
)
STOP_QUESTION = (
    "Desligar o Baja Worker? Para ligar de novo use o ícone perto do relógio ou reinicie o PC."
)
NOT_ON_TAILNET_HEADLINE = "Esta máquina não está conectada à rede Baja."
# What each kind of tailnet wait means (worker.status.TailnetProblemKind).
TAILNET_EXPLANATIONS = {
    "tailscale_not_answering": (
        "O Tailscale (a rede privada da equipe) ainda não respondeu. Logo depois de "
        "ligar o PC isso é normal: espere um minuto."
    ),
    "not_connected": (
        "O Tailscale está desconectado ou saiu da rede (por exemplo, o PC foi "
        "removido da rede Baja)."
    ),
    "address_not_on_this_pc": (
        "O endereço salvo ({saved}) não é mais deste PC: o Tailscale está agora com "
        "{current}. O PC está em outra rede Tailscale, ou entrou de novo como outro aparelho."
    ),
}
TAILNET_FIX = (
    "Para resolver: clique com o botão direito no ícone perto do relógio e escolha "
    '"Concluir conexão" (ou, no menu Iniciar, "Baja Worker - concluir conexão"). '
    'Se a equipe disser que este PC foi removido da rede, use "Registrar de novo" '
    "com um token novo."
)
IDENTITY_REJECTED_TEXT = (
    "O servidor não reconhece mais este PC: ele foi removido da equipe Baja. Para "
    'voltar, peça um token novo à equipe e use "Registrar de novo". Os vídeos '
    "guardados neste PC ficam."
)
OBS_HINT = (
    'No OBS, adicione uma fonte "Fonte de vídeo VLC" (VLC Video Source) e coloque '
    "nela a playlist acima."
)


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


def state_line(document: dict) -> str:
    """The big coloured line, shared by every view of the page."""
    view = view_for(document, ServiceState.RUNNING)
    headline = view.headline[:1].upper() + view.headline[1:]
    return (
        f"<div class='state' style='background:{COLOUR_CSS[view.colour]}'>"
        f"{html.escape(headline)}</div>"
    )


def action_button(name: str, label: str, question: str | None = None) -> str:
    """A button that POSTs to /api/<name> (worker/page_assets/page.js `act`).

    The confirmation question travels in a data-* attribute, so no text
    is ever pasted into JavaScript code.
    """
    question_attribute = f" data-question='{html.escape(question, quote=True)}'" if question else ""
    return (
        f"<button type='button' data-action='{name}'{question_attribute} "
        f"onclick='actFrom(this)'>{html.escape(label)}</button>"
    )


def re_register_button() -> str:
    return action_button("re-register", "Registrar de novo", RE_REGISTER_QUESTION)


def copy_button(text: str) -> str:
    """Copies `text` (worker/page_assets/page.js `copyFrom`)."""
    return (
        f"<button type='button' data-copy='{html.escape(text, quote=True)}' "
        "onclick='copyFrom(this)'>Copiar</button>"
    )


def render_status_body(document: dict, now: datetime) -> str:
    """The live region of an enrolled worker's page, from GET /api/status's document."""
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
    rows.extend(_studio_rows(document.get("studio")))
    facts = "".join(f"<tr><td>{label}</td><td>{value}</td></tr>" for label, value in rows)

    pause_button = (
        action_button("resume", "Retomar")
        if document.get("paused")
        else action_button("pause", "Pausar")
    )
    actions = (
        f"<p class='actions'>{pause_button}"
        f"{action_button('stop', 'Desligar o worker', STOP_QUESTION)}</p>"
    )
    return (
        "<h1>Baja Worker</h1>"
        f"{state_line(document)}{_notices(document)}"
        f"<h2>Agora</h2>{doing_html}<table class='facts'>{facts}</table>"
        f"{_studio_hint(document.get('studio'))}{actions}{_details(document, now)}"
    )


def _notices(document: dict) -> str:
    """Boxes for what the volunteer must act on, with the action right there."""
    notices = []
    if document.get("identity_rejected"):
        notices.append(
            f"<div class='notice red'><p>{html.escape(IDENTITY_REJECTED_TEXT)}</p>"
            f"<p>{re_register_button()}</p></div>"
        )
    problem = document.get("tailnet_problem")
    if problem:
        explanation = TAILNET_EXPLANATIONS.get(problem.get("kind"), "").format(
            saved=problem.get("saved_address") or "?",
            current=problem.get("current_address") or "outro endereço",
        )
        notices.append(
            f"<div class='notice'><p><strong>{html.escape(NOT_ON_TAILNET_HEADLINE)}</strong></p>"
            f"<p>{html.escape(explanation)}</p><p>{html.escape(TAILNET_FIX)}</p>"
            f"<p>{re_register_button()}</p></div>"
        )
    return "".join(notices)


def _studio_rows(studio: dict | None) -> list[tuple[str, str]]:
    if not studio:
        return []
    clip_root, playlist = studio["clip_root"], studio["playlist_path"]
    return [
        ("Pasta dos clipes", f"<code class='path'>{html.escape(clip_root)}</code>"),
        (
            "Playlist para o OBS",
            f"<code class='path'>{html.escape(playlist)}</code> {copy_button(playlist)}",
        ),
    ]


def _studio_hint(studio: dict | None) -> str:
    if not studio:
        return ""
    hint = f"<p>{html.escape(OBS_HINT)}</p>"
    if studio.get("folder_problem"):
        hint += f"<div class='notice red'><p>{html.escape(studio['folder_problem'])}</p></div>"
    return hint


def describe_last_error(details: dict, now: datetime) -> str:
    """ "Último erro" for a volunteer: the error, how long ago, and whether it is over.

    A heartbeat that failed while the VPS restarted stays in the record, but
    once the server answers again it reads "resolvido", so nobody chases a
    problem that is already gone.
    """
    error = details.get("last_error")
    if not error:
        return "—"
    when = _ago(details.get("last_error_at"), now) if details.get("last_error_at") else ""
    if details.get("last_error_resolved_at"):
        return f"{error} ({when}; resolvido)" if when else f"{error} (resolvido)"
    return f"{error} ({when})" if when else error


def _details(document: dict, now: datetime) -> str:
    details = document.get("details") or {}
    rows = {
        "Worker": f"{details.get('worker_name')} ({details.get('worker_id')})",
        "Papel": details.get("role"),
        "Servidor": details.get("orchestrator_url"),
        "Rede Baja (Caddy)": details.get("tailnet_base_url"),
        "Endereço na rede": details.get("tailnet_address") or "—",
        "Rede": (
            "compartilhada (a rede Tailscale deste PC; os outros PCs não o alcançam)"
            if details.get("shared_tailnet")
            else "Baja"
        ),
        "Versão": details.get("version"),
        "Pasta de trabalho": details.get("working_folder") or "—",
        "Último erro": describe_last_error(details, now),
        "Pausado": "sim" if document.get("paused") else "não",
    }
    table = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>"
        for key, value in rows.items()
    )
    re_register = ""
    if document.get("can_re_register"):
        re_register = (
            "<p>Trocar a identidade deste PC na equipe Baja (com um token novo): "
            f"{re_register_button()}</p>"
        )
    return f"<details><summary>Detalhes</summary><table>{table}</table>{re_register}</details>"
