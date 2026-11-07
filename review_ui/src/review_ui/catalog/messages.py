"""Catalog: the sender's messages and the received time (LIVESTACK-49)."""

TEXT: dict[str, str] = {
    # --- Job page header: when it was received, and by whom it was sent ---
    "job.received_at": "Recebido {time}.",
    "job.sent_by": "Enviado por {intake}.",
    # --- Queue rows and the car's other videos ---
    "received.label": "recebido",
    "received.title": "Quando o bot recebeu o vídeo",
    "queue.messages.title.one": "{count} mensagem do remetente sobre este vídeo",
    "queue.messages.title.other": "{count} mensagens do remetente sobre este vídeo",
    # --- "Mensagens de <remetente>" on the job page ---
    "messages.title": "Mensagens de {sender} ({count})",
    "messages.hint": (
        "O que {sender} escreveu ao bot junto com este vídeo: a legenda, respostas a ele e o "
        "que mandou depois dele, até o próximo vídeo."
    ),
    "messages.unknown_sender": "remetente",
    "messages.unavailable": "Não foi possível carregar as mensagens do remetente.",
    "messages.none_after": "Nenhuma mensagem depois deste vídeo.",
    "messages.before": "Antes deste vídeo ({count})",
    "messages.before.hint": "Mandadas depois do vídeo anterior de {sender}, antes deste.",
    "messages.role.caption": "legenda do vídeo",
    "messages.role.reply": "resposta a este vídeo",
    "messages.edited": "editada",
    "messages.deleted": "apagada",
    "messages.kind.image": "(imagem)",
    "messages.kind.audio": "(áudio)",
    "messages.kind.document": "(documento)",
    "messages.kind.sticker": "(figurinha)",
    "messages.kind.location": "(localização)",
    "messages.kind.contact": "(contato)",
    "messages.kind.other": "(mensagem)",
    # --- Settings ---
    "settings.sender_messages_keep": "Guardar as mensagens dos cinegrafistas (dias)",
    "settings.sender_messages_keep.hint": (
        "Textos e legendas que os cinegrafistas mandam ao bot aparecem na página de cada vídeo. "
        "Depois desse prazo são apagados; a legenda do próprio vídeo fica com ele."
    ),
}
