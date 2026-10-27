# Guia para quem envia os vídeos (voluntários)

<!-- For the operator: this answers design §12 open question 1. Pin the
     "Mensagem para fixar" block in the volunteers' WhatsApp group (copy the
     text inside the box, it already uses WhatsApp's *bold* formatting) and
     replace <NÚMERO DO BAJA BOT> first. The bot's replies quoted below are
     the exact texts in orchestrator/domain/bot_messages.py; a test keeps
     them in sync (tests/orchestrator/test_bot_replies.py). -->

## Mensagem para fixar no grupo

```text
🎥 *VÍDEOS DAS PASSAGENS: COMO ENVIAR*

1. Mande cada vídeo no *privado* para o Baja Bot: *<NÚMERO DO BAJA BOT>*
   (salve o número nos contatos). Não mande aqui no grupo.
2. Envie como *DOCUMENTO*, não como vídeo. Como vídeo, o WhatsApp estraga a qualidade.
   • Android: 📎 → Documento → Procurar outros documentos → escolha o vídeo
   • iPhone: + → Documento → Escolher foto ou vídeo → escolha o vídeo
3. Um vídeo por passagem. Não precisa escrever nada nem renomear o arquivo.
4. Espere a resposta do bot:
   ✅ "Vídeo recebido!" → deu certo, pode mandar o próximo.
   ⚠️ "o WhatsApp reduziu a qualidade" → você mandou como vídeo; mande de novo como documento.
   ⏳ "fila está quase cheia" / "fila está cheia" → espere uns minutos antes de mandar mais (no "cheia", reenvie esse).
   ❌ "Não conseguimos receber" → mande de novo como documento.
5. Só apague o vídeo do celular depois do ✅.

Dúvidas: fale com o João.
```

---

## Passo a passo com mais detalhes

### Por que "documento"?

Quando você manda pela galeria (como *vídeo*), o WhatsApp comprime: o
vídeo fica pequeno e borrado, e não dá para usar na transmissão. Como
*documento*, o arquivo chega exatamente como saiu da câmera. O limite do
WhatsApp para documentos é de cerca de 2 GB por arquivo, mais do que
suficiente para uma passagem.

### Android

1. Abra a conversa com o **Baja Bot** (no privado, não no grupo).
2. Toque no **📎 (clipe)** na caixa de mensagem.
3. Toque em **Documento**.
4. O WhatsApp mostra alguns arquivos recentes. Se o vídeo não estiver ali,
   toque em **Procurar outros documentos** e abra **Vídeos** (ou
   **Imagens → Câmera / DCIM**, depende do celular).
5. Toque no vídeo e depois em **Enviar**.

Dica: na lista, o nome do arquivo do vídeo costuma começar com `VID_` e
trazer a data e a hora (ex.: `VID_20261025_101500.mp4`). Pegue o mais
recente.

### iPhone

1. Abra a conversa com o **Baja Bot** (no privado, não no grupo).
2. Toque no **+** ao lado da caixa de mensagem.
3. Toque em **Documento**.
4. Toque em **Escolher foto ou vídeo**, escolha o vídeo e toque em
   **Enviar**.

Se a opção *Escolher foto ou vídeo* não aparecer (WhatsApp antigo):
atualize o WhatsApp. Ou, no app **Fotos**, abra o vídeo → **Compartilhar**
→ **Salvar em Arquivos**, e depois no WhatsApp: **+** → **Documento** →
escolha o vídeo na pasta onde salvou.

### Não precisa

* Renomear o arquivo: o nome não importa.
* Escrever a prova, o carro ou a passagem: quem revisa preenche vendo o
  vídeo.
* Mandar no grupo: os vídeos vão só no privado do Baja Bot.

### Se mandou errado

* Mandou como vídeo? O bot avisa. Mande de novo como documento; quem
  revisa descarta a cópia ruim.
* Mandou o vídeo errado, ou repetido? Tudo bem: quem revisa descarta.

## O que o bot responde

Estas são as respostas exatas do Baja Bot e o que fazer em cada caso.

**Deu certo.** Pode mandar o próximo vídeo.

> ✅ Vídeo recebido! Obrigado.

**Chegou como vídeo comprimido.** O vídeo foi aproveitado, mas com
qualidade pior. Se ainda tiver o original, mande de novo como documento.

> Recebemos seu vídeo, mas o WhatsApp reduziu a qualidade dele. Se puder, envie de novo como *documento* (📎 → Documento) para usarmos o original. Obrigado!

**Recebido, mas a fila está quase cheia.** Este vídeo chegou. Espere
alguns minutos antes de mandar os próximos.

> Recebemos seu vídeo! Nossa fila está quase cheia: por favor, espere alguns minutos antes de enviar os próximos.

**Fila cheia: este vídeo NÃO foi recebido.** Espere alguns minutos e
mande este mesmo vídeo de novo.

> Nossa fila está cheia e não conseguimos receber este vídeo agora. Por favor, espere alguns minutos e envie de novo.

**Falhou.** Algo deu errado no envio. Mande de novo, como documento.

> Não conseguimos receber seu vídeo. Por favor, envie de novo como *documento* (📎 → Documento).

**Sem resposta depois de alguns minutos?** Confira se mandou para o número
certo, no privado. Se sim, avise o João e **não apague o vídeo** do celular.
