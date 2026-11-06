"""Catalog: the admin pages for the event's data and settings (provas, teams, settings).

Wording kept from LIVESTACK-37, except "fila" for the server's inbox, which
is now "caixa de entrada" (the reviewers' queue page is the "Fila").
"""

TEXT: dict[str, str] = {
    # --- After a change worked (?feito=<key>) ---
    "done.user_created": "Usuário criado.",
    "done.user_updated": "Usuário atualizado.",
    "done.contact_created": "Contato adicionado.",
    "done.contact_updated": "Contato atualizado.",
    "done.prova_created": "Prova adicionada.",
    "done.prova_updated": "Prova atualizada.",
    "done.provas_reordered": "Ordem das provas salva.",
    "provas.inline.activated": "Prova reativada.",
    "provas.inline.deactivated": "Prova desativada.",
    "done.team_saved": "Equipe salva.",
    "done.team_deleted": "Equipe removida (ou correção desfeita).",
    "done.synced": "Sincronizado com o site de resultados.",
    "done.token_revoked": "Token revogado.",
    "done.worker_renamed": "Nome do worker salvo.",
    "done.master_changed": (
        "Master trocado. Os PCs envolvidos reiniciam o worker sozinhos no próximo sinal "
        "(cerca de 15 s) e voltam com o novo papel."
    ),
    "done.worker_revoked": "Worker revogado. Remova o PC do tailnet no console do Tailscale.",
    "done.settings_updated": "Configurações salvas.",
    # --- Results-site sync box (Provas and Equipes) ---
    "sync.event": "Evento <code>{event_id}</code> no site de resultados.",
    "sync.last": "Última sincronização: {time}",
    "sync.ok": "ok",
    "sync.failed": "falhou",
    "sync.never": "Ainda não sincronizado.",
    "sync.schedule": "(automática a cada 6 h e quando o evento muda)",
    "sync.now": "Sincronizar agora",
    "sync.no_event": (
        "Nenhum evento configurado. Defina o código do evento (ex.: <code>26BR</code>) em "
        '<a href="/review/admin/settings">Configurações</a> para trazer provas e equipes do '
        "site de resultados."
    ),
    # --- Provas ---
    "provas.hint": (
        "A lista que os revisores escolhem no formulário: primeiro as provas do site de "
        "resultados (na ordem de lá), depois as manuais, para categorias de clipe que não são "
        "provas (“Abertura”, “Pódio”). Desativar esconde a prova do formulário sem afetar "
        "vídeos que já a têm. Renomear (só manuais; as do site são renomeadas pela "
        "sincronização) muda o nome em todos os vídeos, mas os clipes continuam na mesma pasta "
        "do estúdio (coluna <em>Pasta</em>, fixa desde a criação)."
    ),
    "provas.col.order": "Ordem",
    "provas.col.name": "Nome",
    "provas.col.source": "Origem",
    "provas.col.folder": "Pasta",
    "provas.col.status": "Situação",
    "provas.move_up": "Subir {name}",
    "provas.move_down": "Descer {name}",
    "provas.name_of": "Nome da prova {name}",
    "provas.rename": "Renomear",
    "source.manual": "manual",
    "source.site": "site",
    "provas.active": "Ativa",
    "provas.inactive": "Inativa",
    "provas.deactivate": "Desativar",
    "provas.reactivate": "Reativar",
    "provas.empty": (
        "Nenhuma prova ainda. Configure o evento e sincronize, ou adicione uma manual abaixo."
    ),
    "provas.add": "Adicionar prova manual",
    "provas.name": "Nome",
    "provas.name.placeholder": "Ex.: Abertura, Bastidores, Pódio",
    "provas.only_manual_move": "Só provas manuais mudam de ordem aqui.",
    "provas.name_length": "O nome precisa ter de 1 a 64 caracteres.",
    # --- Teams (Equipes) ---
    "teams.hint": (
        "O nome da equipe que aparece na revisão, pelo número do carro, vem do site de "
        "resultados. No clipe aparecem o nome curto e a sigla da escola. Corrija aqui o que "
        "estiver errado: a correção vale até ser desfeita, mesmo depois de novas "
        "sincronizações. Carros que não estão no site podem ser adicionados à mão."
    ),
    "teams.col.car": "Carro",
    "teams.col.team": "Equipe",
    "teams.col.short_name": "Nome curto (clipe)",
    "teams.col.school": "Escola",
    "teams.col.school_short": "Sigla (clipe)",
    "teams.col.source": "Origem",
    "teams.team_of": "Equipe do carro {car}",
    "teams.short_name_of": "Nome curto do carro {car}",
    "teams.school_of": "Escola do carro {car}",
    "teams.school_short_of": "Sigla da escola do carro {car}",
    "teams.on_site": "No site: {team} / {short}",
    "teams.overridden": "corrigida",
    "teams.not_in_event": "fora do evento",
    "teams.save_override": "Salvar correção",
    "teams.remove": "Remover",
    "teams.remove.confirm": "Remover o carro {car} ({team})?",
    "teams.undo_override": "Desfazer correção",
    "teams.undo_override.confirm": ("Desfazer a correção do carro {car} e voltar ao nome do site?"),
    "teams.empty": "Nenhuma equipe ainda. Configure o evento e sincronize.",
    "teams.add": "Adicionar carro que não está no site",
    "teams.car": "Carro",
    "teams.team": "Equipe",
    "teams.short_name": "Nome curto",
    "teams.school": "Escola",
    "teams.school_short": "Sigla da escola",
    "teams.required": "Número do carro (0 a 9999) e nome da equipe são obrigatórios.",
    # --- Settings (Configurações) ---
    "settings.event_id": "Evento no site de resultados (código de 4 letras/números)",
    "settings.event_id.hint": (
        "Provas e equipes vêm deste evento: ao mudar o código, ao clicar em “Sincronizar” "
        "(páginas Provas e Equipes) e a cada 6 h. Maiúsculas e minúsculas importam "
        "(<code>26BR</code>, não <code>26br</code>)."
    ),
    "settings.results_api": "Endereço do site de resultados (API)",
    "settings.intake_mode": "Quem pode enviar vídeos",
    "intake_mode.open": "Aberto (todos, menos bloqueados)",
    "intake_mode.allowlist": "Só permitidos",
    "settings.intake_mode.hint": (
        "Em “só permitidos”, quem não está permitido em Contatos recebe uma vez por dia: "
        "“Este número não está autorizado a enviar vídeos. Fale com a organização.”"
    ),
    "settings.clip_team_name": "Nome da equipe nos vídeos",
    "settings.clip_school_name": "Nome da escola nos vídeos",
    "team_name_style.full": "completo",
    "team_name_style.short": "curto",
    "school_name_style.full": "completo",
    "school_name_style.short": "sigla",
    "settings.clip_names.hint": (
        "Vale para a legenda dos clipes renderizados daqui em diante e para o que esta "
        "interface mostra de cada clipe. Quando falta o nome curto ou a sigla, usa o completo "
        "(e vice-versa)."
    ),
    "settings.inbox_hold": "Caixa de entrada quase cheia a partir de (GB)",
    "settings.inbox_hold.hint": (
        "Acima disso o bot pede para esperar. O limite máximo ({cap} GB, tamanho do disco da "
        "caixa de entrada) só muda no servidor."
    ),
    "settings.proxy_evict": "Apagar proxies antigos quando o disco tiver menos de (GB livres)",
    "settings.discard_grace": "Prazo para restaurar um vídeo descartado (horas)",
    "settings.discard_grace.hint": (
        "Durante esse prazo o vídeo original fica guardado e “Restaurar” funciona. Vale para "
        "os próximos descartes; quem já foi descartado mantém o prazo que tinha."
    ),
    "settings.outbox_keep": ("Guardar na caixa de saída, esperando o arquivo do mestre (horas)"),
    "settings.outbox_keep.hint": (
        "Depois que o estúdio recebeu o clipe, a cópia do servidor espera o mestre arquivá-lo "
        "por no máximo esse tempo (para o caso de o mestre estar desligado)."
    ),
    "settings.installer_url": "Link do instalador (mostrado junto com cada token novo)",
    "settings.status": "Situação",
    "settings.evolution": ("WhatsApp (Evolution, instância <code>{instance}</code>)"),
    "settings.connected": "Conectado",
    "settings.no_answer": "Sem resposta",
    "settings.webhook": "Webhook",
    "settings.webhook.on": "ligado",
    "settings.webhook.off": "desligado",
    "settings.secrets": (
        "Segredos (no arquivo <code>.env</code> do servidor; nunca mostrados aqui)"
    ),
    "settings.secret.configured": "configurado",
    "settings.secret.missing": "faltando",
    "settings.results_sync": "Sincronização com o site de resultados",
    "settings.results_sync.ok": "ok: provas +{provas}, equipes +{teams}",
    "settings.results_sync.failed": "falhou",
    "settings.results_sync.never": "nunca",
    "settings.server_disk": "Disco do servidor",
    "settings.server_disk.usage": (
        "Caixa de entrada {inbox} de {cap} · proxies {proxies} · saída {outbox} · livre {free}"
    ),
    "settings.invalid": (
        "Os limites em GB precisam ser números positivos, os prazos em horas números de 0 a "
        "720, e o evento tem 4 letras ou números."
    ),
}
