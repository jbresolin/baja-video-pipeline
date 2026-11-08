"""Catalog: who sent a video, and their default prova (LIVESTACK-51 items 2 and 5)."""

TEXT: dict[str, str] = {
    # --- Queue filter "Enviado por" (queue.html) ---
    "queue.filter.sender": "Enviado por",
    "queue.filter.any_sender": "Qualquer pessoa",
    # --- Admin → Contatos: the default prova (LIVESTACK-51) ---
    "contacts.default_prova.hint": (
        "Prova padrão: os vídeos novos do contato já chegam com essa prova (quem revisa pode "
        "trocar). Sem prova padrão, como para quem filma de tudo (um “coringa”), a prova "
        "fica em branco para quem revisa escolher."
    ),
    "contacts.waiting_without_prova.one": "1 vídeo esperando revisão sem prova",
    "contacts.waiting_without_prova.other": "{count} vídeos esperando revisão sem prova",
    "script.contacts.apply_default.one": (
        "Aplicar {prova} ao 1 vídeo deste contato que ainda não tem prova?"
    ),
    "script.contacts.apply_default.other": (
        "Aplicar {prova} aos {count} vídeos deste contato que ainda não têm prova?"
    ),
    "done.contact_updated_prova_applied": (
        "Contato atualizado, e a prova padrão foi aplicada aos vídeos que esperavam sem prova."
    ),
    # --- Event log ---
    "event.contact_merged": "contatos unidos (o mesmo número escrito de dois jeitos)",
    "event.default_prova_applied": "prova padrão do contato aplicada",
}
