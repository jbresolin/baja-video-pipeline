"""What the Baja Bot says back to volunteers (in Portuguese, their language)."""

# The video arrived as a WhatsApp *video* (recompressed). It is still used,
# flagged low_res, but a resend as document gives the original quality.
LOW_RES_RESEND_AS_DOCUMENT = (
    "Recebemos seu vídeo, mas o WhatsApp reduziu a qualidade dele. "
    "Se puder, envie de novo como *documento* (📎 → Documento) para usarmos o original. Obrigado!"
)

# Download or verification failed; no job exists.
DOWNLOAD_FAILED_PLEASE_RESEND = (
    "Não conseguimos receber seu vídeo. Por favor, envie de novo como *documento* (📎 → Documento)."
)

# Inbox above BVP_INBOX_HOLD_GB: the video was accepted.
INBOX_NEARLY_FULL_PLEASE_HOLD = (
    "Recebemos seu vídeo! Nossa fila está quase cheia: por favor, espere alguns minutos "
    "antes de enviar os próximos."
)

# Inbox above BVP_INBOX_CAP_GB: the video was refused.
INBOX_FULL_RESEND_LATER = (
    "Nossa fila está cheia e não conseguimos receber este vídeo agora. "
    "Por favor, espere alguns minutos e envie de novo."
)
