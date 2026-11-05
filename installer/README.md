# Baja Worker para Windows: guia do voluntário

Obrigado por emprestar o seu PC! O **Baja Worker** é um programa que roda
em segundo plano durante o evento: ele recebe os vídeos das passagens,
prepara versões leves para os revisores e edita os vídeos finais que vão
para a transmissão. Você instala uma vez, cola um código (o "token") e
pronto: ele funciona sozinho, mesmo sem ninguém logado no Windows.

**Você vai precisar de:**

* Windows 10 ou 11, 64 bits;
* espaço livre em disco: quanto mais, melhor (pelo menos 50 GB);
* o **token de inscrição** que a equipe Baja mandou para você (um código
  longo, só vale uma vez);
* uma placa de vídeo NVIDIA ajuda bastante, mas não é obrigatória.

---

## 1. Baixar

Baixe o arquivo **`BajaWorker-Setup-<versão>.exe`** do link que a equipe
Baja enviou (página *Releases* do projeto no GitHub).

> ![Página de download com o arquivo BajaWorker-Setup](docs/img/01-download.png)

## 2. Aviso do Windows (SmartScreen)

O instalador não tem assinatura digital paga, então o Windows mostra
**"O Windows protegeu o computador"** e "Editor desconhecido". Isso é
esperado. Para continuar:

1. clique em **Mais informações**;
2. clique em **Executar assim mesmo**.

> ![SmartScreen: "Mais informações"](docs/img/02-smartscreen-mais-informacoes.png)
>
> ![SmartScreen: "Executar assim mesmo"](docs/img/03-smartscreen-executar.png)

Se o seu antivírus bloquear o arquivo, fale com a equipe Baja antes de
liberar.

## 3. Permissão de administrador (UAC)

O Windows pergunta **"Deseja permitir que este aplicativo faça alterações
no seu dispositivo?"**. Clique em **Sim**. É a única vez que ele pergunta
durante a instalação.

> ![Pergunta do Controle de Conta de Usuário](docs/img/04-uac.png)

## 4. Instalar

Clique em **Avançar** até o fim. O instalador:

* coloca o programa em `C:\Program Files\BajaWorker`;
* cria a pasta de dados `C:\BajaWorker` (os vídeos ficam aqui, a menos
  que você escolha outra pasta no passo 5);
* instala o **Tailscale** (a rede privada da equipe), se ainda não estiver
  instalado;
* registra o serviço do Windows **Baja Worker**, que liga sozinho junto
  com o PC.

Na última tela deixe marcado **"Abrir a configuração e conectar à rede
Baja"** e clique em **Concluir**.

> ![Última tela do instalador](docs/img/05-concluir.png)

Duas coisas abrem:

* uma **janela preta** (a "janela de conexão"). **Deixe ela aberta**: ela
  espera você terminar o passo 5;
* o **navegador**, na página de configuração `http://localhost:8700`.

## 5. Página de configuração

> ![Página de configuração com os três campos](docs/img/06-configuracao.png)

A página (em inglês) tem três campos:

| Campo | O que colocar |
| --- | --- |
| **Enrollment token** | O token que a equipe Baja mandou. Cole exatamente como veio |
| **Working folder** | A pasta onde os vídeos ficam. Já vem `C:\BajaWorker`. Se o disco C: estiver cheio, use outro disco, por exemplo `D:\BajaWorker` (sempre o caminho completo, começando pela letra do disco) |
| **Storage cap in GB** | Quanto espaço o Baja Worker pode usar no máximo, em GB. Ele nunca passa disso; perto do limite ele para de pegar vídeos novos |

Clique em **Save and connect**.

## 6. Conexão com a rede Baja

Volte para a **janela preta**. Em alguns segundos ela mostra:

```
Conectando à rede Baja como baja-w-1a2b3c4d…
Reiniciando o serviço BajaWorker…
Pronto! Conectado à rede Baja (...).
```

Aperte **Enter** para fechar a janela. Acabou!

**Se você já usa Tailscale** em outra rede (do trabalho, por exemplo), a
janela pergunta se pode trocar para a rede Baja, porque o Windows só fica
em uma rede Tailscale por vez. Responda `s` para continuar ou `n` para
cancelar e falar com a equipe antes.

**Fechou a janela sem querer ou reiniciou o PC no meio?** Abra o menu
Iniciar → **Baja Worker - concluir conexão**. O Windows pede permissão de
administrador de novo; clique em **Sim**.

## 7. O ícone perto do relógio

Depois de instalar, aparece um **ícone redondo com um "B"** na área de
notificação do Windows (perto do relógio; se não aparecer, clique na
setinha **^** para ver os ícones escondidos e arraste-o para fora). Ele
abre sozinho toda vez que alguém faz login no PC. A **cor** diz como o
Baja Worker está, sem precisar abrir nada:

> ![Ícone do Baja Worker perto do relógio](docs/img/09-icone.png)

| Cor | Significa | O que fazer |
| --- | --- | --- |
| 🟢 **Verde** | Trabalhando: está baixando, preparando ou editando um vídeo | Nada |
| 🔵 **Azul** | Conectado e esperando vídeos | Nada |
| 🟡 **Amarelo** | Pausado, ou esperando você: falta concluir a conexão, falta o token, ou está iniciando | Veja a frase ao passar o mouse; se for "conexão não concluída", use **Concluir conexão** no menu do ícone |
| 🔴 **Vermelho** | Problema: sem conexão com o servidor há mais de 1 minuto, disco quase no limite, token recusado, ou o serviço parou sozinho | Passe o mouse para ver qual; se não resolver em alguns minutos, fale com a equipe Baja |
| ⚪ **Cinza** | Desligado (o serviço não está rodando) | Se foi sem querer: menu do ícone → **Ligar o worker** |

Passando o mouse sobre o ícone aparece uma frase, por exemplo
*"Baja Worker: processando 1 vídeo"* ou *"Baja Worker: sem conexão com o
servidor"*.

**Clique com o botão direito** no ícone para o menu:

> ![Menu do ícone](docs/img/10-menu-icone.png)

| Item | O que faz |
| --- | --- |
| **Abrir painel** | Abre a página de status `http://localhost:8700` (clicar duas vezes no ícone faz o mesmo) |
| **Pausar** / **Retomar** | Pausar: termina o vídeo que está fazendo e não pega nenhum novo, mas continua ligado e ajudando os outros PCs com os vídeos que já guardou. Continua pausado mesmo se o PC reiniciar, até você clicar em **Retomar**. Não pede senha de administrador |
| **Desligar o worker** / **Ligar o worker** | Desliga ou liga o serviço do Windows de vez. Não pede senha de administrador |
| **Concluir conexão** | Só aparece enquanto falta o passo 6. Abre a janela de conexão (pede permissão de administrador) |
| **Sair do ícone** | Fecha só o ícone. **O Baja Worker continua funcionando**; o ícone volta no próximo login, ou pelo menu Iniciar → **Baja Worker - ícone perto do relógio** |

O ícone só mostra **avisos do Windows** para coisas em que você precisa
agir: falta concluir a conexão, disco quase no limite, token recusado,
serviço parou sozinho. Nunca um aviso por vídeo.

> ![Aviso do Windows do Baja Worker](docs/img/11-aviso.png)

## 8. A página de status

Menu do ícone → **Abrir painel**, ou menu Iniciar → **Baja Worker**, abre
`http://localhost:8700`. Ela se atualiza sozinha a cada 5 segundos:

> ![Página de status](docs/img/07-status.png)

* uma **faixa grande colorida** com a mesma cor e a mesma frase do ícone;
* **Agora**: o que está fazendo (por exemplo *"Editando o clipe — Enduro,
  carro 42"*), ou "Nenhuma tarefa agora";
* **Espaço usado** do limite que você escolheu, e quantos **vídeos
  guardados** este PC tem;
* **Último contato com o servidor** (deve ser "há poucos segundos");
* os botões **Pausar** / **Retomar** e **Desligar o worker** (os mesmos do
  ícone);
* **Detalhes** (clique para abrir): informações técnicas para a equipe
  Baja, como o número deste PC, endereços, versão e o último erro.

Para saber se está tudo certo: o ícone está **verde** ou **azul**, e o
"último contato" é de poucos segundos atrás. O PC pode ficar com a tela
bloqueada ou sem ninguém logado; só não pode desligar nem entrar em
suspensão durante o evento. Em **Configurações → Sistema → Energia**,
coloque "Suspender" em **Nunca** enquanto estiver emprestando o PC.

## 9. Problemas comuns

| O que aparece | O que fazer |
| --- | --- |
| A página diz *"the enrollment token was refused"* | O token está errado, já foi usado ou venceu. Peça um novo à equipe e cole de novo |
| A página diz *"cannot enroll yet … retrying"* | Sem internet ou o servidor está fora do ar. Ele tenta de novo sozinho a cada 30 segundos |
| A janela preta diz *"Não foi possível entrar na rede Baja"* | Responda `s` para apagar o registro, peça um **novo token** à equipe e repita o passo 5 |
| A página `http://localhost:8700` não abre | Espere um minuto (o serviço pode estar iniciando). Se continuar, reinicie o PC |
| A página ou o ícone dizem *"Conexão não concluída"* | Falta o passo 6: menu do ícone → **Concluir conexão** (ou menu Iniciar → **Baja Worker - concluir conexão**) |
| O ícone fica vermelho, *"sem conexão com o servidor"* | Confira o ícone do Tailscale. Se estiver desconectado, use menu Iniciar → **Baja Worker - concluir conexão** |
| Não vejo o ícone | Clique na setinha **^** perto do relógio. Se não estiver lá: menu Iniciar → **Baja Worker - ícone perto do relógio** |

Se precisar mandar os registros para a equipe: eles ficam em
`C:\BajaWorker\logs\` (só administradores conseguem abrir essa pasta,
porque ela guarda a identidade do PC na equipe).

## 10. Desinstalar

**Configurações → Aplicativos → Aplicativos instalados → Baja Worker →
Desinstalar** (ou menu Iniciar → **Desinstalar Baja Worker**).

> ![Mensagem final da desinstalação](docs/img/08-desinstalar.png)

A desinstalação para e remove o serviço, tira as regras do firewall e
desconecta o PC da rede Baja. **A pasta `C:\BajaWorker` (e a outra pasta
de trabalho, se você escolheu uma) continua no disco**, porque guarda os
vídeos brutos. Só apague essa pasta quando a equipe Baja disser que os
vídeos já foram copiados. O Tailscale continua instalado; se não for usar,
desinstale-o também em Aplicativos.

## O que este programa faz no seu PC

* Usa a internet para baixar vídeos do servidor da Baja e de outros PCs da
  equipe, e para enviar vídeos prontos;
* usa a placa de vídeo (ou o processador) para editar os vídeos;
* aceita conexões só da rede privada da equipe (Tailscale), nas portas
  8710 e 8720;
* não lê nada fora da pasta de trabalho e não mostra nada na tela além do
  ícone perto do relógio e da página `http://localhost:8700`.
