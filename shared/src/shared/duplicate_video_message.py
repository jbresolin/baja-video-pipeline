"""What the Baja Bot says about a video it already had (LIVESTACK-51 item 3).

The same file sent twice, or forwarded by another filmmaker, becomes a
discarded duplicate; the bot quotes it once with this text. João edits it
on Admin → Configurações; an empty text turns the reply off.

One placeholder, `{hora}`: when the first copy arrived, São Paulo time,
"14:32" on the same day, "06/11 14:32" otherwise. Anything else in braces
stays as typed.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

# Shipped as the first value of the setting. WhatsApp formatting: *bold*.
DEFAULT_DUPLICATE_VIDEO_MESSAGE = (
    "⚠️ Este vídeo já tinha sido recebido (às {hora}). Não precisa reenviar."
)

# The longest text the Settings page accepts.
MAX_DUPLICATE_VIDEO_MESSAGE_LENGTH = 500

TIME_PLACEHOLDER = "{hora}"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def normalize_duplicate_video_message(typed: str) -> str:
    """As stored: Windows line ends (from a browser's textarea) become \\n, ends trimmed."""
    return typed.replace("\r\n", "\n").replace("\r", "\n").strip()


def first_copy_time(first_received_at: datetime, now: datetime) -> str:
    """ "14:32", or "06/11 14:32" when the first copy came on another day (São Paulo)."""
    first = first_received_at.astimezone(SAO_PAULO)
    if first.date() == now.astimezone(SAO_PAULO).date():
        return first.strftime("%H:%M")
    return first.strftime("%d/%m %H:%M")


def fill_duplicate_video_message(template: str, first_received_at: datetime, now: datetime) -> str:
    """The text to send: `{hora}` replaced; no other placeholder is read."""
    return template.replace(TIME_PLACEHOLDER, first_copy_time(first_received_at, now))
