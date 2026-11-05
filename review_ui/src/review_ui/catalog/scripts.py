"""Catalog: what the page scripts (static/js/*.js) say.

Every key here starts with "script."; base.html embeds these entries as
JSON and static/js/text.js reads them. The time words are also used by the
server (formatting.age_text), so a time reads the same before and after the
script refreshes it.
"""

TEXT: dict[str, str] = {
    # --- Times (formatting.py and js/format.js) ---
    "script.time.never": "nunca",
    "script.time.seconds_ago": "há {n} s",
    "script.time.minutes_ago": "há {n} min",
    "script.time.hours_ago": "há {n} h",
    "script.time.days_ago": "há {n} d",
    "script.time.relative_with_clock": "{relative} ({clock})",
    # --- Errors (js/common.js) ---
    "script.error.no_connection": "Sem conexão com o servidor. Confira a rede e tente de novo.",
    "script.error.http": "O pedido falhou (HTTP {status}).",
    # --- Auto-refresh indicator (js/poll.js) ---
    "script.live.ok": "ao vivo",
    "script.live.failed": "falha ao atualizar (HTTP {status})",
    "script.live.offline": "sem conexão, tentando de novo",
    # --- Copy buttons (js/clips.js, js/admin.js) ---
    "script.copy": "Copiar",
    "script.copied": "Copiado ✓",
    "script.copy_failed": "Não foi possível copiar",
    "script.selected_press_ctrl_c": "Selecionado: Ctrl+C",
    # --- Job page (js/job.js, js/review_form.js) ---
    "script.proxy_requested": (
        "Proxy pedido. Esta página mostra o proxy assim que um worker com o original enviá-lo."
    ),
    "script.proxy_requested_waiting": "Proxy pedido. {reason}",
    "script.saved_version": "{message} (v{version})",
    "script.confirm_discard": "Descartar este vídeo? Dá para restaurar em “Descartados”.",
    "script.version_conflict": (
        "Outra pessoa alterou este vídeo; a página foi recarregada com a versão dela."
    ),
    "script.version_conflict_now": (
        "Outra pessoa alterou este vídeo (agora v{version}); a página foi recarregada com a "
        "versão dela."
    ),
    "script.no_longer_possible": "Essa ação não é mais possível; a página foi recarregada.",
    "script.your_values": "Os valores que você tinha digitado: {values}.",
    "script.value.prova": "prova {value}",
    "script.value.car_number": "carro {value}",
    "script.value.passagem": "passagem {value}",
    "script.value.cut_in_s": "início {value}",
    "script.value.cut_out_s": "fim {value}",
    "script.team_looking_up": "Procurando a equipe…",
    "script.team": "Equipe: ",
    "script.team_not_found": "O carro {car} não está na lista de equipes. Confira o número.",
    "script.team_lookup_failed": "Não foi possível buscar a equipe: {error}",
}
