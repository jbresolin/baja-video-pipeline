"""Catalog: every page's frame (header, navigation), job states, errors, sign-in."""

TEXT: dict[str, str] = {
    # --- Page frame (base.html) ---
    "brand": "Vídeos Baja",
    "page.title_suffix": "Vídeos Baja",
    "nav.label": "Seções",
    "nav.queue": "Fila",
    "nav.clips": "Clipes",
    "nav.fleet": "Frota",
    "nav.admin": "Admin",
    "header.signed_in_as": "Conectado como {username} ({role})",
    "header.sign_out": "Sair",
    "role.reviewer": "Revisor",
    "role.admin": "Admin",
    # --- Job states (the API values stay English; tests check every JobState) ---
    "state.received": "recebido",
    "state.proxy_ready": "proxy pronto",
    "state.awaiting_review": "aguardando revisão",
    "state.ready": "pronto",
    "state.claimed": "em processamento",
    "state.uploaded": "enviado",
    "state.delivered": "entregue",
    "state.done": "concluído",
    "state.failed": "falhou",
    "state.discarded": "descartado",
    # --- Review actions: button text (tests check every ReviewAction) ---
    "action.save": "Salvar",
    "action.ready": "Pronto",
    "action.reprocess": "Salvar e reprocessar",
    "action.retry": "Tentar de novo",
    "action.discard": "Descartar",
    "action.restore": "Restaurar",
    # What the page says after each action worked (shown by the script).
    "action_done.save": "Salvo.",
    "action_done.ready": "Marcado como pronto. Um worker com o original vai renderizar o clipe.",
    "action_done.reprocess": (
        "Salvo e enviado para reprocessar: a próxima versão do clipe substitui a do ar "
        "quando ficar pronta."
    ),
    # A plain save on a job whose clip is already made (LIVESTACK-43).
    "action_done.save_clip_unchanged": (
        "Salvo. O clipe no ar não muda até você usar “Salvar e reprocessar”."
    ),
    "action_done.retry": "Tentando de novo: o vídeo voltou para pronto.",
    "action_done.discard": (
        "Descartado. O original fica guardado por um tempo; “Restaurar” desfaz o descarte."
    ),
    "action_done.restore": "Restaurado: o vídeo voltou para onde estava antes do descarte.",
    # --- Error codes from the orchestrator (docs/api.md §3) in words.
    # The code itself is never translated; tests check every ErrorCode. ---
    "error.validation_error": "Algum campo está inválido.",
    "error.hash_mismatch": "O arquivo chegou corrompido (soma de verificação diferente).",
    "error.upload_offset_mismatch": "O envio saiu de sincronia; tente de novo.",
    "error.upload_too_large": "O arquivo é grande demais.",
    "error.missing_review_fields": "Faltam campos para isso: preencha os destacados.",
    "error.unknown_prova": "Essa prova não está na lista. Escolha uma prova da lista.",
    "error.unsupported_message": "Tipo de mensagem não suportado.",
    "error.unauthenticated": "Sua sessão terminou. Entre de novo.",
    "error.forbidden": "Só administradores podem fazer isso.",
    "error.enrollment_token_invalid": "Token de cadastro inválido.",
    "error.not_found": "Não encontrado (talvez alguém removeu agora).",
    "error.illegal_transition": "Essa ação não é mais possível neste estado.",
    "error.already_claimed": "Outro worker já pegou essa tarefa.",
    "error.not_claimable": "Essa tarefa não pode ser pega agora.",
    "error.lease_lost": "A tarefa foi passada para outro worker.",
    "error.version_conflict": "Outra pessoa alterou este vídeo antes.",
    "error.no_holder_online": "Nenhum worker com o original está online agora.",
    "error.already_exists": "Já existe.",
    "error.conflict": "Não é possível agora.",
    "error.raw_unavailable": (
        "Não sobrou nenhuma cópia do original: o vídeo precisa ser enviado de novo."
    ),
    "error.inbox_full": "A caixa de entrada do servidor está cheia.",
    "error.rate_limited": "Muitas tentativas. Espere alguns minutos e tente de novo.",
    "error.upstream_error": "O orquestrador não respondeu. Tente de novo em instantes.",
    "error.dev_mode_disabled": "Recurso de desenvolvimento desligado.",
    # A code this UI does not know yet.
    "error.other": "O orquestrador recusou o pedido.",
    # The UI's own refusals (before calling the orchestrator).
    "error.form_from_other_site": "Formulário enviado de outro site.",
    "error.send_as_form": "Envie como um formulário HTML normal.",
    "error.send_as_json": "Envie o corpo como application/json.",
    "error.body_not_json": "O corpo não é um JSON válido.",
    "error.body_not_object": "O corpo precisa ser um objeto JSON.",
    "error.car_not_in_teams": "O carro {car_number} não está na lista de equipes.",
    "error.orchestrator_unreachable": "O orquestrador não respondeu.",
    # --- Error page (error.html), by HTTP status ---
    "error_page.title.401": "É preciso entrar",
    "error_page.title.403": "Sem permissão",
    "error_page.title.404": "Não encontrado",
    "error_page.title.409": "Alterado por outra pessoa",
    "error_page.title.502": "Orquestrador fora do ar",
    "error_page.title.other": "Algo deu errado",
    "error_page.details": "Detalhes do orquestrador:",
    "error_page.back": "Voltar para a fila",
    # --- Sign-in page ---
    "login.title": "Entrar",
    "login.hint": "Use o usuário e a senha que a organização passou para você.",
    "login.username": "Usuário",
    "login.password": "Senha",
    "login.submit": "Entrar",
    "login.wrong_password": "Usuário ou senha incorretos (ou usuário desativado).",
    "login.too_many_attempts": (
        "Muitas tentativas deste endereço. Espere alguns minutos e tente de novo."
    ),
    "login.unavailable": "O orquestrador não respondeu. Tente de novo em instantes.",
    # --- Small shared words ---
    "unknown_value": "?",
    "no_value": "—",
}
