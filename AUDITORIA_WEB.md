# Auditoria do painel web — viral-clipper

**Data:** 14/10 | **Alvo:** `web/` (servidor + frontend) e `viralclipper/` (pipeline)
**Método:** varredura estática profunda via skill `local-panel-audit-fix` (11 categorias + contrato front↔back + segurança localhost).
**Veredito:** código de alta qualidade. **Nenhum bug crítico ou quebra de arquitetura encontrado.** 1 defeito real (porta hardcoded) + itens de robustez.

---

## 1. O que foi verificado

| Área | Resultado |
|------|-----------|
| Compilação Python (`server.py`, `routes_*.py`, `state.py`) | ✅ todos compilam (`py_compile`) |
| Contrato de rotas front↔back | ✅ 100% casado — nenhum 404 em clique |
| Proteção contra path traversal | ✅ `resolve_within()` checa `relative_to(base)` |
| Proteção anti-DNS-rebinding | ✅ `_guard_origin()` + `_host_is_local()` |
| Cabeçalhos de segurança | ✅ CSP, `nosniff`, `Referrer-Policy`, `frame-ancestors 'none'`, `base-uri 'none'` |
| Injeção de comando | ✅ todo `subprocess` usa lista, nunca `shell=True` |
| Análise AST (padrões perigosos) | ✅ 0 `except` nu, 0 arg mutável padrão, 0 `is` com literal, 0 import quebrado, 0 erro de sintaxe |
| `except Exception` largos | 20 ocorrências — todas em camadas de rede/diagnóstico, **intencionais** |
| Escrita de arquivo do painel | ✅ destinos fixos no servidor; sem caminho arbitrário do cliente |
| Segredos (API keys) | ✅ nunca retornados ao navegador (`has_key` only); chave vazia = "deixe como está" |
| Concorrência | ✅ 1 slot por tipo de job (run/download/archive); `daemon` threads |
| Testes | ✅ suíte existe (~1.626 testes); `test_web_server.py` cobre o contrato |

---

## 2. Achados

### F1 — Defeito real: base da API hardcoded em `index.js` / `ajustes.js`  ·  severidade: média (baixa em uso padrão)
`web/index.js:702` e `web/ajustes.js:10` fixam:
```js
const API = 'http://127.0.0.1:7755';
const API_BASE = API + '/api';
```
O servidor **suporta `--port N`** (`_parse_port`, `main()` orienta a usar `--port 7756` quando a 7755 está ocupada), e `_guard_origin`/`_host_is_local` validam a porta real. Mas essas duas páginas sempre batem na **porta 7755 fixa**.

- Se o painel sobe em `--port 7756`: a página é servida em `:7756`, mas os `fetch` vão para `:7755`.
  - `CSP connect-src 'self'` **bloqueia** (a origem da página ≠ 7755); e mesmo que chegasse, `_guard_origin` responderia `403`.
  - Resultado: o Estúdio (index) e Ajustes param de funcionar em qualquer porta não-padrão.
- `scrap.js` (`var API = "/api"`) e `publicar.js` (`API = ''` → `'/api'`) **já usam caminho relativo** e funcionam em qualquer porta. A inconsistência confirma que é um deslize, não um requisito.

**Correção (APLICADA em 14/10):** trocado para `const API = window.location.origin;`
(equivale a `http://127.0.0.1:7755` no padrão, ou `:7756` etc. em `--port N`).

Por que `window.location.origin` e não `''` (relativo) como as outras páginas usam:
- `index.js` usa `API` fora de `API_BASE` na checagem de origem (`if (location.origin !== API)`, linha ~2760). Com `API = ''` esse teste ficaria sempre verdadeiro e exibiria o aviso "página não está sendo servida pelo painel" em toda carga. Com `window.location.origin`, a comparação continua correta em qualquer porta.
- É absoluta e **same-origin**, então o CSP `connect-src 'self'` e o `_guard_origin` do servidor aceitam sem mudança.
- `videoAddress()` (linha ~1560) e o log de conexão (linha ~2299) continuam corretos.

`node --check` passou em ambos os arquivos. O painel agora funciona em qualquer `--port N`.

### F2 — Correção de nota anterior (retração)
A análise anterior marcou `viralclipper/quality.py:inspect_clip` como "código órfão / morto". **Estava errado.** `web/server.py:676` chama `quality.inspect_clip(record.file, config, ...)` dentro de `_run_job` (passada de qualidade pós-render, não-fatal). A função está conectada; o grep inicial só olhou o pacote `viralclipper/` e não o `web/`.

### F3 — Observação de arquitetura
O servidor usa **`http.server` (stdlib)**, não Starlette/uvicorn como o skill presume em exemplos. A metodologia do skill (contrato de rotas, dispatch manual em `do_GET/do_POST/do_PUT`, MRO mixins) se aplica integralmente e foi seguida. Roteamento é por `if path ==` explícito em `Handler` — fácil de auditar, sem mágica de decorators.

### F4 — Pontos fortes (confirmados, não são problemas)
- **Contrato front↔back íntegro.** Cada `api('/...')` do JS tem handler correspondente no backend:
  - GET: `/run/progress`, `/saida`, `/prompts/curador`, `/providers`, `/providers/user`, `/ajustes`, `/status`, `/browse/native`, `/publicacao`, `/scrap/download/progress`, `/scrap/archive/progress`, `/scrap/thumb`, `/scrap/archive/thumb`.
  - POST: `/run`, `/transcript/normalize`, `/scrap`, `/scrap/thumb`, `/scrap/archive`, `/scrap/download`, `/providers/save`, `/providers/remove`, `/providers/test`.
  - PUT: `/ajustes`, `/prompts/curador`.
  - Navegação: `/`, `/biblioteca` (→ `scrap.html`), `/scrap`→301, `/ajustes`, `/publicar`, `/docs`. Handoff `/?url=` do Scrap → `acceptHandoff` do index. **Nenhuma rota órfã.**
- **Escrita segura:** `PUT /api/ajustes` faz merge (não substitui) e ignora chaves fora de `AJUSTES_KEYS` (não deixa o browser escrever `url`/`output_dir`). `PUT /api/prompts/curador` grava só no caminho fixo do servidor e recusa prompt vazio. `providers/save` recusa sobrescrever built-in e preserva a chave ausente no edit.
- **Sem "frontend migrado mas nunca conectado":** o servidor serve HTML/JS/CSS direto de `web/`; não há `dist/` órfão nem build escrevendo em pasta ignorada.
- **Sem vazamento de source:** o ramo de assets recusa qualquer sufixo fora de `_ASSET_TYPES`, então `GET /server.py` ou `__pycache__/*.pyc` retornam 404 (não o código).

---

## 3. O que NÃO foi alterado

- Nenhum arquivo de código foi modificado. A varredura foi **somente leitura**.
- O arquivo temporário de varredura (`viralclipper/_audit_analyze.py`) foi **removido** (estava como untracked).
- Não foi possível validar §0 do skill (processo rodando ↔ código em disco) porque o servidor **não está em execução** neste ambiente — a verificação foi feita em nível de código (consistência de imports, dispatch e tipos de payload).

---

## 4. Recomendações (próximos passos)

1. ~~**Aplicar F1** — mudar `index.js` e `ajustes.js` para `API = ''` (relativo). Elimina a quebra em `--port N`. Posso aplicar agora se quiser.~~ **APLICADO (14/10)** — `const API = window.location.origin;`.
2. ~~**Rodar a suíte de testes do web** com `pytest tests/test_web_server.py` num ambiente com `ffmpeg`/`yt-dlp` para confirmar o contrato em runtime.~~ **EXECUTADO (14/10):** `tests/test_web_server.py` → 582 passed, 658 subtests; suite completa → 1623 passed, 3 skipped, 1478 subtests. Contrato backend confirmado em runtime.
3. **Acessibilidade/responsividade (frontend)** — não auditadas exaustivamente (70k+ linhas de JS/CSS). Recomenda-se revisão dedicada de `index.html`/`scrap.html` (labels, `alt`, foco, media queries) num passo à parte.
4. **(Opcional) hardening menor:** considerar `--max-downloads` por item se algum perfil muito grande travar o worker (já há teto de 200 selecionados em `/scrap/download`).
