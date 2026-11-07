"""Catalog: official pass results in the overlay (LIVESTACK-47).

The job page's "Mostrar resultado da passagem" and its status line, the
sentences for a clip whose result changed, and the Settings page's
"Resultados das passagens" block (configuration, "Testar", fetch status).
"""

TEXT: dict[str, str] = {
    # --- Job page: the toggle and its status line (pass_result_view.py) ---
    "pass_result.show": "Mostrar resultado da passagem",
    "pass_result.on_air": "Na tarja: {text}",
    "pass_result.not_burned": "Não será gravado (resultado: {text}).",
    # One sentence per PassResultProblem (tests check every value).
    "pass_result.problem.not_configured": (
        "Esta prova não está na configuração dos resultados (Configurações)."
    ),
    "pass_result.problem.no_event": "Defina o evento nas Configurações.",
    "pass_result.problem.incomplete_review": (
        "Salve o número do carro e a passagem para buscar o resultado."
    ),
    "pass_result.problem.not_fetched": (
        "A tabela de resultados desta prova ainda não foi baixada."
    ),
    "pass_result.problem.column_missing": (
        "A coluna configurada não está na tabela de resultados; confira as Configurações."
    ),
    "pass_result.problem.car_not_in_table": (
        "Este carro não está na tabela de resultados desta prova."
    ),
    "pass_result.problem.no_such_run": (
        "A tabela de resultados ainda não tem esta passagem deste carro."
    ),
    "pass_result.problem.no_result_yet": "Ainda sem resultado oficial para esta passagem.",
    "pass_result.problem.not_a_number": (
        "O resultado oficial desta passagem não é um número, então não vai na tarja."
    ),
    "pass_result.failing": (
        "API de resultados inacessível desde {since} (último resultado de {last})."
    ),
    "pass_result.failing.never_fetched": (
        "API de resultados inacessível desde {since}; nenhum resultado baixado ainda."
    ),
    # --- The clip on air no longer matches (clip_inputs_view.py) ---
    "outdated.pass_result.published": "O resultado oficial da passagem foi publicado: “{now}”.",
    "outdated.pass_result.changed": (
        "O resultado oficial da passagem foi alterado: o clipe no ar mostra “{clip}”; "
        "agora seria “{now}”."
    ),
    "outdated.pass_result.removed": (
        "O clipe no ar mostra o resultado “{clip}”; a revisão agora diz para não mostrar."
    ),
    # --- Waiting reason, Frota, audit ---
    "waiting.no_holder_can_render_pass_result": (
        "Esperando um PC com o original que saiba desenhar o resultado da passagem "
        "(Baja Worker 0.5.0 ou mais novo)."
    ),
    "render_feature.pass_result": "resultado da passagem",
    "event.pass_results_config_updated": "configuração dos resultados das passagens alterada",
    "done.pass_results_saved": "Configuração dos resultados das passagens salva.",
    # --- Configurações: "Resultados das passagens" (templates/admin/_pass_results.html) ---
    "pass_results.title": "Resultados das passagens",
    "pass_results.hint": (
        "Quais provas mostram o resultado oficial na tarja do clipe, de qual coluna da API de "
        "resultados, e o nome de cada penalidade. É um texto JSON; o formato está em "
        "<code>docs/design.md</code> §31. Chaves que começam com <code>_</code> são ignoradas "
        "(servem de comentário). Vazio desliga os resultados."
    ),
    "pass_results.config": "Configuração (JSON)",
    "pass_results.unavailable": (
        "Não foi possível ler a configuração dos resultados agora; recarregue a página."
    ),
    "pass_results.test": "Testar",
    "pass_results.test.hint": (
        "Testa o texto da caixa acima, mesmo sem salvar: busca a tabela da prova agora e monta "
        "a linha da tarja para um carro e uma passagem."
    ),
    "pass_results.test.prova": "Prova (id na API)",
    "pass_results.test.car": "Carro",
    "pass_results.test.passagem": "Passagem",
    "pass_results.test.missing_fields": (
        "Para testar, preencha a prova (1 a 3 letras ou números), o carro (0 a 9999) e a "
        "passagem (1 a 999)."
    ),
    "pass_results.test.title": "Teste: prova {prova}, carro {car}, passagem {passagem}",
    "pass_results.test.unsaved": "O teste usou o texto da caixa, que ainda não foi salvo.",
    "pass_results.test.fetched": "Tabela <code>{table}</code> baixada {time}.",
    "pass_results.test.unknown_penalties": (
        "Ícones de penalidade sem nome na configuração: {names}"
    ),
    "pass_results.test.row": "Linha do carro na tabela",
    "pass_results.test.row_n": "Linha {n} do carro na tabela",
    "pass_results.col.column": "Coluna",
    "pass_results.col.raw": "Valor",
    "pass_results.col.display": "Como o site mostra",
    "pass_results.status": "Provas configuradas",
    "pass_results.status.empty": "Nenhuma prova configurada.",
    "pass_results.col.prova_id": "Id",
    "pass_results.col.prova": "Nossa prova",
    "pass_results.col.table": "Tabela",
    "pass_results.col.fetch": "Busca",
    "pass_results.no_synced_prova": "nenhuma prova sincronizada com esse id",
    "pass_results.no_event": "sem evento definido",
    "pass_results.polling": "buscando a cada 30 s",
    "pass_results.not_polling": "parada: nenhum vídeo desta prova precisa agora",
    "pass_results.last_fetch.ok": "última busca {time}: ok",
    "pass_results.last_fetch.failed": "última busca {time}: falhou",
    "pass_results.never_fetched": "nunca buscada",
    "pass_results.failing_since": "inacessível desde {time}",
    # --- A configuration refused with 422 (details {path, problem, ...}) ---
    "pass_results.refused.save": (
        "Configuração não salva: {reason}. A configuração anterior continua valendo."
    ),
    "pass_results.refused.test": "Teste não feito: {reason}.",
    "pass_results.refused.at": "em <code>{path}</code>, {problem}",
    "pass_results.config_problem.not_json": (
        "o texto não é JSON válido (linha {line}, coluna {column})"
    ),
    "pass_results.config_problem.wrong_type": "o valor deve ser {expected}",
    "pass_results.config_problem.unknown_key": "chave desconhecida (aceitas: {allowed})",
    "pass_results.config_problem.missing_key": "falta esta chave, que é obrigatória",
    "pass_results.config_problem.empty_text": "o texto não pode ficar vazio",
    "pass_results.config_problem.too_long": ("o texto pode ter no máximo {max_length} caracteres"),
    "pass_results.config_problem.out_of_range": "o valor deve estar entre {minimum} e {maximum}",
    "pass_results.config_problem.not_one_of": "o valor deve ser um destes: {choices}",
    "pass_results.config_problem.bad_id": "o id deve ter de 1 a 3 letras ou números",
    "pass_results.config_problem.bad_name": (
        "o nome do ícone só pode ter letras, números, - e _ (até 40)"
    ),
    "pass_results.config_problem.nothing_to_show": (
        "a prova precisa de um bloco <code>result</code> ou <code>clock</code>"
    ),
    # A problem code this UI does not know yet; the English message follows.
    "pass_results.config_problem.other": "a configuração não é válida",
    "pass_results.expected.object": "um objeto (entre chaves)",
    "pass_results.expected.text": "um texto entre aspas",
    "pass_results.expected.boolean": "<code>true</code> ou <code>false</code>",
    "pass_results.expected.whole_number": "um número inteiro",
}
