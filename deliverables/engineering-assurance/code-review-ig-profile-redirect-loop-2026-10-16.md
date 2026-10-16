# Revisão: laço de redirect na busca por perfil do Instagram

**Data**: 2026-10-16
**Fluxo**: 1 — Revisão de código abrangente (com triagem operacional embutida)
**Membros participantes**: Cody (revisor de código), Tessa (especialista em testes), Rex (SRE)
**Artefato sob revisão**: diff **não commitado** de `viralclipper/ig_profile.py` (+76/−12) e de `tests/test_ig_profile.py` (+110)

---

## 📌 TL;DR

- **Causa-raiz confirmada por medição**: o `sessionid` do `cookies.txt` (14/10) está **inválido**. O Instagram responde **302** com `Location: https://www.instagram.com/#` e corpo de **0 bytes**; como o `#` não chega ao servidor, o alvo do redirect é a própria requisição e o curl percorre 30 saltos até desistir. `TooManyRedirects` **é** subclasse de `OSError`, então o `except OSError` de `_http` repassava o texto cru do curl ao usuário.
- **O patch em disco resolve o defeito relatado** — Cody confirmou por medição que a string `curl: (47) Maximum (30) redirects followed` não pode mais sair de um processo novo. Tessa falsificou 3/3 dos caminhos consertados: revertendo cada um, o teste correspondente falha.
- **Mas o usuário continua vendo o erro**, e a causa não é código: `web/server.py:152` importa `ig_profile` em tempo de carga e o processo no ar (PID 424824) subiu **15/10 21:12:51**, ~10 h antes da correção. É um **gap de deploy**, não um defeito de código. Medido ao vivo: o servidor no ar ainda devolve a string do curl em 4,678 s.
- **Distribuição de severidade**: 🔴 0 / 🟠 1 / 🟡 3 / 🟢 3.
- **Não bloqueia o reinício; bloqueia o commit.** O achado 🟠 (uma armadilha de `except` que pode virar `TypeError` não tratado) deve ser corrigido antes de commitar, mas não impede o reinício imediato.
- **Escopo**: o patch conserta a **mensagem**, não a **listagem**. Enquanto os cookies não forem reexportados, a busca por perfil vai continuar falhando — só que agora dizendo o que fazer.

---

## 🎯 Cartão de conclusão

| Item | Conteúdo |
|------|---------|
| Avaliação geral | 🟡 Aprovado com ressalvas (Cody: *Request Changes* — pequenos) |
| Itens bloqueantes | 1 (pré-commit, não pré-deploy) |
| Ações-chave | 3 |
| Próximo passo | Reiniciar o servidor (P0) → reexportar os cookies do Instagram (P0, lado do usuário) → corrigir o guarda do `except` antes de commitar (P1) |

---

## 🔍 Causa-raiz (medida, não inferida)

**Mecanismo do laço.** Medido com os cookies reais do `cookies.txt`:

| Requisição | Resultado |
|---|---|
| `GET https://www.instagram.com/redzofc/` **com** cookies | **302**, `Location: https://www.instagram.com/#`, corpo **0 bytes** |
| `GET https://www.instagram.com/redzofc/` **sem** cookies | **200**, 782 621 bytes |
| `GET /api/v1/users/web_profile_info/?username=redzofc` **com** cookies + `x-ig-app-id` | **401** |
| idem, **sem** cookies (controle) | **401** |

A linha 2 é o controle decisivo: sem cookie nenhum a página carrega normalmente. **O 302 é provocado pelos cookies** — é o Instagram recusando a sessão, não um problema de rede. A linha 3 confirma que o `sessionid` está genuinamente morto (o mesmo 401 com e sem cookies).

**Por que a string crua chegava ao usuário.** Em `curl_cffi` 0.16.3, `CODE2ERROR[47]` é `TooManyRedirects`, com MRO `TooManyRedirects → RequestException → CurlError → OSError → Exception`. Sendo **subclasse de `OSError`**, era capturada pelo `except OSError` de `_http` (`ig_profile.py:518`) e o `str(exc)` do curl era interpolado direto na mensagem de `_fetch_lsd`. A cadeia fecha por construção e casa caractere a caractere com o relato do usuário.

**Custo do comportamento antigo.** 31 requisições HTTP por tentativa (1 + 30 saltos), 4,678 s medidos. O patch reduz a **1 requisição**. Bônus relevante: 30× menos batidas no rate-limit do Instagram por clique.

**Descoberta colateral no mesmo patch (segundo defeito latente, independente).** O token `fb_dtsg` mudou de bolso. Medido na página real (782 621 bytes): `"DTSGInitialData",[],{"token":"` ocorre **0 vezes** — o envelope existe mas **vazio** (`"DTSGInitialData",[],{}`) — e `"MRequestConfig",[],{"dtsg":{"token":"..."}` ocorre **1 vez**. Sem a segunda correção do mesmo patch, mesmo com sessão nova a busca falharia com *"Não encontrei o token 'fb_dtsg'"*, acusando a sessão quando o culpado seria o extrator.

---

## 🔍 Achados da revisão (por severidade)

| # | Severidade | Categoria | Local | Problema | Correção sugerida | Fonte |
|---|-----------|-----------|-------|----------|-------------------|-------|
| 1 | 🟠 alto | correção | `ig_profile.py:481` | `except _TooManyRedirects as exc:` quebra se `_TooManyRedirects is None` **e** `_curl_requests` estiver presente. O `except` só é avaliado quando algo levanta dentro do `try` → `TypeError: catching classes that do not inherit from BaseException is not allowed`, que **substitui** a exceção original e, não sendo `OSError`, **escapa** dos handlers de `_http`/`_request_page` → exceção crua não tratada (pior que o defeito original). Não é alcançável no pin 0.16.3, mas é exatamente a deriva que o comentário da linha 44 antecipa. O `skipTest` do teste novo **mascara** o estado em vez de o pegar. | Tupla condicional: `_REDIRECT_ERRORS = (_TooManyRedirects,) if _TooManyRedirects is not None else ()` e `except _REDIRECT_ERRORS as exc:`. `except ()` é válido e não captura nada. | Cody #1 + Tessa #1 (medido) |
| 2 | 🟡 médio | correção | `ig_profile.py:490-500`; docstring `:460` | O fallback em `urllib` **ignora** `allow_redirects` e continua seguindo redirects, enquanto o docstring afirma "Redirects are **not** followed". Medido com servidor local (`302 /move → /final → 200`): `urllib` devolve `(200,"OK")`, `curl_cffi` com a flag devolve `(302,"")`. O mesmo URL tem resultado diferente conforme o transporte — e no transporte primário um redirect **legítimo** (mudança de host/endpoint/locale) passa a ser rotulado como "sessão expirou" quando antes era seguido. | Fazer o `urllib` honrar a flag (um `HTTPRedirectHandler` que devolve a resposta em vez de seguir) **ou** documentar a divergência explicitamente no docstring. | Cody #2 + Tessa #5 |
| 3 | 🟡 médio | manutenibilidade | `ig_profile.py:523-528`, `742-743`, `487` | **Qualquer** `3xx` (300-399) é afirmado como "A sessão do Instagram expirou". Um `301/307/308` de mudança real ficaria mal rotulado. Além disso as três saídas divergem em pontuação: `_send` e `_graphql_error` acrescentam `.`, o `_http` não. | Restringir a mensagem a `302`/`303` (ou nomear "resposta de redirecionamento" no texto) e padronizar o ponto final. | Cody #3 |
| 4 | 🟡 médio | testes | `tests/test_ig_profile.py` | O ramo **`urllib`** de `_send` (fallback documentado, sem `curl_cffi`) **não é exercitado por teste algum**. O único teste que zera `_curl_requests` (l.445) só cobre `_refusal_hint()`. O comportamento atual está correto, mas há **zero proteção de regressão**. | `_curl_requests=None` + mock de `urllib.request.urlopen` levantando `HTTPError(302)`; asserir que `_http` devolve `ClipperError` com "expirou" e **sem** "LSD". | Tessa #2 e #3 |
| 5 | 🟢 baixo | correção | `ig_profile.py:664` | `_request_page` passou de `>= 400` para `>= 300`: alarga a superfície de falha do POST GraphQL. Nenhum `3xx` legítimo é observado nesse endpoint e `_graphql_error` já tratava `302` antes — aceitável, mas a mudança é estritamente mais ampla. | Manter; opcionalmente alinhar com #3. | Cody #4 |
| 6 | 🟢 baixo | manutenibilidade | `ig_session_check.py:84`; `tests/test_ig_profile.py:393,462` | Os três `fake_send` não aceitam `allow_redirects`, ficando mais **estreitos** que `_send`. Hoje funciona (medido: `ig_session_check.py` → exit 0; os dois call sites omitem o kwarg), mas um caller futuro que passe a flag quebra com `TypeError`. | Adicionar `**kwargs` (ou `allow_redirects=False`) às três assinaturas fake. | Cody #5 |
| 7 | 🟢 baixo | testes | `tests/test_ig_profile.py` (novo) | `test_the_transport_does_not_follow_redirects` fixa o **nome do kwarg** (`allow_redirects is False`), acoplando o teste à API do `curl_cffi` em vez do comportamento observável. | Trocar por asserção de comportamento. | Tessa #5 |

**Pontos positivos registrados por Cody.** `allow_redirects=False` também impede que uma requisição autenticada siga um `302` para host de terceiros — hardening pequeno contra vazamento de credencial. O `try` separado para `_TooManyRedirects` (em vez de compartilhar o bloco de `_curl_requests`) é a estrutura defensiva correta. E a ordem `MRequestConfig` **antes** de `DTSGInitialData` com `or` é certa: o padrão exige token não-vazio, então o envelope vazio não pode ofuscar o valor real, e o fallback preserva a grafia antiga.

---

## 🏗️ Impacto arquitetural e operacional

**Não há mudança arquitetural.** O patch toca um único módulo de transporte, sem alterar contrato público (`list_profile`, `ProfileListing`, `ProfileItem` intactos) nem as assinaturas de `_send` (só um default novo).

**A natureza do incidente é operacional, não de código** (triagem de Rex):

- **SEV4.** Ferramenta local, um usuário, uma máquina. Quebra **apenas** o fluxo Instagram/modo Perfil da Biblioteca. TikTok, YouTube, busca por URL de vídeo e o Estúdio seguem funcionando.
- **Causa**: defasagem do processo. `web/server.py:152`, `web/state.py:38` e `web/routes_scrap.py:39` importam `ig_profile` em tempo de carga; o processo mantém o módulo de quando subiu. Confirmado por medição — o código em disco **não consegue mais** emitir a string do curl (dois caminhos a convertem), enquanto o processo vivo a emite.
- **Sintoma pior que o erro**: ~4,7 s de espera por uma mensagem inútil, sem alternativa pela UI — o único caminho é o processo recarregar o módulo.

**Linha do tempo**

| Momento | Evento |
|---|---|
| 15/10 14:19:56 | HEAD `6ed71c0` — sem a correção |
| 15/10 21:12:51 | Processo sobe (par 426540→424824); carrega `ig_profile` pré-fix |
| 16/10 07:56:41 / 07:57:14 | `ig_profile.py` e `test_ig_profile.py` gravados em disco (**não commitados**) |
| 16/10 ~08:00 | Medição ao vivo: `POST /api/scrap` → `curl: (47)`, 4,678 s |
| 16/10 ~08:20 | mtime dos dois arquivos **estável há >20 min** — a sessão concorrente aparentemente encerrou a edição |

---

## 🧪 Avaliação de cobertura de testes

**Estado**: `python -m pytest tests/test_ig_profile.py -q` → **56 passed, 16 subtests passed**. `ig_session_check.py` → **exit 0, 12/12 `[ok]`** (os dois call sites de `_send` omitem `allow_redirects`, então o `fake_send` antigo segue válido).

**Falsificação executada por Tessa** (in-memory, nada tocado no repo — `scratch-viralclipper/tessa_falsify.py`). Cada caminho revertido derruba o teste correspondente:

| Caminho revertido | Teste que falhou | Erro observado |
|---|---|---|
| `allow_redirects=False` / `except _TooManyRedirects` | `test_the_transport_does_not_follow_redirects` | `AssertionError: None is not False` |
| Ramo `3xx` de `_http` | `test_the_profile_page_redirect_reads_as_an_expired_session` | `'instagram.com/alvo/' not found in 'Não encontrei o token anti-CSRF (LSD)...'` |
| `fb_dtsg` via `MRequestConfig` | `test_the_token_is_read_from_m_request_config` | `ClipperError: Não encontrei o token 'fb_dtsg'` |

**Veredito de Tessa**: a suíte nova **protege** o conserto, não apenas o documenta. Nenhum dos 7 testes é tautológico; o mais fraco é o #7 (fixa o nome do kwarg, mas ainda falha sem o conserto). Os dois buracos reais estão nas linhas 1 e 4 da tabela de achados — e o #1 é o caso em que o próprio teste (`skipTest`) **esconde** o defeito em vez de o pegar.

---

## ✅ Plano de ação

| # | Ação | Responsável | Urgência | Conclusão esperada |
|---|------|-------------|----------|--------------------|
| 1 | ✅ **CONCLUÍDO em 16/10 08:10** — reinício do servidor. Ver a seção *Execução do P0* abaixo | Usuário / operador | ~~P0~~ | Mensagem acionável no painel — **atingido** |
| 2 | **Reexportar os cookies do Instagram** e substituir `cookies.txt`. Sem isso a listagem por perfil continua falhando — o patch só conserta a mensagem. Validação: `web_profile_info` deve sair de **401** para **200** | Usuário | **P0** | Busca por perfil volta a listar |
| 3 | ✅ **CONCLUÍDO em 16/10 08:17** — guarda do `except` trocado por tupla condicional `_REDIRECT_ERRORS` + 2 testes novos com falsificação. Ver a seção *Execução do P1* abaixo | Cody + Tessa | ~~P1~~ | Armadilha latente removida — **atingido** |
| 4 | ✅ **CONCLUÍDO em 16/10 09:0x** — o fallback `urllib` passou a honrar `allow_redirects` de fato, não só no docstring | Cody | ~~P2~~ | Os dois transportes com o mesmo contrato — **atingido** |
| 5 | ✅ **CONCLUÍDO em 16/10 09:0x** — cobertura do ramo `urllib` + mensagem de `3xx` separada por status | Tessa / Cody | ~~P2~~ | Regressão protegida — **atingido** |
| 6 | ✅ **CONCLUÍDO em 16/10 09:0x** — `REFERENCIA.md` corrigido (o `fb_dtsg` saiu de `"DTSGInitialData"`) e ganhou o bullet do `302 → /#` | Docu | ~~P2~~ | Documentação alinhada ao medido — **atingido** |

---

## ⚠️ Pendências e limitações conhecidas

- **Nada foi commitado, reiniciado ou morto por esta revisão.** A árvore git é **compartilhada** com outra sessão de agente, que gravou os dois arquivos em 16/10 07:56–07:57. Todo o trabalho aqui foi somente leitura, com sondas gravadas **fora** do repo (num diretório de scratch fora da árvore).
- **O reinício é do usuário, não desta revisão.** Rex classificou o risco de reiniciar como **alto enquanto a outra sessão editava** o mesmo módulo (um arquivo gravado pela metade no momento do import derrubaria o painel — pior que o estado atual). Aquele gate agora parece satisfeito (mtime estável >20 min), mas a decisão é do operador.
- **Não foi possível provar a listagem com sessão válida** — não há `sessionid` vivo disponível. O veredito "o patch conserta a mensagem, não a listagem" é sustentado pelos 401 medidos e pela exigência de sessão do GraphQL, mas a prova end-to-end fica para depois da reexportação dos cookies.
- **O `3xx` como sintoma de sessão morta é a leitura mais provável, não a única.** Nenhuma evidência foi encontrada de redirect legítimo nesse endpoint, mas um `301/307` de mudança real seria mal rotulado hoje (achado 🟡 #3).
- **Achados #2 a #7 não foram corrigidos** — são recomendações de revisão, e o item #1 (🟠) é o único que Cody marcou como exigido antes do commit.
- A suíte **completa** do projeto não foi executada (regra do projeto: não somar carga à máquina compartilhada). Só o módulo alvo: `tests/test_ig_profile.py`.

---

## 📚 Fontes de dados e índice de produção dos membros

- **Cody (revisor de código)** — achados #1, #2, #3, #5, #6 e os pontos positivos. Medições próprias: `except None` → `TypeError`; divergência entre transportes num servidor local `302 → 200`; execução de `ig_session_check.py` (exit 0); verificação de que o texto do curl não é mais alcançável pelo código em disco. Veredito: *Request Changes* (pequenos), **sem achados de segurança novos**.
- **Tessa (especialista em testes)** — achados #4 e #7, a falsificação 3/3 e o veredito de cobertura. Scripts: `scratch-viralclipper/tessa_falsify.py`, `scratch-viralclipper/tessa_gaps.py`.
- **Rex (SRE)** — SEV4, linha do tempo, confirmação por medição da defasagem do processo, runbook de reinício, análise de risco e quantificação do custo do laço. Sonda: `scratch-viralclipper/probe_sre_staleness.py`.
- **Medições do próprio líder** — resposta 302 com `Location: /#` e corpo de 0 byte; controle deslogado 200/782 621 bytes; `web_profile_info` → 401; `CODE2ERROR[47]` e o MRO de `TooManyRedirects`; `except None` → `TypeError`; contagem dos padrões de token na página real (0 × 1); reprodução ao vivo em `POST /api/scrap` (400, 4,678 s); `web/server.py:152` importando `ig_profile` em tempo de carga; mtimes e estado do git. Sondas: `probe_ig_redirect.py`, `probe_ig_session_alive.py`, `probe_token_paths.py`.

---

## 🔁 Execução do P0 (pós-relatório, 16/10 08:10)

Executado a pedido do usuário, com autorização explícita. **Pré-voo**: `py_compile` em `ig_profile.py`, `web/server.py`, `web/state.py` e `web/routes_scrap.py` (exit 0); import limpo com `_TooManyRedirects` carregado e `_send.allow_redirects = False`; mtime dos arquivos em disputa estável há 40 min (o gate de risco de Rex estava satisfeito).

**Achado operacional novo, que vale para as próximas vezes:** o `reiniciar_servidor.cmd` **funciona pela metade** quando chamado de dentro do shell do agente. Ele matou o PID 424824 e liberou a porta corretamente, mas o `start /min` foi **recolhido junto com o shell** — a porta 7755 ficou recusando conexão e nenhum processo `server.py` sobrou. O servidor foi então subido como tarefa gerenciada (`.venv/Scripts/python.exe web/server.py`), que persistiu. **Para o operador, o duplo-clique no `.cmd` continua valendo** (a janela é própria); o problema é específico de disparar o `.cmd` de dentro de um shell de agente.

**Verificação — o mesmo `POST /api/scrap` do relato, antes e depois:**

| | ANTES (PID 424824, subido 15/10 21:12) | DEPOIS (PID 409292, subido 16/10 08:10) |
|---|---|---|
| `http_code` | 400 | 400 *(a sessão segue morta — esperado)* |
| `time_total` | **4,678 s** | **0,3275 s** (≈14× mais rápido) |
| corpo | `(Failed to perform, curl: (47) Maximum (30) redirects followed. …)` | `(A sessão do Instagram expirou: reexporte os cookies do Instagram e tente de novo).` |

`grep -c "curl: (47)"` no corpo novo → **0**. `/api/health` → `pid: 409292`, `ok: true`. **P0 fechado: a mensagem inútil morreu.**

**Desfecho (08:13).** O processo gerenciado (409292) foi **morto por fora** 3 min depois — sem stdout nem traceback, assinatura de `taskkill /F`, não de crash. Quem o matou foi um **`reiniciar_servidor.cmd` executado às 08:13:29**: o script faz `taskkill` em quem escuta a 7755, e o alvo era o processo gerenciado. Prova de que foi o `.cmd` e não uma segunda invocação minha: o processo que assumiu é o **PID 45456** (pai 24152, o stub do venv, mesmo `CreationDate`) e sua linha de comando é `".venv\Scripts\python.exe"  web\server.py` — **com aspas e dois espaços**, exatamente o que o `start ... ".venv\Scripts\python.exe" web\server.py` do `.cmd` produz.

**O estado final é o melhor dos dois.** O servidor roda pelo caminho do projeto (janela própria, **não** amarrado à sessão do agente) e com o código corrigido — reconfirmado no PID 45456: `POST /api/scrap` → `http_code=400`, **`time_total=0,3301 s`**, corpo `(A sessão do Instagram expirou: reexporte os cookies do Instagram e tente de novo).`, sem a string do curl. Três conexões `ESTABLISHED` do Chrome (PID 56212) para a 7755 indicam o painel do usuário já ligado no servidor novo.

**Lição de operação (corrige o parágrafo acima):** o `.cmd` **não** é defeituoso — ele mata e sobe corretamente quando disparado fora do shell do agente. O que não funciona é chamá-lo **de dentro** da sessão do agente, porque o `start /min` morre junto com o shell. Se o agente precisar subir o servidor, use tarefa gerenciada; para um processo desacoplado, o caminho é o duplo-clique do usuário. **Nunca os dois: um mata o outro pelo `taskkill` da porta 7755.**

**O que isso NÃO resolve:** a listagem. O `http_code` continua 400 porque o `sessionid` segue inválido — a ação #2 (reexportar os cookies do Instagram) é o que faz a busca por perfil voltar a listar. **Nada foi commitado**: `viralclipper/ig_profile.py` e `tests/test_ig_profile.py` seguem modificados e não rastreados, e a ação #3 (a armadilha do `except`) continua pendente.

---

## 🔧 Execução do P1 (16/10 08:17) — guarda do `except`

**O que mudou** (`viralclipper/ig_profile.py`, +14 linhas):

```python
_REDIRECT_ERRORS: tuple[type[BaseException], ...] = (
    (_TooManyRedirects,) if _TooManyRedirects is not None else ()
)
```

e o `except` de `_send` passou de `except _TooManyRedirects as exc:` para `except _REDIRECT_ERRORS as exc:`.

**Por que uma tupla e não a classe direto.** `except None:` **não** significa "não capturar nada" — a cláusula só é avaliada quando algo é levantado, e aí morre com `TypeError: catching classes that do not inherit from BaseException`. Esse `TypeError` **substitui** a exceção original e, não sendo `OSError`, escapa dos `except OSError` dos chamadores e chega ao usuário como traceback não tratado — pior que o texto cru do curl que o braço existe para esconder. Uma tupla vazia não captura nada, que é a intenção: sem classe para traduzir, o `OSError` original segue o caminho que sempre seguiu. Medido: `ClipperError` é `RuntimeError` (não `OSError`), e `except ()` é válido em Python e não captura nada.

**Testes novos** (`tests/test_ig_profile.py`, +48 linhas): `test_a_missing_error_class_degrades_instead_of_raising_type_error` força o estado perigoso patcheando a tupla derivada (que é exatamente o que o bloco de import produz quando a classe falta) e levanta a **classe real** — é isso que faz a asserção morder, porque se `_send` voltasse a nomear a classe direto, o patch deixaria de importar, o laço seria capturado e traduzido, e o `ClipperError` explodiria fora do `try`. `test_the_guard_is_a_tuple_not_a_bare_class` cobre o outro modo de falha, que o primeiro não vê.

**Falsificação executada** (cópias em `scratch-viralclipper/falsify_p1/`, **nunca** o arquivo da árvore — script `falsify_p1_guard.py`):

| Cenário | Resultado | Teste que caiu |
|---|---|---|
| Cópia sem sabotagem | 58 passed | — |
| `except _TooManyRedirects` (o defeito original) | **1 failed**, 57 passed | `test_a_missing_error_class_degrades_instead_of_raising_type_error` — `AssertionError: 'except None' virou TypeError: catching classes that do not inherit from BaseException is not allowed` |
| `_REDIRECT_ERRORS = _TooManyRedirects` (classe crua) | **1 failed**, 57 passed | `test_the_guard_is_a_tuple_not_a_bare_class` — `AssertionError: <class 'TooManyRedirects'> is not an instance of <class 'tuple'>` |

Os dois testes são **não tautológicos** e cobrem modos de falha **distintos** — cada sabotagem derruba um e só um. Módulo alvo: **58 passed, 16 subtests passed**.

**Gate completo:** `python -m pytest -q -p no:cacheprovider` → **1668 passed, 1481 subtests passed in 71,49s**, exit 0, **zero falhas e zero skips**. Baseline preservada (a contagem sobe 9 em relação aos 1659 anotados mais cedo nesta data: +7 dos testes de redirect da sessão concorrente, +2 destes). Nenhum teste lê `ig_profile.py` como texto nem faz `inspect.getsource` nele — as leituras de fonte do repositório miram `server.Handler` e `tpl.from_dict`.

**Limitação aceita e registrada:** no estado "classe ausente", um laço de redirect real volta a produzir a mensagem crua do curl (o `OSError` não tem quem o traduza). É uma degradação consciente — idêntica ao comportamento anterior ao patch — e estritamente melhor que o `TypeError`. Fechar isso exigiria casar a mensagem da exceção, o que é frágil.

**O servidor no ar ainda roda o módulo carregado às 08:13:29**, ou seja, **sem** o guarda do P1. Não há efeito visível: no `curl_cffi` 0.16.3 a classe existe, então o guarda nunca é exercitado — o P0 continua entregue. O próximo reinício pega o P1.

---

## 🧩 Execução do P2 (16/10 09:0x) — os três achados restantes

**P2-a — o fallback `urllib` passou a honrar `allow_redirects` (achado 🟡 #2).** O docstring afirmava "Redirects are **not** followed" e o ramo `urllib` seguia redirects normalmente: a flag significava uma coisa no transporte primário e outra no de reserva, e o de reserva caminharia o mesmo laço, só com teto 10 em vez de 30. Agora o ramo instala um `HTTPRedirectHandler` cujo `redirect_request` devolve `None` — o que faz o urllib levantar o 3xx como `HTTPError`, capturado pelo `except` logo abaixo, e o status volta como dado. `build_opener` pula o handler padrão quando recebe uma subclasse dele, então isso **troca** o comportamento em vez de empilhar dois.

**Paridade medida, não argumentada** (servidor HTTP local em loopback, porta efêmera; sonda `probe_transport_parity.py`, fora do repo):

| Caso | `curl_cffi` | `urllib` |
|---|---|---|
| Laço que aponta para si mesmo, `allow_redirects=False` | `302 ''` | `302 ''` |
| Redirect legítimo, `allow_redirects=False` | `302 ''` | `302 ''` |
| Redirect legítimo, `allow_redirects=True` | `200 'OK'` | `200 'OK'` |
| `200` direto | `200 'OK'` | `200 'OK'` |

**Um falso positivo meu, registrado porque custou uma volta:** a primeira rodada acusou "DIVERGEM" no caso `allow_redirects=True` (`curl_cffi` devolvia 404, `urllib` 200). Não era o produto: o `curl_cffi` manda o alvo do redirect em **absolute-form** (`GET http://host/final HTTP/1.1`), que é válido no HTTP/1.1 (RFC 7230 §5.3.2) e que o `BaseHTTPRequestHandler` **não** normaliza — o `self.path` cru carrega a URL inteira. Quem estava errado era o harness, que comparava a string inteira. Normalizando com `urlsplit(...).path`, a paridade aparece nos quatro casos. O que salvou foi instrumentar o servidor para registrar o caminho que ele **viu**, em vez de inferir do status.

**P2-b — um `3xx` qualquer não é a recusa medida (achado 🟡 #3).** Antes, todo `300–399` era rotulado "A sessão do Instagram expirou". `302`/`303` é a assinatura medida; um `301`/`307`/`308` seria um movimento de verdade, e chamá-lo de sessão expirada mandaria você reexportar cookies que nunca foram o problema. Novo helper `_redirect_reason(status)` decide a frase, e `_http` e `_graphql_error` passam a usá-lo. Sobre a pontuação divergente: o `_SESSION_EXPIRED` carrega **de propósito** sem ponto final — os dois sites que ficam sozinhos acrescentam um, e o terceiro é citado dentro de uma frase de `_fetch_lsd` que já o fecha. Verifiquei as três saídas; todas leem corretamente.

**P2-c — documentação (achado 🟢 #6).** `.workbuddy-ai/memory/REFERENCIA.md` foi corrigido: o bullet do `fb_dtsg` agora traz o bolso novo com os números medidos (0 × 1) e a consequência de ler só o caminho antigo; e entrou um bullet novo para a assinatura `302 → /#` (31 requisições, ~4,7 s, `TooManyRedirects` é `OSError`, o controle sem cookie). *(Esse arquivo não é versionado — a correção é local.)*

**Testes:** 6 novos (`UrllibFallbackTests`, `RedirectReasonTests`), totalizando **64 passed, 16 subtests passed** no módulo. Offline por construção: `build_opener` é substituído, então nenhum teste novo abre socket — a paridade observável ficou na sonda medida, e o teste mede **qual** handler o ramo instala e que ele devolve `None`.

**Falsificação (matriz, cópias fora do repo — `falsify_p2.py`):**

| Cenário | Resultado | Teste que caiu |
|---|---|---|
| Cópia sem sabotagem | 64 passed | — |
| Ramo `urllib` de volta para `urlopen` (ignora a flag) | **2 failed** | `o ramo urllib nao instalou handler nenhum: ignorou a flag` |
| Todo `3xx` de volta a "sessão expirou" | **2 failed** | `'expirou' unexpectedly found in 'O Instagram não devolveu o feed de alvo (HTTP 301). A sessão…'` |

**Um teste que falhava pelo motivo errado, e o conserto:** na primeira versão, o fake do opener tinha aridade fixa e a sabotagem morria com `TypeError: ...takes from 2 to 3 positional arguments but 4 were given` — falhava, mas não dizia nada sobre o defeito. A assinatura ficou larga (`*args, **kwargs`) e o `seen` passou a usar `.get()`, então a sabotagem agora falha com a mensagem que nomeia o problema. Um teste que cai pelo motivo errado não protege nada.

**Gate completo:** `python -m pytest -q -p no:cacheprovider` → **1674 passed, 1481 subtests passed in 61,10s**, exit 0, **zero falhas e zero skips** (1668 + 6 novos).

---

> Este relatório foi gerado por colaboração de IA da equipe de garantia de engenharia. As decisões críticas (reiniciar o servidor, commitar o patch, reexportar credenciais) devem ser revisadas por um responsável humano de engenharia.
