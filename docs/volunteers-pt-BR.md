# Guia para quem envia os vídeos (voluntários)

<!-- For the operator: this answers design §12 open question 1. Pin the
     "Mensagem para fixar" block in the volunteers' WhatsApp group (copy the
     text inside the box, it already uses WhatsApp's *bold* formatting) and
     replace <NÚMERO DO BAJA BOT> first. The bot's replies quoted below are
     the exact texts in orchestrator/domain/bot_messages.py; a test keeps
     them in sync (tests/orchestrator/test_bot_replies.py). Since
     2026-11 the bot reacts on the video (✅ ⚠️ ❌) and sends texts as
     quoted replies to it (orchestrator/domain/bot_replies.py). -->

## Mensagem para fixar no grupo

```text
🎥 *VÍDEOS DAS PASSAGENS: COMO ENVIAR*

1. Mande cada vídeo no *privado* para o Baja Bot: *<NÚMERO DO BAJA BOT>*
   (salve o número nos contatos). Não mande aqui no grupo.
2. Envie como *DOCUMENTO*, não como vídeo. Como vídeo, o WhatsApp estraga a qualidade.
   • Android: 📎 → Documento → Procurar outros documentos → escolha o vídeo
   • iPhone: + → Documento → Escolher foto ou vídeo → escolha o vídeo
3. Um vídeo por passagem. Não precisa escrever nada nem renomear o arquivo.
4. O bot responde *no próprio vídeo* (uma reação nele):
   ✅ → deu certo, pode mandar o próximo. O bot não escreve nada.
   ⚠️ → o vídeo foi aproveitado, mas leia a resposta do bot logo abaixo dele.
   ❌ → este vídeo NÃO foi recebido; leia a resposta e mande de novo.
   Quando o bot escreve, a mensagem é uma *resposta* ao vídeo de que fala.
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
* Mandar no grupo: o bot ignora vídeos postados em grupos. Só vale no
  privado do Baja Bot.

### Se mandou errado

* Mandou como vídeo? O bot avisa. Mande de novo como documento; quem
  revisa descarta a cópia ruim.
* Mandou o vídeo errado, ou repetido? Tudo bem: quem revisa descarta.

## O que o bot responde

O bot responde com uma **reação no próprio vídeo**, para você saber de
qual vídeo ele está falando mesmo mandando vários seguidos. Quando ele
precisa explicar algo, manda também um texto como **resposta** àquele
vídeo (aparece com o vídeo citado em cima). Estas são as respostas exatas
e o que fazer em cada caso.

**✅ Deu certo.** O vídeo chegou e foi conferido. O bot não escreve nada:
pode mandar o próximo.

**⚠️ Chegou como vídeo comprimido.** O vídeo foi aproveitado, mas com
qualidade pior. Se ainda tiver o original, mande de novo como documento.

> Recebemos este vídeo, mas o WhatsApp reduziu a qualidade dele. Se puder, envie de novo como *documento* (📎 → Documento) para usarmos o original. Obrigado!

**⚠️ Recebido, mas a fila está quase cheia.** Este vídeo chegou. Espere
alguns minutos antes de mandar os próximos.

> Recebemos este vídeo! Nossa fila está quase cheia: por favor, espere alguns minutos antes de enviar os próximos.

**❌ Fila cheia: este vídeo NÃO foi recebido.** Espere alguns minutos e
mande este mesmo vídeo de novo.

> Nossa fila está cheia e não conseguimos receber este vídeo agora. Por favor, espere alguns minutos e envie de novo.

**❌ Falhou.** Algo deu errado no envio. Mande de novo, como documento.

> Não conseguimos receber este vídeo. Por favor, envie de novo como *documento* (📎 → Documento).

**❌ Número não autorizado.** Neste evento só números cadastrados podem
mandar vídeos, e o seu ainda não está na lista. O vídeo NÃO foi recebido.
Fale com a organização para liberarem seu número e depois mande de novo.
(O bot avisa no máximo uma vez por dia; nos outros vídeos desse dia não
aparece reação nenhuma.)

> Este número não está autorizado a enviar vídeos. Fale com a organização.

**Nenhuma reação no vídeo depois de alguns minutos?** Confira se mandou para o número
certo, no privado. Se sim, avise o João e **não apague o vídeo** do celular.
