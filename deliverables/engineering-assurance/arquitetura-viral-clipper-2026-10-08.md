# Avaliação arquitetural — viral-clipper

**Data**：2026-10-08
**Workflow**：2 — System Design (arquitetura / design)
**Participantes**：Archi（arquiteto）· Rex（SRE）· Tessa（testes）· Docu（documentação）· Zhen（lead, orquestração e verificação)

**Decisões de produto tomadas pelo dono durante a avaliação**（mudam a arquitetura-alvo）：
| # | Decisão | Consequência |
|---|---|---|
| **a** | `public-site/` **sai de vez** | Não volta como repositório separado; `design-system/.../pages/public-site.md` sai junto |
| **b** | **O painel web É o produto principal** | Cai a justificativa de "extração mecânica" do `server.py`; ele passa a merecer arquitetura de **aplicação** |
| **c** | **Uso em uma máquina**（single-user） | Sem sessão por usuário, multitenancy ou autenticação; `_state` global + lock de `output_dir` bastam. Corolário: **não há redundância nenhuma** — backup deixa de ser opcional |

---

## 📌 TL;DR（sumário executivo）

- **O núcleo está são; a casca está inchada.** O pipeline Python tem camadas limpas, dependências acíclicas e uma costura central correta (`score_windows()` / `pick_windows()` com o ranker exatamente entre as duas, `pipeline.py:258-262`). O risco não está na lógica de negócio — está em `web/server.py` (3.415 linhas), `tests/test_web_server.py` (7.361 linhas) e no **estado do git**.
- **Distribuição de severidade**：🔴 Severo **5** · 🟠 Alto **9** · 🟡 Médio **11** · 🟢 Baixo **3**（28 achados, 2 deles reprovados na verificação e descartados）。
- **Bloqueante**：sim, dois — (1) **37 entradas não commitadas**, incluindo a deleção irreversível de `public-site/` (10 arquivos) e `docs/auditoria-2026-10-07.md` ainda *untracked*; (2) **zero testes de integração HTTP** — o roteamento e o `_guard_origin()` só são provados lendo o código-fonte.
- **Não bloqueante, mas caro**：sem `/health`, sem watchdog de job, sem checagem de disco, estado de execução só em RAM。
- **O custo da suíte foi mal diagnosticado.** Medida completa: **1.769 s (29 m 29 s)** para 1.435 testes — e o gargalo **não é mídia/ffmpeg**: os 25 testes mais lentos custam **10–22 s cada**, por overhead de filesystem/subprocess no Windows (um write atômico trivial leva 22,25 s; `test_missing_file_raises` leva **18,8 s**, medido por mim).
- **Três decisões de produto foram tomadas durante a avaliação**（a: `public-site/` sai de vez · b: **o painel é o produto principal** · c: **uso em uma máquina**）。A **(b) reescreve a recomendação central**: a refatoração do `server.py` deixa de ser extração mecânica e passa a ser arquitetura de aplicação (§4), e `/health` + persistência de jobs deixam de ser opcionais.
- **Duas premissas da documentação interna estavam erradas** e foram corrigidas nesta sessão（ver §8）。

---

## 🎯 Cartão de conclusão principal

| Item | Conteúdo |
|------|----------|
| **Avaliação geral** | 🟡 **Aprovada com ressalvas** — arquitetura do núcleo 🟢; casca web 🟠; higiene de repositório 🔴 |
| **Bloqueadores** | **2**（trabalho não commitado; ausência de teste de integração HTTP) |
| **Ações-chave** | **14**（3 × P0, 9 × P1, 2 × P2) |
| **Próximo passo** | Fase 0 (commitar em frentes separadas) → **Fase 1: teste de integração HTTP**, que é o *gate* da extração → só então extrair |
| **O que NÃO fazer** | Reescrever `server.py` do zero; Flask/FastAPI; bundler; `/api/v2/`; banco para o estado; **auth/multiusuário (fora de escopo por decisão (c))**; trocar o `.venv` para 3.14 |

---

## 1. Requisitos e objetivos（o que o sistema precisa ser）

Derivados do código e dos docs, não inventados:

| Requisito | Evidência | Como a arquitetura atende |
|---|---|---|
| Rodar 100 % local, numa máquina (8 threads / 7,8 GB / sem GPU) | `config.py:229` (`workers=2`), `pipeline.py:503` | Painel em `127.0.0.1:7755`, stdlib only, sem nuvem |
| Módulos opcionais nunca fatais | `reframe.py:190-192`, `pipeline.py:84-132`, `ranker.py:637` | Detecção por **artefato** (caminho do cascade), não por `import`; degrada + loga |
| Um run por `output_dir` | `lock.py:40`, README:601-651 | Lock de SO por byte-range no offset 4096, exit 5 |
| Ajustes persistidos e reutilizáveis | `ajustes.toml` na raiz, `server.py:146-167` | `AJUSTES_KEYS` = subconjunto de `ClipConfig` (87 campos) |
| Chave de API nunca volta ao navegador | `_providers_payload()` | Só `has_key`; run lê de `os.environ` |
| Render 9:16 1080×1920 como constante do produto | rótulo fixo na UI | Sem "chip" — virou rótulo |

**Objetivo do trabalho**: decidir se a arquitetura atual sustenta a evolução, e em que ordem mexer.

---

## 2. Arquitetura atual（design de alto nível）

**Núcleo — limpo.** `cli.py` → `pipeline.py` → estágios (`pipeline.py:11-18`). Direção acíclica: `score.py` depende só de `hooks`/`audio`/`config`/`transcribe` (`score.py:22-25`); `ranker.py` de `config`/`score`/`util`/`providers` (`ranker.py:44-51`); `providers.py` só de `config`/`util` (`providers.py:35-36`). Não há ciclo `ranker ↔ providers`.

**Três ciclos são quebrados por import tardio, e de propósito**: `config.validate` importa `caption_presets`/`providers`/`template` dentro do método (`config.py:300,321,335`); `ranker.build_provider` importa `providers` na função (`ranker.py:585`); `providers` importa `user_providers` na função (`providers.py:205`).

**A costura central está certa.** `score.score_windows()` (`score.py:332`) é separado de `pick_windows()` (`score.py:397`), e `pipeline.select_windows` chama `ranker.apply` **exatamente entre os dois** (`pipeline.py:258-262`). É isso que deixa o curador de IA entrar sem reescrever a seleção.

**A casca — problemática.** `web/server.py` é um segundo produto dentro do repositório:

| Fato | Evidência |
|---|---|
| 3.415 linhas, classe `Handler` a partir de `:2399` | `server.py:2399` |
| `do_GET` com ~20 rotas num único método (`:2551-2776`) | `server.py:2551` |
| `do_PUT` (`:2793`) e `do_POST` (`:2823`) no mesmo handler | `server.py:2793,2823` |
| ~40 funções de módulo **antes** da classe (`_run_job` `:765`, `_options_to_config` `:407`) | `server.py:407,765` |
| Estado global `_state` + `_lock` compartilhado com threads de job | `server.py:197-198` |
| **A ordem das rotas importa**: o guard `if path != "/api/run":` (`:2861`) precisa vir depois dos POSTs | `server.py:2861` |
| O painel reimplementa orquestração que a CLI já tem (`_run_job` chama `pipeline.*` direto) | `server.py:765` |
| `quality.inspect_clip` é usado **só** pelo servidor, nunca pela CLI | `server.py:884` |

**Acoplamentos a vigiar**: `ClipConfig` (87 campos) é o contrato universal e `AJUSTES_KEYS` (27) o replica à mão (`server.py:146-167`); metadados do yt-dlp circulam como `dict` sem tipo (`pipeline.py:290`); `window.components` é `dict[str,float]` mas às vezes recebe string (`ranker.py:736` insere `llm_motivo`).

---

## 3. Registros de decisão（ADR das decisões vigentes）

| # | Decisão | Contexto / Consequência | Status |
|---|---|---|---|
| **ADR-01** | Painel sem bundler, stdlib only, tokens CSS duplicados de propósito | `server.py:1-5`; `index.css × scrap.css` = 18 seletores repetidos, `shared.css` = 9, `scrap ∩ shared` = 0 | Aceita — **razão revista**: era "ninguém se importa"; passa a ser "trade-off deliberado, reabre se houver multiusuário" |
| **ADR-02** | ~~`server.py` monolito~~ → **arquitetura em camadas** | Substituído por: roteador declarativo + `state.py` + `handlers/`; guard centralizado em `handle_one_request()`; rota nova = 1 linha em `ROUTES` + 1 handler | **Superseded** → novo ADR-02 Aceito |
| **ADR-03** | Módulos opcionais degradam; opcional detectado pelo **artefato** | `reframe.py:190-192` checa `opencv_cascade_path()`; `_transcribe_hybrid` devolve exit 4 (`pipeline.py:84-132`); `_judge` nunca levanta (`ranker.py:637`) | Aceita |
| **ADR-04** | `.venv` preso ao Python 3.13 | Wheels de `ctranslate2`/`av`/`opencv`; **não recriar em 3.14** | Aceita |
| **ADR-05** | Testes extraem a função REAL + DOM falso; handler sem socket | `inspect.getsource` de `do_GET`/`do_POST`, `attr.__get__(self, cls)`; **consequência: nunca editar `.py` com a suíte rodando** | Aceita — **e é o motivo de o teste de integração ser o gate da extração** |
| **ADR-06** | `.py` exige reiniciar o processo | Rota nova = 404 até reiniciar; `web/*` é lido a cada request | Aceita, **consequência ampliada**: com o painel sendo produto, persistir o histórico de jobs deixa de ser opcional — hoje o restart zera a galeria (`server.py:197-198`) |
| **ADR-07** | Lock de SO por `output_dir` em vez de pidfile | Byte-range no offset 4096, nunca apagado, exit 5 (`lock.py:40`) | Aceita |
| **ADR-08** | Remoção do `public-site/` (10 arquivos) | **Decisão (a): definitiva** — não volta como repo separado | Aceita（era "em andamento"） |

---

## 4. Arquitetura-alvo e refatoração em fases

**Premissa**: o painel é o produto (decisão b). A arquitetura-alvo deixa de ser "extração mecânica" e passa a ser **arquitetura de aplicação** — mas sem reescrever `server.py` do zero.

### 4.1 Camadas propostas para o `web/`

| Módulo | Responsabilidade | O que sai de `server.py` |
|---|---|---|
| `web/server.py` | **Só bootstrap**: `UiServer` (`:2322`), `Handler` fino, `main` (`:3385`). Mantém o nome — é o arquivo que os testes leem | — |
| `web/httpio.py` | Como falar HTTP: `send_json`, `send_file`, `send_redirect`, `etag_for`, CSP | `:2411-2549` |
| `web/router.py` | **Tabela declarativa** `ROUTES = [(method, matcher, handler), ...]`, matcher exato ou prefixo (para `/api/thumb/`, `/api/clips/`) | o despacho de `do_GET`/`do_PUT`/`do_POST` |
| `web/state.py` | **Único dono** de `_state`/`_lock` (`:197-198`) e dos slots `_RUN_SLOT`/`_DOWNLOAD_SLOT`/`_ARCHIVE_SLOT` (`:1575/1562/1568`) | estado global + construtores de record |
| `web/handlers/` | `jobs.py` (`_run_job` `:765`, `_options_to_config` `:407`), `pages.py`, `ajustes.py`, `providers.py`, `scrap.py`, `media.py`, `prompts.py`, `transcript.py` | os `_handle_*` |
| `web/paths.py` | `resolve_within`, `_ASSET_TYPES` (`:955`), `asset_content_type`, `list_library` | — |

**Por que tabela e não decorador**: com decorador a ordem fica implícita na ordem de import — exatamente a armadilha atual. Na tabela a ordem passa a ser **dado**, e o `if path != "/api/run":` (`:2861`) some: `/api/run` vira só mais uma linha e o 404 é o "nada casou".

**O guard deixa de depender de disciplina.** Hoje `_guard_origin()` é chamado em cada verbo (`:2552`, `:2809`, `:2831`) — um `do_DELETE` futuro escapa. Centralizar em `Handler.handle_one_request()` (uma vez por requisição, antes de selecionar o verbo) torna o guard **inescapável por construção**, e `test_both_entrypoints_call_the_guard` (`test_web_server.py:4402`) vira obsoleto — substituído por um teste de integração que prova 403 para qualquer verbo.

**Custo a declarar**: importar `web.router` como pacote exige `web/__init__.py` e mexer no `sys.path` do harness (ADR-05 lê `server.py` por `getsource`).

### 4.2 Contrato HTTP como contrato de produto

O docstring `:7-15` já fixa o contrato real: `/...` = páginas (endereço do usuário, muda com redirect 301, `:2576-2581`); `/api/...` = dados/ações (só o JS do próprio servidor chama). Proposta: (a) fonte única = `ROUTES`; (b) `docs/painel-web.md` gerado a partir dela; (c) **teste de contrato** comparando `ROUTES` com a lista documentada — renomear rota sem atualizar o doc falha. **Não** criar `/api/v2/` (YAGNI — não há consumidor externo). A migração `/api/` em curso deve ser commitada como **um commit atômico** (server.py + JS + testes juntos), sem redirect nas rotas de dados e mantendo 301 só para páginas.

### 4.3 Sequência revisada

| Fase | Mudança | Por que nesta ordem |
|---|---|---|
| **0** | Commitar em frentes separadas: (a) `public-site/`, (b) `web/publicar.*`, (c) migração `/api/` + docstring | Pré-requisito de tudo |
| **1** | **Teste de integração HTTP** (sobe `Handler` em `127.0.0.1:0`) — **gate da extração** | O ADR-05 acopla os testes ao *layout do arquivo* via `getsource`; a extração vai quebrá-los. A rede de segurança precisa ser **comportamental**, não de source |
| **2** | Extração + tabela de rotas + `state.py`, guard em `handle_one_request()` | Já com a rede de segurança no lugar |
| **3** | Persistência do histórico de jobs + `/health` | Deixaram de ser opcionais com a decisão (b) |
| **4** | Tipar metadados (`TypedDict`/dataclass); remover código morto (`score.rank_windows` `score.py:446`, `viral_report.py:331`) | Inalterado |

**O que NÃO mudar**（e por quê）：
- A separação `score_windows` / `pick_windows` — é o que permite o curador entrar no meio.
- Sinais absolutos em [0,1] + `_BOUNDED_SIGNALS`/`_BANDED_SIGNALS` — normalizar por percentil é a regressão que o projeto já pagou para evitar.
- Detecção de opcional por artefato — `import` mente quando a wheel está instalada e o modelo não.
- O lock de SO — resolve o caso real (dois runs escrevendo os mesmos arquivos).
- **Reescrever `server.py` do zero é a opção errada**: é feio, mas cada linha documenta um incidente real.

**O que não fazer mesmo agora**: Flask/FastAPI (quebra ADR-04 e não se paga em mono-máquina); bundler (ADR-01); `/api/v2/`; banco para o estado (a retomada vem do filesystem); **auth/multiusuário — fora de escopo por decisão (c)**, não por indefinição; tocar em `score`/`pipeline` — o núcleo está são.

---

## 5. Operabilidade（Rex）

**Postura atual.** Um processo stdlib, `127.0.0.1:7755` (`server.py:192-193`), `ThreadingHTTPServer` (`:2322`). O guard de instância é **por porta** (`SO_EXCLUSIVEADDRUSE`, `server.py:2332-2339`) — impede dois na 7755, **não** impede 7755 + 7761 + 7801 simultâneos, que já foram encontrados em campo.

**Persiste em disco**: `ajustes.toml`, `provedores-usuario.toml`, `prompts/curador.txt`, `.ajustes-transcript-source`, `output/clips.json`+`clips.md`, `.viralclipper.lock`, caches de transcrição.
**Perde no restart**: **todo** o estado de execução — fila, galeria e progresso vivem em `_state` em RAM (`server.py:197-198`); reiniciar zera (`server.py:1855-1870`).
**Observabilidade**: **não existe `/health`**. Saúde só se infere de `GET /` e `/api/status` (`:2679-2687`). O log é `Logger` em stdout com 4 prefixos (`util.py:36-69`) — sem timestamp, sem nível, sem correlação, sem arquivo.

**Modos de falha（probabilidade × impacto）**：

| # | Falha | Sintoma | Mitigação |
|---|---|---|---|
| 1 | **Disco cheio** (alta × alto) | `OSError Errno 28` no render/download; 19 falhas fantasma no pytest | **Zero checagem de disco no repo**; limpeza só roda no `finally` (`server.py:928-934`) — kill duro deixa `output/_work` |
| 2 | **Worker preso/zumbi** (média × alto) | `/api/run/progress` com `active:true` e `elapsed` crescendo sem linhas novas | `_run_job` é **síncrono na thread do handler** (`server.py:2869`), sem timeout; só resta `taskkill /F /T` |
| 3 | **Múltiplos painéis** (média × médio) | UI "antiga" | Provar de qual diretório o processo serve |
| 4 | `.py` sem restart (alta × baixo) | Rota nova = 404 | Reiniciar antes de validar |
| 5 | Opcional ausente `reframe`/`ranker` (média × baixo) | Degrada + loga | Por design |
| 6 | Chave de API ausente/expirada (média × baixo) | Veredito `no-key` / 401 / 403-1010 | `_export_saved_key` lê `os.environ` (`server.py:837`) |
| 7 | Sessão do Instagram expirada (média × médio) | Listagem vazia | Prova offline: `ig_session_check.py` |
| 8 | **OOM no render** (média × alto) | `BrokenProcessPool` | Retry **re-renderiza TODAS as tarefas, inclusive as prontas** (`pipeline.py:517-524`) = encodes duplicados |

**Reclassificação após as decisões (b) e (c)** — o que era "barato, dá para depois" muda de categoria quando o painel é o produto:

| Lacuna | Antes | Agora | Por que mudou |
|---|---|---|---|
| `/health` real | barata, opcional | **requisito de produto** | Produto sem sonda de saúde não é operável; hoje só se infere por `GET /` e `/api/status` (`:2679-2687`) |
| Persistência do histórico de jobs | cara, adiável | **requisito de produto** | Restart zera fila/galeria/progresso (`server.py:197-198`) — aceitável num atalho, inaceitável num produto |
| Watchdog / timeout de job | barata, opcional | **requisito de produto** | `_run_job` é síncrono na thread do handler (`:2869`); job preso só se resolve com `taskkill` |
| Checagem de disco | barata, opcional | **requisito de produto** | Disco já chegou a 0 livre de 238 GB; falha como `Errno 28` no meio do render |
| Log estruturado em arquivo | barata, opcional | **requisito de produto** | Hoje é stdout com 4 prefixos (`util.py:36-69`); sem arquivo, incidente pós-kill é irrecuperável |
| **Backup do estado + `output/`** | não estava na lista | **NOVO obrigatório** | Decisão (c): uma máquina só, **zero redundância** — a queda do disco leva `ajustes.toml`, `provedores-usuario.toml`, `prompts/curador.txt` e `output/` |
| **CI** | não estava na lista | **NOVO obrigatório** | Não há `.github/`; com o painel sendo produto, a suíte de 29 min precisa de um lugar para rodar |
| Guard de instância **entre** portas | caro, adiável | caro, adiável | **Não muda**: com uma máquina e uma pessoa, rodar dois painéis é escolha do operador, não acidente |
| Autenticação / multiusuário | risco | **fora de escopo por decisão** | Decisão (c). O risco corrente não é exposição remota: é processo de outra conta na mesma máquina, extensão de navegador ou malware local. Ressalva: **se um dia houver túnel/port-forward, a avaliação muda** |
| Retry do pool re-renderizando o pronto | caro, adiável | caro, adiável | **Não muda**: é otimização de custo, não de operabilidade |

> **Lacuna estrutural**: nenhuma telemetria sobrevive ao processo. Todo incidente pós-kill é arqueologia.

---

## 6. Estratégia de testes（Tessa）

**Pirâmide real**：**1.435 testes** em 26 arquivos. `test_web_server.py` sozinho tem **465 testes (32 %)**. Três níveis, na prática:
- **Unitário puro** (`viralclipper/*`) — fakes injetados, nunca mock de rede. Rápido.
- **Contrato de frontend** (lê `.html/.css/.js` reais e asserta string/regex) — a maior parte de `test_web_server.py`: 116 `read_text(`, 104 `server.WEB_DIR`, 15 `page_source(` (`:196`), 6 `inspect.getsource`.
- **Integração HTTP — AUSENTE.** Zero ocorrências de `serve_forever|HTTPServer|make_server`. O HTTP só é provado por handler-fake (`_FakeHandler` + `_handle_*` via `attr.__get__(self, cls)`, `test_ajustes.py:946`).
- **E2E — fora da suíte**: `smoke_test.py`, `cache_e2e_test.py`, `band_parity_check.py`, `ig_session_check.py`, `reframe_check.py`, `_scratch/*.mjs`.

**Custo — a hipótese de mídia estava ERRADA.** `test_web_server.py` = **88,7 s** isolado (7 failed, 458 passed, 412 subtests). A suíte inteira foi medida: **`9 failed, 1426 passed, 918 subtests in 1769.25 s (29 m 29 s)`** para 1.435 testes — **~1,2 s por teste em média**, espalhado; não existe um "monolito de mídia". Os 25 testes mais lentos custam **10–22 s cada** e são dominados por **overhead de filesystem/subprocess no Windows**:

| Teste | Tempo | Comentário |
|---|---|---|
| `test_ajustes.py::AjustesAtomicWriteTests::test_a_good_write_replaces_and_leaves_no_part` | 22,25 s | um write atômico trivial |
| `test_config_file.py::LoadingTests::test_missing_file_raises` | 12,3 s → **18,8 s** (medido por mim) | deveria ser instantâneo |
| `test_transcript_cache.py::SaveLoadRoundTripTests::test_empty_transcript_is_cacheable` | 16,00 s | |
| `test_curator.py::LoadPromptTests::*` | ~11,6 s cada | |
| `test_lock.py::CrossProcessTests` | 11,7 s | |
| `test_web_server.py::PublicacaoPayloadTests::*` | 10,9–13,0 s × 6 testes | |

Causa provável: `tempfile.mkdtemp` + `fsync`/`os.replace` reais + `subprocess` + varredura de antivírus por arquivo. **O ganho de tempo está em reduzir o custo POR TESTE, não em cortar mídia.**

**Rotas sem teste**：

| Rota | Evidência |
|---|---|
| `GET /api/scrap/archive/thumb` | `server.py:2633` — `_archive_thumb_path` não aparece em nenhum teste |
| `POST /api/transcript/normalize` | `server.py:3343` — `_handle_normalize` não é referenciado |
| `POST /api/providers/remove` | `server.py:3267` — `_handle_remove_provider` não é referenciado |

**Módulos sem teste nenhum**：`quality.py` (`inspect_clip` roda ffprobe e alimenta o selo da galeria, `server.py:884`) e **`audio.py`** (`frame_db`, `adaptive_threshold`, `detect_silences`, `quiet_segments`, `analyze_audio`, `load_wav_mono`, `extract_audio` — `:73-208`). Este segundo é o mais grave: `audio.py` é a **entrada dos sinais do score**; um bug ali muda o ranking inteiro e nenhum teste cai.

**Parcial**: `caption_presets.py` (só `resolve()`), `render_task._render_with_retry` (`:130`), `providers.py`/`provider_probe.py`.

**Invariantes do score — TRAVADAS, e bem.** `AbsoluteScoreTests` (`test_score.py:297`) prova independência dos vizinhos (`:316`), ausência de nota alta sem gancho (`:323`), que sinais bounded **não** são reescalados (`:344`) e que loudness é *banded*, não *ranked* (`:357`). `AudioOnlyCeilingTests` (`:426`) deriva o teto de `WEIGHTS`, não de constante. A regra "nunca normalizar por percentil" está protegida como invariante, não como valor.

**Reorganização proposta**：quebrar `test_web_server.py` por responsabilidade num pacote `tests/web/` (mover **verbatim**, sem reescrever): `_helpers.py`, `test_routes_contract.py`, `test_index_page.py`, `test_ajustes_page.py`, `test_scrap_page.py`, `test_publicar_page.py`, `test_gallery.py`, `test_providers.py`, `test_handlers.py`. Marcar os pesados (`reframe`, `render`, `download`, `ig_profile`, `template`) com `-m slow` para o default rodar a camada rápida.

**Top 5 testes a escrever primeiro**：
1. `tests/test_audio.py` — `frame_db`/`adaptive_threshold`/`detect_silences` com array sintético, provando **números absolutos**.
2. `_handle_normalize` via `_FakeHandler` — 400 com corpo vazio, 200 com normalização aplicada.
3. `_handle_remove_provider` — remove do arquivo e **recusa nome de fábrica** (espelha o guard do `save`).
4. `GET /api/scrap/archive/thumb` — 400 com índice inválido, 404 sem thumb.
5. `tests/test_web_integration.py` — **o primeiro teste de integração**: subir `Handler` em `127.0.0.1:0`, `GET /api/status` = 200 `{jobs,clips}`, `POST /api/run` sem url = 400.

**Falhas pré-existentes（suíte completa medida hoje）**：**`9 failed, 1426 passed, 918 subtests in 1769.25 s (29 m 29 s)`**. A **composição** mudou em relação à memória do projeto:
- **7 × `test_web_server.py::HeroPreviewTests`** — testam `preview-card`/`preview-video`/`data-live`, markup que o produto abandonou (o hero virou `.hero.studio-welcome`). Recomendação: **remover/reescrever, não `xfail`** — `xfail` permanente é dívida que esconde regressão futura.
- **2 × `test_reframe.py`** — `FocusCenterXTests::test_returns_the_median_center` e `CleanupSamplesTests::test_focus_center_x_survives_a_cleanup_failure` (`TypeError` em `test_reframe.py:330`); **reproduzidos por mim**. São as "suas" do reframe: **quarentenar (`xfail` com motivo) ou consertar** — não deixar vermelho na suíte default.
- **Os 2 que a memória listava do frontend PASSAM agora**: `AjustesHeadingTests::test_no_heading_level_is_skipped` (os comentários com `<h2>` foram reescritos em `ajustes.html`) e `SharedStyleSheetTests`.

---

## 7. Estrutura de documentação（Docu）

**Inventário**：

| Arquivo | Tam. | Status |
|---|---|---|
| `README.md` | 941 l. | Atual, **inchado e misto** |
| `HISTORICO.md` | 52 l. | **Desatualizado** (narra a perda do `.git` como se não houvesse remote) |
| `docs/auditoria-2026-10-07.md` | 580 l. / 50 KB | **Efêmero + permanente misturado; não commitado** |
| `docs/curador.md` | 144 l. | Atual — **o melhor doc do repo** |
| `docs/modelos-gratuitos.md` / `-nvidia.md` | 128 / 131 l. | Atuais (retratos datados) |
| `docs/superpowers/specs/2026-09-27-*.md` | 26 KB | **Parcialmente obsoleto** (descreve `web/templates.html`/`templates.js`, que não existem mais) |
| `design-system/viral-clipper/MASTER.md` | 254 l. | Atual |
| `design-system/.../pages/public-site.md` | ~2 KB | **Órfão** (o site foi removido) |
| `prompts/curador.txt` | 12 KB | **Não é doc** — artefato de runtime, correto onde está |
| `config.example.toml` + `.yaml` | 6,5 / 6,0 KB | Atuais, mas dois formatos sem explicação |
| `Dockerfile` | 0,7 KB | Atual, **sem doc**; copia `config.example.yaml` enquanto o painel usa TOML |

**Defeitos concretos encontrados**：`## Tests` aparece **duas vezes** (`README.md:37` e `:900`); a tabela de caption presets diz "21 presets" (`:243`) mas lista ~37, com um segundo bloco sobreposto (`:289-296`) e um `<details>Full list` com 21 (`:298-305`).

**Lacunas**: índice de `docs/`; doc de arquitetura viva; runbook; guia de configuração; guia do painel web; índice de ADRs; onboarding/Docker; sem `CONTRIBUTING`/`LICENSE`/`CHANGELOG`; **sem CI** (não há `.github/`).

**Estrutura proposta**：
```
docs/
  README.md          # índice: o que existe, para quem, o que ler primeiro
  arquitetura.md     # pipeline + mapa de módulos + degradação graciosa
  configuracao.md    # todas as chaves, TOML vs YAML, armadilha da [table]
  painel-web.md      # 4 páginas, rotas, CSP/porta, _guard_origin, ajustes.toml
  runbook.md         # subir, reiniciar, diagnosticar, exit codes 0-5/130
  convencoes.md      # idioma, "POR QUE" no comentário, medição citada
  adr/               # NNNN-titulo.md, uma decisão por arquivo
  provedores/        # gratuitos.md, nvidia.md
  curador.md         # (fica)
  historico/         # datado e imutável: auditoria-2026-10-07.md, spec-2026-09-27
```
**Regra**: `docs/` na raiz = **vivo e atemporal**; `docs/historico/` = **datado e imutável**. Um número com data pertence ao histórico; um contrato que precisa continuar verdadeiro pertence ao vivo.

---

## 8. Riscos, trade-offs e correções de premissa

**Riscos de maior ordem**：
1. **Trabalho não commitado (37 entradas)** — enquanto não commitado, um `stash`/reset perde trabalho, a deleção de `public-site/` é irreversível no working tree, e o HEAD não reflete o produto.
2. **Os dois monolitos se travam mutuamente** — `server.py` (3.415) e `test_web_server.py` (7.361, que lê `server.py` por `getsource`). Refatorar o servidor sem a suíte como rede é ponto de não-retorno.
3. **A suíte não prova o HTTP** — o roteamento e o guard só são provados por leitura de source; um POST caindo no guard `if path != "/api/run"` vira 404 silencioso sem nenhum teste acusar.

**Correções de premissa（verificadas nesta sessão）**：

| Alegação original | Verificação | Veredito |
|---|---|---|
| `web/*` não manda `Cache-Control` | `server.py:2493-2545` manda `max-age=300` + `ETag` + `Range` | ❌ desatualizada → **corrigida** |
| `templates/meme-pov.toml` é órfão | lido por `tests/test_template.py:1033`, citado em `README.md:439` | ❌ falso → **corrigido** |
| Baseline de 9 falhas pré-existentes | medido na suíte completa: **9** — 7 `HeroPreviewTests` + 2 `test_reframe.py`; a **composição** mudou (os 2 do frontend passam agora) | ⚠️ número certo, composição errada → **corrigida** |
| `score._normalize` é código morto | **usado** em `score.py:384` | ❌ falso → **descartado do relatório** |
| Auditoria A5 (`_record` duplicado) | `pipeline.py:17` importa de `render_task` — já resolvido | ❌ desatualizada → auditoria é parcialmente obsoleta |
| `do_PUT` existe além de GET/POST | `server.py:2793`, com guard em `:2809` | ✅ confirmado — e **não** é coberto pelo assert do guard (`test_web_server.py:4402` itera só `do_GET`/`do_POST`) |

---

## ✅ Lista de ações（por prioridade）

| # | Ação | Responsável | Urgência | Conclusão esperada |
|---|------|-------------|----------|---------------------|
| 1 | Commitar as 37 entradas em frentes separadas: (a) remoção de `public-site/`, (b) `web/publicar.*`, (c) migração para `/api/`, (d) `docs/auditoria-2026-10-07.md` | Dono do produto | **P0** | Working tree limpo; HEAD reflete o produto; trabalho irrecuperável deixa de existir |
| 2 | Escrever `tests/test_web_integration.py`: subir `Handler` em `127.0.0.1:0` e provar `GET /api/status` = 200 e `POST /api/run` sem url = 400 — **é o gate da extração** | Dev | **P0** | Primeiro teste socket→handler; o roteamento deixa de depender de leitura de source **e** a extração passa a ter rede comportamental |
| 3 | Estender `test_both_entrypoints_call_the_guard` (`test_web_server.py:4402`) para incluir `do_PUT` | Dev | **P0** | Método novo com guard esquecido passa a falhar |
| 4 | `tests/test_audio.py` — `frame_db`/`adaptive_threshold`/`detect_silences` com números absolutos | Dev | P1 | A entrada dos sinais do score deixa de ser ponto cego |
| 5 | Cobrir as 3 rotas descobertas (`archive/thumb`, `transcript/normalize`, `providers/remove`) | Dev | P1 | Nenhuma rota sem teste |
| 6 | `GET /health` (200 + versão + pid + porta + disco livre), checagem de disco no topo de `/api/run` e **persistência do histórico de jobs** | Dev | P1 | O painel deixa de perder a galeria num restart, e disco cheio deixa de virar `Errno 28` no meio do render |
| 7 | Remover/reescrever os 7 `HeroPreviewTests` contra o markup atual (`.hero.studio-welcome`) | Dev | P1 | Suíte verde; `xfail` permanente evitado |
| 8 | Destilar `docs/auditoria-2026-10-07.md` em `docs/arquitetura.md` + `painel-web.md` + `configuracao.md`; mover o resto para `docs/historico/` | Docu | P2 | Conhecimento permanente sai de um documento pontual |
| 9 | Corrigir `README.md`: `## Tests` duplicado (`:37`/`:900`) e a tabela de presets (`:243` diz 21, lista ~37) | Docu | P2 | Doc de entrada volta a ser confiável |
| 10 | Extração do `server.py` para as camadas de §4.1 (`web/router.py` com tabela `ROUTES`, `web/state.py`, `web/handlers/`, `web/httpio.py`, `web/paths.py`) + guard em `handle_one_request()` | Dev | **P1**（era P2) | Rota nova = 1 linha em `ROUTES`; guard inescapável por construção |
| 11 | Reduzir o custo **por teste**: reusar um `tmp_path` por **classe** em vez de `mkdtemp` por teste; revisar `AjustesAtomicWriteTests` e `PublicacaoPayloadTests` (11–22 s cada); mockar `time.sleep` de retry | Dev | P1 | Suíte default deixa de custar 29 min — ROI maior que marcar mídia como `slow` |
| 12 | Decidir os 2 `test_reframe.py` vermelhos (`:330`): `xfail` com motivo **ou** conserto | Dev | P1 | Suíte default sem vermelho alheio ao trabalho em curso |
| 13 | **Backup** de `ajustes.toml`, `provedores-usuario.toml`, `prompts/curador.txt` e `output/` | Dono do produto | P1 | Decisão (c): zero redundância — a queda do disco deixa de levar o estado do produto junto |
| 14 | Criar CI (`.github/workflows`): camada rápida no push, pesados em job noturno | Dev | P1 | A suíte de 29 min passa a rodar em algum lugar além da máquina do dono |

---

## ⚠️ Pendências / limitações conhecidas

- **DECIDIDAS pelo dono em 2026-10-08**：(a) **`public-site/` sai de vez** — a remoção é definitiva, não volta como repositório separado (`design-system/.../pages/public-site.md` sai junto; `Dockerfile` e `README.md` não devem referenciar o site). (b) **O painel é o produto principal** — `server.py` merece arquitetura de aplicação (§4.1). (c) **Uso em uma máquina** — `_state` global + lock de `output_dir` bastam; auth/multiusuário fora de escopo **por decisão**, não por indefinição.
- **Nada pendente de decisão de produto.** O que resta são escolhas de execução, não de rumo.
- **Não verificado**：a contagem exata de rotas de `do_GET` é aproximada por leitura, não instrumentada.
- **Executado**：a suíte completa rodou — **1.769,25 s (29 m 29 s)**, `9 failed / 1426 passed / 918 subtests`. Amostragem independente do lead confirmou as durações (`test_missing_file_raises` = **18,8 s**) e as **2 falhas do `test_reframe.py`** (`TypeError` em `:330`).
- **Estado do repositório no momento do relatório**：37 entradas não commitadas; branch `master`; remote privado `origin`. **Qualquer ação de commit deve ocorrer antes das refatorações.**

---

## 📚 Fontes de dados e índice de produtos dos membros

- **Archi（arquiteto）** — mapa de módulos, direção de dependências, 8 ADRs, arquitetura-alvo, perguntas abertas. Evidência por `arquivo:linha`. **Revisão (r2)**: arquitetura-alvo em camadas para o `web/` (§4.1), tabela `ROUTES` em vez de decorador, guard centralizado em `handle_one_request()`, contrato HTTP como contrato de produto, ADR-02 marcado *superseded*, ordem com o teste de integração como *gate*.
- **Rex（SRE）** — postura operacional, 8 modos de falha, runbook mínimo, checklist Go/No-Go, lacunas de confiabilidade. Detectou a correção do `Cache-Control`. **Revisão (r2)**: reclassificação das lacunas sob a decisão (b) — `/health`, persistência de jobs, watchdog, disco e log sobem a requisito de produto, e **backup** e **CI** entram como novos obrigatórios; enquadramento do risco de autenticação sob a decisão (c).
- **Tessa（testes）** — pirâmide real (1.435 testes), mapa rota-a-rota, módulos descobertos, plano de quebra do monolito, top 5 testes, baseline de falhas medido e **perfil de duração da suíte completa** (`--durations=25`), que derrubou a hipótese de que o custo vinha de mídia.
- **Docu（docs）** — inventário, defeitos do `README.md`, estrutura proposta de `docs/`, convenções e ordem de escrita.
- **Verificação do lead** — execução de `pytest tests/test_web_server.py` (7 failed / 458 passed / 88,7 s), confirmação de `do_PUT`, `meme-pov.toml`, `_normalize`, `rank_windows`, `viral_report.py:331`, `pipeline.py:17`.
- **Evidência bruta do workspace** — `git status --porcelain` (37 entradas), inventário de linhas por arquivo, `server.py:2399/2431/2551/2793/2823`, `test_web_server.py` (64 classes).

---

> Este relatório foi gerado por colaboração de IA (time de garantia de engenharia). Decisões-chave — em especial as de §「Pendências」 e a ordem de commit de P0 — devem ser revisadas por um responsável humano antes de virarem ação.
