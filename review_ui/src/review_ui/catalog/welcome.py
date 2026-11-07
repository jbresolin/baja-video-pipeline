"""Catalog: the welcome message for new contacts (LIVESTACK-44).

Its field on Configurações and its controls on Contatos. The message
itself is not here: it is a setting João writes (default in
shared.welcome_message).
"""

TEXT: dict[str, str] = {
    # --- Configurações ---
    "settings.welcome_message": "Mensagem de boas-vindas para contatos novos",
    "settings.welcome_placeholder.nome": "o nome do contato (nada, se ele não tiver nome)",
    "settings.welcome_placeholder.prova": "a prova padrão do contato (nada, se não tiver)",
    "settings.welcome_message.hint": (
        "O bot envia esta mensagem pelo WhatsApp quando você adiciona um contato em "
        '<a href="/review/admin/contacts">Contatos</a> com “Enviar mensagem de boas-vindas” '
        "marcado, ou clica em “Enviar boas-vindas” na linha dele. Os nomes entre chaves acima "
        "são trocados ao enviar; qualquer outro texto entre chaves fica como está. Deixe vazia "
        "para não enviar nada."
    ),
    "settings.welcome_preview": "Prévia, com o nome “{nome}” e a prova “{prova}”:",
    # Shown in the preview box (also by js/admin.js while typing).
    "script.welcome_preview_empty": "Mensagem vazia: nada é enviado.",
    # --- Contatos ---
    "contacts.send_welcome": "Enviar mensagem de boas-vindas",
    "contacts.col.welcome": "Boas-vindas",
    "contacts.welcome.sent": "enviada",
    "contacts.welcome.never": "nunca enviada",
    "contacts.welcome.send": "Enviar boas-vindas",
    "contacts.welcome.confirm": "Enviar a mensagem de boas-vindas para {who} agora?",
    "contacts.welcome.disabled_empty": ("A mensagem de boas-vindas está vazia (Configurações)."),
    "contacts.welcome.disabled_blocked": "Contatos bloqueados não recebem boas-vindas.",
    "contacts.welcome.empty_hint": (
        "A mensagem de boas-vindas está vazia, então não pode ser enviada. Escreva-a em "
        '<a href="/review/admin/settings">Configurações</a>.'
    ),
    "contacts.welcome.create_failed": "Contato criado, mas a mensagem não foi enviada: {reason}",
    "contacts.welcome.send_failed": "A mensagem de boas-vindas não foi enviada: {reason}",
    "done.contact_created_welcome_sent": "Contato adicionado e mensagem de boas-vindas enviada.",
    "done.welcome_sent": "Mensagem de boas-vindas enviada.",
    # --- Event log and "Últimas alterações" ---
    "event.welcome_sent": "boas-vindas enviadas",
    "event.welcome_failed": "boas-vindas não enviadas",
}
