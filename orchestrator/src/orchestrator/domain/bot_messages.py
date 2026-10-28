"""What the Baja Bot says back to volunteers (in Portuguese, their language).

Every text the bot can send lives here, so docs/volunteers-pt-BR.md can
quote them and a wording change is one edit. When each one is sent:

    RECEIVED_OK                    a document arrived and passed every check (job created)
    LOW_RES_RESEND_AS_DOCUMENT     it came as a WhatsApp *video*: kept (low_res), resend asked
    INBOX_NEARLY_FULL_PLEASE_HOLD  accepted, but the VPS inbox is above BVP_INBOX_HOLD_GB
    INBOX_FULL_RESEND_LATER        refused: the inbox is above BVP_INBOX_CAP_GB
    DOWNLOAD_FAILED_PLEASE_RESEND  the download or a hash check failed; no job exists
"""

# A document arrived and was verified; the job exists. Not sent for a
# low_res video, which already got LOW_RES_RESEND_AS_DOCUMENT.
RECEIVED_OK = "✅ Vídeo recebido! Obrigado."

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

ALL_REPLIES = (
    RECEIVED_OK,
    LOW_RES_RESEND_AS_DOCUMENT,
    DOWNLOAD_FAILED_PLEASE_RESEND,
    INBOX_NEARLY_FULL_PLEASE_HOLD,
    INBOX_FULL_RESEND_LATER,
)
