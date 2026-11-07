"""Filling the welcome message's placeholders, in Python and in the page script.

The orchestrator fills {nome} and {prova} when it sends the message
(shared/welcome_message.py). The Settings page shows a preview filled
with example values, first rendered by the server with that same Python
function, then refreshed while João types by static/js/welcome.js. If the
two disagreed, the preview would show a text the contact never gets.

So every case below runs through both: the Python function directly, and
the JavaScript one in Node (skipped when Node is not installed).
"""

import json
import shutil
import subprocess

import pytest

from review_ui.templating import STATIC_DIR
from shared.welcome_message import (
    DEFAULT_WELCOME_MESSAGE,
    fill_welcome_message,
    normalize_welcome_message,
)

# (message as typed, the contact's values, what is sent)
FILL_CASES = [
    # A name with accents and a default prova, both filled in.
    (
        "Olá {nome}! Sua prova: {prova}.",
        {"nome": "Conceição Araújo", "prova": "Enduro de Resistência"},
        "Olá Conceição Araújo! Sua prova: Enduro de Resistência.",
    ),
    # No name: the placeholder and the space before it go ("Olá!", not "Olá !").
    ("Olá {nome}!", {"nome": None, "prova": "Enduro"}, "Olá!"),
    ("Olá {nome}!", {}, "Olá!"),
    # A name of only spaces counts as no name.
    ("Olá {nome}!", {"nome": "   "}, "Olá!"),
    # A comma right before an empty placeholder goes too ("Olá!", not "Olá,!"),
    # with or without a space between them.
    ("Olá, {nome}!", {"nome": None}, "Olá!"),
    ("Olá,{nome}!", {}, "Olá!"),
    # With a name, the comma and the space stay.
    ("Olá, {nome}!", {"nome": "Conceição"}, "Olá, Conceição!"),
    # No default prova, mid-sentence: only the one space before it goes.
    ("Sua prova {prova} começa cedo.", {"prova": None}, "Sua prova começa cedo."),
    # Placeholder at the start of a line: nothing before it to remove.
    ("{nome}, bem-vindo!\n{prova}", {"nome": "Ana"}, "Ana, bem-vindo!\n"),
    # Stray braces stay exactly as typed and never break anything.
    ("Envie { o vídeo {nome", {"nome": "Ana"}, "Envie { o vídeo {nome"),
    ("50% {} :-{", {"nome": "Ana"}, "50% {} :-{"),
    # Unknown placeholders (or the wrong case) are not placeholders.
    ("Oi {Nome} da {equipe}", {"nome": "Ana"}, "Oi {Nome} da {equipe}"),
    # Doubled braces: the inner {nome} is still filled in.
    ("{{nome}}", {"nome": "Ana"}, "{Ana}"),
    # The same placeholder twice.
    ("{nome} e {nome}", {"nome": "Ana"}, "Ana e Ana"),
    # A value that looks like a placeholder is not filled again.
    ("Olá {nome}!", {"nome": "{prova}", "prova": "Enduro"}, "Olá {prova}!"),
    # The shipped message, for a contact without a name.
    (
        DEFAULT_WELCOME_MESSAGE,
        {},
        DEFAULT_WELCOME_MESSAGE.replace(" {nome}", ""),
    ),
]


@pytest.mark.parametrize(("template", "values", "expected"), FILL_CASES)
def test_python_fills_the_placeholders(template, values, expected):
    assert fill_welcome_message(template, values) == expected


def test_the_default_message_greets_without_a_name_cleanly():
    # The first line a nameless contact reads.
    assert fill_welcome_message(DEFAULT_WELCOME_MESSAGE, {}).startswith("Olá! 👋 Este é o")


def test_line_ends_from_a_browser_are_normalized_and_the_ends_trimmed():
    assert normalize_welcome_message("\r\n  Oi\r\n\r\nTchau\r ") == "Oi\n\nTchau"
    assert normalize_welcome_message("   ") == ""


# --- JavaScript (the same cases, in Node) ---

NODE_SCRIPT = """
const input = JSON.parse(process.env.WELCOME_CASES);
const { fillWelcomeMessage } = await import(input.module);
console.log(JSON.stringify(input.cases.map(([template, values]) =>
  fillWelcomeMessage(template, values))));
"""


def test_the_page_script_fills_them_the_same_way():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    # JSON has no None: a missing value is sent as null, which the script
    # treats like Python's None.
    cases = [[template, values] for template, values, _ in FILL_CASES]
    payload = {"module": (STATIC_DIR / "js" / "welcome.js").as_uri(), "cases": cases}
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={"WELCOME_CASES": json.dumps(payload), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout) == [expected for _, _, expected in FILL_CASES]
