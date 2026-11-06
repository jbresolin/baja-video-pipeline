"""Catalog: the admin pages for people and machines (users, contacts, enrollment, workers).

The admin pages were written in pt-BR from the start (LIVESTACK-37); their
wording is kept as it was. Only terms that two pages named differently were
aligned (see the glossary in the LIVESTACK-39 pull request).
"""

TEXT: dict[str, str] = {
    # --- Every admin page (admin/_layout.html) ---
    "admin.heading": "Administração",
    "admin.subnav": "Páginas de administração",
    "admin.section.users": "Usuários",
    "admin.section.contacts": "Contatos",
    "admin.section.provas": "Provas",
    "admin.section.teams": "Equipes",
    "admin.section.enrollment": "Cadastro de PCs",
    "admin.section.workers": "Workers",
    "admin.section.settings": "Configurações",
    "admin.audit": "Últimas alterações",
    "admin.audit.empty": "Nenhuma alteração ainda.",
    "admin.add": "Adicionar",
    "admin.save": "Salvar",
    "admin.revoke": "Revogar",
    "admin.filter": "Filtrar",
    # --- Users ---
    "users.hint": (
        "Quem entra na revisão. Revisores revisam vídeos; administradores também abrem "
        "estas páginas. Usuários não são apagados: desative quem não deve mais entrar."
    ),
    "users.col.username": "Usuário",
    "users.col.role": "Papel",
    "users.col.active": "Ativo",
    "users.col.new_password": "Nova senha",
    "users.role_of": "Papel de {username}",
    "users.status_of": "Situação de {username}",
    "users.active": "Ativo",
    "users.disabled": "Desativado",
    "users.keep_password": "deixe vazio para manter",
    "users.new_password_of": "Nova senha de {username}",
    "users.add": "Adicionar usuário",
    "users.username": "Usuário",
    "users.password": "Senha",
    "users.role": "Papel",
    "users.invalid": (
        "Nome: letras, números e . _ @ - (sem espaços). Senha: de 8 a 72 caracteres."
    ),
    "users.password_length": "A nova senha precisa ter de 8 a 72 caracteres.",
    # --- Contacts ---
    "contacts.hint": (
        "Todo número que escreve para o bot aparece aqui como <em>Desconhecido</em>. "
        "Permita ou bloqueie com um clique. No modo “só permitidos” (Configurações), só "
        "números permitidos geram vídeos. Bloqueados nunca geram vídeo nem recebem "
        "resposta. Com uma prova padrão, os vídeos do contato já chegam com ela preenchida."
    ),
    "contacts.show": "Mostrar",
    "contacts.all": "Todos",
    "contact_status.allowed": "Permitido",
    "contact_status.blocked": "Bloqueado",
    "contact_status.unknown": "Desconhecido",
    "contacts.col.phone": "Número",
    "contacts.col.status": "Situação",
    "contacts.col.name": "Nome",
    "contacts.col.default_prova": "Prova padrão",
    "contacts.col.notes": "Notas",
    "contacts.col.last_message": "Última mensagem",
    "contacts.whatsapp_name": "WhatsApp: {name}",
    "contacts.allow": "Permitir",
    "contacts.inline.allowed": "Contato permitido.",
    "contacts.inline.blocked": "Contato bloqueado.",
    "contacts.block": "Bloquear",
    "contacts.name_of": "Nome de {phone}",
    "contacts.default_prova_of": "Prova padrão de {phone}",
    "contacts.notes_of": "Notas de {phone}",
    "contacts.never_wrote": "nunca escreveu",
    "contacts.empty": "Nenhum contato ainda.",
    "contacts.empty_with_status": "Nenhum contato com essa situação ainda.",
    "contacts.add": "Adicionar contato",
    "contacts.add.hint": "Para liberar alguém antes de a pessoa escrever (modo “só permitidos”).",
    "contacts.phone": "Número (com DDI e DDD)",
    "contacts.name": "Nome",
    "contacts.status": "Situação",
    "contacts.default_prova": "Prova padrão",
    "contacts.notes": "Notas",
    "contacts.invalid_phone": "Número inválido: use DDI + DDD + número.",
    # --- Enrollment (Cadastro de PCs) ---
    "enrollment.created": (
        "<strong>Token para {name}</strong> ({role}), válido até {until}. Ele aparece "
        "<strong>só agora</strong>: copie e envie para a pessoa."
    ),
    "enrollment.copy": "Copiar",
    "enrollment.installer": "Instalador:",
    "enrollment.created.hint": (
        "No PC: instalar o BajaWorker e colar o token na página de configuração que abre no "
        "fim. Cada token serve uma única vez."
    ),
    "enrollment.new_token": "Novo token",
    "enrollment.volunteer": "Voluntário",
    "enrollment.volunteer.placeholder": "Nome de quem vai instalar",
    "enrollment.role": "Papel",
    "enrollment.validity": "Validade",
    "enrollment.validity.24": "1 dia",
    "enrollment.validity.72": "3 dias",
    "enrollment.validity.168": "7 dias",
    "enrollment.validity.720": "30 dias",
    "enrollment.create": "Gerar token",
    "enrollment.tokens": "Tokens",
    "enrollment.col.volunteer": "Voluntário",
    "enrollment.col.role": "Papel",
    "enrollment.col.status": "Situação",
    "enrollment.col.created": "Criado",
    "enrollment.col.expires": "Validade",
    "enrollment.col.used_by": "Usado por",
    "enrollment.revoke.confirm": "Revogar o token de {name}?",
    "enrollment.empty": "Nenhum token ainda.",
    "enrollment.empty_unused": "Nenhum token por usar. Os já usados estão abaixo.",
    "enrollment.other_section": "Usados, expirados e revogados ({count})",
    "enrollment.name_required": "Informe o nome do voluntário.",
    "token_status.unused": "Não usado",
    "token_status.used": "Usado",
    "token_status.expired": "Expirado",
    "token_status.revoked": "Revogado",
    # --- Workers ---
    "workers.hint": (
        "Os PCs cadastrados (a mesma informação da página Frota). Revogar um worker invalida "
        "o token dele na hora: ele para de receber tarefas e deixa de contar como cópia "
        "dos vídeos originais."
    ),
    "workers.master_hint": (
        "Só existe um master por vez: “Tornar master” passa o papel para outro PC e o master "
        "atual volta a ser um worker comum. Vale só entre master e worker."
    ),
    "workers.tailnet_warning": (
        "<strong>Depois de revogar, remova o PC do tailnet à mão:</strong> no console do "
        'Tailscale (<a href="https://login.tailscale.com/admin/machines" rel="noopener" '
        'target="_blank">login.tailscale.com/admin/machines</a>), procure a máquina '
        "<code>baja-w-…</code> (ou <code>baja-studio</code>) e escolha "
        '<em lang="en">Remove</em>.'
    ),
    "workers.col.name": "Nome",
    "workers.col.role": "Papel",
    "workers.col.status": "Situação",
    "workers.col.last_contact": "Último contato",
    "workers.col.held_raws": "Originais guardados",
    "workers.col.enrolled": "Cadastrado",
    "workers.revoked_at": "Revogado {time}",
    "workers.revoke.confirm": (
        "Revogar {name}? O PC para de trabalhar na hora e precisa de um token novo para voltar."
    ),
    "workers.empty": "Nenhum worker cadastrado ainda. Gere um token em “Cadastro de PCs”.",
    "workers.revoked_section": "Revogados ({count})",
    "workers.make_master": "Tornar master",
    "workers.make_master.confirm": (
        "Tornar {name} o master? {current} deixa de ser o master e volta a ser um worker "
        "comum. Os dois PCs reiniciam o worker sozinhos em alguns segundos."
    ),
    "workers.make_master.confirm_no_master": (
        "Tornar {name} o master? Os PCs reiniciam o worker sozinhos em alguns segundos."
    ),
    "workers.studio_not_master": (
        "O estúdio não vira master: ele usa outra etiqueta na rede (tag:studio). Para isso, "
        "cadastre o PC de novo com um token de master."
    ),
    "workers.name_of": "Nome de exibição de {name}",
    "workers.rename": "Salvar nome",
    "workers.name_required": "Informe um nome para o worker.",
    "workers.hostname": "PC: {hostname}",
}
