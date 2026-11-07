"""The welcome message the Baja Bot sends to a contact the admin adds (LIVESTACK-44).

One text for everyone, edited on Admin → Configurações. Two placeholders
are filled in for each contact when the message is sent:

    {nome}   the contact's display name (nothing when it has none)
    {prova}  the contact's default prova (nothing when it has none)

Anything else in braces stays exactly as typed ("{Nome}", "{", "{}"), so a
stray brace never breaks a send; the preview on the Settings page shows it.

When a placeholder has nothing to put in, the one space right before it
goes too, so "Olá {nome}!" reads "Olá!" rather than "Olá !".

The orchestrator fills the text it sends; the review UI fills the same
text with example values for the preview. static/js/welcome.js mirrors
fill_welcome_message for the live preview, tested with the same cases.
"""

import re

# What each placeholder stands for, in the order the Settings page lists them.
PLACEHOLDERS = ("nome", "prova")

# Values the Settings page's preview fills in.
EXAMPLE_VALUES = {"nome": "Maria Conceição", "prova": "Enduro"}

# Shipped as the first value of the setting; João edits it on the Settings
# page. WhatsApp formatting: *bold*.
DEFAULT_WELCOME_MESSAGE = (
    "Olá {nome}! 👋 Este é o *Baja Bot*, da Baja SAE Brasil.\n"
    "\n"
    "Os vídeos que você enviar aqui vão direto para a equipe da transmissão ao vivo.\n"
    "\n"
    "Como enviar:\n"
    "• Envie cada vídeo como *documento* (📎 → Documento), não como vídeo, "
    "para manter a qualidade.\n"
    "• Um vídeo por passagem.\n"
    "\n"
    "Salve este número nos seus contatos. Obrigado!"
)

# The longest welcome message the Settings page accepts.
MAX_WELCOME_MESSAGE_LENGTH = 2000

# A known placeholder, with the single space before it (if any) captured
# separately so it can be dropped together with an empty value.
_PLACEHOLDER = re.compile(r"( ?)\{(" + "|".join(PLACEHOLDERS) + r")\}")


def fill_welcome_message(template: str, values: dict[str, str | None]) -> str:
    """The message with {nome} and {prova} replaced by `values` (missing/None = nothing)."""

    def replace(match: re.Match) -> str:
        space_before, name = match.groups()
        value = (values.get(name) or "").strip()
        if not value:
            return ""
        return space_before + value

    return _PLACEHOLDER.sub(replace, template)


def normalize_welcome_message(typed: str) -> str:
    """As stored: Windows line ends (from a browser's textarea) become \\n, ends trimmed."""
    return typed.replace("\r\n", "\n").replace("\r", "\n").strip()
