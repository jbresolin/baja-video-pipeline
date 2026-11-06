"""The localhost page's other views: broken worker.json, token form, studio folder form.

worker.setup_page picks one view per request (and per in-place refresh)
and splits it in two parts:
  * live HTML (`#live`): headline, state line and messages. Replaced on
    every refresh; never holds a form field;
  * form HTML (`#forms`) with a key ("token", "studio_folder" or "none").
    The page script replaces it only when the key changes and nobody is
    typing in it (worker/page_assets/page.js, item 14).
All text is pt-BR (volunteers).
"""

import html
from dataclasses import dataclass
from pathlib import Path

from worker.config import GIGABYTE, WorkerConfig
from worker.config_problems import ConfigProblem
from worker.display_name import display_name_input
from worker.status_page import re_register_button, state_line
from worker.studio.clip_folder import suggested_clip_root

NO_FORM = "none"
TOKEN_FORM = "token"
STUDIO_FOLDER_FORM = "studio_folder"

SETUP_TITLE = "Configuração do Baja Worker"
STATUS_TITLE = "Baja Worker"


@dataclass(frozen=True)
class PageParts:
    title: str
    live_html: str
    form_key: str = NO_FORM
    form_html: str = ""

    def as_json(self) -> dict:
        return {
            "title": self.title,
            "live_html": self.live_html,
            "form_key": self.form_key,
            "form_html": self.form_html,
        }


def config_problem_parts(problem: ConfigProblem, document: dict) -> PageParts:
    """worker.json is broken: what, where, and how to fix it (item 3)."""
    escape = html.escape
    where = ""
    if problem.line is not None:
        position = f"linha {problem.line}" + (
            f", coluna {problem.column}" if problem.column is not None else ""
        )
        where = f"<p>Onde: <strong>{escape(position)}</strong></p>"
    shown_line = ""
    if problem.line_text is not None:
        marker = ""
        if problem.column is not None:
            # A caret under the exact column, like a compiler message.
            marker = "\n" + " " * (problem.column - 1) + "^"
        shown_line = f"<pre class='line'>{escape(problem.line_text + marker)}</pre>"
    fix = ""
    if problem.fixable_by_re_registering:
        fix = f"<p>{re_register_button()}</p>"
    live = (
        f"<h1>{STATUS_TITLE}</h1>{state_line(document)}"
        "<div class='notice red'>"
        f"<p><strong>{escape(problem.message)}</strong></p>"
        f"<p>Arquivo: <code class='path'>{escape(problem.file)}</code></p>"
        f"{where}{shown_line}<p>{escape(problem.hint)}</p>{fix}"
        "<p>Salve o arquivo corrigido: esta página percebe sozinha em poucos segundos "
        "e o Baja Worker continua. Não precisa reiniciar.</p></div>"
    )
    return PageParts(STATUS_TITLE, live)


def token_form_parts(config: WorkerConfig, config_path: Path, message: str) -> PageParts:
    """Before enrollment: token, working folder and storage cap (design §9)."""
    escape = html.escape
    live = f"<h1>{SETUP_TITLE}</h1>" + (f"<p class='err'>{escape(message)}</p>" if message else "")
    folder = config.resolved_working_folder(config_path)
    cap_gb = config.storage_cap_bytes / GIGABYTE
    form = (
        "<form method='post' action='/setup'>"
        "<label>Token de inscrição (a equipe Baja manda)"
        "<input name='token' required autocomplete='off'></label>"
        # LIVESTACK-42 item 7: the display name, prefilled with the host name.
        f"{display_name_input(config)}"
        "<label>Pasta de trabalho (os vídeos ficam aqui; caminho completo, como D:\\BajaWorker)"
        f"<input name='folder' required value='{escape(str(folder), quote=True)}'></label>"
        "<label>Limite de espaço em GB (o Baja Worker nunca usa mais que isso)"
        f"<input name='cap_gb' type='number' min='1' step='1' required value='{cap_gb:.0f}'>"
        "</label><button type='submit'>Salvar e conectar</button></form>"
    )
    return PageParts(SETUP_TITLE, live, TOKEN_FORM, form)


def studio_folder_parts(config: WorkerConfig, config_path: Path, message: str) -> PageParts:
    """A studio right after enrollment: where OBS will play the clips from (item 5)."""
    escape = html.escape
    live = (
        f"<h1>{SETUP_TITLE}</h1>"
        "<p>Este PC é o <strong>estúdio</strong>: ele recebe os clipes prontos e o OBS "
        "os toca. Escolha a pasta onde os clipes vão ficar. O Baja Worker cria a pasta "
        "se ela não existir e deixa os usuários deste PC (e o OBS) lerem os vídeos.</p>"
        + (f"<p class='err'>{escape(message)}</p>" if message else "")
    )
    suggestion = suggested_clip_root(config.resolved_working_folder(config_path))
    form = (
        "<form method='post' action='/setup/studio-folder'>"
        "<label>Pasta dos clipes (caminho completo)"
        f"<input name='clip_folder' required value='{escape(suggestion, quote=True)}'></label>"
        "<button type='submit'>Salvar pasta</button></form>"
    )
    return PageParts(SETUP_TITLE, live, STUDIO_FOLDER_FORM, form)
