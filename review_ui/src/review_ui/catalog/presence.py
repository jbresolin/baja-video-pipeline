"""Catalog: two reviewers on the same video (LIVESTACK-51 item 1)."""

TEXT: dict[str, str] = {
    # --- Job page: the banner on opening, the note while working ---
    "presence.one": "{names} está revisando este vídeo (desde {since}).",
    "presence.many": "{names} estão revisando este vídeo (desde {since}).",
    "presence.names": "{first} e {last}",
    "presence.review_anyway": "Revisar mesmo assim",
    "presence.banner_hint": (
        "Para não fazerem o mesmo trabalho, o formulário fica bloqueado até você escolher. "
        "Se a outra pessoa sair, ele é liberado sozinho."
    ),
    # --- Queue row ---
    "presence.queue_marker": "👤 {names}",
    "presence.queue_marker.title": "Alguém está com este vídeo aberto agora",
}
