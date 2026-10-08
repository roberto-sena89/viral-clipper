# Auditoria técnica, de produto e de mercado — Viral Clipper

**Data:** 2026-10-07
**Auditado:** `C:\Users\USUARIO\viral-clipper` (branch `master`, commit `6b8f078`)
**Escopo autorizado:** auditar, corrigir P0 e iniciar a aba de publicação
**Natureza:** ferramenta pessoal, execução local (Windows), open source, sem cobrança

---

## 1. Resumo executivo

O Viral Clipper não é um protótipo. São **112 commits, 44.322 linhas e 1.386 testes automatizados**, com documentação interna melhor que a de muito produto comercial. O problema não é falta de engenharia — é que a engenharia foi aplicada onde já estava boa e não onde dói.

Quatro conclusões dominam esta auditoria:

**1. A sua dor nº 1 ("a espera") é estrutural e está medida.** As 24 chamadas ao curador de IA acontecem **uma atrás da outra**, num laço `for` sem concorrência (`viralclipper/ranker.py:642-655`). Medi o custo disso: com 0,5 s por chamada, 24 janelas levam **12,44 s** — dos quais 12,00 s são pura espera de rede e só 0,44 s são trabalho. O tempo é 100% latência, e portanto escala linearmente. Com as latências reais dos modelos que o próprio repositório mediu (`docs/modelos-nvidia.md`), o bloco custa:

| modelo | 1 chamada | 24 em série |
|---|---|---|
| `meta/llama-3.2-11b-vision-instruct` | 2,0 s | **48 s** |
| `google/gemma-4-31b-it` | 4,5 s | **108 s** |
| `nvidia/nemotron-3-super-120b-a12b` | 4,9 s | **118 s** |
| `moonshotai/kimi-k3` | 22,9 s | **550 s** |
| `openai/gpt-oss-20b` | 60,3 s | **1.447 s (24 min)** |
| `z-ai/glm-5.3` | 73,0 s | **1.752 s (29 min)** |

Com 5 a 20 vídeos por dia, isso é **10 a 39 minutos por dia parados esperando** só nesta etapa — e ela é o coração do produto. As chamadas são independentes entre si e cada uma tem chave de cache própria: paralelizar é seguro e é o maior ganho disponível no projeto.

**2. Existe um risco de perda total, e ele já se materializou uma vez.** O `origin` aponta para `github.com/roberto-sena89/viral-clipper`, mas o `origin/master` está parado em `6204825` (2026-09-30): são **49 commits locais sem backup remoto**. Em 2026-09-22 os `.pack` do `.git` sumiram e 24 commits morreram. A nota "o repo NÃO TEM REMOTE" na memória do projeto está desatualizada — o remote existe, só não está sendo usado.

**3. A funcionalidade que você listou como "planejada" já está construída no motor.** O curador já devolve `headline` e `hashtags` (`MAX_HASHTAGS = 10`, `HEADLINE_TARGET_CHARS = 70`), já grava em `clips.md` e `viral_report.md`, e há teste travando o contrato (`ShippedPromptTests`). Ver `docs/curador.md`. **O que falta é a tela**, não a capacidade. Isso muda o esforço da aba de publicação de "grande" para "médio".

**4. A espera é longa e opaca.** A escada de progresso do painel tem 4 degraus (`web/server.py:1546-1551`), mas três blocos longos não emitem nada dentro deles: a transcrição (o próprio repo mede **~16 min** num vídeo), o laço do curador (2 a 29 min) e o render (`-loglevel error`, sem `-progress`). Durante o bloco mais caro do produto, a tela diz "Escolhendo os melhores trechos" e nada mais.

**A boa notícia sobre custo:** com execução local e provedores gratuitos, o custo por vídeo hoje é **R$ 0,00 em dinheiro**. O custo dominante é o seu tempo. "Reduzir custo por vídeo" neste projeto significa, na prática, **reduzir tempo por vídeo**.

---

## 2. Escopo analisado e limitações

### O que eu acessei e verifiquei

| Material | Como foi acessado |
|---|---|
| Código-fonte completo (24 módulos Python, 6 arquivos JS, 3 páginas HTML, 5 folhas CSS) | leitura direta |
| Suíte de testes (1.386 testes coletados) | executada |
| Histórico git (112 commits) e estado do remote | comandos git |
| Painel web em `127.0.0.1:7755` | requisições HTTP + navegador (Playwright) |
| Documentação (`README.md` 46 KB, `docs/`, `design-system/`, `MEMORY.md` 471 linhas) | leitura direta |
| Site público (`public-site/dist/`) | leitura direta |
| Páginas oficiais de preço de 5 concorrentes | navegador (são JS-rendered) |

### O que eu NÃO acessei — e não vou fingir que acessei

- **Nenhum vídeo real foi processado.** `output/` está vazio (0 arquivos) neste checkout. Não baixei nenhum vídeo do YouTube/Instagram: seria ação com efeito externo que você não autorizou.
- **Nenhuma chave de API foi usada.** Não pedi e não preciso. O custo da serialização do curador foi medido com um provedor falso que dorme um tempo fixo — o que isola exatamente o custo da *serialização*, não da rede real.
- **Não medi o pipeline ponta a ponta.** As latências por estágio vêm de (a) medições que o próprio repositório já fez e registrou em `docs/modelos-nvidia.md` e na memória do projeto, e (b) medição minha do overhead de serialização.
- **Não medi Core Web Vitals.** O painel é local, servido em loopback, com 195 thumbnails em cache e vídeos de demonstração de até 19 MB — os números não representariam nada para um usuário real. Digo isso em vez de inventar um LCP.
- **A suíte completa estava rodando durante a escrita** (leva 24-28 min, conforme a memória do projeto). O número que reporto abaixo é do escopo web, medido nesta sessão.

### Decisões suas que delimitaram o escopo

Você respondeu: **ferramenta pessoal (só você)**, **roda só no seu PC**, **grátis/open source**, **prioridade = aba de publicação + robustez + custo por vídeo**, **volume 5-20 vídeos/dia**, **dor = a espera**.

Consequência direta: **esta auditoria não recomenda autenticação, multi-tenancy, cobrança, quotas ou hospedagem em nuvem.** Não porque sejam ruins, mas porque para uma ferramenta local de uma pessoa eles são custo puro. Estão listados no item "o que evitar construir agora".

---

## 3. Pesquisa de mercado com fontes

Consulta em **2026-10-07**. Preços lidos nas **páginas oficiais** (carregadas em navegador, porque são renderizadas por JS e o fetch simples volta vazio).

### 3.1 Categoria A — reaproveitamento de vídeo longo (o seu grupo)

| Produto | Grátis | Entrada paga | Meio | Limites que importam |
|---|---|---|---|---|
| **OpusClip** | 60 créditos/mês, até 1080p, **marca d'água**, **sem edição**, clipe **deixa de ser exportável após 3 dias** | **$15/mês** (Starter, só mensal) — 150 créditos, sem marca d'água, editor, remoção de silêncio, auto-post em Shorts/TikTok/Reels | **$29/mês** (Pro) ou $14,50/mês no anual ($174) — 300 créditos, créditos acumulam 2 meses, 2 assentos, 6 contas sociais, agendador, export p/ Premiere e DaVinci, dublagem | **1 crédito = 1 minuto de vídeo enviado.** Uma live de 3 h = 180 créditos |
| **Vizard** | 60 créditos/mês, export 720p, **3 dias de armazenamento**, 1 conta social, upload de até 60 min / 1 GB | **$29/mês** (Creator) ou $14,50/mês no anual ($174) — 600+ créditos, export 4K, sem marca d'água, 6 contas, 100 GB | **$39/mês** (Business) ou $19,50/mês no anual ($234) — workspace compartilhado, 20 contas, brand kit, armazenamento ilimitado | Publica limites de API: 1/min·10/h no grátis, 3/min·20/h no Creator, 10/min·60/h no Business |
| **Klap** | (não verificado) | **$14/mês** (Basic, anual) — 100 clipes/mês | **$39/mês** (Pro, anual) — 300 clipes/mês · **$94/mês** (Pro+) — 1.000 clipes/mês | Cobra por **clipe**, não por minuto. Contas sociais ilimitadas em todos os planos |

**Padrões que se repetem na categoria:**
1. **A unidade de cobrança é o minuto de vídeo enviado**, não o clipe gerado (OpusClip e Vizard). Klap é a exceção.
2. **O plano grátis é uma demonstração, não um plano.** Marca d'água (OpusClip, Vizard) + sem edição (OpusClip) + expiração do material (3 dias nos dois).
3. **Publicação direta e agendamento** são recursos de plano pago, não do grátis.
4. **Sugestão de post por IA já é recurso de tabela** — o Vizard lista "AI post suggestions" entre os recursos. A sua ideia da aba de publicação não é diferencial; é paridade.
5. **Teto de upload** (60 min / 1 GB no grátis do Vizard) é como a categoria controla o custo de processamento.

### 3.2 Categoria B — editores gerais (não são seus concorrentes)

| Produto | Grátis | Planos pagos (página oficial) |
|---|---|---|
| **Descript** | (não li a tabela do grátis na fonte) | Hobbyist **$16/pessoa/mês** anual ($24 mensal) — 10 h de mídia/mês, 400 créditos de IA, export 1080p sem marca d'água · Creator **$24** anual ($35 mensal) — 30 h de mídia, 800 créditos, export 4K |
| **VEED** | (página serviu preços em BRL) | Creator **R$20**/usuário/mês anual (R$239/ano) — 6.000 créditos, sem marca d'água · Pro **R$30** (R$361/ano) — 30.000 créditos, tradução p/ 50+ idiomas · Studio **R$49** (R$583/ano) — 180.000 créditos |

**Nota de rigor:** os agregadores de preço de terceiros divergem muito entre si para Descript e VEED (encontrei "$12 a $65" para o Descript). Por isso fui às páginas oficiais. Onde a página oficial não expôs o dado (CapCut bloqueou o carregamento; VEED serviu em BRL), eu **digo que não verifiquei** em vez de completar com o número de um blog.

### 3.3 Diferenciais viáveis para o seu caso

O que a categoria **não** oferece, e que uma ferramenta local pode oferecer de graça:

1. **Custo marginal zero e sem cota.** OpusClip cobra por minuto enviado; você processa 5-20 vídeos/dia sem pagar nada. Isso é um argumento real, não marketing.
2. **O vídeo nunca sai da sua máquina.** Nenhum concorrente da categoria A consegue prometer isso — o modelo de negócio deles exige upload. O seu site público já vende exatamente esse ponto.
3. **Prompt do curador editável.** Você pode reescrever o critério de seleção. Nenhum concorrente de prateleira permite.
4. **Sem marca d'água e sem expiração de 3 dias** — as duas limitações mais irritantes do grátis alheio.
5. **Exportação em 1080×1920 com legendas queimadas e presets** já é paridade com a categoria paga.

### 3.4 Recursos que NÃO devem ser priorizados agora

- **Publicação direta nas redes.** Exige OAuth por plataforma, revisão de app pela Meta/TikTok e manutenção de tokens que expiram. O retorno é economizar um upload manual. Não faça.
- **Colaboração, brand kit, múltiplos assentos, aprovação em fluxo.** Você é uma pessoa.
- **Dublagem e tradução.** Custo de IA alto, e o público é pt-BR.
- **API pública e rate limits.** Só faz sentido se houver terceiros.

---

## 4. Pontos fortes do projeto

Coisas que estão certas e que eu **não** recomendo mexer:

1. **Nenhum `shell=True`, nenhum `os.system`, nenhum `eval`.** Todo comando externo é uma lista de argumentos via `subprocess` (`viralclipper/util.py:154-260`, `render.py:403`). Isso elimina a classe inteira de injeção de comando.
2. **Segredos fora do versionamento.** `cookies`, `cookies.txt`, `provedores-usuario.toml`, `config.toml/yaml`, `.env` estão todos no `.gitignore` e nenhum está rastreado. Verifiquei arquivo por arquivo.
3. **1.386 testes.** Para um projeto de uma pessoa, é uma densidade de teste incomum — e a memória do projeto registra a disciplina de sabotar o próprio teste para provar que ele cai.
4. **Documentação interna excepcional.** `README.md` com 46 KB, `docs/curador.md` explicando o contrato do prompt, `design-system/` com a paleta e a medição de contraste, e 471 linhas de `MEMORY.md` com as decisões e o *porquê* delas. Isso é ativo real.
5. **Degradação graciosa desenhada de propósito.** `reframe` e `ranker` são opcionais e nunca fatais; a transcrição no modo `hybrid` degrada em vez de abortar, com retentativa única só em falha de alocação (`pipeline.py:84-116`).
6. **A perda de transcrição tem exit code próprio (4).** `cli.py:651`. A memória registra o motivo: o run terminava com exit 0 indistinguível de sucesso enquanto entregava clipes sem legenda e com score 39 em vez de 68. Isso é maturidade operacional.
7. **Guarda anti-DNS-rebinding** em GET, POST e PUT, com a porta vinda do socket real (`web/server.py:2320-2347`).
8. **Caminho de transcrição fornecida** (`--transcript-text`/`--transcript-file`) que pula o estágio mais lento do pipeline. É o único atalho de latência que já existe — e é bom.
9. **Cache de transcrição e de veredito do curador** com chave derivada do conteúdo (`transcript_cache.py`, `ranker.py:488`). Rerun do mesmo vídeo é barato.
10. **Uma paleta só para o produto inteiro**, com a duplicação medida em vez de suposta (`SharedStyleSheetTests`).

---

## 5. Diagnóstico de arquitetura e organização

### 5.1 Mapa de módulos

O pacote `viralclipper/` tem 24 módulos, sem ciclos de import rígidos (dois são quebrados por import tardio, em `providers.py:205` e `user_providers.py:29`).

```
cli.py ─────────────────► pipeline.py ──► download / audio / transcribe / score
  │                            │           ranker / render / report / viral_report
  │                            └──► render_task.py ──► render.py ──► template.py
  ├──► config.py (ClipConfig, 87 campos)  ──► caption_presets / providers / template
  ├──► batch.py (SQLite)  ├──► lock.py  ├──► archive.py  └──► ig_profile.py
```

O fluxo é: `download → audio → transcribe → score → ranker(opcional) → render → report`.

### 5.2 Onde a arquitetura está boa

- **`score.py` separa `score_windows()` de `pick_windows()`** (portão + não-sobreposição), e é exatamente isso que permite o curador entrar no meio sem reescrever a seleção. Decisão certa.
- **Sinais de score são absolutos em [0,1]**, com `_BOUNDED_SIGNALS`/`_BANDED_SIGNALS` obrigando uma política para cada sinal novo. Sem normalização por percentil — o que torna o score comparável entre vídeos.
- **`config_from_args` filtra `vars(args)` pelos campos da dataclass** (`cli.py:487-493`), então uma flag nova não consegue quebrar a montagem da configuração.

### 5.3 Dívida técnica concreta

| # | Achado | Evidência |
|---|---|---|
| A1 | **`build_parser` tem 446 linhas numa função só** | `cli.py:39-484` |
| A2 | **`ClipConfig` tem 327 linhas e 87 campos** | `config.py:10-336` |
| A3 | **`render_clip` tem 205 linhas** | `render.py:722` |
| A4 | **O handler HTTP tem 900+ linhas** num único `do_GET`/`do_POST` | `server.py:2288-3222` |
| A5 | **`_record` está duplicado** em dois arquivos | `pipeline.py:542-562` e `render_task.py:196-219` |
| A6 | **`_escape` é uma cópia assumida** do `web/server.py` | `user_providers.py:138` (o próprio comentário admite) |
| A7 | **Retorno inalcançável** | `viral_report.py:331` (depois do `return` da linha 330) |
| A8 | **Código usado só por teste** | `score.rank_windows` (`score.py:446`), `score._normalize` (`:275`) |
| A9 | **`quality.inspect_clip` só é chamado pelo servidor web** | `quality.py:25` → `web/server.py:847`; nunca pela CLI |
| A10 | **Zero TODO/FIXME/HACK no pacote** | grep confirmado — ponto a favor |

Nenhum desses justifica reescrita. São candidatos a extração quando você já estiver mexendo no arquivo por outro motivo.

### 5.4 Contratos e tipagem

- **Só dataclasses.** Nenhum Pydantic, nenhum `TypedDict`, nenhum `NamedTuple`.
- **A validação existe e é centralizada:** `ClipConfig.validate` (`config.py:230-336`), `Template.validate`, `Zone.validate`, `user_providers.validate`.
- **O buraco:** os metadados do yt-dlp circulam como `dict` sem tipo (`pipeline.py:290`, `render_task.py:35`), e `window.components` é um `dict[str, float]` que recebe também strings (`llm_motivo`) — a própria memória do projeto registra que headline/hashtags tiveram de sair de lá para não virar string num campo que todo mundo soma.
- **`extra_ytdlp_args` (`config.py:117`) é repassado cru ao yt-dlp** (`download.py:198`), aceito da CLI, do arquivo de config e do painel. Não é injeção de comando (não há shell), mas é uma superfície de flags arbitrárias sem validação.

### 5.5 Modelo de dados e concorrência

- **Não existe um store de jobs para um vídeo único.** A persistência é por artefato: `clips.json`/`clips.md` (`report.py:76,86`), `viral_report.md`, cache de transcrição JSON, cache de veredito JSON, e o manifesto em **SQLite** do modo batch (`batch.py:38-49`), que tem estados `pending/running/done/failed` e recuperação de `running` órfão (`:169`).
- **O lock é um byte-range no offset 4096** de `<output>/.viralclipper.lock` (`lock.py:40,53,138-147`), mantido durante todo o run. O arquivo nunca é apagado — há justificativa de corrida no comentário. O pid é só uma dica.
- **Render paralelo com `ProcessPoolExecutor`** (`pipeline.py:505`) e `limit_native_threads()` chamado **antes** de criar o pool — detalhe correto e não óbvio: sem isso, cada worker abre um pool BLAS e estoura a RAM.

### 5.6 Idempotência — o ponto fraco real

- **`render_clip` apaga e reencoda o destino incondicionalmente** (`render.py:904-906`). Não há "pula se existe".
- **Pior:** se o pool paralelo levantar exceção, `pipeline.py:517-524` **re-renderiza todas as tarefas sequencialmente** — inclusive as que os workers já terminaram. Em um run de 5 clipes, isso pode significar 5 encodes extras.
- **A cobrança duplicada de LLM está protegida** (cache por janela) e a de transcrição também (cache por conteúdo). Downloads são "pula se existe". O render é o único sem proteção.

---

## 6. Diagnóstico de UX/UI e acessibilidade

### 6.1 Os fluxos avaliados

| # | Fluxo | Estado |
|---|---|---|
| 1 | Cadastro/onboarding | **Não existe** — e não deve existir nesta decisão de escopo |
| 2 | Criar/importar projeto | Existe: Cortes (URL) e Biblioteca (perfil) |
| 3 | Upload ou geração de conteúdo | Existe: link do YouTube/Instagram, ou transcrição colada |
| 4 | Edição e personalização | Parcial: Ajustes tem 27 chaves; não há timeline editável |
| 5 | Preview | Existe: prévia dos clipes na galeria |
| 6 | Exportação | Existe: arquivos em `output/` + `clips.md` |
| 7 | Compartilhar e retornar ao projeto | **Fraco** — não há projeto persistente entre sessões além do `ajustes.toml` |
| 8 | Assinatura/créditos/limites | **Não se aplica** |

### 6.2 Problemas confirmados, com evidência

**U1 — A porta está fixada no código e a alternativa documentada não funciona. (Severidade: alta)**
`web/index.js:617` e `web/ajustes.js:10` fazem `const API = 'http://127.0.0.1:7755'`. O CSP do servidor é `connect-src 'self'`. A memória do projeto documenta `python web/server.py --port 7756` como saída "se a 7755 estiver ocupada" — mas nessa porta **Estúdio e Ajustes quebram**, porque o `fetch` vai para 7755 e é recusado pelo CSP antes de qualquer coisa. `web/scrap.js:11` usa `/api` relativo e por isso sobrevive.
*Impacto:* o contorno documentado não funciona; você fica sem painel se a 7755 estiver ocupada.
*Solução:* derivar a base de `location.origin` nos três arquivos (uma constante em `comum.js`).
*Critério de aceitação:* subir em `--port 7756`, abrir `http://127.0.0.1:7756/` e as três páginas carregarem dados sem erro de console.

**U2 — A corrupção do `ajustes.toml` é silenciosa e vira perda permanente. (Severidade: alta)**
`AJUSTES_PATH.write_text(...)` (`web/server.py:2761`) não é atômica: sem arquivo temporário + `replace`, um travamento no meio da escrita deixa o arquivo truncado. Pior, `_read_ajustes` **engole TOML inválido e devolve `{}`** (`web/server.py:608-611`) — então a página passa a dizer "nada salvo ainda" e o **próximo save sobrescreve o arquivo inteiro com os defaults**. As 27 chaves se perdem sem uma única mensagem de erro.
*Comparação interna:* o cache de thumbnails **já faz certo** (`tmp.replace(target)`, `web/server.py:1936-1938`).
*Critério de aceitação:* escrever com temp+replace; um `ajustes.toml` malformado faz a página **avisar**, não resetar.

**U3 — A espera não tem contador nos três blocos que mais demoram. (Severidade: alta — é a sua dor nº 1)**
A escada tem 4 degraus (`web/server.py:1546-1551`): "Baixando e transcrevendo", "Escolhendo os melhores trechos", "Montando o relatorio", "Renderizando os clips". Dentro deles:
- transcrição: sem callback de progresso (`transcribe.py:218-242`); a memória registra ~16 min num vídeo;
- curador: `ranker.apply` só loga **no fim** (`ranker.py:679-686`); o laço das 24 chamadas é mudo;
- render: `-loglevel error` sem `-progress` (`render.py:403`, `:862`), e `_render_task` só loga aviso/erro.
*Solução:* emitir uma linha por item nos três (`julgando 3/24`, `clip 2/5`, e o `segment.end` da transcrição já dá o percentual). O painel já transmite os passos `[>]` do pipeline para a caixa de log.
*Critério de aceitação:* durante um run, a caixa de log mostra progresso incremental nas três etapas.

**U4 — Três cópias de cada utilitário de front. (Severidade: média)**
- Três funções `toast`: `index.js:119`, `ajustes.js:59`, `scrap.js:18`.
- Três wrappers HTTP: `api()` em `index.js:625` e `ajustes.js:265`, `post()`/`get()` em `scrap.js:66,84`.
- `$`/`$$` redefinidos em cada arquivo.
- Dois handlers `.toggle` (`index.js:75`; `ajustes.js:69` **e** `:493`).
- Dois escapers no `index.js`: `escapeHtml` (linha 420, **não escapa `'`**) e `esc` (linha 1627), o segundo vindo de `comum.js:29`.
*Impacto:* a correção de `api()` de ler o corpo do erro (que existe e está certa) teve de ser feita duas vezes, e a memória registra que as duas cópias divergem na primeira mudança.

**U5 — CSS duplicado entre páginas, com a memória já medindo 18 seletores iguais entre `index.css` e `scrap.css`.** `shared.css` tem só 9, e `scrap ∩ shared = 0`. Além disso `index.css` redefine `.btn` e `.card` duas vezes no próprio arquivo (`:117`/`:1448` e `:243`/`:1354`).
*Não é bug de aparência* — é risco de divergência. A memória já documenta o incidente real: a Biblioteca renderizou índigo enquanto as outras duas renderizavam magenta.

**U6 — Acessibilidade: 4 lacunas pontuais.**
| Achado | Evidência |
|---|---|
| `#save-pill` (Salvando/Salvo/Falha) **sem `role` e sem `aria-live`** | `ajustes.html:457` |
| `#toast-zone` com `aria-live` mas **sem `role="status"`** no Estúdio e Ajustes, enquanto a Biblioteca tem | `index.html:650`, `ajustes.html:478` vs `scrap.html:380` |
| `#queue-list` é `aria-live="polite"` mas `renderQueue` **reescreve o `innerHTML` inteiro a cada poll** — risco de anúncio repetido | `index.html:570` + `index.js:1398` |
| Barras de progresso com `aria-valuenow` mas **sem `aria-valuetext`** | `scrap.js:487`, `:1144` |

O resto está bom: há `role`, `aria-live`, `aria-describedby`, `role="switch"` com `aria-checked`, e os tokens de contraste estão documentados em `shared.css:252-266` com alvo ≥ 4,5:1.

**U7 — Erro de rede tratado de forma diferente em cada página.** `index.js` distingue `sem_conexao` (`:646-651`); `ajustes.js` e `scrap.js` não. E **nenhum dos três tem retry, timeout ou `AbortController`** — um `fetch` em voo não é cancelado ao navegar, e a falha de `poll` não marca a galeria como obsoleta.

---

## 7. Bugs confirmados e riscos a validar

### 7.1 Confirmados (com reprodução)

**B1 — A aba "Cortes" e "Ajustes" não funcionam em porta alternativa.**
- *Ambiente:* Windows, `.venv` 3.13, `python web/server.py --port 7756`.
- *Passos:* subir na 7756 → abrir `http://127.0.0.1:7756/`.
- *Esperado:* o painel carrega os dados normalmente.
- *Observado:* o `fetch` vai para `http://127.0.0.1:7755` (`index.js:617`, `ajustes.js:10`) e o CSP `connect-src 'self'` recusa.
- *Evidência:* as duas constantes no código; o comentário em `index.js:2180` explica o CSP.
- *Causa:* base da API fixada em vez de derivada de `location.origin`.
- *Teste de regressão:* teste que falha se qualquer `.js` de `web/` contiver um `http://127.0.0.1:` literal.

**B2 — Perda silenciosa de todos os ajustes.**
- *Passos:* corromper `ajustes.toml` (uma aspa não fechada) → abrir Ajustes → salvar qualquer campo.
- *Esperado:* a página avisa que o arquivo está inválido e não o destrói.
- *Observado:* `_read_ajustes` devolve `{}` (`server.py:608-611`), a página diz "nada salvo ainda", e o save regrava só o campo que você tocou — as outras 26 chaves somem.
- *Causa:* exceção engolida + escrita não atômica + merge a partir de um dicionário vazio.
- *Teste de regressão:* gravar um TOML malformado e afirmar que a resposta de `GET /api/ajustes` traz um aviso, e que um `PUT` posterior **não** apaga as chaves válidas.

**B3 — O bloco do curador custa 24 latências de rede em série.**
- *Evidência:* medição própria — 24 chamadas × 0,5 s = **12,44 s**, com 12,00 s de espera pura e 0,44 s de overhead (`viralclipper/ranker.py:642-655`).
- *Impacto:* 48 s a 1.752 s por vídeo, conforme o modelo escolhido.
- *Causa:* laço sequencial sem pool.
- *Teste de regressão:* com um provedor que registra o instante de cada chamada, afirmar que houve sobreposição temporal (concorrência > 1).

**B4 — Re-render duplicado quando o pool falha.**
- *Evidência:* `pipeline.py:517-524` percorre **todas** as tarefas após uma exceção do pool, mesmo as que já concluíram.
- *Impacto:* tempo de CPU desperdiçado proporcional ao número de clipes.
- *Causa:* o retry não consulta `task.rendered`.

**B5 — 8 testes vermelhos no escopo web, todos pré-existentes.**
Medido nesta sessão: `8 failed, 431 passed` em `tests/test_web_server.py`. A lista, comparada com o baseline registrado, é **subconjunto estrito** do baseline (9 falhas), ou seja: **zero regressões**. As classes são `HeroPreviewTests` (7) e `SharedStyleSheetTests::test_no_selector_is_declared_on_both_sides`. Uma falha do baseline (`test_the_shared_rules_are_not_duplicated_in_the_pages`) **passou** — efeito do redesign em curso na árvore de trabalho, não deste trabalho.

### 7.2 Riscos a validar (hipóteses, não bugs)

| # | Hipótese | Como validar |
|---|---|---|
| H1 | `BatchedInferencePipeline` do faster-whisper corta a transcrição em 2-4× | Rodar o mesmo vídeo nos dois caminhos e cronometrar. **Não afirmo número**: em CPU com `int8` o ganho é menor que em GPU |
| H2 | `workers=2` ainda é o ótimo nesta máquina | A memória registra a medição que fixou 2 (8 threads / 7,8 GB / sem GPU). Se a máquina mudou, re-medir antes de subir |
| H3 | O render é o segundo maior bloco da espera | Não medido. Requer um run real com `--dry-run` desligado e instrumentação de tempo |
| H4 | A leitura integral de arquivos em RAM (`server.py`, 8 ocorrências de `read_bytes()`) atrapalha clipes grandes | Medir com um clipe de 100 MB+. Hoje os clipes são pequenos e isso provavelmente não dói |

---

## 8. Desempenho, segurança e custos

### 8.1 Onde o tempo vai

| Estágio | Fonte do número | Ordem de grandeza |
|---|---|---|
| Download do áudio | não medido | dezenas de segundos |
| **Transcrição** | memória do projeto: "~16 min" | **~16 min** |
| Score heurístico | CPU puro, sem I/O | segundos |
| **Curador (24 chamadas seriais)** | medição própria + benchmark do repo | **48 s a 1.752 s** |
| Render (N clipes / 2 workers) | não medido | não medido |

Duas observações que mudam a leitura: a transcrição tem **cache por conteúdo**, e o curador tem **cache por janela** — então o *segundo* run do mesmo vídeo é muito mais barato. A espera de 10 a 39 min/dia vale para **vídeo novo**, que no seu volume (5-20/dia) é praticamente todo o dia.

### 8.2 Segurança

O modelo de ameaça é **uma pessoa, em loopback**. Dentro desse modelo:

| Achado | Severidade | Comentário |
|---|---|---|
| `/api/clips/` serve **qualquer** arquivo sob `output/`, sem allowlist de sufixo (usa só `asset_content_type`) | baixa | Diferente da rota estática, que tem allowlist (`server.py:2634`). Expõe `clips.json`/`clips.md`. Em loopback, impacto real ≈ zero |
| **Sem teto de tamanho de corpo**; `int(Content-Length)` sem try (`server.py:2654`) | baixa | `Content-Length` malformado → traceback. Local |
| `progress_bar_color` interpolado cru no filtergraph (`render.py:522`) e `font` no cabeçalho ASS (`render.py:353`) | baixa | **Não é execução de código**: não há shell. O pior caso é um render malformado. Mas `ajustes.toml` editado à mão com uma vírgula na cor quebra o render sem dizer por quê |
| `extra_ytdlp_args` sem validação (`config.py:117` → `download.py:198`) | baixa | Flags arbitrárias ao yt-dlp. Você é o único autor |
| **Sem autenticação** | **não é achado** | Decisão correta para ferramenta local; o site público inclusive orienta a manter em 127.0.0.1 |
| Guarda Host/Origin, CSP com `frame-ancestors 'none'`, `nosniff`, sem CORS | **ponto forte** | Anti-rebinding funcionando |

### 8.3 Custo — o modelo, com premissas explícitas

Você não forneceu orçamento, então explicito as premissas: execução local, sem GPU, provedores de LLM no tier gratuito.

| Item | Custo em dinheiro | Observação |
|---|---|---|
| Transcrição | **R$ 0,00** | faster-whisper local |
| Render | **R$ 0,00** | FFmpeg local; custo = energia + tempo de CPU |
| Armazenamento | **R$ 0,00** | disco local; `output/` vazio hoje |
| Transferência/CDN | **R$ 0,00** | não existe |
| **Curador (LLM)** | **R$ 0,00** | Groq: 1.000 req/dia → 24 req/vídeo → **~41 vídeos/dia**. OpenRouter: 50/dia → **~2 vídeos/dia** |

**Conclusão do modelo:** o custo monetário por vídeo é zero e o teto é o **limite de requisições do provedor gratuito**, não o dinheiro. Para 5-20 vídeos/dia, o Groq cobre com folga (41/dia). O OpenRouter não cobre.

**Portanto: o custo dominante é o seu tempo.** A 4,9 s por chamada, o bloco do curador consome 118 s por vídeo — **10 min/dia a 5 vídeos, 39 min/dia a 20 vídeos**. Com as chamadas em paralelo (teto de 6), o mesmo bloco cai para ~20-25 s, economizando **8 a 33 min por dia**. Essa é a métrica que "reduzir custo por vídeo" deveria perseguir neste projeto.

---

## 9. Matriz de alternativas tecnológicas

### Decisão 1 — Como cortar a espera do curador

| | A. Manter serial | B. Pool de threads com teto | C. Lote numa chamada só |
|---|---|---|---|
| Adequação | nenhuma — é a dor | **alta** — chamadas independentes, cache por janela | média — o prompt teria de mudar |
| Custo | 48-1.752 s/vídeo | 1 latência + fila | 1 latência, mas prompt maior |
| Esforço | 0 | **baixo** (~30 linhas) | alto (reescreve o contrato do prompt) |
| Manutenção | — | baixa | alta |
| Desempenho | pior | **~6× com teto de 6** | melhor, se o modelo cooperar |
| Privacidade | igual | igual | pior (mais texto por chamada) |
| Dependência | — | stdlib (`concurrent.futures`) | do provedor aceitar lote |
| Risco | — | respeitar o limite de requisições | quebra o parse e o cache |

**Recomendação: B.** É a alternativa com melhor razão ganho/risco, usa só a stdlib, e as falhas por janela já são não-fatais (`ranker.py:651-655`) — o que significa que a paralelização **não introduz um modo de falha novo**. Um teto configurável (padrão 6) respeita o limite do provedor gratuito.
**Quando A volta a ser melhor:** nunca, neste volume.
**Quando C passa a ser melhor:** se você migrar para um modelo que aceite lote e o custo por token cair abaixo do custo de 24 chamadas.
**O que mudaria a decisão:** se o provedor gratuito começar a contar requisições por minuto (o Vizard publica 1/min·10/h no grátis — se o Groq fizer o mesmo, o teto cai para 1-3).

### Decisão 2 — Como cortar a transcrição

| | A. Manter `small`/`int8`/beam 1 | B. `BatchedInferencePipeline` | C. Provedor de transcrição em nuvem |
|---|---|---|---|
| Adequação | já é o ajuste de velocidade | alta | alta |
| Custo | R$ 0 | R$ 0 | passa a ter custo por minuto |
| Esforço | 0 | **baixo** (troca a chamada) | médio |
| Desempenho | baseline | **não medido** (H1) | o mais rápido |
| Privacidade | máxima | máxima | **o áudio sai da máquina** — contraria o posicionamento |
| Dependência | — | mesma lib | fornecedor externo |

**Recomendação: B, condicionada à medição.** Faça a medição antes de adotar. **C está descartada** por contradizer o único diferencial defensável do produto (o vídeo não sai da sua máquina).

### Decisão 3 — Como tornar a espera legível

| | A. Nada | B. Log incremental por item | C. Barra de progresso real por etapa |
|---|---|---|---|
| Esforço | 0 | **baixo** | alto (exige callback do FFmpeg, do whisper e do pool) |
| Ganho | — | **alto por real investido** | médio adicional |
| Risco | — | nenhum | refatora assinaturas de várias funções |

**Recomendação: B agora, C depois.** O painel já transmite os passos do pipeline para a caixa de log (`server.py:1852`), então uma linha por item aparece na tela sem tocar em CSS ou HTML.

---

## 10. Backlog priorizado

**Severidade** = risco técnico. **Prioridade** = impacto no seu dia. São eixos separados: um item pode ser severidade baixa e prioridade alta.

> Esta tabela é o **retrato de 2026-10-07, antes das correções**. O que já saiu do papel está na **seção 14** ("O que foi executado neste ciclo"), com a prova de cada item.

| ID | Problema | Evidência | Impacto | Severidade | Prioridade | Recomendação | Esforço | Dependências | Critério de aceitação |
|---|---|---|---|---|---|---|---|---|---|
| **P-01** | 49 commits sem backup remoto | `origin/master` em `6204825` (30/09); `git rev-list --count origin/master..master` = 49; `.git` já corrompeu em 22/09 | **Perda total do trabalho** | Crítica | **P0** | `git push origin master` + verificar que o push está limpo | 5 min | nenhuma | `git rev-list --count origin/master..master` = 0 |
| **P-02** | 24 chamadas do curador em série | medição: 12,44 s com 12,00 s de espera pura; `ranker.py:642-655` | 10-39 min/dia parados | Alta | **P0** | Pool de threads com teto configurável (padrão 6) | 2-4 h | nenhuma | teste prova sobreposição temporal; run real mais rápido |
| **P-03** | Espera opaca nos 3 blocos longos | `transcribe.py:218`, `ranker.py:679`, `render.py:403` | Percepção de travamento | Média | **P1** | Log incremental por item nas 3 etapas | 2-3 h | P-02 (mesmo laço) | log mostra `julgando n/24`, `clip n/N` |
| **P-04** | Perda silenciosa dos ajustes | `server.py:2761` (não atômica) + `:608-611` (engole TOML inválido) | **Perde as 27 chaves sem avisar** | Alta | **P0** | Escrita temp+replace; avisar em vez de resetar | 2-3 h | nenhuma | teste: TOML malformado → aviso, sem apagar |
| **P-05** | Porta fixa quebra 2 das 3 páginas | `index.js:617`, `ajustes.js:10` vs CSP `connect-src 'self'` | O contorno documentado não funciona | Média | **P1** | Base derivada de `location.origin` | 1 h | nenhuma | teste: nenhum `127.0.0.1:` literal em `web/*.js` |
| **P-06** | Sem CI; 1.386 testes que ninguém roda | sem `.github/`; 8 falhas pré-existentes no escopo web | Regressão entra sem aviso | Alta | **P1** | Workflow rodando a suíte + corrigir ou marcar as 8 | 3-5 h | P-01 | CI verde (ou com as 8 documentadas) em todo push |
| **P-07** | Re-render duplicado quando o pool falha | `pipeline.py:517-524` | CPU desperdiçada | Média | **P2** | Retry só do que não tem `task.rendered` | 1 h | nenhuma | teste: pool falho não re-renderiza clipe pronto |
| **P-08** | Aba de publicação inexistente | `docs/curador.md`: headline e hashtags **já existem**; faltam só a tela e o fluxo | O seu diferencial fica invisível | Média | **P1** | Página nova no rail + rota + painel de metadados | 1-2 dias | P-04 | clipe renderizado abre a aba com título e hashtags prontos para copiar |
| **P-09** | 3 cópias de `toast`/`api`/`$` no front | `index.js:119,625`; `ajustes.js:59,265`; `scrap.js:18,66` | Correção precisa ser feita 3× | Baixa | **P2** | Mover para `comum.js` | 2-3 h | nenhuma | teste: cada utilitário definido 1× |
| **P-10** | Acessibilidade: 4 lacunas | `ajustes.html:457`; `index.html:650`; `index.js:1398`; `scrap.js:487` | Leitor de tela não anuncia estado | Baixa | **P2** | `role="status"` + `aria-valuetext` + parar de reescrever a lista viva | 2 h | nenhuma | teste de atributo por elemento |
| **P-11** | `/api/clips/` sem allowlist de sufixo | `server.py:2604-2623` vs `:2634` | Exposição irrelevante em loopback | Baixa | **P3** | Reusar a allowlist da rota estática | 30 min | nenhuma | teste: `clips.json` responde 404 |
| **P-12** | Sem teto de corpo; `int()` sem try | `server.py:2654` | Traceback com header malformado | Baixa | **P3** | Teto + try | 30 min | nenhuma | teste: header inválido → 400 |
| **P-13** | Cores/fonte interpoladas cruas | `render.py:522`, `:353` | Render malformado sem mensagem | Baixa | **P3** | Validar formato com mensagem clara | 1 h | nenhuma | teste: cor inválida → erro explicativo |
| **P-14** | `_record` duplicado | `pipeline.py:542-562` + `render_task.py:196-219` | Divergência futura | Baixa | **P3** | Extrair para `report.py` | 1 h | nenhuma | teste existente continua passando |
| **P-15** | `ajustes.toml` fora do `.gitignore` | `git check-ignore ajustes.toml` → não ignorado | Um `git add -A` commita config local | Baixa | **P3** | Adicionar ao `.gitignore` | 5 min | nenhuma | `git check-ignore ajustes.toml` responde |

---

## 11. Roadmap sugerido — 30, 60 e 90 dias

### 30 dias — parar a dor e fechar o risco

1. **Push para o GitHub** (P-01). Cinco minutos que removem o maior risco do projeto.
2. **Paralelizar o curador** (P-02). O maior ganho de tempo disponível.
3. **Progresso incremental nas 3 etapas** (P-03). Sem isso, o ganho de P-02 é menos perceptível.
4. **Escrita atômica e aviso de TOML inválido** (P-04).
5. **Porta derivada de `location.origin`** (P-05).
6. **CI rodando a suíte** (P-06).

**Resultado esperado:** o dia fica 8 a 33 min mais curto, o trabalho está em dois lugares, e uma corrupção de config deixa de ser fatal.

### 60 dias — a aba de publicação

7. **Aba de publicação** (P-08): página nova no rail, rota nova, painel que mostra headline, headline alternativa e hashtags por clipe, com botão de copiar. O motor já entrega os dados — isto é tela e fluxo.
8. **Unificar `toast`/`api`/`$` em `comum.js`** (P-09).
9. **Acessibilidade das 4 lacunas** (P-10).
10. **Medir H1** (`BatchedInferencePipeline`) e adotar só se o ganho for real.

### 90 dias — eficiência e polimento

11. **Medir o render** (H3) e decidir sobre `workers`.
12. **Corrigir o re-render duplicado** (P-07).
13. **Endurecer as bordas** (P-11 a P-14).
14. **Um comando só de diagnóstico** que cronometra cada estágio — hoje o projeto tem cache e degradação, mas não tem medição de tempo.

---

## 12. Plano de testes e critérios de sucesso

### Como testar cada correção

| Item | Teste | Tipo |
|---|---|---|
| P-01 | `git rev-list --count origin/master..master` = 0 | verificação de comando |
| P-02 | provedor falso que grava o instante de cada chamada; afirmar sobreposição | unitário |
| P-03 | capturar as linhas de log de um run curto e afirmar a presença de `n/N` | unitário |
| P-04 | gravar TOML malformado; `GET` traz aviso; `PUT` posterior não apaga chaves válidas | unitário |
| P-05 | nenhum `http://127.0.0.1:` literal em `web/*.js` | unitário (varredura) |
| P-06 | workflow verde no push | CI |
| P-08 | rota responde 200 e o HTML contém os campos de metadados | unitário + E2E |

### Regra de teste que o projeto já pratica e que deve valer para tudo isso

**Sabote e confirme que o teste cai.** A memória do projeto registra um caso em que a sabotagem "passou" porque o alvo não foi encontrado e o arquivo ficou intacto. Cada teste novo acima precisa da sua sabotagem correspondente.

### Critérios de sucesso do ciclo

1. `origin/master` == `master` (zero commits pendentes).
2. Bloco do curador abaixo de **30 s** por vídeo com o modelo atual.
3. A caixa de log mostra progresso em todas as etapas longas.
4. Um `ajustes.toml` corrompido nunca causa perda de configuração.
5. CI verde em todo push.
6. A aba de publicação mostra título e hashtags de um clipe renderizado.

---

## 13. Perguntas e decisões pendentes

1. **Qual provedor de LLM você usa hoje no curador?** Isso define a latência real do bloco (4,9 s a 73 s) e o teto de concorrência seguro. Com o Groq (1.000 req/dia) um teto de 6 é conservador; com o OpenRouter (50/dia) o gargalo deixa de ser latência e passa a ser cota.
2. **Quantos clipes por vídeo você costuma pedir?** Se for 3-5, `ranker_top_n = 24` está julgando 5× mais janelas do que você usa — reduzir para 8-10 corta o bloco em 3× sem mudar o resultado.
3. **A máquina ainda é a mesma (8 threads / 7,8 GB / sem GPU)?** Se mudou, `workers=2` e `threads=2` merecem re-medição.
4. **Você quer a aba de publicação como página nova no rail, ou como painel dentro do Estúdio?** A memória do projeto registra um pedido seu recorrente: "uma seção, um dono". Uma página nova tem o custo de manter mais um lugar.
5. **O site público deve ser publicado?** O `canonical` está em `https://exemplo.com/` e o `public-site/README.md` manda definir a URL real antes do build. Hoje o site não está no ar.
6. **A `MEMORY.md` do projeto está ~11% acima do limite de 30 KB** (registro dela mesma). Vale destilar.

---

## As 5 ações mais importantes para começar

1. **`git push origin master`.** Cinco minutos. Remove o único risco de perda total, e ele já se materializou uma vez.
2. **Paralelizar as 24 chamadas do curador.** Ganho medido de 8 a 33 minutos por dia, com risco baixo porque a falha por janela já é não-fatal.
3. **Progresso incremental na transcrição, no curador e no render.** A espera continuará existindo; deixar de ser opaca muda a experiência mais do que qualquer outra coisa barata.
4. **Escrita atômica dos arquivos de estado.** Hoje uma aspa fora de lugar no `ajustes.toml` apaga as 27 configurações em silêncio.
5. **A aba de publicação.** O motor já produz título e hashtags. Falta a tela — e é o que transforma o produto de "gerador de cortes" em "ferramenta de publicação".

## O que manter como está

- A ausência de autenticação. É a decisão certa para ferramenta local.
- O `subprocess` com lista de argumentos, sem shell.
- A degradação graciosa de `reframe` e `ranker`, e o exit code 4 para transcrição perdida.
- O cache de transcrição e de veredito.
- A paleta única e o `design-system/`.
- O caminho de transcrição fornecida.
- `workers=2` / `threads=2` — até que uma medição nova diga o contrário.

## O que evitar construir neste momento

- **Autenticação, multiusuário e isolamento por cliente.** Você respondeu "ferramenta pessoal"; isso vira custo puro.
- **Cobrança, créditos e quotas.** Você respondeu "grátis".
- **Publicação direta nas redes sociais.** OAuth por plataforma, revisão de app e tokens expirando, para economizar um upload manual.
- **Editor com timeline arrastável.** É outro produto. O seu valor está em *encontrar* o trecho, não em refinar quadro a quadro.
- **Dublagem e tradução.** Custo alto, público pt-BR.
- **Migração para nuvem.** Contraria o único diferencial defensável que a pesquisa de mercado revelou: nenhum concorrente da categoria consegue prometer que o vídeo não sai da sua máquina.

## A principal oportunidade de diferenciação

**Ser a única ferramenta de cortes em que o vídeo nunca sai da sua máquina, com custo marginal zero e o critério de seleção aberto para você reescrever.**

A pesquisa de mercado mostra que os três líderes da categoria (OpusClip, Vizard, Klap) têm o mesmo desenho: upload obrigatório, cobrança por minuto enviado, marca d'água e expiração de 3 dias no grátis, e um prompt de seleção que você não pode tocar. Nenhum deles pode mudar isso sem trocar o modelo de negócio.

O seu produto já é o oposto disso, e o site público já sabe disso — o hero diz "EDIÇÃO CURTA. FLUXO SOB SEU CONTROLE." e a seção final promete "Seus arquivos ficam no seu fluxo de trabalho". **A oportunidade não é construir mais recursos. É tornar essa diferença visível e verdadeira:** a espera curta (P-02), o progresso transparente (P-03) e a aba de publicação (P-08) são o que transforma a promessa em experiência.

---

*Auditoria conduzida por inspeção direta do repositório, execução da suíte de testes, medição própria do custo de serialização e leitura das páginas oficiais de preço em 2026-10-07. Onde não houve verificação, o relatório diz que não houve.*

---

## 14. O que foi executado neste ciclo

Esta seção não é parte da auditoria — é o registro do que saiu do papel logo depois dela. Está aqui porque um relatório que não diz o que foi corrigido vira ficção na segunda leitura.

### Estado do backlog

| ID | Estado | O que existe agora | Prova |
|---|---|---|---|
| **P-01** | ✅ **feito** | 49 commits enviados; `origin/master` = `6b8f078` | `git rev-list --count origin/master..master` = **0**. Antes do push, os 59 arquivos / 18.975 linhas adicionadas foram varridos por `sk-`, `nvapi-`, `gsk_`, `sessionid=`, `api_key=` e `Bearer`: nada. |
| **P-02** | ✅ **feito** | `ranker_concurrency` (padrão **6**) em `config.py`, `--ranker-concurrency` na CLI, os dois `config.example.*`, `README.md` e `docs/curador.md`; `ranker.apply` julga em `ThreadPoolExecutor` | 11 testes novos (`RankerConcurrencyTests`, `RankerProgressTests`, `RankerConcurrencyConfigTests`). Sabotagem: forçar `workers = 1` faz **2 testes reprovarem**. |
| **P-03** | 🟡 **parcial** | O laço do curador agora emite `Curador: n/N janelas julgadas` **da thread principal** (logar de dentro do pool corromperia `RunLogger.lines`, que não é sincronizado) | 2 testes. Transcrição e render continuam mudos — é o que falta. |
| **P-04** | ✅ **feito** | `_write_atomically` (temp + `replace`) nos dois arquivos de estado; `_load_ajustes` devolve o motivo em vez de engolir; `/api/ajustes` expõe `malformed`; a página de Ajustes avisa e **não** sobrescreve | 8 testes (`AjustesAtomicWriteTests`). Sabotagem: tirar o temp+replace faz **1 teste reprovar**. |
| **P-05** | 🟡 **parcial** | A página **nova** não repete o defeito: `const API = '';` (origem implícita), então ela funciona em `--port 7756` | Teste que recusa `127.0.0.1` literal em `publicar.js`. `index.js:617` e `ajustes.js:10` continuam com a porta fixa — **P-05 segue aberto para essas duas**. |
| **P-06** | ❌ **aberto** | Sem `.github/`. A suíte continua sendo rodada à mão | — |
| **P-08** | 🟡 **iniciado** | Rota `/publicar` (+ alias `.html`), `web/publicar.html`, `publicar.js`, `publicar.css`, `_publicacao_payload()`, `GET /api/publicacao`, entrada em `RAIL_PAGES`, docstring do servidor atualizado | 17 testes novos. Verificado em navegador: rail marca Publicar, 2 cartões, cópia escreve a legenda inteira no clipboard, zero erros de console, sem overflow em 390px. |
| **P-14** | ✅ **feito** | A cópia de `_record` em `pipeline.py` foi **apagada**; `pipeline` importa a de `render_task` | 3 testes, incluindo `pipeline._record is render_task._record`. Sabotagem: tirar `headline`/`hashtags` do `_record` faz **2 testes reprovarem**. Isto não era cosmético — a cópia do `pipeline` omitia os dois campos, então um `clips.json` de dry-run perdia o texto do curador. |
| **P-15** | ✅ **feito** | `ajustes.toml` entrou no `.gitignore` | `git check-ignore ajustes.toml` responde. |

### O que a aba de publicação faz hoje

Lê o **mesmo** `output/clips.json` da galeria e mostra, por clip: miniatura 9:16 (o jpg que fica ao lado do mp4), headline, nota, trecho (`00:12 → 00:40`), duração, termos de gancho, a **legenda pronta para colar** num campo de texto somente-leitura e dois botões de cópia. O trecho transcrito fica num `<details>` recolhido, para conferir se o clip é o que se pensa antes de postar.

Três decisões que valem registro:

1. **O caminho do clip é relativizado no servidor**, não confiado ao arquivo. `file` chega absoluto do motor e `/api/clips/` recusa o que sai de `output/`; entregar o absoluto seria entregar um endereço que o próprio servidor nega.
2. **A legenda fica visível ao lado do botão.** É o que salva a cópia quando o clipboard é negado (permissão, `http` puro): o aviso manda "selecione e copie", e para isso o texto tem de estar na tela.
3. **A página não escreve nada.** Nenhum POST, nenhum PUT, nenhum upload. O contrato está escrito na própria página e travado por teste.

### O que ainda falta na aba

- Publicação direta nas redes (OAuth por plataforma) — **não recomendado**, ver "O que evitar construir".
- Marcar clip como publicado, para não repetir.
- Headline alternativa (o prompt já pede; a tela mostra uma).
- Atualização automática enquanto um render roda.

### Uma armadilha encontrada no caminho

Havia **um painel antigo ainda em execução** (porta 7801, cabeçalho `Server: ViralClipper/1.0`) carregado antes destas mudanças: ele responde `/publicar.html` com 404 porque as rotas foram lidas na inicialização do processo. **Reinicie o painel** para ver a aba nova — o código em disco já está certo.

### Suíte completa, no estado congelado

| | |
|---|---|
| Resultado | **11 falhas, 1.415 passaram, 909 subtestes** (32m41s) |
| Linha de base (antes deste ciclo) | 10 falhas, 1.377 passaram |
| **Regressões introduzidas** | **zero** |

As 11 falhas se explicam, uma a uma:

- **7 × `HeroPreviewTests`** (`index.html`): o teste procura `class="preview-card"` e não acha. `grep -c preview-card` dá **0 tanto em `HEAD` quanto no disco**, e `web/index.html` **não está modificado** — a falha é anterior a este ciclo. É o mesmo arquivo que a outra sessão está redesenhando.
- **1 × `SharedStyleSheetTests::test_no_selector_is_declared_on_both_sides`**: `.pressable:active` está em `index.css` **e** em `shared.css`. Medi os dois arquivos em `HEAD` e no disco: em `HEAD` havia **11 seletores colidindo**, no disco restam **2**. A outra sessão está no meio dessa correção — não é minha.
- **2 × `test_reframe.py`** (`focus_center_x` devolvendo `None`): pré-existentes, sem relação com o painel.
- **1 × `test_lock.py::CrossProcessTests::test_killing_the_holder_releases_the_lock`**: **flaky sob carga**, não regressão — rodado isolado, passa em 11 s. O `kill()` do Windows libera o handle do arquivo alguns instantes depois de o processo morrer, e a suíte desta vez levou 32 min em vez de 29.

Os 17 testes novos da aba e os 11 do curador concorrente estão entre os 1.415 que passaram.

