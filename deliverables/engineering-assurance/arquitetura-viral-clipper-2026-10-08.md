# Avaliação arquitetural — viral-clipper

**Data**：2026-10-08
**Workflow**：2 — System Design (arquitetura / design)
**Participantes**：Archi（arquiteto）· Rex（SRE）· Tessa（testes）· Docu（documentação）· Zhen（lead, orquestração e verificação)

---

## 📌 TL;DR（sumário executivo）

- **O núcleo está são; a casca está inchada.** O pipeline Python tem camadas limpas, dependências acíclicas e uma costura central correta (`score_windows()` / `pick_windows()` com o ranker exatamente entre as duas, `pipeline.py:258-262`). O risco não está na lógica de negócio — está em `web/server.py` (3.415 linhas), `tests/test_web_server.py` (7.361 linhas) e no **estado do git**.
- **Distribuição de severidade**：🔴 Severo **5** · 🟠 Alto **9** · 🟡 Médio **10** · 🟢 Baixo **3**（27 achados, 2 deles reprovados na verificação e descartados).
- **Bloqueante**：sim, dois — (1) **37 entradas não commitadas**, incluindo a deleção irreversível de `public-site/` (10 arquivos) e `docs/auditoria-2026-10-07.md` ainda *untracked*; (2) **zero testes de integração HTTP** — o roteamento e o `_guard_origin()` só são provados lendo o código-fonte.
- **Não bloqueante, mas caro**：sem `/health`, sem watchdog de job, sem checagem de disco, estado de execução só em RAM.
- **Duas premissas da documentação interna estavam erradas** e foram corrigidas nesta sessão（ver §8）。

---

## 🎯 Cartão de conclusão principal

| Item                | Conteúdo                                                                                                                      |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **Avaliação geral** | 🟡 **Aprovada com ressalvas** — arquitetura do núcleo 🟢; casca web 🟠; higiene de repositório 🔴                             |
| **Bloqueadores**    | **2**（trabalho não commitado; ausência de teste de integração HTTP)                                                           |
| **Ações-chave**     | **10**（3 × P0, 4 × P1, 3 × P2)                                                                                                |
| **Próximo passo**   | Commitar o estado atual em frentes separadas **antes** de qualquer refatoração — sem isso, todo o resto fica em cima de areia |
| **O que NÃO fazer** | Reescrever `server.py` do zero, adicionar bundler ao painel, trocar o `.venv` para 3.14                                       |

---

## 1. Requisitos e objetivos（o que o sistema precisa ser）

Derivados do código e dos docs, não inventados:

| Requisito                                                      | Evidência                                                   | Como a arquitetura atende                                                        |
| -------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Rodar 100 % local, numa máquina (8 threads / 7,8 GB / sem GPU) | `config.py:229` (`workers=2`), `pipeline.py:503`            | Painel em `127.0.0.1:7755`, stdlib only, sem nuvem                               |
| Módulos opcionais nunca fatais                                 | `reframe.py:190-192`, `pipeline.py:84-132`, `ranker.py:637` | Detecção por **artefato** (caminho do cascade), não por `import`; degrada + loga |
| Um run por `output_dir`                                        | `lock.py:40`, README:601-651                                | Lock de SO por byte-range no offset 4096, exit 5                                 |
| Ajustes persistidos e reutilizáveis                            | `ajustes.toml` na raiz, `server.py:146-167`                 | `AJUSTES_KEYS` = subconjunto de `ClipConfig` (87 campos)                         |
| Chave de API nunca volta ao navegador                          | `_providers_payload()`                                      | Só `has_key`; run lê de `os.environ`                                             |
| Render 9:16 1080×1920 como constante do produto                | rótulo fixo na UI                                           | Sem "chip" — virou rótulo                                                        |

**Objetivo do trabalho**: decidir se a arquitetura atual sustenta a evolução, e em que ordem mexer.

---

## 2. Arquitetura atual（design de alto nível）

**Núcleo — limpo.** `cli.py` → `pipeline.py` → estágios (`pipeline.py:11-18`). Direção acíclica: `score.py` depende só de `hooks`/`audio`/`config`/`transcribe` (`score.py:22-25`); `ranker.py` de `config`/`score`/`util`/`providers` (`ranker.py:44-51`); `providers.py` só de `config`/`util` (`providers.py:35-36`). Não há ciclo `ranker ↔ providers`.

**Três ciclos são quebrados por import tardio, e de propósito**: `config.validate` importa `caption_presets`/`providers`/`template` dentro do método (`config.py:300,321,335`); `ranker.build_provider` importa `providers` na função (`ranker.py:585`); `providers` importa `user_providers` na função (`providers.py:205`).

**A costura central está certa.** `score.score_windows()` (`score.py:332`) é separado de `pick_windows()` (`score.py:397`), e `pipeline.select_windows` chama `ranker.apply` **exatamente entre os dois** (`pipeline.py:258-262`). É isso que deixa o curador de IA entrar sem reescrever a seleção.

**A casca — problemática.** `web/server.py` é um segundo produto dentro do repositório:

| Fato                                                                                                   | Evidência             |
| ------------------------------------------------------------------------------------------------------ | --------------------- |
| 3.415 linhas, classe `Handler` a partir de `:2399`                                                     | `server.py:2399`      |
| `do_GET` com ~20 rotas num único método (`:2551-2776`)                                                 | `server.py:2551`      |
| `do_PUT` (`:2793`) e `do_POST` (`:2823`) no mesmo handler                                              | `server.py:2793,2823` |
| ~40 funções de módulo **antes** da classe (`_run_job` `:765`, `_options_to_config` `:407`)             | `server.py:407,765`   |
| Estado global `_state` + `_lock` compartilhado com threads de job                                      | `server.py:197-198`   |
| **A ordem das rotas importa**: o guard `if path != "/api/run":` (`:2861`) precisa vir depois dos POSTs | `server.py:2861`      |
| O painel reimplementa orquestração que a CLI já tem (`_run_job` chama `pipeline.*` direto)             | `server.py:765`       |
| `quality.inspect_clip` é usado **só** pelo servidor, nunca pela CLI                                    | `server.py:884`       |

**Acoplamentos a vigiar**: `ClipConfig` (87 campos) é o contrato universal e `AJUSTES_KEYS` (27) o replica à mão (`server.py:146-167`); metadados do yt-dlp circulam como `dict` sem tipo (`pipeline.py:290`); `window.components` é `dict[str,float]` mas às vezes recebe string (`ranker.py:736` insere `llm_motivo`).

---

## 3. Registros de decisão（ADR das decisões vigentes）

| #          | Decisão                                                             | Contexto / Consequência                                                                                                                                  | Status                           |
| ---------- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| **ADR-01** | Painel sem bundler, stdlib only, tokens CSS duplicados de propósito | `server.py:1-5`; `index.css × scrap.css` = 18 seletores repetidos, `shared.css` = 9, `scrap ∩ shared` = 0                                                | Aceita                           |
| **ADR-02** | `server.py` monolito, guard único no topo de cada verbo             | `server.py:2431,2552,2809,2831`; rota nova exige editar o método gigante e **reiniciar**; a ordem importa                                                | Aceita **com dívida**            |
| **ADR-03** | Módulos opcionais degradam; opcional detectado pelo **artefato**    | `reframe.py:190-192` checa `opencv_cascade_path()`; `_transcribe_hybrid` devolve exit 4 (`pipeline.py:84-132`); `_judge` nunca levanta (`ranker.py:637`) | Aceita                           |
| **ADR-04** | `.venv` preso ao Python 3.13                                        | Wheels de `ctranslate2`/`av`/`opencv`; **não recriar em 3.14**                                                                                           | Aceita                           |
| **ADR-05** | Testes extraem a função REAL + DOM falso; handler sem socket        | `inspect.getsource` de `do_GET`/`do_POST`, `attr.__get__(self, cls)`; **consequência: nunca editar `.py` com a suíte rodando**                           | Aceita                           |
| **ADR-06** | `.py` exige reiniciar o processo                                    | Rota nova = 404 até reiniciar; `web/*` é lido a cada request                                                                                             | Aceita                           |
| **ADR-07** | Lock de SO por `output_dir` em vez de pidfile                       | Byte-range no offset 4096, nunca apagado, exit 5 (`lock.py:40`)                                                                                          | Aceita                           |
| **ADR-08** | Remoção do `public-site/` (site de divulgação, 10 arquivos)         | Some o build Vercel; se voltar, deve ser **repositório separado**                                                                                        | **Em andamento — não commitada** |

---

## 4. Arquitetura-alvo e refatoração em fases

Ordem: **menor risco → maior risco**.

| Fase  | Mudança                                                                                                                   | Trade-off                                                              | Rede de segurança                                                                  |
| ----- | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **0** | **Commitar o estado atual em frentes separadas** (`public-site` / `publicar.*` / migração para `/api/`)                   | Nenhum — é pré-requisito                                               | `git add` de um lado + `git diff > patch` + `git apply --cached` (sem `stash`)     |
| **1** | Extrair as ~40 funções de `server.py` para um pacote (`web/state.py`, `web/routes_*.py`), mantendo `do_GET` como despacho | Mecânico, mas mexe no arquivo que os testes leem por `getsource`       | Rodar `test_web_server.py` antes e depois; comparar a LISTA `classname::name`      |
| **2** | Transformar `do_GET` numa **tabela de rotas** `(path, handler)`                                                           | Torna a ordem explícita e mata a armadilha do `if path != "/api/run":` | Teste de integração (§6) cobre o despacho de verdade                               |
| **3** | Tipar os metadados (`TypedDict`/dataclass); limpar `window.components` de strings                                         | Toca `ranker.py` e `pipeline.py`                                       | `test_score.py` + `test_ranker.py`                                                 |
| **4** | Remover código morto confirmado                                                                                           | Baixo                                                                  | `score.rank_windows` (`score.py:446`), `viral_report.py:331` (return inalcançável) |


**O que NÃO mudar**（e por quê）：

- A separação `score_windows` / `pick_windows` — é o que permite o curador entrar no meio.
- Sinais absolutos em [0,1] + `_BOUNDED_SIGNALS`/`_BANDED_SIGNALS` — normalizar por percentil é a regressão que o projeto já pagou para evitar.
- Painel sem bundler — o ganho não paga o custo de um build numa UI local.
- Detecção de opcional por artefato — `import` mente quando a wheel está instalada e o modelo não.
- O lock de SO — resolve o caso real (dois runs escrevendo os mesmos arquivos).
- **Reescrever `server.py` do zero é a opção errada**: é feio, mas cada linha documenta um incidente real.

---

## 5. Operabilidade（Rex）

**Postura atual.** Um processo stdlib, `127.0.0.1:7755` (`server.py:192-193`), `ThreadingHTTPServer` (`:2322`). O guard de instância é **por porta** (`SO_EXCLUSIVEADDRUSE`, `server.py:2332-2339`) — impede dois na 7755, **não** impede 7755 + 7761 + 7801 simultâneos, que já foram encontrados em campo.

**Persiste em disco**: `ajustes.toml`, `provedores-usuario.toml`, `prompts/curador.txt`, `.ajustes-transcript-source`, `output/clips.json`+`clips.md`, `.viralclipper.lock`, caches de transcrição.
**Perde no restart**: **todo** o estado de execução — fila, galeria e progresso vivem em `_state` em RAM (`server.py:197-198`); reiniciar zera (`server.py:1855-1870`).
**Observabilidade**: **não existe `/health`**. Saúde só se infere de `GET /` e `/api/status` (`:2679-2687`). O log é `Logger` em stdout com 4 prefixos (`util.py:36-69`) — sem timestamp, sem nível, sem correlação, sem arquivo.

**Modos de falha（probabilidade × impacto）**：

| # | Falha                                               | Sintoma                                                                      | Mitigação                                                                                                               |
| - | --------------------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 1 | **Disco cheio** (alta × alto)                       | `OSError Errno 28` no render/download; 19 falhas fantasma no pytest          | **Zero checagem de disco no repo**; limpeza só roda no `finally` (`server.py:928-934`) — kill duro deixa `output/_work` |
| 2 | **Worker preso/zumbi** (média × alto)               | `/api/run/progress` com `active:true` e `elapsed` crescendo sem linhas novas | `_run_job` é **síncrono na thread do handler** (`server.py:2869`), sem timeout; só resta `taskkill /F /T`               |
| 3 | **Múltiplos painéis** (média × médio)               | UI "antiga"                                                                  | Provar de qual diretório o processo serve                                                                               |
| 4 | `.py` sem restart (alta × baixo)                    | Rota nova = 404                                                              | Reiniciar antes de validar                                                                                              |
| 5 | Opcional ausente `reframe`/`ranker` (média × baixo) | Degrada + loga                                                               | Por design                                                                                                              |
| 6 | Chave de API ausente/expirada (média × baixo)       | Veredito `no-key` / 401 / 403-1010                                           | `_export_saved_key` lê `os.environ` (`server.py:837`)                                                                   |
| 7 | Sessão do Instagram expirada (média × médio)        | Listagem vazia                                                               | Prova offline: `ig_session_check.py`                                                                                    |
| 8 | **OOM no render** (média × alto)                    | `BrokenProcessPool`                                                          | Retry **re-renderiza TODAS as tarefas, inclusive as prontas** (`pipeline.py:517-524`) = encodes duplicados              |

**Lacunas priorizadas**：barato — `/health` real (~30 linhas), checagem de disco no topo de `/api/run`, `pid`/`porta`/`start_time` no `/api/status`, log rotativo em arquivo, watchdog de job. Caro — guard de instância **entre** portas, histórico de jobs persistido, retry do pool sem re-renderizar o pronto, progresso incremental em transcrição/render.

> **Lacuna estrutural**: nenhuma telemetria sobrevive ao processo. Todo incidente pós-kill é arqueologia.

---

## 6. Estratégia de testes（Tessa）

**Pirâmide real**：**1.435 testes** em 26 arquivos. `test_web_server.py` sozinho tem **465 testes (32 %)**. Três níveis, na prática:

- **Unitário puro** (`viralclipper/*`) — fakes injetados, nunca mock de rede. Rápido.
- **Contrato de frontend** (lê `.html/.css/.js` reais e asserta string/regex) — a maior parte de `test_web_server.py`: 116 `read_text(`, 104 `server.WEB_DIR`, 15 `page_source(` (`:196`), 6 `inspect.getsource`.
- **Integração HTTP — AUSENTE.** Zero ocorrências de `serve_forever|HTTPServer|make_server`. O HTTP só é provado por handler-fake (`_FakeHandler` + `_handle_*` via `attr.__get__(self, cls)`, `test_ajustes.py:946`).
- **E2E — fora da suíte**: `smoke_test.py`, `cache_e2e_test.py`, `band_parity_check.py`, `ig_session_check.py`, `reframe_check.py`, `_scratch/*.mjs`.

**Custo**：`test_web_server.py` = **88,7 s** (medido: 7 failed, 458 passed, 412 subtests). A suíte inteira = ~24-28 min, e o tempo **não** está no frontend (que é leitura de texto) — está em mídia/ffmpeg: `test_reframe.py`, `test_render.py`, `test_download.py`, `test_ig_profile.py`, `test_template.py`.

**Rotas sem teste**：

| Rota                             | Evidência                                                            |
| -------------------------------- | -------------------------------------------------------------------- |
| `GET /api/scrap/archive/thumb`   | `server.py:2633` — `_archive_thumb_path` não aparece em nenhum teste |
| `POST /api/transcript/normalize` | `server.py:3343` — `_handle_normalize` não é referenciado            |
| `POST /api/providers/remove`     | `server.py:3267` — `_handle_remove_provider` não é referenciado      |

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

**Falhas pré-existentes（medido hoje, nesta árvore）**：`test_web_server.py` = **7 falhas, TODAS `HeroPreviewTests`**. Recomendação: **remover/reescrever, não `xfail`** — testam `preview-card`/`preview-video`/`data-live`, markup que o produto abandonou (o hero virou `.hero.studio-welcome`); `xfail` permanente é dívida que esconde regressão futura.

---

## 7. Estrutura de documentação（Docu）

**Inventário**：

| Arquivo                                    | Tam.           | Status                                                                                         |
| ------------------------------------------ | -------------- | ---------------------------------------------------------------------------------------------- |
| `README.md`                                | 941 l.         | Atual, **inchado e misto**                                                                     |
| `HISTORICO.md`                             | 52 l.          | **Desatualizado** (narra a perda do `.git` como se não houvesse remote)                        |
| `docs/auditoria-2026-10-07.md`             | 580 l. / 50 KB | **Efêmero + permanente misturado; não commitado**                                              |
| `docs/curador.md`                          | 144 l.         | Atual — **o melhor doc do repo**                                                               |
| `docs/modelos-gratuitos.md` / `-nvidia.md` | 128 / 131 l.   | Atuais (retratos datados)                                                                      |
| `docs/superpowers/specs/2026-09-27-*.md`   | 26 KB          | **Parcialmente obsoleto** (descreve `web/templates.html`/`templates.js`, que não existem mais) |
| `design-system/viral-clipper/MASTER.md`    | 254 l.         | Atual                                                                                          |
| `design-system/.../pages/public-site.md`   | ~2 KB          | **Órfão** (o site foi removido)                                                                |
| `prompts/curador.txt`                      | 12 KB          | **Não é doc** — artefato de runtime, correto onde está                                         |
| `config.example.toml` + `.yaml`            | 6,5 / 6,0 KB   | Atuais, mas dois formatos sem explicação                                                       |
| `Dockerfile`                               | 0,7 KB         | Atual, **sem doc**; copia `config.example.yaml` enquanto o painel usa TOML                     |


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

| Alegação original                   | Verificação                                                       | Veredito                                                                                                        |
| ----------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `web/*` não manda `Cache-Control`   | `server.py:2493-2545` manda `max-age=300` + `ETag` + `Range`      | ❌ desatualizada → **corrigida**                                                                                 |
| `templates/meme-pov.toml` é órfão   | lido por `tests/test_template.py:1033`, citado em `README.md:439` | ❌ falso → **corrigido**                                                                                         |
| Baseline de 9 falhas pré-existentes | medido: 7 falhas, todas `HeroPreviewTests` (88,7 s)               | ❌ desatualizada → **corrigida**                                                                                 |
| `score._normalize` é código morto   | **usado** em `score.py:384`                                       | ❌ falso → **descartado do relatório**                                                                           |
| Auditoria A5 (`_record` duplicado)  | `pipeline.py:17` importa de `render_task` — já resolvido          | ❌ desatualizada → auditoria é parcialmente obsoleta                                                             |
| `do_PUT` existe além de GET/POST    | `server.py:2793`, com guard em `:2809`                            | ✅ confirmado — e **não** é coberto pelo assert do guard (`test_web_server.py:4402` itera só `do_GET`/`do_POST`) |

---

## ✅ Lista de ações（por prioridade）

| #  | Ação                                                                                                                                                             | Responsável     | Urgência | Conclusão esperada                                                                  |
| -- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- | -------- | ----------------------------------------------------------------------------------- |
| 1  | Commitar as 37 entradas em frentes separadas: (a) remoção de `public-site/`, (b) `web/publicar.*`, (c) migração para `/api/`, (d) `docs/auditoria-2026-10-07.md` | Dono do produto | **P0**   | Working tree limpo; HEAD reflete o produto; trabalho irrecuperável deixa de existir |
| 2  | Escrever `tests/test_web_integration.py`: subir `Handler` em `127.0.0.1:0` e provar `GET /api/status` = 200 e `POST /api/run` sem url = 400                      | Dev             | **P0**   | Primeiro teste socket→handler; o roteamento deixa de depender de leitura de source  |
| 3  | Estender `test_both_entrypoints_call_the_guard` (`test_web_server.py:4402`) para incluir `do_PUT`                                                                | Dev             | **P0**   | Método novo com guard esquecido passa a falhar                                      |
| 4  | `tests/test_audio.py` — `frame_db`/`adaptive_threshold`/`detect_silences` com números absolutos                                                                  | Dev             | P1       | A entrada dos sinais do score deixa de ser ponto cego                               |
| 5  | Cobrir as 3 rotas descobertas (`archive/thumb`, `transcript/normalize`, `providers/remove`)                                                                      | Dev             | P1       | Nenhuma rota sem teste                                                              |
| 6  | `GET /health` (200 + versão + pid + porta + disco livre) e checagem de disco no topo de `/api/run`                                                               | Dev             | P1       | Disco cheio deixa de virar `Errno 28` no meio do render                             |
| 7  | Remover/reescrever os 7 `HeroPreviewTests` contra o markup atual (`.hero.studio-welcome`)                                                                        | Dev             | P1       | Suíte verde; `xfail` permanente evitado                                             |
| 8  | Destilar `docs/auditoria-2026-10-07.md` em `docs/arquitetura.md` + `painel-web.md` + `configuracao.md`; mover o resto para `docs/historico/`                     | Docu            | P2       | Conhecimento permanente sai de um documento pontual                                 |
| 9  | Corrigir `README.md`: `## Tests` duplicado (`:37`/`:900`) e a tabela de presets (`:243` diz 21, lista ~37)                                                       | Docu            | P2       | Doc de entrada volta a ser confiável                                                |
| 10 | Extrair as ~40 funções de `server.py` para `web/routes_*.py` + `web/state.py`, com `do_GET` como despacho                                                        | Dev             | P2       | Monolito quebrado **sem** mudar comportamento (suíte antes/depois)                  |

---

## ⚠️ Pendências / limitações conhecidas

- **Decisões que dependem do dono do produto**：(a) `public-site/` sai de vez ou vira repositório separado? (b) o painel é o produto principal ou só um atalho local — decide se `server.py` merece arquitetura de aplicação ou só extração? (c) uso em uma máquina ou multiusuário — decide se `_state` global + lock de `output_dir` bastam?
- **Não verificado**：a contagem exata de rotas de `do_GET` é aproximada por leitura, não instrumentada. O tempo de `test_reframe.py` isolado (>300 s) não foi confirmado com `--durations`.
- **Não executado**：a suíte completa (~24-28 min) não foi rodada nesta sessão — apenas `test_web_server.py`. Os números dos demais arquivos vêm de medição dos especialistas.
- **Estado do repositório no momento do relatório**：37 entradas não commitadas; branch `master`; remote privado `origin`. **Qualquer ação de commit deve ocorrer antes das refatorações.**

---

## 📚 Fontes de dados e índice de produtos dos membros

- **Archi（arquiteto）** — mapa de módulos, direção de dependências, 8 ADRs, arquitetura-alvo em 5 fases, perguntas abertas. Evidência por `arquivo:linha`.
- **Rex（SRE）** — postura operacional, 8 modos de falha, runbook mínimo, checklist Go/No-Go, lacunas de confiabilidade. Detectou a correção do `Cache-Control`.
- **Tessa（testes）** — pirâmide real (1.435 testes), mapa rota-a-rota, módulos descobertos, plano de quebra do monolito, top 5 testes, baseline de falhas medido.
- **Docu（docs）** — inventário, defeitos do `README.md`, estrutura proposta de `docs/`, convenções e ordem de escrita.
- **Verificação do lead** — execução de `pytest tests/test_web_server.py` (7 failed / 458 passed / 88,7 s), confirmação de `do_PUT`, `meme-pov.toml`, `_normalize`, `rank_windows`, `viral_report.py:331`, `pipeline.py:17`.
- **Evidência bruta do workspace** — `git status --porcelain` (37 entradas), inventário de linhas por arquivo, `server.py:2399/2431/2551/2793/2823`, `test_web_server.py` (64 classes).

---

> Este relatório foi gerado por colaboração de IA (time de garantia de engenharia). Decisões-chave — em especial as de §「Pendências」 e a ordem de commit de P0 — devem ser revisadas por um responsável humano antes de virarem ação.
