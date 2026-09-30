# Viral Clip Studio

Aplicação de chat que traduz pedidos em linguagem natural para execuções do
pipeline **viral-clipper**. Você escreve *"faz 4 cortes de 45s, legenda neon,
para o TikTok"*; o agente transforma isso em parâmetros validados, mostra o
comando exato e só executa depois da sua confirmação.

Construído sobre o template `codebuddy-chat-web` (React + Vite + TDesign +
Express). O **laço de agente é próprio** (`server/llm.ts`), não o SDK — fala
OpenAI-format direto com os provedores configurados em `.codebuddy/models.json`.
A documentação original do template, ainda útil para entender hooks, banco e
frontend, está em `DEVELOPMENT.md`.

## Arquitetura

```
chat (React)
  │  o agente NUNCA escreve shell: devolve um objeto JSON de parâmetros
  ▼
server/llm.ts           ← laço de agente próprio: mensagens, tool_calls, fallback
  │  fala OpenAI-format (POST /chat/completions) com Vyce, Kilo, NVIDIA...
  │  SEM CODEBUDDY_API_KEY. Só executa as ferramentas que recebe.
  ▼
server/chatRoute.ts     ← as 4 ferramentas do Diretor + ponte SSE
  │  clip_plan → server/planPayload.ts  ← o contrato do cartão de confirmação
  │               (construtor único: produção e teste chamam o MESMO)
  ▼
server/clipOptions.ts   ← allowlist: única fonte de verdade sobre o que é aceito
  │  valida tipo, faixa e coerência; monta argv como array (sem string de shell)
  ▼
server/clipRunner.ts    ← gate de confirmação em duas etapas
  │  1. plan   → devolve o comando e espera aprovação humana
  │  2. commit → spawn, com cwd fixo na raiz do repositório
  │  cancel   → encerra o processo e a árvore dele (taskkill /T no Windows)
  ▼
python -m viralclipper  → output/*.mp4 + clips.json + viral_report.md
```

## Por que o desenho é assim

**1. O `cwd` não é preferência, é obrigatório.**
O pacote `viralclipper` **não está instalado** no venv. Ele é encontrado via
`sys.path[0] = cwd`. Rodar `-m viralclipper` de qualquer outro diretório falha
com **exit code 1 e sem mensagem útil** (medido nesta máquina). Todo spawn passa
por uma função que fixa `cwd = VIRCAL_ROOT`; um `cwd` vindo do cliente é
ignorado de propósito.

**2. A confirmação é do servidor, não do modelo.**
O agente não tem rota para aprovar um plano. `clip_commit` recusa qualquer plano
não aprovado — o gate não depende da boa vontade do LLM. Além disso, rodar é
caro: download + Whisper **em CPU** + encode x264 de cada corte.

**3. Allowlist fechada, não lista negra.**
`output_dir`, `work_dir`, `ffmpeg`, `ffprobe`, `extra_ytdlp_args` e
`cookies_from_browser` **não** estão na allowlist. São caminhos, binários e
vetores de execução arbitrária — não cabe ao chat decidir isso. Adicionar um
campo é uma decisão consciente de segurança, não um esquecimento.

**4. O que já vem ligado não precisa ser pedido.**
Legenda queimada, vertical 1080x1920, `--layout focus` e `loudnorm` são padrão.
O system prompt (`server/directorPrompt.ts`) instrui o agente a não oferecer o
que já é o comportamento natural — pedir de novo é ruído.

## Limitações conhecidas do ambiente

| Item | Estado | Efeito |
|---|---|---|
| detecção de rosto | **funciona** (OpenCV Haar, `opencv-python-headless` 4.14) | `--layout focus` reenquadra de verdade. Verificado: rosto em `center_x=0.593` desloca o crop em 238px. |
| `torch` / CUDA | ausente | Whisper roda em CPU. Vídeo longo demora bastante — **mas dá para parar pelo painel de execução.** |
| `--version` no CLI | não existe | O preflight valida por `import`, não por flag. |

### Sobre a detecção de rosto (correção de um erro meu)

Eu havia registrado que faltava `pip install mediapipe`. **Estava errado por dois
motivos:**

1. O pipeline **nunca usou mediapipe**. O detector é o **Haar cascade do
   OpenCV**, escolhido justamente porque o XML vem dentro do wheel — sem
   download de modelo, sem runtime extra (`viralclipper/reframe.py`, docstring
   de `OpenCvDetector`).
2. **Já está funcionando.** `reframe.available_backend()` devolve `"opencv"`,
   o cascade existe em `cv2/data/haarcascade_frontalface_default.xml`, e uma
   imagem real de rosto é detectada em 3/3 frames.

Não instale nada. Rodar `pip install mediapipe` adicionaria ~100MB de
dependência que nenhuma linha do projeto importa.

**A armadilha que me enganou:** `opencv-python` 5.x **removeu**
`CascadeClassifier` e parou de embarcar os XMLs. Se o venv fosse atualizado para
o 5.x, o `focus` cairia para centro — e aí sim seria um problema real. O código
já trata isso e manda uma mensagem explícita em vez de degradar em silêncio:

```
"OpenCV is installed but ships no Haar cascade
 (opencv-python 5 removed CascadeClassifier).
 Install the 4.x line: pip install \"opencv-python-headless<5\""
```

Ou seja: o pin em `4.14.0` **é a feature**. Não atualize para 5.x sem antes
verificar o cascade.

**Limitação real que sobra:** o Haar cascade é de 2001 — acerta rostos frontais
e de perfil leve, mas erra em ângulos extremos, oclusão e iluminação ruim. Para
o que este módulo precisa (o *centro* do rosto, não uma caixa precisa) isso não
é um custo relevante. Mas não é um detector moderno; não vale prometer precisão
de deep learning.

## Configuração

```powershell
cd C:\Users\USUARIO\viral-clipper\studio
Copy-Item .env.example .env
# edite .env e preencha ao menos UMA chave de modelo (VYCE_API_KEY recomendada)
npm install
npm run doctor     # confere ambiente + chaves
npm run dev
```

- Backend: `http://localhost:3000`
- Frontend: `http://localhost:5173`

O chat precisa de **pelo menos uma** chave de provedor de modelo. Sem nenhuma, o
`/api/models` volta vazio e o `/api/chat` responde 503 com instrução — o servidor
sobe, mas não tem como responder. `CODEBUDDY_API_KEY` **não é mais necessária**.

### Variáveis

| Variável | Para que serve |
|---|---|
| `VYCE_API_KEY` | Provedor principal. 4 modelos, 100% na sonda de protocolo. |
| `KILO_API_KEY` | Dá acesso a `openrouter/free` (roteador gratuito). |
| `NVIDIA_API_KEY` | 2 modelos, mas instáveis — fora da rede de fallback. |
| `OPENROUTER_API_KEY` | Opcional. A chave atual está morta (ver abaixo). |
| `VIRCAL_ROOT` | Raiz do repositório do viral-clipper. É o `cwd` obrigatório. |
| `VIRCAL_PYTHON` | Interpretador do pipeline. Vazio = `<VIRCAL_ROOT>/.venv`. |
| `PORT` | Porta do backend. Padrão 3000. |

**Precedência:** variável de ambiente do processo **vence** o arquivo `.env`.
Se você exportou uma chave antiga no shell, ela sobrescreve a do arquivo.

> `CODEBUDDY_API_KEY` e `CODEBUDDY_AUTH_TOKEN` **não aparecem mais nesta tabela**:
> nenhuma das duas afeta o chat. Elas existiam para o modo SDK, que foi removido
> do projeto junto com a dependência `@tencent-ai/agent-sdk` e o painel de login
> do `SettingsPage`. Se você as tem no `.env`, pode apagar — nada as lê.

## Como usar, do zero

### 1. Subir

```bash
cd C:\Users\USUARIO\viral-clipper\studio
npm run dev
```

Sobe os dois processos de uma vez (`concurrently`): a API em `3000` e o Vite em
`5173`. **Abra `http://localhost:5173`** — não a porta 3000. A 3000 é API-only:
`GET /` responde **404 de propósito**, então abrir lá parece que o app está
quebrado.

Se preferir os dois em terminais separados:

```bash
npm run server     # terminal 1 — a API
npm run dev:client # terminal 2 — o front
```

Antes da primeira vez, `npm run doctor` diz em um lugar só o que pode impedir o
chat de responder (chaves, `models.json`, venv, ffmpeg).

### 2. Conferir que o chat tem com que responder

No topo da tela, o seletor de modelo precisa listar modelos. Se estiver vazio, é
chave faltando em `.env` — `npm run check:models` testa cada modelo contra a API
de verdade e diz qual falha. Não adianta tentar conversar: o `/api/chat` devolve
**503** com a instrução, em vez de falhar de um jeito obscuro.

### 3. Pedir em linguagem natural

Não existe sintaxe. Escreva o que você quer, como pediria a uma pessoa:

```
corta esse vídeo em 3 clipes de 30 segundos: https://www.youtube.com/watch?v=...
```

O agente lê o pedido, escolhe os parâmetros e chama `clip_plan`. Resposta real de
uma execução (transcrição de verdade, não exemplo):

```
Entendi: 3 clipes de 30s cada, cortados pelos momentos mais virais. Vou gerar o plano.

  → clip_plan { "url": "https://www.youtube.com/watch?v=7OWUenfg2-U",
                "params": { "count": 3, "target_duration": 30 } }

**Plano gerado — preciso de sua confirmação antes de executar.**

- **URL:** `https://www.youtube.com/watch?v=7OWUenfg2-U`
- **3 clipes de ~30s** cada, escolhidos automaticamente pelos mais virais.

> O pipeline já aplica tudo que vem "de fábrica": corte vertical 1080×1920 com
> reenquadramento facial, legenda karaoke queimada, áudio em -14 LUFS e relatório
> de viralização.

**Confirma para eu rodar?**
```

**Só a URL é obrigatória.** Tudo o mais o agente preenche com o padrão. Pedidos
que funcionam:

| Você escreve | O que acontece |
|---|---|
| `corta em 3 clipes de 30s: <url>` | `count: 3`, `target_duration: 30` |
| `quero os 5 melhores momentos desse vídeo: <url>` | `count: 5`, resto no padrão |
| `só quero o plano, não executa: <url>` | chama `clip_plan` e para |
| `faz um clipe de 15s sem legenda: <url>` | `count: 1`, `caption_style: none` |
| `usa o modo full de download: <url>` | `download_mode: full` — ver passo 7 |
| `quais parâmetros eu posso ajustar?` | consulta o cardápio (opcional) |

Se você citar um parâmetro que não existe, o erro volta **com a lista das 31
chaves válidas**, e o agente corrige numa chamada só — sem ida e volta.

### 4. Aprovar — o passo que não dá para pular

O plano aparece como um **cartão no fim da conversa**, fora das mensagens, com o
comando exato que vai rodar. Nada executa até você clicar em **Executar agora**.

O botão **copiar** do cartão serve para rodar o mesmo comando no terminal, fora
do Studio — útil quando você quer ver a saída crua ou deixar rodando sozinho.

O gate é do **servidor**, não do modelo. Comprovável:

```bash
# sem aprovar — recusa, mesmo que o agente insista
$ curl -X POST localhost:3000/api/studio/commit/<plano_id>
{"ok":false,"error":"Este plano ainda não foi aprovado pelo usuário."}

# depois de aprovar
$ curl -X POST localhost:3000/api/studio/approve/<plano_id>
{"ok":true,"plan_id":"<plano_id>","approved":true}
```

### 5. Acompanhar

Clicou em Executar: abre o **painel de execução** com a saída do CLI em tempo
real via SSE — download, análise de áudio, transcrição, corte, render. O `stderr`
aparece em cor normal de propósito: o Whisper manda progresso por ali, e pintar de
vermelho assustaria sem motivo.

Para **parar**, o botão **Parar** no painel. Ele encerra o processo **e a árvore
dele** (`taskkill /T /F` no Windows) — sem isso o ffmpeg continuaria rodando órfão.

Preços do Parar, para não haver surpresa: o plano vira `consumed` (cancelar
consome o plano, então executar de novo exige gerar outro) e o diretório
`output/_work/` fica para trás, porque o processo morto não roda a limpeza.
Medido: **52 MB** (`analysis.wav` + `source_audio.webm`), num vídeo de 13 min.
Apagar à mão é seguro — é intermediário, e o run seguinte recria o que faltar.

**Dois planos ao mesmo tempo não rodam.** `work_path()` é `output/_work` para
qualquer run, sem id de execução; dois processos escreveriam os mesmos
`source_audio.webm` / `analysis.wav`. O sintoma não seria um erro, seria um
resultado errado em silêncio: medi um run que analisou **1127 s** de um vídeo de
**793,5 s**, porque reaproveitou o áudio que o outro processo tinha acabado de
escrever. O servidor agora recusa o segundo commit com 400 — e a recusa **não**
queima o plano: ele continua aprovado, e basta executar de novo quando o atual
terminar. Detalhes em "Dois renders ao mesmo tempo: a trava".

### 6. Onde o resultado aparece

```
output/
├── <nome-do-video>_<n>.mp4     ← os clipes, 1080×1920, com legenda
├── clips.json                  ← metadados de cada corte
├── clips.md                    ← o mesmo, legível
├── viral_report.md             ← análise de potencial de viralização
├── cache/transcripts/          ← transcrições do Whisper, reaproveitadas
└── _work/                      ← intermediários; apagado no fim de cada run
```

O caminho é relativo ao **`cwd` do processo**, que é fixado em `VIRCAL_ROOT` —
o mesmo para o Studio e para o terminal. `output/` é gitignored.

**O cache de transcrição é o que torna a segunda rodada barata.** Transcrever é
o passo mais caro (Whisper **em CPU**); a chave é o sha1 do áudio de análise mais
os parâmetros que mudam o resultado (modelo, idioma, beam, VAD, device,
compute type). Medido nesta máquina, mesmo vídeo e mesmos parâmetros:

| | tempo | log |
|---|---|---|
| 1ª rodada | ~16 min | `[>] Transcribing analysis.wav (cache miss, key 1738ce847743...)` |
| 2ª rodada | **18 s** | `[+] Transcript cache hit (2279 words, key 1738ce847743...)` |

O cache mora em `output/cache/`, **fora** de `_work/` — de propósito. Antes ele
ficava dentro do diretório de trabalho, que o `cli.py` apaga no `finally`, então
era escrito e destruído na mesma execução: toda rodada do mesmo vídeo pagava a
transcrição de novo. Trocar `count`, `caption_preset` ou `download_mode` **não**
invalida o cache; trocar `whisper_model` invalida.

**`--plan-only` não sobrescreve `clips.json` nem `clips.md`.** Um plano não
renderiza nada, então todo registro sairia com `file: ""` — e isso apagaria o
manifesto do último run de verdade, deixando os `.mp4` no disco sem nada que os
indexe. O `viral_report.md` continua sendo escrito: ele é uma leitura editorial
das janelas escolhidas, não um registro do que foi renderizado.

### 7. Quando falha no meio: o 403 do YouTube

Falha **medida**, não hipotética. No modo padrão (`download_mode: sections`) o
yt-dlp baixa só as faixas de tempo de cada corte, e o YouTube passou a responder
**403 Forbidden** em parte dessas requisições com `Range`. O resultado é o pior
formato possível: **o primeiro corte passa e o segundo morre**, então parece que
funcionou.

```
[info] 7OWUenfg2-U: Downloading 1 time ranges: 495.7-535.8
[https @ ...] HTTP error 403 Forbidden
Error opening input: Server returned 403 Forbidden (access denied)
ERROR: ffmpeg exited with code 3436169992
! yt-dlp failed to cut a section with ffmpeg. Retry, or use --download-mode full
  to download the whole video at once.
```

**O conserto está na própria mensagem**, e agora o agente consegue aplicá-lo.
Peça direto:

```
usa o modo full de download e refaz: <url>
```

ou, se preferir o parâmetro explícito:

```
corta em 3 clipes de 30s com download_mode full: <url>
```

`full` baixa o vídeo inteiro uma vez (mais lento no começo, sem 403 no meio).
A chave `download_mode` foi adicionada à allowlist **por causa desta falha** — a
flag existia no `cli.py` e não existia no Studio, então o único caminho era ler o
log, copiar o comando e editar à mão no terminal.

**O 403 também atinge o download do áudio de análise, e é intermitente.** Não é
exclusivo do `Range` das seções. Medido, duas execuções `--plan-only` idênticas
seguidas: a primeira morreu no download do áudio e a segunda passou, sem mudar
nada.

```
[info] 7OWUenfg2-U: Downloading 1 format(s): 251-2
ERROR: unable to download video data: HTTP Error 403: Forbidden
! unable to download video data: HTTP Error 403: Forbidden The download was
  interrupted before finishing. Retry; if it keeps happening, pass
  --cookies-from-browser chrome.
```

Aqui `--download-mode full` **não** ajuda: o áudio de análise é baixado antes de
qualquer decisão de modo. O que resolve é repetir. `cookies_from_browser` está
fora da allowlist de propósito (é um vetor de leitura do perfil do navegador),
então esse caminho é terminal, não do chat.

### 8. Quando a transcrição falha: degradado, mas não silencioso

Se o Whisper não conseguir alocar memória, o run **não** morre — ele tenta **uma
vez** de novo e, se falhar de novo, degrada e continua, com uma linha de aviso:

```
[>] Loading whisper model 'small' on auto (int8)
! Transcription ran out of memory; retrying once (Transcription failed: Unable to
  allocate 241. MiB for an array with shape (1, 78957, 400) and data type float64)
! Transcription unavailable (Transcription failed: Unable to allocate 241. MiB
  for an array with shape (1, 78957, 400) and data type float64). The clips will
  have NO burned captions, and the selection falls back to audio energy alone —
  which scores far lower and picks different moments.
```

O resultado não parece quebrado: os clipes saem, `clips.json` é escrito, o
relatório é escrito. Mas o que muda é grande. Mesmo vídeo, mesmos parâmetros,
transcrição OK contra transcrição ausente:

| | score dos cortes | `language` | texto do corte |
|---|---|---|---|
| transcrição OK | **68.3 / 68.1** | `pt` | presente |
| só áudio | 39.3 / 39.3 | `nao transcrito` | vazio |

Sem transcrição, o hook e a densidade de fala zeram e o ranking passa a decidir
por energia. Pior: sem palavras não há legenda para queimar, que é o padrão do
pipeline.

**O run degradado termina com exit 4, não com 0.** Antes os dois casos terminavam
com exit 0 — indistinguível de um sucesso — e o custo prático era um lote não ter
como retentar justamente a falha transitória. O lote já trata `code != 0` como
falha retentável (`batch.py`), então o 4 vira retentativa de graça.

| Código | Significa |
|---|---|
| 0 | Sucesso. |
| 1 | Erro de execução (`ClipperError`), com a mensagem no log. |
| 2 | Erro de uso/argumentos. |
| 3 | Algum clipe ficou abaixo da duração mínima. |
| **4** | **Run degradado: clipes renderizados sem legenda, seleção só por áudio.** |
| **5** | **Pasta de saída ocupada: outra execução está usando. Nada quebrou — espere e rode de novo.** |
| 130 | Interrompido pelo usuário (`Ctrl-C` / botão Parar). |

O 4 vem **antes** do 3: os dois podem ser verdade juntos, e "degradado" é mais
acionável — a duração curta é consequência da seleção degradada, não um problema
independente. E ele **não** dispara em `--plan-only`: um plano não renderizou
nada, então não há legenda para perder.

Sinais de que aconteceu, em ordem de facilidade: o **exit 4** (ou a fase
"Degradado" em laranja no painel); a linha `!` no log; o `language` em
`clips.json` (`nao transcrito`); o `text` de cada corte vazio.

#### Por que uma retentativa, e por que só nesta falha

A retentativa vale aqui porque a causa é **estado da máquina naquele instante**
(um build em paralelo, um navegador com cinquenta abas), não a entrada. Um wav
corrompido ou um checkpoint ausente falha idêntico na segunda vez — esses não
podem pagar por uma retentativa.

Foi por isso que a falha precisou virar um **tipo** (`TranscriptionOutOfMemory`),
em vez de continuar sendo uma mensagem. E havia uma armadilha: o detector que já
existia (`is_memory_error`) **não reconhecia a falha que de fato aconteceu**. Ele
casava `"failed to allocate"`, mas a mensagem real era `"Unable to allocate 241.
MiB for an array with shape ..."`. Medido: `is_memory_error(MemoryError(<msg
real>))` devolvia `False`. O conserto foi checar o **tipo** primeiro
(`isinstance(exc, MemoryError)` — e `numpy._ArrayMemoryError` herda de
`MemoryError`), deixando os marcadores de texto como rede para erros de terceiros
que chegam como string.

Isto é contenção de memória, não defeito de lógica. O gatilho medido foi uma
máquina de **8 GB com ~0,9 GB livres** durante um `npm run build` em paralelo.
Rodar sozinho, com o navegador fechado, passou.

Sintoma que **não** é isso: se falhar em `GET /api/models` ou o chat responder
503, é chave de modelo faltando — veja o passo 2.

## Modelos de terceiros

O Studio pode usar modelos OpenAI-compatíveis de fora, declarados em
`<VIRCAL_ROOT>/.codebuddy/models.json` — escopo **project-level**, então não
mexe no seu setup global do CodeBuddy.

### A restrição que define tudo

A documentação oficial (`cn/cli/models.md`, embutida no próprio pacote do SDK)
diz, textualmente:

> **目前仅支持 OpenAI 接口格式的 API** — *só é suportado API no formato OpenAI.*

`Anthropic` não aparece em nenhum ponto da documentação do SDK. Não existe
`ANTHROPIC_API_KEY`, não há suporte ao endpoint `/v1/messages`. Portanto o
formato nativo da Anthropic **não funciona** e não adianta configurar.

O caminho para usar Claude é um gateway que exponha Claude em formato OpenAI:
OpenRouter, NVIDIA ou Kilo. Você usa os modelos, sem falar direto com a API da
Anthropic.

### A regra do `id` (esta foi cara de descobrir)

O SDK usa o campo `id` **literalmente** como o nome do modelo enviado à API.
Não existe campo separador — `apiModel` não aparece no bundle (grep: zero
ocorrências). Consequência:

```
API espera "nvidia/nemotron-3-super-120b-a12b"  -> id igual, COM o "nvidia/"
API espera "openai/gpt-oss-20b"                -> id igual, SEM prefixo de vendor
```

No mesmo catálogo da NVIDIA convivem os dois formatos. Não há heurística que
resolva: tirar o `nvidia/` do Nemotron dá 404, mantê-lo no GPT-OSS dá 404. O
`id` tem de ser copiado do catálogo do provedor, caractere por caractere.

Como o `id` é o nome real, **dois gateways que ofereçam o mesmo modelo colidem**
(o último declarado vence). O campo `name`, que é livre, é o que distingue na UI.

### Validar antes de confiar

```powershell
cd C:\Users\USUARIO\viral-clipper\studio
npm run check:models              # testa todos contra a API real
npm run check:models openrouter   # filtra por substring do id
npm run test:models               # 10 testes estruturais do models.json
```

`check:models` fala **direto com a API de cada provedor**. É o único teste que
prova o que interessa: que o endpoint responde e que o modelo sabe chamar
ferramenta.

O validador não se limita a checar HTTP 200. Ele **exige um tool-call**: manda
uma função `ping` e verifica se o modelo a chama. Um modelo pode responder "oi"
perfeitamente e nunca emitir `clip_plan` — para o Diretor de Cortes isso é
equivalente a estar quebrado, e o veredito `sem-tool-call` marca exatamente
esse caso.

### Testar a chave antes de culpar a config

`check:models` diz *como o modelo se comportou*, não *por que*. Para separar
"chave ruim" de "modelo ruim", teste a credencial no endpoint que identifica a
conta:

| Provedor | Endpoint que valida a chave | Endpoint que **não** valida |
|---|---|---|
| OpenRouter | `GET /api/v1/key` | `GET /api/v1/models` — público, 200 para qualquer chave |
| Kilo | `POST /api/gateway/chat/completions` | `GET /api/gateway/models` — lista sem checar crédito |
| NVIDIA | `POST /v1/chat/completions` | `GET /v1/models` — autentica sem checar entitlement |

Os três têm a mesma armadilha: o endpoint de catálogo responde 200 mesmo com
uma credencial que não serve para inferir. No NVIDIA isso é literalmente o caso
— a chave `SqTq` listava os 81 modelos e devolvia 403 em toda chamada de
inferência. Uma chave pode ser "válida" e mesmo assim não servir.

Vereditos:

| Veredito | Significa | Bloqueia? |
|---|---|---|
| `ok` | Respondeu e chamou a ferramenta. | não |
| `sem-tool-call` | Respondeu texto, nunca chamou a ferramenta. | **sim** |
| `falhou` | Erro de rede, HTTP ou configuração. | **sim** |
| `nao-testado` | Falta a variável de ambiente. | não |

O resumo final separa **aprovado** de **não verificado**: um modelo pulado por
falta de chave não é um modelo aprovado, e a mensagem não trata os dois como se
fossem a mesma coisa.

### Por que não existe um verificador "o SDK enxerga o arquivo"

Existiu (`server/modelVerify.ts`) e foi removido por dar resposta não confiável.
Registro aqui para não ser reinventado:

`session.getAvailableModels()` **não lê o `models.json`**. Ele envia uma
requisição de controle `get_available_models` ao processo do CLI, cujo handler
responde com o resultado de `agentManager.getInitializeMetadata()` — um
**snapshot computado uma vez** e cacheado em `initializeMetadataPromise`,
derivado do contexto do agente e de `product.json`. Não é releitura do arquivo
em disco.

Efeito prático observado: o **mesmo** `models.json`, sem nenhuma alteração,
foi reportado como "6 de 6 ausentes", depois "6 de 6 visíveis", depois
"4 de 4". Nenhuma dessas respostas dizia respeito ao arquivo — eram estados
internos da sessão. Um verificador assim é pior que nenhum: manda procurar
defeito onde não há e aprova onde não deve.

Se precisar conferir se um modelo aparece no seletor, isso é verificação visual
no próprio app. O que se automatiza é `check:models`.

### `$comment` e `$disabled` no models.json

| Campo | Efeito |
|---|---|
| `$disabled` | Desativa a entrada — o validador a ignora. |
| `$comment` | **Nenhum efeito funcional.** Serve para anotar decisões. |

A distinção importa: um campo de documentação que muda comportamento mente sobre
o que faz. O Nemotron já foi removido do teste por causa disso.

### Estado verificado em 2026-09-28 (última bateria: 7 modelo(s) em `check:models`)

Sonda de protocolo: 2 cenários × 2 execuções por modelo, exigindo tool-call real.

| Modelo | Sonda | Papel |
|---|---|---|
| `claude-sonnet-4-6` (Vyce) | **100%** | **Padrão.** 270k de contexto. |
| `gpt-6-luna` (Vyce) | **100%** | Fallback 1 |
| `deepseek-v4.1` (Vyce) | **100%** | Fallback 2 |
| `agnes-3.0-flash` (Vyce) | **100%** | Fallback 3 |
| `openrouter/free` (Kilo) | **100%** | Fallback 4 |
| `nvidia/nemotron-3-super-120b-a12b` | 50% | **Fora do fallback.** HTTP 500 espontâneo. |
| `openai/gpt-oss-20b` (NVIDIA) | 0% | **Fora do fallback.** Timeout sistemático. |

Os dois NVIDIA continuam no `models.json` para uso manual, mas **não** entram na
cadeia de fallback (`FALLBACK_ORDER` em `chatRoute.ts`) — um fallback que falha
50% não é fallback; é só uma segunda forma de falhar.

Sobre o GPT-OSS: o `check:models` tem `TIMEOUT_MS = 60_000`, mas ele estourou
5 minutos sem veredito. O `AbortController` aborta o `fetch`, mas aparentemente
o `response.text()` não rejeita junto. **Vale investigar** — hoje o efeito
prático é que a última linha do relatório trava em vez de dizer "timeout".

**A chave do OpenRouter está inválida.** Não é saldo — é a chave. Verificado em
dois endpoints que identificam a conta:

```
GET /api/v1/key      -> 401 {"error":{"message":"User not found.","code":401}}
GET /api/v1/credits  -> 401 {"error":{"message":"User not found.","code":401}}
```

O formato é válido (`sk-or-v1-` + 64 hex), então a chave foi revogada ou a conta
por trás dela não existe mais. **`GET /api/v1/models` responde 200 — mas é
público e responde 200 para uma chave inventada também.** Foi por isso que o
diagnóstico demorou: o endpoint mais óbvio de testar não testa nada. Para
validar uma chave OpenRouter use `/api/v1/key`, nunca `/models`.

Enquanto a chave não for trocada, os modelos OpenRouter ficam fora de
`models.json` — declará-los só encheria o relatório de 401. O acesso gratuito a
modelos foi mantido por outro caminho: o gateway do Kilo serve `openrouter/free`
com saldo 0, e esse é o modelo padrão hoje.

**Kilo:** o JWT é válido (exp 2031) e o endpoint autentica. Modelos pagos
retornam 402 com `"balance": 0`; `openrouter/free` passa e responde tool-call.
Ou seja, o Kilo funciona sem crédito porque repassa o nível gratuito do
OpenRouter. Vale saber: o catálogo do Kilo (395 modelos) **já inclui** os
`:free` do OpenRouter — os dois gateways alcançam o mesmo conjunto.

**Vyce:** `https://vyceai.com/v1` é OpenAI-compatible de verdade — o envelope
de erro traz `type`/`param`/`code`/`request_id`, atrás de Cloudflare. 8 modelos
no catálogo; os 4 melhores deram 100% na sonda. **`qwen3.8-flash` responde
"Unsupported client"** — não usar.

**NVIDIA:** havia duas chaves diferentes em jogo. A que estava no `.env`
(terminada em `SqTq`) autentica em `/v1/models` mas devolve **403 Forbidden em
`/chat/completions`** — lista, não infere. A do ambiente (terminada em `fvyW`)
faz as duas coisas. O `.env` foi corrigido. Isso passou despercebido porque o
ambiente tem precedência sobre o arquivo: o `check:models` acertava pelo
motivo errado, e quebraria no dia em que o servidor subisse sem essa variável.

Dois modelos NVIDIA originalmente escolhidos (`meta/llama-3.3-70b-instruct` e
`qwen/qwen2.5-coder-32b-instruct`) **estavam mortos** — EOL em maio e agosto de
2026. O validador pegou isso antes de chegarem ao chat. Modelos do catálogo
NVIDIA expiram com frequência; confira antes de adicionar.

A instabilidade do Nemotron é real mas intermitente: ele respondeu OK, depois
deu 500 em todas as tentativas, e voltou a responder OK. Não é defeito de
configuração — é o provedor. Por isso ele fica declarado e continua sendo
testado a cada `check:models`, em vez de ser removido.

### Duas armadilhas do próprio validador (já corrigidas)

Ambas eram regras minhas que erravam em silêncio:

1. **`$comment` desativava o modelo.** A rotina de carga tratava `$comment` e
   `$disabled` como equivalentes. Resultado: o Nemotron, documentado com um
   `$comment` explicando sua instabilidade, desapareceu do teste — justamente o
   modelo que precisava de vigilância. Um campo de documentação não pode ter
   efeito funcional.
2. **"Todos prontos para uso" com modelos não testados.** A mensagem de sucesso
   não distinguia "aprovado" de "pulado por falta de chave". Agora o resumo
   separa os dois explicitamente.

Há teste de regressão para os dois casos (`npm run test:models`).

### Terceira armadilha: a repetição multiplicava o timeout

O sintoma reportado foi *"o `openai/gpt-oss-20b` estoura o timeout do
`check:models`"*. Não era o modelo.

O que estava errado era a política de repetição:

| | Antes | Agora |
|---|---|---|
| Timeout por tentativa | 60s | 20s |
| Teto de tempo por modelo | **não existia** | 45s |
| Abort / erro de rede | transitório → 3 tentativas | transitório → 3 tentativas, limitadas pelo teto |
| `sem-tool-call` | reprovava na 1ª amostra | ganha 1 nova amostra |

Pior caso de **um** modelo antes: `3 × 60s + backoff(1,5s + 3s) ≈ 185s` — três
minutos parados no mesmo id, sem imprimir nada depois do `id ... `. Com 7
modelos, o teto teórico era ~21 min. O validador não travava; ele somava.

Medições que definiram os números novos (o endpoint foi sondado, não suposto):

- Latência do ping com tool-call: **2s a 9s** (pior caso medido: `gpt-oss-20b`,
  8,6s). Os 20s dão ~2× de folga sobre o pior caso observado.
- O `AbortController` **derruba a leitura do corpo**, não só a espera pelos
  headers. Foi verificado contra um servidor local que manda headers e nunca
  fecha o corpo: `res.text()` rejeita com `AbortError` em ~6ms após o abort.
  (A hipótese de que `text()` ignoraria o abort estava errada.)
- `gpt-oss-20b` devolveu tool-call em **6 de 6** amostras seguidas com
  `max_tokens: 256` — não é falta de orçamento de tokens. Mas numa rodada
  anterior do `check:models` devolveu HTTP 200, texto de raciocínio e **zero**
  tool-call. É não-determinismo do provedor.

Daí a segunda mudança: `sem-tool-call` não é erro de configuração (ao contrário
de 401/404, que se repetem idênticos) — é uma amostra ruim. Julgar o modelo por
uma única amostra produzia falso negativo, e falso negativo aqui é pior que
falso positivo: manda o usuário caçar um problema de configuração que não
existe. Agora há uma segunda amostra, **sem backoff** (não há infraestrutura
caída, só um sorteio ruim).

Se a segunda amostra passar, o modelo é aprovado — mas marcado como
`(instavel)`, e o resumo lista os instáveis à parte:

```
  openai/gpt-oss-20b ... [ OK ] (instavel) (2475ms) tool-call OK (ping) — 1a amostra sem tool_call, 2a passou (modelo instavel)
```

Aprovado sem ser reprovado, mas também sem ser escondido atrás de um "OK" limpo.
Quem for confiar o Diretor a esse modelo tem direito de saber.

Os dois tetos são sobrescrevíveis (`MODEL_CHECK_TIMEOUT_MS`,
`MODEL_CHECK_BUDGET_MS`) — é o que permite o teste exercitar o estouro sem
esperar 45s de verdade. Os 9 testes de `server/modelCheckRetry.test.ts` rodam
contra um servidor local e **não tocam a rede real**.

### O que não dá para plugar

| Provedor | Motivo |
|---|---|
| **Claude CLI local** | Não é endpoint HTTP. O SDK tem laço de agente próprio e não delega orquestração; o `_meta` compatível com ACP é metadata de mensagem, não integração de agente. |
| **cline.bot**, **opencode.ai/zen** | Autenticação por conta Google. Token de sessão não é API key — o SDK é orquestrador de agente, não navegador de sessão. |
| **vyceai.com** | ✅ **Integrado.** `https://vyceai.com/v1` é OpenAI-compatible de verdade. 8 modelos no catálogo; 4 passaram 100% na sonda de protocolo. Evitar `qwen3.8-flash` (responde "Unsupported client"). |

## Interface: o gate de confirmação na prática

O pipeline não roda sem um clique humano. Isso aparece na tela em três peças:

| Peça | Arquivo | O que faz |
|---|---|---|
| Cartão de confirmação | `src/components/ConfirmPlanCard.tsx` | Mostra URL, resumo e o **comando exato**. Botões Executar / Descartar. |
| Painel de execução | `src/components/RunLogPanel.tsx` | Log do CLI em tempo real via SSE, com stdout/stderr separados. |
| Botão Parar | dentro do painel | Encerra o render em andamento. Só aparece enquanto roda. |
| Botão copiar | dentro do cartão | Copia o comando para rodar no terminal, fora do Studio. |

### Fluxo

```
agente chama clip_plan
  → tool_result traz { plano_id, comando, resumo }
  → src/utils/planParser.ts extrai e valida
  → ConfirmPlanCard aparece abaixo das mensagens
  → usuário clica "Executar agora"
  → POST /api/studio/approve/:planId   (marca aprovado)
  → POST /api/studio/commit/:planId    (dispara o processo)
  → SSE /api/studio/events alimenta o painel
  → ... e se precisar parar:
  → POST /api/studio/cancel/:runId     (encerra o processo e a árvore dele)
```

O `planParser` é **estrito de propósito**: só devolve plano quando o conteúdo
tem `plano_id` E `comando`. Se o `clip_plan` falhou (parâmetro inválido), o
conteúdo é um `{ erro: ... }` e o parser devolve `null` — sem card. Um card de
confirmação para um plano que não existe no servidor seria o pior defeito
possível aqui: o usuário aprovaria algo vazio e levaria 404.

### Detalhes que não são acidentais

- **O cartão fica fora das mensagens**, no fim da lista. Se ficasse dentro da
  mensagem, rolaria para fora da tela conforme a conversa cresce, e o usuário
  perderia de vista o que falta aprovar.
- **stderr não é vermelho.** O Whisper manda progresso normal por stderr.
  Pintar de vermelho assustaria sem motivo.
- **O autoscroll respeita quem está lendo.** Se o usuário rolou para cima, o
  painel não arrasta a tela de volta; aparece um botão "voltar ao fim".
- **Um plano por vez.** Dois cards na tela criariam dúvida sobre qual vale.
- **Não existe "não perguntar de novo".** Se existisse, o gate deixaria de ser
  um gate.
- **"Cancelado" não é "falhou".** Os dois terminam com código não-zero, mas o
  segundo foi acidente e o primeiro foi um pedido. O painel usa laranja e o texto
  "Execução cancelada pelo usuário" no primeiro caso — pintar de vermelho seria
  mentir sobre o que aconteceu.

### Parar um render

`cancelRun` e `cancelAll` existiam no `clipRunner` desde o começo e **nenhuma
rota HTTP os expunha** — só os testes os chamavam. Como `RUN_TIMEOUT_MS` é de 6
horas, um render disparado por engano não tinha como ser interrompido pela
interface: a única saída era esperar a tarde inteira ou matar o processo do
servidor à mão. Faltava o freio.

O que foi feito:

- `POST /api/studio/cancel/:runId`, com 404 quando não há o que cancelar (runId
  desconhecido ou execução já terminou);
- botão Parar no painel, visível só com `phase === 'running'`, com confirmação em
  popover (não modal). O ícone fica numa fileira de botões sem rótulo e um clique
  errado custa caro: o plano vira `consumed`, então refazer exige gerar um plano
  novo;
- a fase `'cancelled'` no `RunState`, separada de `'failed'`.

Dois detalhes que não são óbvios:

**Quem decide a fase é o `exit`, não o pedido.** O evento `cancelled` só entra no
log; a fase muda quando o `exit` chega. Marcar "cancelado" no clique deixaria o
painel dizendo que acabou enquanto o Python ainda está vivo.

**No Windows, matar o processo direto não mata o render.** `child.kill()` vira
`TerminateProcess` no processo direto e os filhos sobrevivem — e o pipeline chama
ffmpeg via `subprocess` (`viralclipper/util.py`, `download.py`). Parar o Python no
meio de um encode deixaria um ffmpeg órfão queimando CPU: o botão não pararia
justamente a parte cara. Por isso `killTree()` usa `taskkill /pid <pid> /T /F`.

Medido nesta máquina, com o Python do venv no meio e um segundo Python como neto
(espelhando servidor → viralcli → ffmpeg):

| como encerra | processo direto | neto (o "ffmpeg") |
|---|---|---|
| nada | vivo | vivo *(controle: o neto é independente)* |
| `child.kill('SIGTERM')` | morto | **vivo — órfão** |
| `taskkill /F` | morto | **vivo — órfão** |
| `taskkill /T /F` | morto | morto |

**Armadilha, porque custou uma medição errada:** a primeira sonda usou Node como
processo intermediário e mostrou que `child.kill()` **já** matava o neto. Era
artefato — no Windows o libuv coloca os filhos num job object, então filhos de
Node morrem junto com o pai. Filhos de Python não. A sonda só passou a valer
quando o intermediário virou o Python do venv. Se tivesse ficado na primeira
medição, o `killTree` teria sido descartado como complexidade inútil.

Limitação conhecida: `taskkill /T /F` não tem desligamento limpo. O Python não
roda cleanup e arquivos temporários ficam para trás. Entre deixar o render rodando
e deixar lixo em disco, quem clicou em Parar escolheu o segundo.

#### O preço era maior do que "lixo em disco"

O `finally` que apaga o diretório de trabalho (`cli.py:532`) **não roda** quando o
processo é morto à força. E `util.ensure_dir` só faz `mkdir(exist_ok=True)`, sem
limpar. A rodada seguinte **herda o diretório sujo** — e o guarda de sanidade do
`render.py` pergunta a coisa errada:

```python
temp_output = work / f"render{Path(destination).suffix or '.mp4'}"   # nome FIXO
util.run_streaming(command, cwd=work, logger=logger)
if not temp_output.exists():        # ← checa EXISTÊNCIA, não PROCEDÊNCIA
    raise ClipperError(f"ffmpeg did not create {temp_output}")
temp_output.replace(destination)
```

A cadeia: rodada A cancelada deixa `_work/clip_NN/render.mp4` truncado. A rodada B
renderiza o mesmo clip com `-y`, que sobrescreve — caso normal, ok. Mas se o ffmpeg
da rodada B morrer **antes de escrever byte nenhum**, o arquivo velho continua
existindo, o guarda passa, e o `replace` **promove o truncado da rodada cancelada
ao nome final**.

O `probe_duration`/`probe_video_size` logo depois provavelmente pega (mp4 cortado
perde o `moov`, porque `+faststart` escreve no fim) — mas "provavelmente pego por
uma sonda mais adiante" é garantia fraca para um caminho de corrupção que duas
linhas eliminam. `render.py` agora faz `temp_output.unlink(missing_ok=True)` antes
de montar o comando, que é o mesmo padrão defensivo que `reframe.py` já usava com
os `sample_*.jpg`.

Vale a correção de escopo: a conclusão anterior — *"o `replace` é atômico, então um
render cancelado não deixa `.mp4` truncado sob o nome final"* — estava **certa e
incompleta**. Vale para a rodada cancelada; não vale para a interação entre rodadas.
O lixo em `_work/` continua sendo o preço aceito.

#### Dois renders ao mesmo tempo: a trava

`work_path()` é `output/_work` para **qualquer** execução — sem id de run. Dois
processos escrevem os mesmos `source_audio.webm` e `analysis.wav`. O sintoma não é
erro, é **resultado errado em silêncio**.

Medido, ao disparar dois planos em paralelo: um run analisou **1127 s** de um
vídeo de **793,5 s**, porque reaproveitou o áudio que o outro processo tinha
acabado de escrever. Passou por todos os guardas seguintes — o vídeo tem 13:14, os
cortes caíram dentro da duração, o score saiu normal — e viraria clipe publicado.

`commitPlan` agora recusa quando já existe um run vivo, via `runInFlight()`, com
uma mensagem que nomeia o run bloqueador e há quanto tempo ele roda. Três detalhes
que são decisão, não acaso:

- **A checagem vem antes de `consumed = true`.** Recusar não pode queimar o plano:
  o usuário espera o run atual terminar e commita o mesmo plano, sem gerar outro.
  Criar e aprovar continuam liberados — dá para preparar o próximo enquanto o
  atual roda.
- **O erro carrega um código (`'em-andamento'`), não só texto.** O `clip_commit`
  do agente reage diferente: no caso comum (*payload errado*) a orientação é
  reenviar o resumo; aqui é dizer que o plano segue aprovado e basta executar
  depois. Casar a mensagem por texto quebraria na primeira reescrita da frase.
- **A trava tem de liberar.** Um teste que só verificasse o 400 passaria com um
  bug que bloqueasse todo commit depois do primeiro. Por isso o
  `singleRun.test.ts` também confirma que, com o run A terminado, o commit de B é
  aceito — e que o plano B continua `approved: true, consumed: false` depois da
  recusa. Verificado por mutação: com a guarda neutralizada, o teste falha com
  `expected: 400, actual: 200`.

**A trava também não aparecia.** A falha de commit morria num `console.error` no
`App.tsx`: quem clicasse em "Executar agora" via o painel abrir e parar em
"Falhou", sem uma linha dizendo por quê. Agora as três falhas de
`approveAndRun` (aprovar, commitar, exceção) entram no log do painel — que é onde
o usuário está olhando. A recusa é uma frase inteira, escrita para ele; não
adiantava escrevê-la e não entregar.

#### As duas travas, e por que não são duplicatas

Esta trava cobre o **Studio**. Ela nunca cobriu o resto: um `python -m
viralclipper` rodando num terminal escrevia no mesmo `output/` sem ninguém
recusar. O lado Python agora tem a **sua** trava — lock exclusivo do SO em
`output_dir`, em `viralclipper/lock.py`, com recusa por **exit 5**.

As duas continuam valendo, porque resolvem momentos diferentes:

- **A do Studio recusa antes de spawnar.** Consequência prática: o plano **não é
  queimado**. Se dependesse só do lock do Python, o processo subiria, seria
  recusado com 5 e o plano já estaria `consumed` — o usuário teria de gerar outro
  para fazer exatamente a mesma coisa.
- **A do Python alcança o que a do Studio não vê.** É ela que pega o terminal
  contra o Studio, e um lote contra outro lote.

Quando a recusa vem do Python, o painel mostra a fase **"Ocupado"**, em laranja e
não vermelho: nada quebrou, a ação é esperar e rodar de novo. O rodapé traz a
frase do exit 5, que diz isso em palavras. Detalhes do desenho (por que lock do
SO e não arquivo-pid, e as duas armadilhas de offset medidas) estão no
`README.md` da raiz, em "One run per output directory".

### Sobre a entrega do plano

Medido com 4 pedidos de corte em linguagem natural: **3 entregaram `clip_plan`**.
O quarto não chamou ferramenta nenhuma — fez a pergunta certa (*"quantos clipes
e qual duração?"*), que é o comportamento pedido pelo system prompt quando o
usuário não diz `count`. Ou seja: o caso "sem plano" nem sempre é falha; às
vezes é o protocolo funcionando.

O que **é** falha — e foi medido, não suposto — é o **turno vazio**: o modelo
chamar `clip_get_options` e encerrar o turno **sem escrever nada**. Taxa
medida: **1 em 10 execuções**. O usuário pedia "corta em 3 clipes" e recebia uma
tela em branco — nem resposta, nem cartão de confirmação, e **nenhum erro para
ler**. Era o pior desfecho possível: silêncio é a única coisa sobre a qual o
usuário não tem como agir.

#### A tentação era corrigir só o prompt

O prompt foi ajustado: a consulta a `clip_get_options` virou explicitamente
opcional (o convite *"Consulte antes de montar um plano"* saiu da descrição da
ferramenta, do passo 2 do protocolo e da descrição de `params`), e o erro de
chave inválida passou a listar as 31 chaves válidas **inline**, em vez de mandar
o modelo de volta à consulta.

Mas disciplina de prompt reduz frequência, não elimina. E havia um detalhe nos
dados que mudava tudo: no caso que falhou, o texto final era **vazio**. Um turno
que termina sem nenhuma palavra depois de chamar ferramenta **nunca** é um
desfecho válido — é uma condição estrutural, não de humor do modelo. Então dá
para tornar o caso impossível em vez de improvável.

#### As duas camadas

**1. O laço insiste** (`continuation` em `llm.ts`, política em
`directorContinuation` no `chatRoute.ts`). Quando o modelo encerra o turno sem
pedir ferramenta, a política decide se injeta uma mensagem e continua. O
mecanismo é genérico; a política é do Diretor.

A condição é deliberadamente estreita, para não atropelar comportamento
legítimo:

| Situação | Ação | Por quê |
|---|---|---|
| `hadText` | não mexe | pode ser pergunta de esclarecimento, que o prompt **pede** |
| nenhuma ferramenta chamada | não mexe | conversa normal ("oi", "quanto custa") |
| já chamou `clip_plan` | não mexe | já entregou o que tinha de entregar |
| já insistiu uma vez | não mexe | encerrar é melhor que virar laço |
| **vazio + ferramenta chamada** | **insiste** | nunca é um desfecho válido |

**2. A rede final** (`fallbackTextIfSilent`). Se mesmo assim a resposta vier
vazia, o `/api/chat` emite uma frase antes de fechar o stream. Aqui já não
importa o motivo: resposta vazia não chega à interface.

#### Cobertura

`npm run test:loop` — **5 testes, sem LLM**. Um provedor HTTP falso local serve
um roteiro fixo (`clip_get_options` → vazio → `clip_plan`), então o teste mede o
**comportamento do laço**, não o humor do modelo. Um teste que dependesse do
provedor real não conseguiria reproduzir o defeito sob demanda: ele aparece em 1
de cada 10 execuções, que é o pior lugar possível para se apoiar.

O teste 3 é o **controle**: sem a política, o mesmo roteiro encerra calado com
`chamadas.length === 2` e `text === ''`. É o que impede os outros de passarem
por acidente — se a opção `continuation` fosse ignorada, o teste do caso
corrigido e o controle dariam o mesmo resultado.

A política testada é a **real**, importada de `chatRoute.ts`. Uma cópia da regra
no teste provaria o mecanismo e deixaria sem cobertura justamente onde o
comportamento mora.

### Sobre o agente que perguntava a mesma coisa

Queixa relatada, e reproduzida: *"ele fica me perguntando a mesma coisa várias
vezes e não consegue seguir adiante"*. Não era o prompt. Eram **dois defeitos de
fiação**, os dois medidos:

**1. O histórico nunca chegava ao modelo.** O `chatRoute` lia `history` do corpo
da requisição, e o cliente **nunca enviava esse campo** — `grep -rn history src/`
não devolvia uma ocorrência sequer. Cada mensagem era uma conversa nova, com o
system prompt e mais nada. O sintoma é a consequência direta: o agente pergunta
*"quantos cortes?"*, o usuário responde *"4"*, e no turno seguinte o modelo
recebe **só a palavra "4"** — sem URL, sem a própria pergunta. Não tem como agir
sobre isso, então pergunta de novo. Para sempre.

**2. O `systemPrompt` do usuário era ignorado.** A interface tem um
`AgentConfigDialog` que deixa escrever o prompt do agente, com validação de
não-vazio, e o cliente o envia em todo POST (`useChat.ts:152`). O servidor não
tinha **nenhuma** referência ao campo e usava sempre o prompt fixo. Quem
escrevesse *"sempre 4 cortes de 45s com legenda neon"* não via efeito nenhum — a
queixa *"não entende o tipo de trabalho que desejo"* era literal.

> Os dois defeitos sobreviveram porque **nenhum teste os tocava**. O
> `gateLlm.test.ts` manda `history: []` explícito, e array vazio se comporta
> igual a campo ausente; e nenhum teste jamais olhou o `systemPrompt`. Um campo
> que ninguém lê é indistinguível de um campo que não existe.

#### O desenho: histórico do banco, memória explícita

**O histórico vem do BANCO** (`chatContext.ts` + `getMessagesBySession`), não do
cliente. O servidor já grava cada mensagem, então é ele que sabe a conversa —
o contexto sobrevive a um recarregamento de página e não depende de o frontend
lembrar de mandar. Se um cliente antigo MANDAR `history`, ele é ignorado de
propósito: duas fontes de contexto divergem, e a do servidor é a que tem a
conversa inteira.

Teto de 30 mensagens. Uma conversa longa estoura a janela do modelo e o custo por
turno cresce sem limite. O que precisa durar mais que isso é **preferência**, e
preferência tem lugar próprio.

**As instruções do usuário são ANEXADAS, nunca substitutas.** Deixar o
`systemPrompt` da interface substituir o prompt do Diretor entregaria a quem
escreve ali o poder de apagar o protocolo de execução — inclusive a exigência de
aprovação humana, que é a única coisa que impede o agente de rodar um pipeline
caro sozinho. Então o texto vai junto com uma frase explícita dizendo qual regra
vence: as instruções mandam no **padrão** (quantos cortes, qual legenda, que
duração); o protocolo continua mandando em si mesmo.

**A memória é uma FERRAMENTA, não um resumo automático.** Duas ferramentas novas,
`lembrar_preferencia` e `esquecer_preferencia`, gravando numa tabela
`preferences` (chave/valor, inspecionável e apagável).

A alternativa óbvia — resumir a conversa automaticamente — foi descartada, e por
um motivo que vale registrar: um resumo guardaria **o que foi dito numa terça** (o
vídeo específico, o número de cortes daquele pedido), não o que o usuário quer
**sempre**. E guardaria em silêncio, sem o usuário poder ver nem corrigir. Com a
ferramenta, quem decide o que é durável é o próprio agente, e ele é obrigado a
**dizer o que registrou** — o usuário lê e discorda se estiver errado.

A distinção está escrita no prompt, porque é ela que separa memória útil de lixo
acumulado:

| É preferência (vale no próximo vídeo) | Não é (vale só para este pedido) |
|---|---|
| `count`, `target_duration`, `caption_preset` | "corta ESTE vídeo em 3" |
| plataforma de destino, nicho, tom | a URL |
| "eu sempre quero...", "no meu perfil eu posto..." | um ajuste pontual porque um corte ficou ruim |

O prompt também ganhou a proibição explícita de repetir pergunta já respondida,
com a instrução de reler o histórico e a lista de preferências **antes** de
escrever qualquer pergunta.

`GET /api/studio/preferences` e `DELETE /api/studio/preferences/:key` existem
porque memória que o usuário não consegue LER não é confiável: se o agente
registrar algo errado ("ele sempre quer 10 cortes") e isso passar a valer em toda
conversa, ele não tem como descobrir de onde veio.

#### Cobertura

`npm run test:context` — **19 testes**, sem rede externa, sem LLM real.

Os testes puros (`chatContext.test.ts`) provam `buildHistory`,
`renderPreferences` e `composeSystemPrompt` — inclusive que a mensagem de agora é
excluída **por id** e não por posição (dois registros podem empatar no mesmo
milissegundo) e que o corte no teto mantém o **mais recente**, não o mais antigo.

Mas função pura correta e não usada tem o mesmo efeito de função errada — era
exatamente esse o defeito. Por isso `chatContextRoute.test.ts` sobe a rota de
verdade contra um **provedor OpenAI-compatible falso** em localhost e inspeciona
o que o servidor **mandou**: o histórico do banco na ordem, a mensagem de agora
uma única vez, o `systemPrompt` do usuário no bloco de sistema, as ferramentas de
memória no payload.

O último teste é a **prova negativa**: o corpo da requisição inclui um
`history: [{content: 'TEXTO-DO-CLIENTE-QUE-NAO-PODE-APARECER'}]`. Se alguém
voltar a ler o campo do cliente, a isca aparece no payload e o teste falha. Sem
ela, reverter ao defeito antigo passaria despercebido — que é precisamente como
ele sobreviveu até aqui.

Ambos os defeitos foram confirmados por **mutação**: reintroduzir a leitura de
`history` do corpo derruba 2 testes; trocar `userPrompt` por `null` derruba 1.

## Rotas

| Rota | O que faz |
|---|---|
| `GET /api/health` | Liveness. |
| `GET /api/models` | Modelos utilizáveis, lidos de `models.json` com as chaves resolvidas. |
| `POST /api/chat` | SSE. Laço de agente próprio + 4 ferramentas do Diretor + 2 de memória. O contexto vem do banco, não do cliente. |
| `GET /api/studio/preflight` | Estado do ambiente (raiz, python, deps, ffmpeg, capacidades). |
| `GET /api/studio/options` | Cardápio de parâmetros + presets + system prompt. |
| `GET /api/studio/preferences` | O que o agente aprendeu sobre o usuário, para poder conferir e apagar. |
| `DELETE /api/studio/preferences/:key` | Esquece uma preferência. 404 se a chave não existe. |
| `POST /api/studio/plan` | Valida a intenção e devolve o comando. **Não executa.** |
| `POST /api/studio/approve/:planId` | Aprovação humana. |
| `POST /api/studio/commit/:planId` | Executa. Recusa plano não aprovado, já executado, ou quando já existe um run vivo (400 com `em andamento`). |
| `POST /api/studio/cancel/:runId` | Encerra o render em andamento e a árvore de processos. 404 se não há o que cancelar. |
| `GET /api/studio/events` | SSE com a saída do CLI em tempo real. |

### Contrato SSE do `/api/studio/events`

| Evento | Quando |
|---|---|
| `hello` | Sempre primeiro, no aceite da conexão. Não é linha do processo. |
| `started` | O processo subiu. Traz `runId`. |
| `stdout` | Linha da saída padrão. |
| `stderr` | Linha do erro padrão. **Não é sinônimo de erro** — o Whisper manda progresso normal por aqui. |
| `cancelled` | O usuário pediu para parar. Emitido **antes** de o processo morrer. |
| `exit` | O processo terminou de fato. Traz `code` e `cancelled`. |
| `error` | Falha ao iniciar ou ao encerrar o processo. |

`exit.cancelled` é o campo que separa "cancelado" de "falhou" na interface. Sem
ele, os dois casos chegam com código não-zero e o painel não teria como saber que
o segundo foi um pedido do usuário.

### Contrato SSE do `/api/chat`

| Evento | Quando |
|---|---|
| `init` | Sempre primeiro. `sessionId`, `userMessageId`, `model`. |
| `model_active` | Qual modelo está atendendo. Repetido a cada turno do laço. |
| `fallback` | O modelo primário falhou; inclui `model` (o novo) e `reason`. |
| `tool` | O modelo chamou uma ferramenta. `id`, `name`, `input`, `status`. |
| `tool_result` | Resultado da ferramenta. `toolId`, `content`, `isError`. |
| `text` | Texto da resposta. Pode vir em vários pedaços. |
| `done` | Fim. `model` que efetivamente atendeu. |
| `error` | Todos os modelos da cadeia falharam. |

## Testes

```powershell
cd C:\Users\USUARIO\viral-clipper\studio

# 62 testes unitarios: allowlist, duracoes, argv, .env, modelos, politica de
# repeticao do validador, parser de plano e traducao de codigo de saida. Sem rede.
npm run test:unit

# Contexto do agente: historico, preferencias e system prompt. 19 testes. Os
# puros + a rota real contra um provedor falso em localhost, inspecionando o
# payload que o servidor MANDA. ~17s.
npm run test:context

# O laço do agente: turno vazio depois de ferramenta. Provedor HTTP falso
# local, sem LLM. ~1s.
npm run test:loop

# Rotas HTTP do Studio: preflight, options, plan, approve, SSE, 404s. ~16s.
npm run test:routes

# Gate: formato real do clip_plan -> parser da UI, e recusa de commit sem
# aprovacao. Deterministico, sem rede. ~3s.
npm run test:gate

# Cancelamento: a rota e o freio. Dispara um render de verdade e mata.
# ~3s.
npm run test:cancel

# Execucao unica: o segundo commit e recusado, NAO queima o plano, e a trava
# libera quando o run termina. Sobe o servidor na 3298. ~5s.
npm run test:lock

# Tudo que e deterministico (unitarios + contexto + laco + rotas + gate +
# cancelamento + execucao unica). ~90s.
npm test

# Conferencia de tipos dos DOIS projetos (front e servidor). Nao emite nada.
npm run typecheck

# Ponta a ponta pela API de modulo (preflight, plan, gate, allowlist, argv).
npm run smoke

# O build em dist/ servido pelo vite preview consegue falar com a API?
# Sobe a API em 3000 e o preview em 4173. Requer `npm run build` antes.
npm run verify:build

# Conversa real com o modelo: o agente entrega um plano sozinho?
# Chama o provedor de verdade, leva ~20s, e PODE falhar por humor do modelo.
npm run test:llm
```

Total atual: **93 testes**, 0 falhas.

Os testes que valem mais, e por quê:

**`test:gate` — o formato.** Prova que o payload que o `clip_plan` devolve é
digerível pelo **mesmo parser que a interface usa**. Se o handler renomear
`plano_id` ou `comando`, o card de confirmação some sem aviso — falha silenciosa,
a pior categoria.

E o teste exercita a **serialização real**: `buildPlanPayload()` de
`planPayload.ts` é a função que o handler do `clip_plan` chama em produção, e ela
devolve a string já serializada. Não há formato reconstruído no teste.

> Isso já foi falso. O construtor morava em `directorTools.ts` e o handler montava
> o objeto **inline**, com os mesmos nomes de campo em dois lugares. O teste
> passava pelo construtor, a produção passava pelo inline — renomear `plano_id`
> no handler não quebrava teste nenhum. **O teste guardava uma cópia, e a cópia
> não é o que roda.** A correção foi eliminar o segundo caminho (um construtor
> que devolve string), não vigiar a divergência. `directorTools.ts` foi removido
> junto: depois da mudança ele só continha ferramentas mortas, uma cópia de
> `createSdkMcpServer` e um comentário que descrevia um formato que ninguém usava.

**`test:gate` — o gate.** Prova que o servidor **recusa** commit de plano não
aprovado, e aceita depois de aprovado. O gate é do servidor, não do modelo.

**`test:routes` — a camada que a interface usa.** O `smoke` chama `createPlan`
direto; a interface fala HTTP. Entre a função e a rota há serialização, status
code e formato de corpo. É aqui que mora a guarda de regressão do **mediapipe**:
a descrição de `layout` que o modelo lê via `clip_get_options` já afirmou que
`focus` "só funciona com mediapipe instalado" — falso, o detector é o Haar
cascade do OpenCV. A afirmação errada foi corrigida no README, no prompt e no
preflight, e sobreviveu justamente no texto de maior alcance. O teste agora falha
se ela voltar.

**`test:cancel` — o caminho, não a função.** `cancelRun` sempre funcionou; o que
faltava era a rota HTTP que a interface usa. Testar a função diretamente teria
dado verde com o defeito no lugar. Por isso o teste percorre o caminho real:
cria plano, aprova, commita (é a única forma de existir um processo para
cancelar), chama a rota e espera o evento `exit` — que só é emitido no handler de
`close` do filho, ou seja, **depois** de o sistema operacional recolher o
processo. É a diferença entre "o pedido saiu" e "o render parou".

**`test:loop` — o silêncio.** Um defeito de 1-em-10 que só se manifesta como
*ausência*: nenhuma exceção, nenhuma linha de erro, nenhum log. O usuário recebe
uma tela em branco. Nenhum teste que observe o caminho feliz pega isso, e um
teste contra o provedor real não consegue reproduzir sob demanda. Por isso o
provedor falso com roteiro fixo, e por isso o **controle negativo**: sem a
política, o mesmo roteiro tem de falhar. Sem esse controle, o teste passaria
também se a correção não existisse.

**`test:context` — o que o modelo vê.** O defeito era invisível para qualquer
teste que observasse o caminho feliz: a rota funcionava, o SSE fechava, a
resposta chegava. O que faltava era **contexto**. Um teste que passe `history: []`
não distingue "campo ausente" de "campo vazio" — foi assim que o defeito
sobreviveu. Aqui o teste alimenta histórico de verdade, sobe a rota contra um
provedor falso e **inspeciona o payload que o servidor mandou**, que é o único
lugar onde dá para verificar sem adivinhação.

A prova negativa é o que o torna difícil de enganar: o corpo inclui um texto-isca
em `history`. Se alguém voltar a ler o campo do cliente, a isca aparece no payload
e o teste falha. Sem ela, reverter ao defeito antigo daria verde.

**`modelCheckRetry.test.ts` — a espera.** O defeito que ele cobre **não era um
erro, era uma lentidão**. Um bug que só se manifesta como espera não aparece em
teste de unidade nenhum — a não ser que o teste cronometre. Por isso ele sobe um
servidor local que manda headers e nunca fecha o corpo, e afirma um limite
superior de tempo.

**`exitCodes.test.ts` — o contrato entre dois processos.** O painel recebe o
código de saída, não a causa. Traduzir é o que impede o usuário de ler "código 4"
e não saber que perdeu a legenda de todos os cortes.

O teste que vale ali **não é o do mapa** — é o que **lê o `cli.py` de verdade**,
extrai todo `return <n>` e exige que cada um tenha descrição na interface. Um mapa
copiado à mão no teste passaria a mentir no dia em que alguém adicionasse um
código no Python, que é exatamente o que aconteceu com o exit 4. Verificado por
mutação: removendo a entrada do `4` de `src/utils/exitCodes.ts`, o teste falha com
*"código 4 caiu no texto genérico"*.

E ele tem uma guarda contra virar vácuo: um primeiro teste exige que a extração
tenha achado **vários** códigos. Se a formatação do `cli.py` mudar e o regex parar
de casar, a lista viria vazia e o teste principal passaria sem verificar nada —
verde por não ter olhado.

### O que `verify:build` cobre e mais nada cobre

Todos os outros testes batem direto na API, na porta 3000. A interface construída
é servida noutra porta e depende do proxy de `/api`. Se o proxy não valesse para
o preview, o app abriria e **toda chamada de API daria 404** — e nenhum teste
perceberia.

Funciona porque `preview.proxy` cai para `server.proxy` quando não é declarado
(verificado no bundle do Vite: `proxy: preview?.proxy ?? server.proxy`). O
`verify:build` troca esse "em teoria" por medição: sobe a API, sobe o
`vite preview`, e confere que `/` devolve a SPA, que `/api/studio/options`
atravessa o proxy e volta JSON, e que o bundle contém os marcadores da interface.

**A API não serve a SPA, de propósito.** `GET /` na porta 3000 devolve 404. Quem
serve a interface é o Vite (dev) ou o `vite preview` (build). Há teste fixando
isso, para que ninguém "conserte" a API adicionando um catch-all estático — isso
mudaria o dono da rota `/` e poderia mascarar 404 de API como HTML.

### Por que o `test:gate` não chama o LLM

A primeira versão pedia a conversa inteira ao modelo para conseguir um plano.
Isso o tornava intermitente: medido em 4 execuções, 3 entregaram o plano e 1
encerrou o turno depois de `clip_get_options`, sem chamar `clip_plan`. O teste
falhava com *"deveria ter extraído um plano"* — que não é o defeito que ele
existe para pegar.

Um teste intermitente é pior que nenhum: treina a pessoa a reexecutar até
passar, e no dia em que a falha for real ela vai ser ignorada. E havia um
problema de atribuição: uma falha de **prompt** aparecia como falha do
**parser**, apontando para o arquivo errado.

A dependência do modelo virou `gateLlm.test.ts` (`npm run test:llm`), com o
diagnóstico junto — qual modelo atendeu, quais ferramentas chamou, o que
respondeu:

```
modelo que atendeu: gpt-6-luna
erro SSE: (nenhum)
ferramentas chamadas: clip_get_options -> clip_get_options
texto final (300): Preciso saber quantos clipes...
tool_results (300 cada): { "key": "min_duration", ...
```

Sem isso, cada falha ali virava uma sessão de investigação do zero.

**O `test:gate` dispara um render de verdade** (o passo do commit chama
`commitPlan`, que dá `spawn` no pipeline). O cleanup chama `cancelAll()`. A
versão anterior não encerrava nada e o processo filho segurava o event loop por
mais de 2 minutos depois de o teste passar.

Use sempre o `tsx`. O `node --experimental-strip-types` **não** funciona aqui:
ele não resolve um import `./x.js` que aponta para `x.ts`.

**Typecheck limpo não garante que o servidor sobe.** Um `export` faltando em
`chatRoute.ts` passou pelo `tsc --noEmit` e só apareceu no runtime como
`SyntaxError: does not provide an export named ...`. Depois de mexer no
`server/`, suba o servidor de verdade.

**Teste que passa e não sai.** `node --test` espera o event loop drenar. Um
`app.listen` aberto, um socket keep-alive do agente HTTP global, ou um processo
filho vivo mantêm o processo pendurado depois do resultado já impresso. Medido
neste projeto: 134s de espera por um teste de 15s. As três correções foram
`server.closeAllConnections()` antes do `close()`, `agent: false` nas
requisições de teste, e `cancelAll()` para o processo filho.

**Os testes não escrevem no banco de produção.** Os três testes que sobem o
servidor setam `STUDIO_DB=:memory:` antes do import. Sem isso, o `test:llm` (que
manda uma mensagem em `/api/chat`) criava uma sessão de verdade a cada execução:
medido, 35 sessões no banco e as 5 mais recentes eram todas conversas de teste —
a barra lateral do app enchia de `Corta https://www.youtube.com/...`.

`STUDIO_DB` também aceita um caminho de arquivo, para quando for útil inspecionar
o banco de um teste depois de ele rodar.

O `test:context` não precisa disso: ele monta um `express` próprio e chama
`registerChatRoute` com dependências de mentira. Só `server/index.ts` importa
`db.ts`, então essa rota testada isoladamente não tem como tocar o banco de
produção — nem por acidente.

**Ao testar com `curl` neste ambiente, cuidado:** existe um proxy em
`http_proxy` que intercepta `127.0.0.1` e devolve **502** com mensagem de
"upstream connect failed". Parece servidor fora do ar e não é. Use o cliente
`http` do Node (`curl --noproxy '*'` também falha aqui).

**Processo em background não sobrevive entre comandos.** Suba o servidor e faça
a requisição **na mesma** chamada, num script único. Caso contrário você vê o
servidor escutando numa hora e a porta fechada na seguinte.

### Sobre os 18 erros de tipo em `src/`

Os 18 erros (15× TS2322, 2× TS2339, 1× TS2345) tinham **três** causas, não uma.

**1. O tipo dos ícones da lucide (12 dos 18 erros).** `iconMap.ts` e
`NewChatDialog.tsx` declaravam o mapa como
`Record<string, React.ComponentType<{ size?: number; color?: string }>>`. Parece
equivalente a um ícone da lucide, e não é: os ícones são
`ForwardRefExoticComponent<LucideProps>`, e o TS recusa a atribuição por
variância das props (`LucideProps` estende `SVGProps` e traz `strokeWidth`,
`absoluteStrokeWidth`). O tipo certo é o que a própria lib exporta:

```ts
import type { LucideIcon } from 'lucide-react';
export const ICON_MAP: Record<string, LucideIcon> = { Bot, Sparkles, ... };
```

**2. `onClick` no `Card` do tdesign-react.** `CardProps` não estende os atributos
de `div`, então `onClick` simplesmente não existe. O clique foi para um `div`
wrapper, com `key` nele.

**3. API do `Descriptions` usada errada (4 erros, 2 deles silenciosos).**
`Descriptions.Item` **não existe** — o estático é `Descriptions.DescriptionsItem`
(a ausência só apareceu no `tsc`, não em runtime, porque o React não valida isso
ao renderizar). E `itemStyle` não é prop de `TdDescriptionsProps`; o
`paddingBottom` foi para `labelStyle` e `contentStyle`. Isso é o tipo de erro que
o typecheck pega e o navegador não: o componente renderiza, só que sem o estilo.

**O TS2345 em `SettingsPage.tsx`** era um `setFormData` de template sem
`permissionMode`. Corrigido explicitando o campo — os templates não o trazem, e
o estado ficava incompleto em relação ao próprio tipo.

### Sobre o `TS2769` no `app.listen` (por que `HOST` e `Number(process.env.PORT)` andam juntos)

Há um erro de tipo no **servidor** que não é do `src/` e tem causa própria: o
`app.listen` de `server/index.ts`. Vale registrar porque a correção *parece* um
detalhe de estilo e não é, e porque o instinto de "reverter para sumir com o
erro" quebra os testes.

```ts
const PORT = process.env.PORT || 3000;   // tipo: string | 3000
const HOST = '127.0.0.1';

app.listen(PORT, () => { … });           // compila
app.listen(PORT, HOST, () => { … });     // TS2769
```

A causa está nas declarações de `@types/express-serve-static-core`: **toda**
sobrecarga `listen(port, hostname, …)` declara `port: number`. Com
`process.env.PORT` sendo `string | undefined`, a expressão `process.env.PORT ||
3000` vira `string | 3000`, que não casa com `number`. Mas a forma de **dois**
argumentos, `listen(path: string, callback?)`, aceita `string` — então o
`app.listen(PORT, cb)` antigo passava, por acidente, e o tipo errado de `PORT`
ficava escondido. **Foi acrescentar o `hostname` que removeu a única sobrecarga
que casava.** O erro não veio de uma reversão de `Number(...)`; veio de ganhar um
parâmetro.

Por isso o conserto é o companheiro, não o reverso:

```ts
const PORT = Number(process.env.PORT) || 3000;
const HOST = '127.0.0.1';
```

`Number(...)` não é estilo: converte `string | undefined` para `number` e, de
quebra, faz `PORT=abc` cair no padrão (3000) em vez de chegar ao `listen` e
falhar em runtime.

**`HOST = '127.0.0.1'` é funcional, não cosmético — não reverter.** Todos os
testes de rota e o probe do `verify:build` falam com `127.0.0.1` **explícito**
(`host: '127.0.0.1'` em `gateFlow.test.ts`, `studioRoutes.test.ts`,
`gateLm.test.ts`, `singleRun.test.ts`, `cancelRun.test.ts`). Um servidor que
escuta no default (todas as interfaces / `::`) pode não responder em IPv4
loopback no Windows. Reverter o `HOST` para "sumir com o TS2769" trocaria um erro
de compilação por testes de rota que falham por conexão — o remédio seria pior
que a doença.

### Sobre o `tsconfig.node.json` (era o que mantinha `npm run build` quebrado)

`npm run build` é `npm run typecheck && vite build`. O `vite build` sempre
funcionou; o `tsc -b` **nunca** passou. A causa era configuração, não código:

1. O `tsconfig.node.json` era o do template do Vite (feito só para o
   `vite.config.ts`), mas com `server/**/*.ts` adicionado ao `include`. Sem
   `target` explícito o default é ES5 — e todo `for...of` sobre `Map`/`Set`
   virava TS2802. Sem `allowImportingTsExtensions`, os imports `.ts` explícitos
   dos testes viravam TS5097.
2. Sem `outDir`, o `tsc -b` **despejava `.js` e `.d.ts` dentro de `server/` e
   `src/`** — 45 arquivos. Como o projeto roda com `tsx` (TypeScript direto),
   essas eram cópias obsoletas capazes de sombrear o fonte real sempre que um
   resolvedor seguia um especificador `./x.js`.
3. O `export default db` em `db.ts` não conseguia nomear o tipo
   `BetterSqlite3.Database` (namespace não exportado no `@types`) → TS4023 na
   emissão de declaração. Resolvido anotando `const db: Database.Database`.
4. `createSession` exigia `sdk_session_id`, e o único chamador
   (`POST /api/sessions`) não passava. Agora o campo é opcional na entrada e
   normalizado para `null` — toda sessão nasce sem sessão de SDK. Era
   **inconsistência de tipo, não crash**: verificado que o better-sqlite3 aceita
   `undefined` como bind e grava NULL.

#### A armadilha que sobrou depois de consertar o `outDir`

Mandar a emissão para `node_modules/.tmp` resolveu o lixo dentro dos fontes, mas
criou um problema pior e mais silencioso.

**Um projeto que emite declarações passa a POSSUIR os arquivos do seu
`include`.** O TypeScript então resolve os imports desses arquivos para o `.d.ts`
emitido, **não para o fonte** — inclusive no *outro* projeto, o do front.

O sintoma: adicionei `'cancelled'` a `RunState['phase']` em `src/types.ts`, o
`grep` confirmava o membro no arquivo, e o `tsc` continuava dizendo que o tipo era
`'idle' | 'running' | 'done' | 'failed'`. Não era cache nem editor: o programa do
front estava lendo `node_modules/.tmp/tsc-node/src/types.d.ts`, gerado antes da
edição. Um `--listFiles` mostrava o `.d.ts` e **não** mostrava `src/types.ts`.

Diagnóstico e correção:

- `src/types.ts` estava no `include` do projeto Node com a justificativa de ser
  "import de tipo do servidor". **Nada no servidor o importa** — a justificativa
  nunca foi verdade. Era ele que criava a sombra.
- `src/utils/planParser.ts` continua no `include` (os testes do servidor o
  importam de verdade), mas com `noEmit` ele não gera cópia nenhuma.

A correção foi **eliminar a emissão**, não limpá-la:

- o projeto Node virou `noEmit: true`, sem `composite` e sem `outDir`;
- o `tsconfig.json` deixou de referenciá-lo, e é isso que permite o `noEmit`
  (projeto referenciado não pode ter `noEmit` — TS6310);
- `build` passou a rodar `typecheck`, que confere os dois projetos separadamente.

O servidor não precisa emitir nada: em runtime quem executa é o `tsx`, que apaga
os tipos sem gerar arquivo. Sem emit não existe cópia obsoleta, então não existe
sombra — a classe inteira de bug deixa de ser possível, em vez de ficar sendo
administrada. `node_modules/.tmp` foi removido.

O `include` continua sem `src/**` de propósito: `src` é código de browser (JSX,
DOM, `localStorage`) e pertence ao projeto de `tsconfig.json`; arrastá-lo para o
projeto do servidor trocaria um erro de configuração por dezenas de erros de
ambiente.

### O gate estático: `noUnusedLocals` e `noUnusedParameters`

O projeto **não tem lint**. O único gate estático é o `tsc`, e o `tsc` só pega o
que as flags mandarem pegar. As duas flags de símbolo morto estavam em `false`
nos dois tsconfigs, então a pergunta "existe variável declarada e nunca usada?"
não tinha resposta automática nenhuma.

Medição (não estimativa) — as duas flags ligadas por linha de comando, sem
alterar config:

```
$ tsc -p tsconfig.json      --noUnusedLocals --noUnusedParameters
$ tsc -p tsconfig.node.json --noUnusedLocals --noUnusedParameters
```

14 ocorrências: **7 no front, 7 no servidor**. Ligar as flags não é higiene
cosmética — uma delas era um bug de verdade.

#### O caso que pagou a conta: o `doctor` mentia

```ts
const auth = inspectDotEnv(envPath, REQUIRED);   // resultado calculado…
const hasAuth = (process.env.CODEBUDDY_API_KEY || '').length > 0 || …;
//                 …e descartado em favor de uma checagem pior
```

`inspectDotEnv` é **puro** — lê o arquivo e consulta `process.env`, não escreve
nada. E o `doctor` é o **único** ponto de entrada que não chama `loadDotEnv`
(`index.ts:26`, `modelCheck.ts:438` e `protocolProbe.ts:311` chamam). Ou seja:
no processo do `doctor`, o `.env` **não está** no ambiente. Ler `process.env`
direto ali dava falso negativo para quem guardou a chave no arquivo — que é
exatamente o que a seção 1 do próprio `doctor` acabou de mandar fazer.

E o descarte era pior do que um simples falso negativo: ele colapsava *"não está
no arquivo"* e *"está no arquivo mas vazia"* na mesma frase, que é justamente a
distinção que `inspectDotEnv` existe para fazer. Medido no `.env` real:
`CODEBUDDY_API_KEY` **declarada e vazia**, `CODEBUDDY_AUTH_TOKEN` **ausente** —
dois problemas diferentes que o relatório antigo apresentava como um só.

O veredito era o mesmo; a *ação* que ele sugere não era. A lição: **um resultado
calculado e descartado é a impressão digital de um caminho abandonado no meio** —
quem escreveu a linha certa e depois trocou por outra pior. Vale procurar o que
mais ficou para trás no mesmo lugar.

**E foi exatamente o que aconteceu.** Aquela seção do `doctor` checava
`CODEBUDDY_API_KEY` / `CODEBUDDY_AUTH_TOKEN` — credenciais do modo SDK, que não
afetam o chat. Corrigida a mentira, a seção ficou *precisa e inútil*. Quando o SDK
saiu do projeto (junto com `/api/check-login`, `/api/save-env-config`, o painel de
login do `SettingsPage` e a dependência), ela saiu junto. O `doctor` agora tem
**4 seções em vez de 5**, e todas falam de algo que pode realmente impedir o chat
de responder.

A armadilha, porém, continua valendo — e por isso o `doctor` **não** chama
`loadDotEnv`: as chaves que restaram passam por `inspectDotEnv`, que consulta o
processo **e** o arquivo na ordem de precedência. O comentário no lugar da seção
removida registra isso, para a lição não morrer com o código.

#### O gate provando o próprio valor

Quando o painel de login saiu do `SettingsPage`, o `tsc` acusou **sozinho** os 8
imports que ficaram órfãos — `useEffect`, `useCallback`, `Loading`, `Link`, `Tag`,
`CheckCircleFilledIcon`, `CloseCircleFilledIcon`, `RefreshIcon`:

```
src/components/SettingsPage.tsx(1,20): error TS6133: 'useEffect' is declared but its value is never read.
src/components/SettingsPage.tsx(10,3): error TS6133: 'Loading' is declared but its value is never read.
…
```

Sem as flags, isso passaria como código morto silencioso. Com elas, a limpeza
virou parte da própria edição.

#### Os outros 13

| Onde | O quê | Gravidade |
| --- | --- | --- |
| `Sidebar.tsx` | prop `agents` nunca lida | ruído de API — a lista funciona via `getAgent` |
| `ToolCallsCollapse.tsx` | 4 ícones + `index` do `map` | imports mortos |
| `useAgents.ts` | `useEffect` importado e não usado | import morto |
| `chatRoute.ts` | `listModels(projectRoot, …)` | parâmetro que o corpo nunca leu |
| `index.ts` | 4 handlers com `req` não usado | viraram `_req` (convenção do TS) |
| `llm.ts` | `sleep` | sobrou da política de retry antiga |

Nenhum desses 13 muda comportamento. Estão listados porque **o custo não é o
símbolo, é o sinal**: cada um é um lugar onde o código diz uma coisa e faz outra,
e é assim que um `agents` que ninguém lê vira, seis meses depois, um bug de
verdade. O `doctor` é a prova de que o padrão acontece.

As flags agora estão em `true` nos dois tsconfigs, e o `npm run typecheck` — que
é a primeira metade de `npm run build` — passa a falhar em qualquer símbolo morto
novo. Isso é o mais perto de um lint que este projeto tem, e é de graça.

## Manutenção

**Quando um flag novo aparecer no `cli.py`, ele precisa ser adicionado a
`server/clipOptions.ts`.** A lista fechada é proposital: é ela que impede o
agente de executar algo que ninguém revisou.

### Nota sobre `npm install`

Dois modos de falha conhecidos, **nenhum dos dois é dependência faltando de
verdade** — os dois se resolvem com um comando específico.

**1. `EBUSY` em `node_modules/esbuild/install.js`.** É o post-install tentando
executar o binário enquanto o build nativo ainda segura o arquivo. **Rodar
`npm install` de novo resolve.**

**2. `Cannot find module '@rollup/rollup-win32-x64-msvc'` depois de um
`npm install` que removeu pacotes.** Bug do npm com dependências **opcionais por
plataforma**: quando o install re-resolve a árvore, os binários nativos do rollup
somem junto. Medido: remover uma dependência removeu 52 pacotes e deixou
`node_modules/@rollup/` **vazio**.

```
$ vite build
Error: Cannot find module '@rollup/rollup-win32-x64-msvc'

# conserto — a versão tem de casar com o rollup instalado
$ node -e "console.log(require('./node_modules/rollup/package.json').version)"
4.63.5
$ npm install @rollup/rollup-win32-x64-msvc@4.63.5 --save-optional
```

**E a lição que fica:** `typecheck` **não** pega isso — o `tsc` não toca no
bundler. Sempre que mexer em `package.json`, rode `npm run build` **depois**, não
só `npm run typecheck`. O `verify:build` também só falha aqui de forma indireta
(ele depende do `dist/`).
