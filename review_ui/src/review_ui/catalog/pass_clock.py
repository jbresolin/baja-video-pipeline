"""Catalog: the running clock in the overlay, and the overlay preview (LIVESTACK-48).

The job page's "Cronômetro" block (largada mark, "Mostrar cronômetro" and
its status line), the "Pré-visualizar overlay" toggle, the sentences for a
clip whose clock changed, the waiting reason for workers too old to draw
it, and the clock line of "Testar" on the Settings page.
"""

TEXT: dict[str, str] = {
    # --- Job page: "Cronômetro" (pass_clock_view.py, _pass_clock.html) ---
    "pass_clock.legend": "Cronômetro",
    "pass_clock.hint": (
        "Começa em 0 na largada, corre e para no tempo oficial da passagem (sem penalidades)."
    ),
    "pass_clock.largada": "Largada",
    "pass_clock.set_largada.title": "Marca a largada no quadro atual do vídeo",
    "pass_clock.clear_largada": "Limpar",
    "pass_clock.clear_largada.title": "Tira a marca da largada: o clipe fica sem cronômetro",
    "pass_clock.show": "Mostrar cronômetro",
    "pass_clock.on_air": "Na tarja: da largada até {official} s, e para.",
    "pass_clock.not_burned": "Não será gravado (pararia em {official} s).",
    "pass_clock.no_largada": "Marque a largada para gravar o cronômetro (para em {official} s).",
    # The cut ends before the clock stops, so the result never appears
    # (LIVESTACK-53; js/clock_late.js shows it, it never blocks).
    "pass_clock.ends_before_freeze": (
        "O clipe termina antes do cronômetro parar; o resultado não vai aparecer. "
        "Estenda o fim do corte."
    ),
    # Why there is no official time: the result line's sentences
    # (pass_result.problem.*), after this.
    "pass_clock.no_official": "Sem tempo oficial ainda:",
    # --- The overlay preview over the player (js/overlay_preview.js) ---
    "preview.toggle": "Pré-visualizar overlay",
    "preview.hint": (
        "Desenhada pelo navegador para conferir textos e cronômetro antes de processar; "
        "o clipe final é desenhado pelo PC e pode diferir em detalhes. Usa os nomes salvos."
    ),
    # --- Shortcuts box ---
    "shortcuts.largada": "Marcar a largada (cronômetro)",
    # --- The clip on air no longer matches (clip_inputs_view.py) ---
    "outdated.clock.published": "O cronômetro agora entraria no clipe: {now}.",
    "outdated.clock.changed": "O cronômetro mudou: o clipe no ar tem {clip}; agora seria {now}.",
    "outdated.clock.removed": (
        "O clipe no ar tem cronômetro ({clip}); a revisão agora diz para não mostrar."
    ),
    "outdated.clock.describe": "largada em {largada}, parando em {official} s",
    # --- Waiting reason, Frota ---
    "waiting.no_holder_can_render_clock": (
        "Esperando um PC com o original que saiba desenhar o cronômetro "
        "(Baja Worker 0.6.0 ou mais novo)."
    ),
    "render_feature.clock": "cronômetro",
    # --- Configurações, "Testar" ---
    "pass_results.test.clock": "Cronômetro para em <strong>{text} s</strong>.",
    "pass_results.test.clock_problem": "Cronômetro: {reason}",
    # --- Page scripts ---
    "script.clock.official": "para em {official} s",
}
