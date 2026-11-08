"""Catalog: duplicate videos (LIVESTACK-51 item 3)."""

TEXT: dict[str, str] = {
    # --- Job page status strip (templates/_job_status.html) ---
    "duplicates.discarded_copy_of": ("Descartado automaticamente: duplicado de"),
    "duplicates.copy_of": "Mesmo arquivo que o vídeo",
    "duplicates.also_received_from": "Também recebido de:",
    # --- Queue badge ---
    "badge.duplicate": "duplicado",
    "badge.duplicate.title": "O mesmo arquivo já tinha chegado em outro vídeo",
    # --- Configurações: the bot's reply to a duplicate ---
    "settings.duplicate_video_message": "Resposta a um vídeo repetido",
    "settings.duplicate_video_message.hint": (
        "O bot responde uma vez, citando o vídeo, quando recebe um arquivo que já tinha "
        "chegado (o mesmo enviado de novo ou encaminhado por outra pessoa). "
        "<code>{placeholder}</code> vira a hora em que chegou a primeira cópia. "
        "Vazio = não responde. Uma cópia com bytes diferentes (recomprimida) não é reconhecida."
    ),
}
