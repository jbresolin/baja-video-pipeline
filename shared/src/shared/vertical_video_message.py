"""What the Baja Bot says about a video filmed upright (LIVESTACK-50).

When the first holder's probe shows a vertical video, the bot quotes that
video once with this text, asking for horizontal next time. The clip still
goes out (blurred sides by default), so the reaction on the video stays ✅.
João edits the text on Admin → Configurações; an empty text turns the
reply off. No placeholders: the quote already says which video.
"""

# Shipped as the first value of the setting. WhatsApp formatting: *bold*.
DEFAULT_VERTICAL_VIDEO_MESSAGE = (
    "📱 Vídeo na vertical. Da próxima vez, grave na horizontal, por favor."
)

# The longest text the Settings page accepts.
MAX_VERTICAL_VIDEO_MESSAGE_LENGTH = 500


def normalize_vertical_video_message(typed: str) -> str:
    """As stored: Windows line ends (from a browser's textarea) become \\n, ends trimmed."""
    return typed.replace("\r\n", "\n").replace("\r", "\n").strip()
