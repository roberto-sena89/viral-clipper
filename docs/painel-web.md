# O painel web

Documento **vivo**. Descreve o contrato do painel local — páginas, rotas, CSP e
o que cada arquivo pode ou não escrever. Números aqui são os que a suíte trava;
se divergirem do código, conserte o arquivo.

## Subir

```powershell
python web/server.py            # 127.0.0.1:7755
python web/server.py --port 7756
```

- O servidor é `http.server` da stdlib. O processo **guarda o código de quando
  subiu**: editar um `.py` (rota nova, handler novo) exige **reiniciar** — a rota
  nova responde 404 até lá.
- `web/*.html|css|js` **não** exigem reinício: são lidos a cada request.
- Os arquivos estáticos **não** mandam `Cache-Control` (os assets versionados
  mandam `max-age=300` + `ETag`). Se você vir "versão antiga", não é cache —
  confira de qual diretório o processo está servindo.

## As páginas

O rail é renderizado por `renderRail` em `comum.js`, a partir de `RAIL_PAGES`.
Cada página tem dois `<ul data-rail-list>` vazios; o JS preenche.

| Página        | Rota         | Arquivo        | `h1`                  |
| ------------- | ------------ | -------------- | --------------------- |
| Estúdio       | `/`          | `index.html`   | `#studio-title`       |
| Publicar      | `/publicar`  | `publicar.html`| (ver `publicar.js`)   |
| Ajustes       | `/ajustes`   | `ajustes.html` | `#ajustes`            |
| Biblioteca    | `/biblioteca`| `scrap.html`   | `#scrap-title`        |

Aliases `.html` (`/index.html`, `/ajustes.html`, …) e o redirect do endereço
antigo (`/scrap`) existem por compatibilidade. **Página nova** = entrada em
`RAIL_PAGES` **e** rota em `do_GET` — as duas, senão a página existe sem menu ou
o menu aponta para um 404.

## Rotas

Contagem medida pelos dois instrumentos que o `ApiNamespaceTests` usa (AST + o
`rotas()` do teste). Estado atual:

| Verbo    | Total | Páginas | Dados (`/api/`) |
| -------- | ----- | ------- | --------------- |
| `do_GET` | 27    | 11      | 16              |
| `do_POST`| 9     | 0       | 9               |
| `do_PUT` | 2     | 0       | 2               |

**A regra do prefixo:** `/...` é página (HTML, o que aparece na barra de
endereço); `/api/...` é dado e ação (JSON, chamado só pelo JS que este servidor
entrega). **Nenhuma rota que devolve JSON mora na raiz** — `ApiNamespaceTests`
reprova se aparecer uma.

Duas armadilhas de roteamento, ambas já custaram caro:

1. **`path != "..."` também nomeia uma rota.** `if path != "/api/run": 404` é
   *como* `/api/run` existe; ignorar o `!=` subconta as rotas de `do_POST`.
2. **Endpoint POST tem de vir ANTES do guard `if path != "/api/run":`** em
   `do_POST`. Depois dele, vira 404 silencioso — sem teste acusando, a menos que
   o teste bata no socket.

## Segurança

O modelo de ameaça é **uma pessoa, em loopback**. Dentro dele:

- **Guarda anti-DNS-rebinding** (`_guard_origin`) no topo de `do_GET`, `do_POST`
  e `do_PUT`. A porta vem do **socket real** (`self.server.server_address[1]`),
  não de uma constante — é o que faz a guarda valer também em `--port 7756`.
- **CSP** em toda resposta, JSON incluído: `connect-src 'self'`,
  `frame-ancestors 'none'`, `base-uri 'none'`, `script-src 'self'`. Sem CORS.
- `X-Content-Type-Options: nosniff` e `Referrer-Policy: no-referrer`.
- Sem autenticação — decisão correta para ferramenta local em `127.0.0.1`.

> **A ordem da guarda importa.** Um `Host` estranho com corpo inválido devolve
> **403**, não o **400** da rota: prova que a guarda roda *antes* da rota. Isso é
> travado por teste de socket em `tests/test_web_integration.py`.

## `ajustes.toml`

O painel de Ajustes lê e escreve `ajustes.toml` (no `.gitignore`). Contrato:

- **Escrita atômica** (`_write_atomically`: temp + `replace`). Um travamento no
  meio da escrita não deixa o arquivo truncado.
- **Arquivo malformado não é engolido.** `_load_ajustes` devolve
  `(settings, malformed)`; a resposta de `GET /api/ajustes` traz o motivo em
  `malformed` e a página **avisa** em vez de resetar. Sem isso, a página dizia
  "nada salvo ainda" e o próximo save sobrescrevia as 27 chaves com os defaults —
  perda silenciosa e permanente.
- **`AJUSTES_KEYS` = 27 chaves** (a superfície editável do painel). O
  `ClipConfig` do motor tem 88 campos — o painel expõe um subconjunto.
- **Campo cujo valor só é lido sob condição TEM de desabilitar sob a condição**
  (`<fieldset disabled>`): um controle que parece ativo e não tem efeito é pior
  que um controle ausente. Travado por `CuradorFieldsFollowTheSwitchTests`.

## A aba Publicar

Lê o **mesmo** `output/clips.json` da galeria e mostra, por clip: miniatura 9:16,
headline, nota, trecho, duração, termos de gancho e a **legenda pronta para
colar**. Três decisões que valem registro:

1. O caminho do clip é **relativizado no servidor** — `/api/clips/` recusa o que
   sai de `output/`, então entregar o caminho absoluto seria entregar um endereço
   que o próprio servidor nega.
2. A legenda fica **visível ao lado do botão**: é o que salva a cópia quando o
   clipboard é negado (permissão, `http` puro).
3. **A página não escreve nada** — nenhum POST, PUT ou upload. O contrato está
   escrito na página e travado por teste.

## CSS e JS

- **Sem bundler.** Tokens duplicados entre arquivos são de propósito.
- **Escopo por página** via `[data-page="..."]` no `<body>`. Em `@media`, a regra
  base `X > .classe` vence `.outra-classe` — cuidado ao sobrepor.
- **Duplicação real medida:** `index.css` × `scrap.css` (18 seletores iguais).
  `shared.css` tem 9, e `scrap ∩ shared = 0`. É risco de divergência, não bug de
  aparência — a Biblioteca já renderizou índigo enquanto as outras duas
  renderizavam magenta.
- **`[hidden]` sozinho perde para uma classe com `display`.** Precisa de
  `[hidden] { display: none !important; }` ou um seletor `[hidden]` explícito
  por elemento (o autor vence o user-agent).
- **`aria-describedby` nunca aponta para região viva.** A dica tem texto já no
  HTML; apontar para um `#status` que nasce vazio faz o teste reprovar.

## Defeito aberto

**A porta está fixada no código em duas das páginas.** `web/index.js` e
`web/ajustes.js` fazem `const API = 'http://127.0.0.1:7755'`. Com o CSP
`connect-src 'self'`, subir em `--port 7756` faz o `fetch` ir para a 7755 e ser
recusado **antes** de qualquer coisa: Estúdio e Ajustes quebram. `publicar.js` e
`scrap.js` usam base relativa (`''` / `/api`) e sobrevivem.

- **Cura:** derivar a base de `location.origin` nos dois arquivos (uma constante
  em `comum.js`).
- **Critério de aceitação:** subir em `--port 7756`, abrir a página e as três
  carregarem dados sem erro de console. O teste de regressão recusa um
  `http://127.0.0.1:` literal em `web/*.js`.

## Ver também

- [`arquitetura.md`](arquitetura.md) — o motor por trás do painel.
- [`historico/auditoria-2026-10-07.md`](historico/auditoria-2026-10-07.md) — a
  auditoria de UX/UI completa, com os fluxos avaliados e as lacunas de
  acessibilidade medidas no dia.
