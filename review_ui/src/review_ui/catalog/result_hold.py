"""Waiting for the official result before rendering (LIVESTACK-62): page, queue, log, Settings."""

TEXT = {
    # --- Job page: the box next to "Pronto" / "Salvar e reprocessar" ---
    "result_wait.box": "Esperar o resultado oficial (até {minutes} min)",
    "result_wait.box.title": (
        "O clipe só é renderizado quando o resultado oficial desta passagem (e o tempo "
        "do cronômetro) chegar da API de resultados, ou depois de {minutes} min, o que "
        "vier primeiro. Desmarque para renderizar já."
    ),
    # --- Job page: the status strip while the render waits ---
    "result_hold.status": "Aguardando o resultado oficial (até {time}).",
    "result_hold.status.hint": (
        "O clipe é renderizado assim que o resultado chegar, ou às {time} sem ele."
    ),
    "result_hold.render_now": "Renderizar agora",
    "result_hold.render_now.title": "Renderizar já, sem esperar o resultado oficial",
    # The waiting reason the orchestrator writes for a held job (queue rows).
    "waiting.official_result": "Aguardando o resultado oficial.",
    # --- Queue and "Outros vídeos": the marker ---
    "result_hold.marker.until": "⏳ aguardando resultado até {time}",
    "result_hold.marker.title": (
        "Pronto, mas a renderização espera o resultado oficial desta passagem"
    ),
    # --- Event log ---
    "event.result_hold_started": "renderização esperando o resultado oficial",
    "event.result_hold_started.until": "renderização esperando o resultado oficial até {time}",
    "event.result_hold_released_result_arrived": (
        "resultado oficial chegou: renderização liberada"
    ),
    "event.result_hold_released_deadline_passed": (
        "prazo do resultado oficial acabou: renderização liberada sem ele"
    ),
    "event.result_hold_released_by_reviewer": "renderizar agora: espera pelo resultado encerrada",
    "event.result_hold_dropped": (
        "espera pelo resultado cancelada: nem resultado nem cronômetro vão no clipe"
    ),
    # --- Settings ---
    "settings.result_wait": "Esperar o resultado oficial por até (minutos)",
    "settings.result_wait.hint": (
        "Quanto tempo “Pronto” com “Esperar o resultado oficial” marcado segura a "
        "renderização, de 1 a 120 minutos. Vale para os próximos vídeos marcados como "
        "prontos; os que já estão esperando mantêm o prazo que tinham."
    ),
    # --- After "Renderizar agora" (shown by js/job.js) ---
    "result_hold.released": "Renderização liberada. Um worker com o original vai pegar.",
}
