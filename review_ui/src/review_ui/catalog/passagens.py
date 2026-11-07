"""Catalog: recording times and the job page's passagem help (LIVESTACK-45)."""

TEXT: dict[str, str] = {
    # --- When a video was filmed (queue rows, job page, panel) ---
    "recorded.label": "gravado",
    # A small marker after a time that is not the file's own.
    "recorded.hint.whatsapp": "envio",
    "recorded.hint.whatsapp.title": (
        "Horário de envio pelo WhatsApp: o arquivo não diz quando foi gravado."
    ),
    "recorded.hint.received": "chegada",
    "recorded.hint.received.title": (
        "Horário em que o vídeo chegou: nem o arquivo nem o WhatsApp dizem quando foi gravado."
    ),
    "recorded.job.metadata": "Gravado {time} (horário do arquivo).",
    "recorded.job.whatsapp": (
        "Gravado antes de {time}: o arquivo não diz quando, este é o horário de envio pelo "
        "WhatsApp."
    ),
    "recorded.job.received": (
        "Gravado antes de {time}: nem o arquivo nem o WhatsApp dizem quando, este é o horário "
        "em que o vídeo chegou."
    ),
    # --- "Outros vídeos de <prova>, carro <n>" (job page) ---
    "passagens.title": "Outros vídeos de {prova}, carro {car}",
    "passagens.label": "Outros vídeos deste carro nesta prova",
    "passagens.none": "Nenhum outro vídeo deste carro nesta prova.",
    "passagens.this_video": "este vídeo",
    "passagens.no_passagem": "sem passagem",
    "passagens.passagem": "passagem {passagem}",
    "passagens.open": "abrir",
    "passagens.registered": "Passagens já registradas: {items}",
    "passagens.registered_item": "{passagem} ({time})",
    "passagens.registered_none": "Nenhuma passagem registrada ainda.",
    "passagens.suggest_after": (
        "Sugestão: passagem {passagem} — gravado depois da passagem {previous}, de {time}"
    ),
    "passagens.suggest_first": (
        "Sugestão: passagem {passagem} — gravado antes de todas as passagens registradas"
    ),
    "passagens.suggest_first_none_numbered": (
        "Sugestão: passagem {passagem} — nenhum vídeo deste carro tem passagem ainda"
    ),
    "passagens.suggest_first_no_others": (
        "Sugestão: passagem {passagem} — é o único vídeo deste carro nesta prova"
    ),
    "passagens.no_suggestion_taken": (
        "Sem sugestão: pela ordem de gravação seria a passagem {passagem}, que já é o vídeo "
        "de {time}. Confira os dois."
    ),
    "passagens.use_suggestion": "Usar passagem {passagem}",
    "passagens.approximate": (
        "Horários marcados “envio” ou “chegada” não são da gravação: a ordem é aproximada."
    ),
    "passagens.warning_taken": "A passagem {passagem} já é o vídeo de {time}.",
    "passagens.warning_filmed_before": (
        "Passagem {passagem} fora da ordem: este vídeo foi gravado antes da passagem {other}, "
        "de {time}."
    ),
    "passagens.warning_filmed_after": (
        "Passagem {passagem} fora da ordem: este vídeo foi gravado depois da passagem {other}, "
        "de {time}."
    ),
    "passagens.warnings_on_ready": "Atenção à passagem (não impede de continuar):",
    # --- What js/passagem_help.js says ---
    "script.passagens.confirm": "{warnings}\n\nContinuar mesmo assim?",
    "script.passagens.loading": "Procurando os outros vídeos deste carro…",
    "script.passagens.failed": "Não foi possível buscar os outros vídeos deste carro: {error}",
}
