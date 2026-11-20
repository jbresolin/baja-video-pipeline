"""Catalog: the top speed under the clock, "Aceleração e Velocidade" (LIVESTACK-67).

The speed mark in the job page's "Cronômetro" block and its status line,
the warnings, the sentences for a clip whose speed changed, the waiting
reason for workers too old to draw it, and the speed line of "Testar" and
the speed table's status on the Settings page.
"""

TEXT: dict[str, str] = {
    # --- Job page: the speed mark in "Cronômetro" (pass_speed_view.py, _pass_clock.html) ---
    "pass_speed.mark": "Velocidade",
    "pass_speed.set_mark.title": (
        "Marca o quadro atual: o carro passa pela medição de velocidade. "
        "A velocidade aparece daí até o fim do clipe."
    ),
    "pass_speed.clear_mark": "Limpar",
    "pass_speed.clear_mark.title": "Tira a marca de velocidade: o clipe fica sem a velocidade",
    "pass_speed.on_air": (
        "Velocidade na tarja: {speed}, da marca até o fim do clipe, sob o cronômetro."
    ),
    "pass_speed.no_mark": (
        "Sem marca de velocidade, o clipe sai sem ela. Marque o momento em que o carro "
        "passa pela medição para gravar {speed}."
    ),
    "pass_speed.needs_clock": (
        "A velocidade ({speed}) só entra no clipe junto com o cronômetro: marque a largada "
        "e deixe “Mostrar cronômetro” ligado."
    ),
    # Why there is no official speed: the result line's sentences after this.
    "pass_speed.no_official": "Sem velocidade oficial ainda:",
    # Both marks set, the speed's before the largada (js/speed_order.js; never blocks).
    "pass_speed.before_largada": (
        "A marca de velocidade está antes da largada. Confira se marcou o quadro certo."
    ),
    # --- Shortcuts box ---
    "shortcuts.speed_mark": "Marcar a velocidade (passagem pela medição)",
    # --- The clip on air no longer matches (clip_inputs_view.py) ---
    "outdated.speed.published": "A velocidade agora entraria no clipe: {now}.",
    "outdated.speed.changed": "A velocidade mudou: o clipe no ar tem {clip}; agora seria {now}.",
    "outdated.speed.removed": (
        "O clipe no ar tem velocidade ({clip}); a revisão agora diz para não mostrar."
    ),
    "outdated.speed.describe": "{speed} a partir de {mark}",
    # --- Waiting reason, Frota ---
    "waiting.no_holder_can_render_speed": (
        "Esperando um PC com o original que saiba desenhar a velocidade sob o cronômetro "
        "(Baja Worker 0.8.0 ou mais novo)."
    ),
    "render_feature.speed": "velocidade sob o cronômetro",
    # --- Configurações, "Testar" and the tables' status ---
    "pass_results.test.speed": "Velocidade: <strong>{text}</strong>.",
    "pass_results.test.speed_problem": "Velocidade: {reason}",
    "pass_results.speed_table": "velocidade",
    "pass_results.config_problem.speed_needs_clock": (
        "o bloco <code>speed</code> precisa de um bloco <code>clock</code> na mesma prova "
        "(a velocidade aparece sob o cronômetro)"
    ),
}
