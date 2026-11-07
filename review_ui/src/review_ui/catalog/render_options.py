"""Catalog: vertical videos, "Girar", "Enquadramento" and "Sem áudio" (LIVESTACK-50)."""

TEXT: dict[str, str] = {
    # --- Queue and job header ---
    "badge.vertical": "vertical",
    "badge.vertical.title": "Filmado na vertical: o clipe preenche as laterais (Enquadramento)",
    # --- Job page: the "Imagem e som" box (templates/_render_options.html) ---
    "render.legend": "Imagem e som",
    "render.rotation": "Girar",
    "render.rotation.hint": (
        "Para um celular que gravou deitado mas marcou o vídeo como em pé (ou o contrário). "
        "O player gira junto; enquanto girado, use o teclado e a barra do corte para controlar."
    ),
    "render.rotation.0": "Não girar",
    "render.rotation.90": "90° para a direita",
    "render.rotation.180": "180° (de cabeça para baixo)",
    "render.rotation.270": "90° para a esquerda",
    "render.framing": "Enquadramento",
    "render.framing.hint": (
        "O clipe é sempre 1920×1080 (16:9). Um vídeo em outro formato fica inteiro no meio "
        "e as bordas são preenchidas assim:"
    ),
    "render.framing.blurred": "Desfocado: o próprio vídeo, ampliado e escurecido, nas bordas",
    "render.framing.black_bars": "Barras pretas",
    "render.framing.crop": "Cortar: uma faixa 16:9 do vídeo preenche a tela",
    "render.crop_position": "Posição da faixa",
    "render.crop_position.hint": (
        "A faixa clara sobre o player mostra o que entra no clipe; o resto fica de fora."
    ),
    "render.no_audio": "Sem áudio: o clipe vai sem som",
    "render.no_audio.hint": "O player fica mudo enquanto isto estiver marcado.",
    # --- The orientation line (also refreshed by js/render_options.js) ---
    "script.orientation.vertical": "Vídeo na vertical ({width}×{height}).",
    "script.orientation.horizontal": "Vídeo na horizontal, 16:9 ({width}×{height}).",
    "script.orientation.other": (
        "Vídeo na horizontal, mas não 16:9 ({width}×{height}): as bordas seguem o Enquadramento."
    ),
    "script.orientation.unknown": (
        "O formato aparece depois que um worker com o original analisar o arquivo."
    ),
    # --- A clip that no longer matches (clip_inputs_view.py) ---
    "outdated.rotation": "O clipe no ar usa “{clip}” em Girar; a revisão agora diz “{now}”.",
    "outdated.framing": (
        "O clipe no ar usa o enquadramento “{clip}”; a revisão agora diz “{now}”."
    ),
    "outdated.framing.none": "nenhum (16:9)",
    "outdated.framing.blurred": "desfocado",
    "outdated.framing.black_bars": "barras pretas",
    "outdated.framing.crop": "cortar, faixa a {percent}%",
    "outdated.audio.dropped_now": "O clipe no ar tem som; a revisão agora diz “Sem áudio”.",
    "outdated.audio.kept_now": "O clipe no ar está sem som; a revisão agora quer o som.",
    # --- Waiting reason (job_view.describe_waiting_reason) ---
    "waiting.no_holder_can_render": (
        "Esperando um PC com o original que saiba fazer estas opções de imagem e som "
        "(Baja Worker 0.4.0 ou mais novo)."
    ),
    # --- Frota: what each worker version can render ---
    "fleet.render_features": "Sabe fazer: {features}",
    "fleet.render_features.none": "Versão antiga: não faz vídeo na vertical nem “Sem áudio”",
    "render_feature.framing": "vertical e girar",
    "render_feature.no_audio": "sem áudio",
    # --- Configurações ---
    "settings.vertical_video_message": "Resposta a vídeo na vertical",
    "settings.vertical_video_message.hint": (
        "O bot responde uma vez, citando o vídeo, quando o arquivo mostra que foi gravado em pé. "
        "O vídeo é usado normalmente. Deixe vazio para não responder."
    ),
    # --- Event log ---
    "event.vertical_reply_sent": "pedido de vídeo na horizontal enviado",
    "event.vertical_reply_failed": "pedido de vídeo na horizontal não enviado",
}
