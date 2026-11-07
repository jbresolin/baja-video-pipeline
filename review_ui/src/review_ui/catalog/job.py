"""Catalog: the queue page, the job page (status strip, file details) and the event log."""

TEXT: dict[str, str] = {
    # --- Queue page ---
    "queue.title": "Fila de revisão",
    "queue.hint": (
        "<kbd>↑</kbd><kbd>↓</kbd> ou <kbd>j</kbd><kbd>k</kbd> para mover, <kbd>Enter</kbd> "
        "para abrir. Atualiza a cada {seconds} s."
    ),
    "queue.presets": "Filtros rápidos",
    "queue.preset.needs_human": "Precisam de alguém",
    "queue.preset.discarded": "Descartados",
    "queue.filter.state": "Estado",
    "filter.prova": "Prova",
    "filter.any_prova": "Qualquer",
    "queue.filter.low_res_only": "Só baixa qualidade",
    "queue.preset.outdated": "Clipes desatualizados",
    "queue.filter.outdated_only": "Só clipes desatualizados",
    "filter.apply": "Aplicar",
    "queue.count.one": "{count} vídeo",
    "queue.count.other": "{count} vídeos",
    "queue.no_thumbnail": "sem miniatura",
    "queue.row_title": "{prova} · carro {car} · passagem {passagem}",
    "queue.not_reviewed": "Ainda não revisado",
    "queue.unknown_sender": "remetente desconhecido",
    "queue.empty": "Nada aqui. Vídeos novos aparecem sozinhos.",
    "badge.low_res": "baixa qualidade",
    "badge.low_res.title": "Chegou como vídeo do WhatsApp (recomprimido)",
    "badge.no_proxy": "sem proxy",
    "badge.outdated": "desatualizado",
    "badge.outdated.title": "O clipe no ar não corresponde mais à revisão",
    # --- Job page: header and player ---
    "job.title": "Vídeo {id}",
    "job.back": "← Fila",
    "job.hint": "De {intake} · recebido {received} · {size} · {duration} · v{version}",
    "job.stale": "Alguém alterou este vídeo depois que você abriu a página.",
    "job.reload": "Recarregar",
    "job.player": "Player do proxy",
    "job.proxy_missing": "O proxy não está disponível.",
    "job.request_proxy": "Pedir proxy",
    "job.cut_bar.title": "Clique para ir até esse ponto",
    "job.media_missing": (
        "Os detalhes do arquivo aparecem depois que um worker com o original analisar o arquivo."
    ),
    # --- Job page: keyboard shortcuts ---
    "shortcuts.title": "Atalhos de teclado",
    "shortcuts.space_key": "Espaço",
    "shortcuts.play": "tocar / pausar",
    "shortcuts.set_marks": "marcar início / fim do corte na posição atual",
    "shortcuts.jump_to_marks": "ir para o início / fim do corte",
    "shortcuts.frame_step": "um quadro para trás / para a frente ({fps} fps)",
    "shortcuts.second_step": "um segundo para trás / para a frente",
    "shortcuts.nudge": "mover a última marca um quadro para trás / para a frente",
    "shortcuts.play_cut": "tocar o corte, do início ao fim",
    "shortcuts.save": "salvar",
    "shortcuts.primary": "pronto / salvar e reprocessar / tentar de novo",
    "shortcuts.leave_field": "sair de um campo de texto (os atalhos voltam a funcionar)",
    # --- Job page: review form ---
    "job.form": "Revisão",
    "job.cut_in": "Início do corte",
    "job.cut_out": "Fim do corte",
    # Shown in an empty cut field: "ready" without cuts renders the whole video.
    "job.cut_in.placeholder": "início do vídeo",
    "job.cut_out.placeholder": "fim do vídeo",
    "job.seconds_unit": "s",
    "job.set_mark": "Marcar",
    "job.set_in.title": "Marcar na posição atual (i)",
    "job.set_out.title": "Marcar na posição atual (o)",
    "job.cut_length": "Duração",
    "job.prova": "Prova",
    "job.choose_prova": "Escolha uma prova…",
    "job.no_provas": "Ainda não há provas: um admin adiciona na página Admin → Provas.",
    "job.car_number": "Número do carro",
    "job.passagem": "Passagem",
    "job.team": "Equipe: <strong>{name}</strong>",
    "job.team_with_school": "Equipe: <strong>{name}</strong> ({school})",
    "job.show_pass": "Mostrar o número da passagem na tarja do clipe",
    "job.no_actions": "Nenhuma ação de revisão neste estado.",
    "job.download_clip": "Baixar clipe",
    "job.download_clip.title": "O clipe como está na caixa de saída do servidor",
    "job.download_original": "Baixar original",
    "job.download_original.title": (
        "O vídeo como chegou pelo WhatsApp, em qualidade total (o proxy é só 480p)"
    ),
    "job.rerender_proxy": "Renderizar o proxy de novo",
    "job.rerender_proxy.title": "Pedir a um worker com o original que renderize o proxy de novo",
    # --- The clip on air no longer matches the review (clip_inputs_view.py) ---
    "outdated.title": "O clipe no ar (versão {version}) não corresponde mais à revisão:",
    "outdated.cut": "O clipe no ar usa corte {clip}; a revisão agora diz {now}.",
    "outdated.prova": "O clipe no ar é da prova {clip}; a revisão agora diz {now}.",
    "outdated.car_number": "O clipe no ar mostra o carro {clip}; a revisão agora diz {now}.",
    "outdated.passagem": "O clipe no ar mostra a passagem {clip}; a revisão agora diz {now}.",
    "outdated.show_pass.hidden_now": (
        "O clipe no ar mostra o número da passagem; a revisão agora diz para não mostrar."
    ),
    "outdated.show_pass.shown_now": (
        "O clipe no ar não mostra o número da passagem; a revisão agora diz para mostrar."
    ),
    "outdated.team_name": "O clipe no ar mostra a equipe “{clip}”; agora seria “{now}”{why}.",
    "outdated.school_name": "O clipe no ar mostra a escola “{clip}”; agora seria “{now}”{why}.",
    "outdated.style_changed": " ({style} nas Configurações)",
    "outdated.style.team.full": "nome completo",
    "outdated.style.team.short": "nome curto",
    "outdated.style.school.full": "nome completo",
    "outdated.style.school.short": "sigla",
    "outdated.raw": "O clipe no ar foi feito de outro arquivo original.",
    "outdated.no_value": "nenhum",
    "outdated.reprocess_to_fix": (
        "Nada muda no ar até você usar “Salvar e reprocessar”: isso gera a versão {version}, "
        "e a versão no ar continua até a nova chegar."
    ),
    "outdated.new_version_coming": (
        "A versão {version} já está na fila com a revisão atual; a do ar continua até ela chegar."
    ),
    # --- Job page: where the raw came from (intake_text) ---
    "intake.text": "{sender}, como {received_as}",
    "intake.with_filename": "{sender}, como {received_as} ({filename})",
    "intake.unknown_sender": "remetente desconhecido",
    "received_as.document": "documento do WhatsApp",
    "received_as.video": "vídeo do WhatsApp (recomprimido)",
    "received_as.test_upload": "envio de teste",
    # --- Status strip: one sentence per state (tests check every JobState) ---
    "explain.received": "Original recebido. Esperando um worker baixar e renderizar o proxy.",
    "explain.proxy_ready": "Proxy enviado; o vídeo está entrando na fila de revisão.",
    "explain.awaiting_review": (
        "Aguardando revisão: marque o corte e preencha os campos, depois “Pronto”."
    ),
    "explain.ready": "Revisado. Esperando um worker com o original pegar o processamento.",
    "explain.claimed": "Um worker está renderizando o clipe.",
    "explain.uploaded": "Clipe renderizado e enviado. Esperando o estúdio confirmar.",
    "explain.delivered": "O estúdio confirmou o clipe; a limpeza está terminando.",
    "explain.done": "Concluído: o clipe está no PC do estúdio.",
    "explain.failed": (
        "Um worker não conseguiu processar este vídeo. Escolha “Tentar de novo” ou “Descartar”."
    ),
    "explain.discarded": (
        "Descartado. Nada mais acontece com este vídeo, a não ser que seja restaurado."
    ),
    "explain.discard_grace": (
        "O original fica guardado até {until}; até lá “Restaurar” volta o vídeo para {state}."
    ),
    "explain.discard_grace_over": (
        "O prazo acabou: os workers podem ter apagado o original. “Restaurar” só funciona "
        "enquanto algum worker ainda tiver uma cópia (um que estava offline, por exemplo); "
        "senão o vídeo precisa ser enviado de novo."
    ),
    "explain.low_res": (
        "Chegou como vídeo do WhatsApp, então foi recomprimido (qualidade menor). "
        "O bot pediu para quem enviou mandar de novo como documento."
    ),
    "explain.proxy_evicted": (
        "O proxy não está no servidor (apagado para liberar disco). "
        "Use “Pedir proxy” para um worker com o original renderizar de novo."
    ),
    "explain.no_verified_copy": "Nenhum worker tem ainda uma cópia verificada do original.",
    "explain.task_running": "{who} está {doing}.",
    "explain.task_failed": "{who} falhou ao {task}{exit_code}:",
    "explain.exit_code": " (código de saída {code})",
    # Why a job waits, when the orchestrator gives one of its known reasons.
    "waiting.no_holder_online": (
        "Esperando um worker com o original voltar a ficar online (ou sair da pausa)."
    ),
    "waiting.task_failed_on": "Falhou ao {task} em {worker}:",
    # --- Tasks (tests check every TaskKind): ongoing, and after "falhou ao" ---
    "task.doing.download_raw": "baixando o original",
    "task.doing.make_proxy": "renderizando o proxy",
    "task.doing.process": "renderizando o clipe",
    "task.doing.deliver": "entregando o clipe ao estúdio",
    "task.doing.archive_clip": "arquivando o clipe",
    "task.doing.upload_original": "enviando o original para download",
    "task.verb.download_raw": "baixar o original",
    "task.verb.make_proxy": "renderizar o proxy",
    "task.verb.process": "renderizar o clipe",
    "task.verb.deliver": "entregar o clipe ao estúdio",
    "task.verb.archive_clip": "arquivar o clipe",
    "task.verb.upload_original": "enviar o original para download",
    # --- File details under the player (media_info_view.py) ---
    "media.title": "Detalhes do arquivo",
    "media.location_present": "tinha localização",
    "media.location_present.title": (
        "O arquivo tinha tags de GPS; as coordenadas foram removidas no worker "
        "e nunca são guardadas."
    ),
    "media.all_tags": "Todas as tags ({count})",
    "media.recorded_with": "Gravado com",
    "media.recorded_at": "Gravado em",
    "media.container": "Formato",
    "media.duration": "Duração",
    "media.file_size": "Tamanho",
    "media.bit_rate": "Taxa de bits",
    "media.video": "Vídeo",
    "media.resolution": "Resolução",
    "media.rotation": "Rotação",
    "media.frame_rate": "Taxa de quadros",
    "media.pixel_format": "Formato de pixel",
    "media.colour": "Cor",
    "media.audio": "Áudio",
    "media.variable": "(variável)",
    "media.transfer": "transferência {value}",
    "media.primaries": "primárias {value}",
    "media.no_audio": "nenhum",
    "media.channels": "{count} canais",
    # --- Event log (job page and fleet page) ---
    "events.title": "Histórico",
    "events.unavailable": "Histórico indisponível agora; tenta de novo na próxima atualização.",
    "events.empty": "Nenhum evento registrado para este vídeo ainda.",
    "events.created_as": "criado como {state}",
    "actor.system": "sistema",
    "actor.admin_token": "token de admin",
    "actor.evolution": "WhatsApp",
    "actor.worker_id": "worker {id}",
}
