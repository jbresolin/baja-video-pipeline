"""Watching and downloading the rendered clip (LIVESTACK-52): "Ver clipe", "Baixar clipe"."""

TEXT = {
    # --- "Ver clipe": the preview player (job page and clips page) ---
    "clip_preview.title": "Ver clipe",
    "clip_preview.title.version": "Ver clipe (versão {version})",
    "clip_preview.summary.title": (
        "Uma prévia pequena (480p) do clipe renderizado: orientação, cortes, overlay, "
        "resultado e cronômetro como foram para o ar"
    ),
    "clip_preview.watch": "Ver",
    "clip_preview.close": "Fechar",
    "clip_preview.small": "Prévia em 480p; para a qualidade final, use “Baixar clipe”.",
    # Why there is no preview (tests check every ClipPreviewState).
    "clip_preview.state.ready": "Prévia pronta.",
    "clip_preview.state.making": (
        "A prévia está sendo feita pelo PC que renderizou o clipe. Recarregue a página "
        "em instantes."
    ),
    "clip_preview.state.switched_off": (
        "Este clipe não tem prévia: “Gerar prévia dos clipes” estava desligado nas "
        "Configurações quando ele foi renderizado."
    ),
    "clip_preview.state.older_worker": (
        "Este clipe não tem prévia: o PC que o renderizou usa uma versão do Baja Worker "
        "anterior à 0.7.0."
    ),
    "clip_preview.state.evicted": (
        "A prévia deste clipe foi apagada para liberar espaço no servidor."
    ),
    "clip_preview.state.missing": "Este clipe não tem prévia.",
    "clip_preview.use_download": "Para conferir o clipe, use “Baixar clipe”.",
    # --- "Baixar clipe" ---
    "clip_download.button": "Baixar clipe",
    "clip_download.button.title": (
        "Baixar o clipe como foi para o ar, em qualidade final. O servidor apaga a "
        "cópia pouco depois de baixada."
    ),
    # --- Settings ---
    "settings.clip_previews": "Gerar prévia dos clipes",
    "settings.clip_previews.hint": (
        "Depois de renderizar, o PC envia uma prévia pequena (480p, cerca de 12 MB por "
        "minuto de clipe) para “Ver clipe”. Desligue num evento em que o disco do "
        "servidor estiver apertado; clipes renderizados com isto desligado ficam sem prévia."
    ),
    "settings.clip_download_keep": (
        "Apagar do servidor o clipe pedido, depois da primeira vez que foi baixado inteiro "
        "(minutos)"
    ),
    "settings.clip_download_unused": "Apagar o clipe pedido e nunca baixado (horas)",
    "settings.clip_download.hint": (
        "“Baixar clipe”: o tempo depois de baixado serve para tentar de novo quando o "
        "arquivo não chegou inteiro. Com o disco apertado, as cópias saem antes."
    ),
    # --- Event log ---
    "event.clip_preview_uploaded": "prévia do clipe enviada ao servidor",
    "event.clip_preview_evicted": "prévia do clipe apagada para liberar disco",
    "event.clip_download_requested": "clipe pedido para baixar",
    "event.clip_download_uploaded": "clipe enviado ao servidor para baixar",
    "event.clip_downloaded": "clipe baixado",
    "event.clip_download_deleted": "cópia do clipe para baixar apagada",
    # --- Frota: what each worker declares ---
    "render_feature.clip_preview": "prévia do clipe",
    "render_feature.upload_clip": "enviar clipe para baixar",
    # --- Page scripts ---
    "script.clip_download.preparing": (
        "Preparando o clipe… um PC da equipe está enviando ao servidor."
    ),
    "script.clip_download.ready": "Clipe pronto:",
    "script.clip_download.no_source": (
        "Nenhum PC que tem este clipe está ligado agora. Tente de novo mais tarde."
    ),
}
