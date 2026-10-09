# Revisão visual e de arquitetura front-end — painel Viral Clipper

**Data**：2026-10-09
**Workflow**：1 (revisão de código) adaptado a revisão de design/front-end, com o corpo do workflow 2 (arquitetura)
**Participantes**：Cody (code-reviewer) · Archi (architect) · Tessa (testing-expert) · coordenado por Zhen (engineering-director)

---

## 📌 TL;DR (resumo executivo)

- **O painel está em melhor forma do que o pedido sugere.** Existe um sistema de design de verdade
  — escala tipográfica de 10 degraus, tokens de raio/curva/movimento, papéis de cor semânticos —
  e uma suíte de **1525 testes** com contratos de DOM, contraste, escala e alvo de toque.
  O problema **não é ausência de sistema; é uma migração que parou no meio.**
- **Dois defeitos reais de contraste, ambos com causa raiz provada:** um `<a>` gerado por JS que
  escapou do reset (**1,93:1**, `publicar.js:124`) e o rótulo do swatch pintado com a cor do
  *vídeo* do preset sobre o cromo escuro (**1,02:1**, `comum.js:86-97` + presets `candy`/`pop-box`).
- **Nenhum transbordo horizontal** nas 12 combinações medidas (1440/1024/390) — conquista a não regredir.
- **A lacuna mais real é o espaçamento:** 35 valores distintos e **nenhum token** (`--s-*`,
  `--space-*`, `--gap-*` não existem). Ao contrário da tipografia, aqui não há sistema nenhum.
- **O risco da refatoração não é visual, é de teste:** **9 asserções fixam string exata de CSS**
  (todas em `test_web_server.py`). Trocar `padding: 10px 14px` por tokens mantém o comportamento e
  quebra o teste. E `.chip strong` está preso por teste enquanto `.chip` está morto no DOM.
- **`prefers-reduced-motion` existe, mas é ad hoc — e já furou duas vezes.** São 21 blocos
  elemento-a-elemento, sem regra global, e **duas animações ficaram sem guarda nenhuma**: o
  `.toast` do `index.css` e o `.menu-btn .mb-caret` do `shared.css` (a seta gira 180° mesmo com o
  sistema pedindo menos). Verificado duas vezes, independentemente.
- **Distribuição de severidade**：🔴 3 · 🟠 5 · 🟡 7 · 🟢 4 — **19 achados** (20 instâncias; o #5 cobre duas)
- **Bloqueantes**：2 (os dois defeitos de contraste). Os demais são não bloqueantes.

---

## 🎯 Cartão de conclusão

| Item | Conteúdo |
|------|----------|
| Avaliação geral | 🟡 **Aprovado com condições** — a arquitetura está sã; a migração está incompleta e há 2 defeitos de contraste |
| Bloqueantes | **2** (Defeito A: `1,93:1`; Defeito B: `1,02:1`) + 1 bug latente de `!important` |
| Itens de ação | **9** (2 P0, 3 P1, 4 P2) |
| Fases de migração propostas | **5** (+1 fase deferida de reestruturação de páginas); a Fase 2 é dividida em 2A (só `shared.css`, começa já) e 2B (por página) |
| ADRs | **7** |
| Escala proposta | tipografia **8 degraus** (piso 12px) · espaço **8 degraus** base-4 · cor **24 tokens** |
| Próximo passo | Corrigir os 2 defeitos de contraste (2 commits pequenos) → Fase 0/1 (tokens aditivos, pixel-zero) |

---

## 🔍 Achados da revisão (ordenados por severidade)

| # | Sev | Categoria | Arquivo:linha | Problema | Correção | Fonte |
|---|-----|-----------|---------------|----------|----------|-------|
| 1 | 🔴 | Contraste / reset incompleto | `publicar.js:124` | `<a href="/ajustes">Prompt do curador</a>` injetado por JS **sem classe**; não existe regra base `a { color }` em folha nenhuma → o navegador aplica `#0000EE` sobre `rgb(17,22,31)` = **1,93:1**. (O mapa inicial apontava `publicar.html`; a origem real é o JS.) | Dar classe ao link e criar **uma** regra base `a { color: var(--accent-soft) }` no `shared.css` — é reset de 4 páginas | Cody |
| 2 | 🔴 | Contraste / cor de vídeo em UI | `comum.js:86-97`, `:180-183`, presets `:54` e `:62` | `captionTextStyle()` copia a paleta **do vídeo** do preset para o rótulo `.sel-title`. `candy` e `pop-box` têm `color:'#141414'` → quase-preto sobre cromo escuro = **1,02:1** | Não aplicar `captionTextStyle` ao rótulo; o nome do preset é texto de UI. O visual vai num `.cap-demo`, como já é feito nas opções | Cody |
| 3 | 🔴 | Guerra de `!important` / bug latente | `index.css:1514` × `:1908` | `#btn-run-side, #btn-plan { margin-bottom:8px !important }` (1,0,0) vence `.execution-actions .btn { margin:0 !important }` (0,2,0) → o `margin:0` é **código morto** e sobra `margin-bottom:8px` sobre um `display:grid; gap:8px` | Remover o `margin-bottom` de `#btn-plan` e os dois `!important` | Cody |
| 4 | 🟠 | Guerra de `!important` | `index.css:1513` × `:1876` | Dois `!important` na MESMA propriedade de `#progress-label`; o segundo venceria por especificidade mesmo sem `!important` | Apagar a regra 1513; a 1876 perde os dois `!important` | Cody |
| 5 | 🟠 | Acessibilidade / movimento — **2 instâncias** | `index.css:944` e `shared.css:177` | (a) `.toast { animation: toastIn 260ms }` **sem** guarda de movimento reduzido: as 9 media queries de `index.css` não citam `.toast`, só `scrap.css:846` desliga → em Estúdio/Ajustes/Publicar o toast entra animado com `prefers-reduced-motion: reduce`. (b) `.menu-btn .mb-caret { transition: transform var(--transition) }` **não tem guarda em folha nenhuma** — as guardas existentes cobrem `.pressable`, não o caret → a seta gira 180° mesmo com o sistema pedindo menos. Verificado por mim e pela Tessa, independentemente | Adicionar ambos ao bloco reduced-motion do `shared.css`. A regra global da ADR-007 cobre os dois. O `.mb-caret` vira o **caso positivo** do teste novo de cobertura | Cody / Tessa |
| 6 | 🟠 | Valor mágico / movimento | `index.js:125`, `publicar.js:39`, `scrap.js:32` | A transição de saída do toast é escrita **inline** em JS, 3 cópias: `el.style.transition = 'opacity 300ms'`. Inline vence CSS e **não** é alcançada por nenhum `prefers-reduced-motion` | Mover para CSS (`.toast.saindo`), o JS só troca classe | Cody |
| 7 | 🟠 | Arquitetura do CSS | `index.css:113` + `scrap.css:63` vs `shared.css:185` | `@keyframes railMenuIn` é declarado idêntico nas duas folhas de página, mas quem consome é o **shared** — dependência invertida: uma folha que esqueça o keyframe faz a animação sumir em silêncio | Subir o keyframe (e o par reduced-motion) para o `shared.css`, ao lado do consumidor | Cody / Archi |
| 8 | 🟠 | Duplicação página↔página | `index.css` × `scrap.css` | **20 seletores** redefinidos no nível de topo (`.btn`, `.card`, `.log-box`, `.toast`, `.toast-zone`, `.chip`, `body{padding-left}`, `@keyframes railMenuIn`…), e **~26** quando a varredura desce para dentro de `@media` — dos quais **2 são deliberados** (as guardas `.pressable` e `.rail-dropdown .rail-menu`, que precisam existir nas duas folhas) e o resto é dívida. O teste de duplicação cobre shared↔página e **não** página↔página, então isto passa sem aviso | Estender a extração para o `shared.css` onde os valores JÁ coincidem; e criar o teste página↔página **que desça para dentro de `@media`/`@layer`** antes da Fase B | Cody / Tessa |
| 9 | 🟡 | Dependência de ordem de carregamento | `shared.css` topo + 4 `<head>` | O `shared.css` **precisa** vir antes do CSS de página, senão a `@media (max-width:920px)` que esconde o rail perde. Preso por teste, mas é acoplamento frágil de cascata | Manter; resolver por `@layer` na Fase 2 (o teste vira um de camadas) | Cody / Archi |
| 10 | 🟡 | Valores mágicos de tipografia | ver cadeias abaixo | 43 tamanhos computados; os "estranhos" têm **três origens provadas**: `em` aninhado, `vw` de `clamp()`, e literais `rem` em `SEM_DEGRAU` | Ver "cadeias provadas" | Cody |
| 11 | 🟡 | Valores mágicos de espaço | `index.css:1076,1980,1982`; `scrap.css:866` | `gap: clamp(22px,4vw,52px)` etc. geram os avulsos medidos (`40.96` = 4vw de 1024). **Não são escala — são vw** | Se o espaço virar token, documentar que os `clamp(vw)` são responsivos de propósito e ficam fora | Cody |
| 12 | 🟡 | CSS morto (provado por grep) | ver tabela abaixo | `.brand`, `.brand small`, `.header-inner > .brand*`, `.hero .badge-row`, `.chip`, `.chip strong`, `.chip-row`, `.btn-ghost` — nenhum aparece no markup nem no JS. ~15 linhas | Apagar | Cody |
| 13 | 🟡 | Estilo inline no markup | `index.html:454,513,619`; `scrap.html:270,329` | 5 `style="…"` no HTML | Trocar por classes | Cody |
| 14 | 🟡 | Estilo inline no JS | `scrap.js:1221-1224`, `:1553-1595` | JS escreve `color`/`fontFamily`/`fontSize`/`marginLeft` inline, duplicando tokens que já existem | Classe única + CSS | Cody |
| 15 | 🟡 | Acoplamento JS↔markup por `#id` | `index.css` + `scrap.css` | ~**26 ids** usados como gancho de estilo; `index.css` unifica **16** deles. Renomear qualquer um é par HTML+JS+teste | Migrar estilo por id para classe/`[data-*]` onde não for `getElementById` inevitável | Cody / Tessa |
| 16 | 🟢 | Drift comentário↔código | `index.css:2215-2218` | O comentário diz que o ícone do `h2` "virou SVG", mas `index.html:133,292,364,476` **ainda usa glifos** `▶ ✦ ✚ ✎` (Ajustes/Publicar usam SVG) | Converter os 4 ou corrigir o comentário | Cody |
| 17 | 🟢 | A11y — **falso positivo meu** | `index.html:133,292,364,476` | Os glifos estão em `aria-hidden="true"` → **não** entram no nome acessível. Meu achado inicial estava errado | Nada a fazer | Cody |
| 18 | 🟢 | Alvo de toque — **rebaixado** | links de 15px | São links **de prosa inline**; a WCAG 2.5.8 tem exceção para link em bloco de texto. Os alvos **de controle** já chegam a 24/44px e têm teste | Não inflar; documentar a exceção | Cody |
| 19 | 🟢 | Código órfão menor | `scrap.css:844` | `.toast.err, .toast.bad` — o alias `.bad` é mantido "para não quebrar chamada antiga", mas não há chamada `'bad'` no JS | Confirmar e remover | Cody |

### Cadeias provadas dos "valores mágicos"

| Valor medido | Cadeia | Evidência |
|---|---|---|
| `11.776px` | `0.92em` × `--text-caption` (0.8rem=12.8) | `index.css:2277` dentro de `.section-sub-muted` (`:2270`) |
| `12.42px` | `0.92em` × `--text-body-sm` (0.84375rem=13.5) | `index.css:2277` dentro de `.section-sub` (`:1367`) |
| `13.34px` | `0.92em` × `--text-cta` (0.90625rem=14.5) | `index.css:2016` dentro de `.ajustes-abertura .section-sub` (`:2009`) |
| `12.032px` | `0.94em` × `--text-caption` (0.8rem=12.8) | `scrap.css:315` dentro de `.auth-note` (`:313`) |
| `32.768px` | `3.2vw` de 1024 | `index.css:1113` `.studio-welcome h1 { clamp(1.8rem, 3.2vw, 2.65rem) }` |
| `34.816px` | `3.4vw` de 1024 | `index.css:2003` `.ajustes-abertura h1 { clamp(1.85rem, 3.4vw, 2.55rem) }` |
| `41.984px` | `4.1vw` de 1024 | `scrap.css:889` `.scrap-welcome h1 { clamp(2rem, 4.1vw, 3.2rem) }` |

**Conclusão:** o `em` aninhado é o que atravessa o piso de 12px — **7,68px computado é menor que o
menor token existente (11px)**. Valor relativo não respeita o piso que o sistema promete.

### Estado medido (4 páginas × 3 larguras, 1440/1024/390)

| Dimensão | Estúdio | Ajustes | Biblioteca | Publicar |
|---|---|---|---|---|
| Transbordo horizontal | 0 | 0 | 0 | 0 |
| Contraste — menor razão | 6,03:1 | 6,08:1 | 6,08:1 | **1,93:1** |
| Falhas de contraste | 0 | 1 | 0 | 1 |
| Tamanhos de fonte distintos (computados) | 27 | 23 | 22 | 19 |
| Espaçamentos distintos | 26 | 24 | 24 | 22 |
| `H1` / `H2` / `H3` | 1/7/0 | 1/5/1 | 1/3/0 | 1/2/0 |

**União das 4 páginas: 43 tamanhos computados · 35 espaçamentos · 58 cores de texto.**

**Métricas de dívida declarada:** 271 declarações de `font-size` (56 via `var(--text-*)`, 215 com
literal `rem`, **52 valores literais distintos**) · 16 `!important` (13 em `index.css`, 3 em
`scrap.css`; 1 dentro de comentário) · 21 blocos `prefers-reduced-motion` (index 9, scrap 9,
shared 3) · 24 `transition:` + 21 `animation:` · 21 blocos `@keyframes`.

---

## 🏗️ Avaliação de impacto arquitetural

O que já existe (e que o pedido original supunha ausente): `web/shared.css:256-274` define a escala
`--text-*` de 10 degraus com `-lh` pareado; há `--radius-sm|md|lg` (8/14/22), `--ease-spring`,
`--ease-out-expo`, `--motion-fast` e os papéis de cor `--text-ink*` / `--text-primary|secondary|muted`.
**Archi reconhece o sistema existente e propõe terminá-lo, não substituí-lo.**

### Camadas propostas

```
@layer reset, tokens, base, primitives, components, pages, utilities;
```

| Camada | Arquivo | Conteúdo |
|---|---|---|
| `reset` | `shared.css` topo | `box-sizing`, normalização de `img/svg/button/input` |
| `tokens` | **`tokens.css` (NOVO)** | `@property` + `:root` (cor, tipo, espaço, raio, sombra, movimento, z) + a regra global de reduced-motion |
| `base` | `shared.css` | seletores de elemento (`body`, `a`, `h1..h4`, `code`, `:focus-visible`) — inclui o `a{color}` que hoje **não existe** (Defeito A) |
| `primitives` | `shared.css` | `.btn`, `.field`, `.card`, `.pill`, `.tag`, `.jump-links`, `.rail-item`, `.sr-only` |
| `components` | `shared.css` | `.site-footer`, `.rail`, `.rail-dropdown`, `.toast`, `.log-box`, `.select-trigger`, **+ os `@keyframes` que hoje moram nas páginas** |
| `pages` | `index.css`, `scrap.css`, `publicar.css` | o que é de UMA página; vence `primitives`/`components` por arquitetura, não por ordem de `<link>` |
| `utilities` | `shared.css` | `.mudo`, `.mini`, `.sr-only`, espaçadores |

**Ordem de `<link>` (corrigida depois da revisão de código):**

```html
<link rel="stylesheet" href="/shared.css?v=…">    <!-- continua PRIMEIRO -->
<link rel="stylesheet" href="/tokens.css?v=…">    <!-- NOVO, segundo -->
<link rel="stylesheet" href="/index.css?v=…">     <!-- ou scrap.css / +publicar.css -->
```

> **Por que não "tokens.css primeiro":** `tests/test_web_server.py:2145` exige `shared.css` como
> **primeira** folha — invertê-la reintroduz o bug do rail no mobile (medido: 100/156 elementos).
> A ordem de folha não afeta `var()`; a declaração `@layer` vai no topo de `shared.css`, e o teste
> fica verde sem ser tocado. Na Fase 2 ele vira um teste de *camadas*.

**Teto de 5 arquivos.** Cada `<link>` a mais é um round-trip e um ponto de sincronização do `?v=`
numa árvore compartilhada com outra sessão. `tokens.css` é um arquivo **novo** — logo, o único que
a outra sessão não está editando. `primitives.css` separado e `tokens-color.css` foram rejeitados.

### Escalas propostas

**Tipografia — 8 degraus, piso rígido de 12px.** Razão 1.25 nos títulos, comprimida (~1.07) no
corpo, porque a UI é densa em rótulo e número. Cobre os **43 valores medidos sem sobrar nenhum**;
os **11 abaixo de 12px sobem ao piso**.

| Token | Valor | Papel |
|---|---|---|
| `--fs-xs` | `0.75rem` (12px) | metadado, selo, contador, monograma |
| `--fs-sm` | `0.8125rem` (13px) | dica de campo, nota |
| `--fs-base` | `0.875rem` (14px) | corpo, rótulo, input, item de nav |
| `--fs-md` | `1rem` (16px) | leitura, subtítulo de card |
| `--fs-lg` | `1.25rem` (20px) | título de card |
| `--fs-xl` | `1.5rem` (24px) | título de seção |
| `--fs-2xl` | `clamp(1.75rem, 1.5rem + 1vw, 2.25rem)` | título de página |
| `--fs-3xl` | `clamp(2.25rem, 1.6rem + 2.2vw, 3rem)` | número-herói |

**Espaço — 8 degraus base-4** (`2, 4, 8, 12, 16, 24, 32, 48`). Base 4 e não 8 porque o piso real é
denso (`1px`×48, `7px`×30, `9px`×51) e a UI precisa de 4 e 12px para chip, pill e gap de ícone.
Cobre os 35 → 8; os resíduos de `em` (`36.864`, `40.96`) desaparecem.

**Cor — 11 cruas + 13 papéis (24 tokens)** para substituir 176 hex e 58 cores de texto. Os seis
cinzas que fazem o mesmo trabalho colapsam em 3 papéis. **Exceção crítica:** a família de amostras
(`rgb(255,255,0)`, `rgb(0,229,255)`, `rgb(255,45,45)`, `rgb(0,255,136)`) é **conteúdo**, não tema —
tokenizá-la criaria "tokens" que o usuário sobrescreve. Fica onde nasce.

**Raio:** manter 8/14/22 (os valores atuais). Arredondar para 8/12/18 é churn visual sem ganho e
destrói a verificação pixel-zero da Fase 2. **O protótipo foi corrigido para 8/14/22.**

### Plano de migração (5 fases + 1 deferida)

| Fase | O que muda | Visual | Reversível por | Aceite |
|---|---|---|---|---|
| **0** | Snapshot das 12 capturas; criar `tokens.css` (sem `<link>`) | zero | apagar o arquivo | suíte verde |
| **1** | `<link tokens.css>` nas 4 `<head>` (1 linha cada) | **zero** | remover 4 linhas | diff de pixel = 0 nas 12; `test_line_endings` verde (LF) |
| **2A** | **Só `shared.css`** (seguro, pode começar já): receber `@keyframes railMenuIn` + `.mb-bars/.mb-caret` + `header .nav-actions .btn`; adicionar a regra global de reduced-motion; limpar os `!important` que moram nele (**0**) | **zero** | `git revert` do arquivo | 12 capturas idênticas; suíte verde sem tocar em teste |
| **2B** | **Por página**: envolver `index.css`/`scrap.css`/`publicar.css` no seu `@layer`; apagar as declarações que subiram na 2A; esvaziar os 16 `!important` (13 index + 3 scrap) um a um | **zero** | `git revert` por arquivo | 12 capturas idênticas **antes** de mexer em qualquer regra; teste página↔página (que desce em `@media`) verde |
| **3** | `font-size`→`--fs-*`; espaço→`--sp-*`; hex→token; corrigir os 2 defeitos de contraste; movimento→`--m-*` e remover os 21 blocos locais de reduced-motion | **muda (esperado)** | por arquivo/propriedade | 0 falha de contraste; 0 texto <12px; ≤8 tamanhos distintos por página |
| **4** | `container-type: inline-size` nos 4 componentes; converter suas `@media`-de-página; alvos de toque | muda | reverter os blocos `@container` | sonda em 10 larguras sem overflow nem rótulo quebrado |
| **5** *(deferida)* | Reestruturação das páginas (hero fora, fila em tabela) | muda muito | por seção, suíte inteira | lista `classname::name` **igual** antes/depois (nunca a contagem) |

**Ordem de edição na árvore compartilhada:** `tokens.css` (novo, seguro) → 4 `<head>` →
`shared.css` → `index.css` → `scrap.css` → `publicar.css`. Os arquivos que a outra sessão edita
são tocados **por último**. Um commit por arquivo, edições cirúrgicas, nunca reescrita de arquivo
inteiro (contrato CRLF/LF).

### ADRs

| # | Decisão | Custo |
|---|---|---|
| ADR-001 | `@layer` + `tokens.css` novo, sem bundler; `shared.css` continua primeiro | +1 request/página (irrelevante em `127.0.0.1`); a Fase 2 é a mais arriscada |
| ADR-002 | Tipografia de 8 degraus, piso 12px, `--text-*`→`--fs-*` | renomeação toca todos os usos; **quebra `FontScaleTests`** (ver abaixo) |
| ADR-003 | Espaço base-4, 8 degraus | perde-se 7/9/11px, usados de propósito em algum lugar |
| ADR-004 | 11 cores cruas + 13 papéis, derivados por `color-mix()` | depende de `color-mix()` (Chromium é o alvo); maior diff visual da migração |
| ADR-005 | `@container` só nos 4 componentes; `@media` no shell; **não unificar breakpoints medidos** | dois mecanismos convivendo; a redução é menor que a prometida no protótipo |
| ADR-006 | Migração aditiva em 5 fases, reestruturação deferida | o ganho que o usuário **sente** só vem na Fase 5 |
| ADR-007 | **Uma regra global de reduced-motion com `!important`** — a única exceção autorizada ao "sem `!important`" — mais um **teste de cobertura** que exige um par reduced-motion para cada `animation`/`transition` declarada | as animações de estado "vivas" (spinner) param, o que é o comportamento correto para reduced-motion; a regra precisa vencer a cascata, não a posição (`shared.css:710-717` prova que posição não basta) |

### Onde o architect discorda do protótipo (e por quê)

1. **Breakpoints: o protótipo prometia 17→5.** **Rejeitado.** `shared.css:34-68` registra, com
   medição no navegador (`sticky-probe/breakpoint_peso.js`), que consolidar 760/768 ou 380/420
   quebra componentes **diferentes** que compartilham a faixa — tentativa já feita e revertida.
   Redução realista: **19→~15** (só os ~3-4 que ficarem órfãos). **O protótipo foi corrigido.**
2. **Raio 8/12/18 → mantido 8/14/22.** Churn sem ganho. **O protótipo foi corrigido.**
3. **A escala de 8 degraus é melhor que a de 10 que existe:** o `tools/normalizar_fontes.py`
   deixou um cluster indecidível (12.8/13.5/14.5/15, degraus a <0.4px). A escala de 8 tem degraus
   únicos em 13 e 16 e **resolve todos os empates que o script deixou em aberto**.
   Única discordância: o script deixa os 6,72–10,08px "ficarem"; Archi os absorve no piso de 12px.

---

## 🧪 Avaliação de cobertura de teste

**A suíte tem 1525 testes** (não ~1499): 119 tocam o front-end, em 19 classes. **Existem testes que
medem o DOM** via `tests/_dom_bridge.py` (ponte Node, `f9a3f90` corrigiu a leitura para UTF-8).

### O que já está coberto (contrato real, não suposto)

| Dimensão | Onde | O que fixa |
|---|---|---|
| Contraste | `test_web_server.py:2197` | mínimo de contraste por classe (4,5 / 3,0) sobre as 4 páginas |
| Escala tipográfica | `FontScaleTests` (`:2556`) | o `MAPA` de `tools/normalizar_fontes.py` fica dentro do teto local; cada literal aponta para o degrau mais próximo; nenhuma folha de página usa `rem` cru |
| Escala de largura | `test_web_server.py:2462` | as 19 faixas e o porquê de não haver escala única |
| Alvo de toque | `:8071,8081,8094` | 44px em `pointer: coarse`, 36px no mouse, `.jump-links`, `.queue-filter`, tabs do Scrap |
| Duplicação shared↔página | `:2217` | `.rail-item`, `.rail-dropdown`, `.menu-btn`, `.sr-only`, `.chip strong`, `.wrap`, `header` |
| Ordem de `<link>` | `:2145` | `shared.css` **primeiro** |
| Tokens duplicados | `:2197` | `--rail-w`, `--accent-primary`, `--text-ink-subtle`, `--bg-deep`, `--transition` só no shared |

### Lacunas reais

1. **Duplicação página↔página não tem teste.** `test_no_selector_is_declared_on_both_sides` só
   compara shared↔página → os **20 seletores** duplicados no topo entre `index.css` e `scrap.css`
   (ou **~26** descendo para dentro de `@media`, dos quais 2 são deliberados) passam em silêncio.
   O teste atual, com regex mais grossa, conta 14. **O teste novo precisa descer para dentro de
   `@media` e `@layer`** — senão não vê metade da dívida, e não sobrevive à Fase 2B, que envolve as
   folhas em camadas.
2. **`!important` não tem teste.** Os 16 podem dobrar sem ninguém notar — e já produzem o bug
   latente do `#btn-plan`.
3. **`prefers-reduced-motion` não tem teste de cobertura.** Há 21 blocos ad hoc e nenhum verifica
   que uma animação *nova* ganhe guarda. **Duas instâncias já ficaram de fora** (achado #5): o
   `.toast` do `index.css` e o `.menu-btn .mb-caret` do `shared.css`. O teste novo deve exigir, para
   cada `animation`/`transition` declarada, um par reduced-motion — e o `.mb-caret` é o **caso
   positivo** que ele tem de pegar.
4. **Nenhuma verificação de espaço.** Os 35 valores não são medidos por teste nenhum.
5. **Nenhuma regressão visual.** Não há diff de pixel entre commits.

### O risco central da migração (medido)

**9 asserções fixam string exata de CSS**, todas em `test_web_server.py`:

```
:5000  job.status = (r.error || r.offline) ? 'fail' : 'done';
:5012  setProgress(atual, 'Falha: ' + r.error);
:8071  .prov-item-actions .btn { min-height: 44px;
:8072  .cookies-box > summary { min-height: 44px; }
:8081  @media (pointer: coarse) { .queue-filter { min-height: 44px; padding: 10px 14px; } }
:8094  .jump-links a { min-height: 44px; }
```

Trocar `padding: 10px 14px` por `var(--sp-2) var(--sp-3)` **mantém o comportamento e quebra o
teste**. São 9 de 835 asserções com literal (1,1%) — pequeno, mas cai exatamente na Fase 3/4.
Há ainda **21 leituras de CSS como texto** (`self.css(...)`).

**Contrato acidental confirmado:** `:2217` exige que `.chip strong` **exista** no `shared.css`
enquanto `.chip` está morto no DOM (o achado #12 quer apagá-lo). Apagar `.chip strong` derruba o
teste — um teste que protege código morto.

**Armadilha adicional:** `FontScaleTests` lê a escala de `--text-*` de `shared.css`. Renomear para
`--fs-*` (ADR-002) **quebra essa classe de teste por construção** — o custo da renomeação é maior
que o de trocar valores.

### Critério de aceite numérico (proposto)

| Dimensão | Hoje | Meta | Método (obrigatório fixar) |
|---|---|---|---|
| Transbordo horizontal | 0 em 12 | **0** | sonda Playwright em 1440/1024/390 |
| Falhas de contraste | 2 | **0** | WCAG 2.2 AA (4,5:1 normal, 3:1 grande), fundo composto |
| Texto < 12px | 7,68px | **0** | DOM computado, todas as páginas × 3 larguras |
| Tamanhos de fonte distintos | 43 (união) | **≤8 por página** | DOM computado |
| Espaçamentos distintos | 35 | **≤8** | DOM computado |
| Alvos de controle < 24px | 0 | **0** | só alvos de controle (exceção de link inline na WCAG 2.5.8) |
| `!important` | 16 | **0** (exceto reduced-motion) | grep no CSS, comentários removidos |

> **Atenção metodológica — os dois métodos discordam.** Minha sonda mede **43** tamanhos
> computados na união das 4 páginas; a citação inicial da Tessa falava em **28**. Os dois números
> não são comparáveis porque os métodos diferem (páginas, larguras e carregamento de folhas).
> **Qualquer limiar de aceite precisa fixar o método antes de virar teste** — senão a meta é
> inatingível por definição ambígua, não por código ruim.

---

## ✅ Plano de ação (por prioridade)

| # | Ação | Responsável | Urgência | Aceite |
|---|------|-------------|----------|--------|
| 1 | Criar `a { color: var(--accent-soft) }` no `shared.css` e dar classe ao link de `publicar.js:124` (Defeito A, 1,93:1) | front-end | **P0** | contraste ≥4,5:1 medido |
| 2 | Parar de aplicar `captionTextStyle` ao `.sel-title`; mover a demo de cor para um `.cap-demo` (Defeito B, 1,02:1) | front-end | **P0** | contraste ≥4,5:1 nos presets `candy`/`pop-box` |
| 3 | Remover `margin-bottom:8px !important` de `#btn-plan` e os dois `!important` de `#progress-label` (achados #3, #4) | front-end | **P1** | 0 `!important` nessas duas propriedades |
| 4 | Subir `@keyframes railMenuIn` + `.mb-bars/.mb-caret` + `header .nav-actions .btn` para o `shared.css` (achado #7, pré-requisito da Fase 2) | front-end | **P1** | sem declaração duplicada página↔página nesses 3 |
| 5 | Criar os 3 testes novos — duplicação **página↔página** (descendo em `@media`/`@layer`), cobertura de `prefers-reduced-motion` e `!important` — **e** adicionar as guardas que faltam (`.toast`, `.menu-btn .mb-caret`) no `shared.css`, tudo **antes** da Fase 2B | testes + front-end | **P1** | os 20 duplicados, o `.toast` e o `.mb-caret` ficam vermelhos antes do conserto e verdes depois |
| 6 | Fase 0 + Fase 1: `tokens.css` novo e `<link>` como **segundo** item, `shared.css` primeiro | front-end | P2 | diff de pixel = 0 nas 12 capturas |
| 7 | Fase 2: `@layer` + esvaziar os 16 `!important`, um por commit | front-end | P2 | 12 capturas idênticas antes de mexer em qualquer regra |
| 8 | Apagar o CSS morto provado (`.brand*`, `.hero .badge-row`, `.chip`, `.chip-row`, `.chip strong`, `.btn-ghost`) **junto** com a linha `.chip strong` do teste `:2217` | front-end + testes | P2 | grep = 0 e suíte verde |
| 9 | Fase 3: adotar `--fs-*` / `--sp-*` / tokens de cor; decidir a renomeação `--text-*`→`--fs-*` **sabendo que ela quebra `FontScaleTests`** | front-end | P2 | ≤8 tamanhos distintos/página; 0 texto <12px |

---

## ⚠️ Pendências / limitações conhecidas

- **O painel mudou sob a medição.** Uma segunda sessão trabalha neste mesmo checkout e reescreveu
  `web/shared.css` e `web/index.css` depois das capturas originais. Duas afirmações do meu mapa
  inicial estavam erradas ("não existe token de tipografia" e "sem `prefers-reduced-motion`") e
  foram corrigidas. **Dois outros achados meus eram falsos positivos** (o glifo do `h2` está
  `aria-hidden`; os links de 15px são prosa inline com exceção na WCAG) e foram removidos.
- **O protótipo `proposta-visual.html` estava com números obsoletos** (cita "89 valores distintos",
  medido antes da tokenização; o atual é 59 declarados / 43 computados). Corrigido, junto com a
  promessa de breakpoints (17→5, rejeitada) e o raio (8/12/18 → 8/14/22).
- **Contagem de `!important` varia com o método:** 16 por grep bruto, 15 com comentários removidos.
  Nenhuma ação depende da diferença, mas o teste futuro precisa declarar a regra.
- **As duas medições de tamanho de fonte discordam (43 vs 28)** por diferença de método — resolvido
  acima fixando o método, não o número.
- **A Fase 5 (reestruturação de páginas) não foi estimada em detalhe:** é onde vive o ganho visual
  grande (hero fora, fila em tabela) e onde está o risco de contrato (`id`/markup ↔ testes).
- **Nada foi executado da suíte completa** (~29 min). As verificações dos membros foram por
  leitura de código, sonda Playwright e testes isolados — não por rodada integral.
- **`README.md` continua com texto de prompt colado sobre um exemplo documentado** (achado de
  sessão anterior, ainda não revertido) — fora do escopo desta revisão, mas registrado.

---

## 📚 Fontes & índice das produções dos membros

- **Cody (code-reviewer)** — `.workbuddy-ai/tmp/auditoria/raw-code-review.md` (273 linhas):
  19 achados com evidência, cadeias provadas dos valores mágicos, CSS morto provado por grep,
  métricas de dívida por folha.
- **Archi (architect)** — `.workbuddy-ai/tmp/auditoria/raw-architect.md` (406 linhas):
  camadas e arquivos, escalas completas, responsividade, plano de 5 fases (2A/2B), 7 ADRs,
  o inventário de origem para a Fase 2A e a fronteira da camada `components`.
- **Tessa (testing-expert)** — `.workbuddy-ai/tmp/auditoria/raw-testing.md` (368 linhas):
  inventário de 119 testes de front-end em 19 classes, 5 lacunas priorizadas, 3 testes novos
  propostos, a correção do critério de aceite da Fase 5, a verificação independente do defeito
  do `.mb-caret` e a barreira de pré-condição da Fase 2.
- **Medições próprias** — `~/.workbuddy-ai/binaries/node/sticky-probe/auditoria_visual.js`
  (4 páginas × 3 larguras) e `valida_proposta2.js` (validação do protótipo, com o parser de
  `color(srgb …)` corrigido). Dados brutos: `.workbuddy-ai/tmp/auditoria/relatorio.json`.
  Dossiê de contexto: `.workbuddy-ai/tmp/auditoria/DOSSIE.md`.
- **Entregáveis visuais** — `deliverables/design-review/proposta-visual.html` (protótipo navegável)
  e `deliverables/design-review/antes/` (5 capturas reais das 4 páginas).

---

> Este relatório foi gerado por colaboração de agentes de IA (Engineering Assurance Team).
> Decisões-chave devem ser revisadas por um responsável humano de engenharia.
