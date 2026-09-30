# Editor de geometria das faixas — design

Data: 2026-09-27
Status: aprovado, pronto para execução (revisão Critic de 2026-09-27 aplicada)

## Problema

O painel de templates (`web/templates.html`, `web/templates.js`) tem duas metades
com qualidades muito diferentes.

A galeria e o editor de conteúdo — card de perfil, texto do POV, placa de fundo —
são um editor de composição de verdade: há arraste com Pointer Events, snap,
ponto cruz, edição in-place, ajuste por teclado, e tudo é verificado contra o
motor por testes que leem o `render.py` de verdade.

O editor de zonas (Passo 2) é um formulário de sliders e campos numéricos. Ele
descreve corretamente o motor — inclusive com a mesma matemática de
arredondamento (`evenFloor`) — mas é declarativo: nada se arrasta, nada se
mede no palco, não existe um único handle em todo o `templates.css`.

Enquanto isso, quem monta um meme precisa ajustar a altura da faixa de texto e a
altura do vídeo repetidamente, descendo um número e olhando o resultado. Em
sites de edição profissionais isso é um gesto de arrastar a borda. É esse o
gap que este design fecha.

## Objetivo

Um editor de geometria direto no palco: arrastar a divisória entre duas faixas
redimensiona as duas, o conteúdo de dentro da faixa se move por pan, e o ajuste
fino, o encaixe, a leitura numérica e o undo/redo acompanham o gesto.

Escopo: a faixa de vídeo e a faixa de imagem/frame do template. Não é um editor
de efeitos, nem de timeline, nem de cor.

## Não-objetivos

- Faixas sobrepostas, arraste livre em duas dimensões, reordenação de zonas.
- Crop box com oito handles (janela de recorte) na zona de vídeo.
- Undo/redo sobre os controles que não são de geometria (texto, cor, alinhamento).
- Qualquer endpoint novo no servidor. Nada aqui precisa do backend:
  `web/server.py` não tem — e não passa a ter — nenhuma leitura de `reframe_*`
  nem de geometria de zona.

## Decisões de arquitetura

### Fronteira como objeto primário — em vez de posição absoluta

As zonas são uma pilha: `Template.validate()` recusa o arquivo se as frações
somam algo diferente de 1.0 com tolerância de `1e-6` (`template.py:316-325`).
Hoje a altura de cada faixa é consequência da sua fração, e `plan_bands`
(`template.py:459-549`) empilha deterministicamente.

Três modelos foram considerados.

**Fronteira (escolhido).** O que se arrasta é a divisória entre duas faixas.
Mover a divisória `k` por `d`, em fração de quadro:

```
zones[k].fraction   += d
zones[k+1].fraction -= d
```

O limite do gesto é `MIN_ZONE_FRACTION` (0.02, `template.py:65`), já validado
pelo motor. `toToml()` não muda, porque `fraction` já é escrita.

**Redistribuição proporcional (descartado).** Arrastar a faixa `k` mudaria só
ela, com as outras renormalizadas proporcionalmente. Menor diff conceitual, mas
editar a faixa do meio altera todas as outras — o usuário perde a noção de onde
as bordas ficaram.

**Posição absoluta `y` + `height` (descartado).** É o modelo do Canva, mas é
breaking change: `Zone`, `plan_bands`, validação, TOML e a bateria inteira de
testes de grade de croma (`test_template.py:264-505`) precisariam mudar. Só se
pagaria se quiséssemos faixas sobrepostas, que nenhum template usa.

Borda superior da faixa `k` é a divisória `k-1`; a inferior é a divisória `k`.
Topo da primeira e base da última não têm handle.

### A soma 1.0 sobrevive ao arredondamento do arquivo, não por construção

A primeira redação deste doc dizia que a soma é preservada por construção. Em
memória isso é verdade — depois de 20 000 divisórias a deriva máxima é 2.2e-15,
muito dentro da tolerância de 1e-6. **O problema é a serialização.**

`toToml()` do painel arredonda cada zona isolada (`web/templates.js:1957`):

```js
lines.push("fraction = " + fmtNum(round4(zone.fraction)));
```

Arredondar duas frações a 4 casas não preserva a soma delas. Medido em pilha
realista de três faixas com arrastos de ±0,2%: **3 751 de 15 200 divisórias
(24,7%)** saem com soma ≠ 1.0, e o motor recusa o arquivo:

```
sum = 0.9999
ENGINE REJECTED: Template 'x': as zonas somam 0.9999 da altura; precisam somar
exatamente 1.0.
```

Ou seja: quase um em cada quatro gestures de arraste produziria um `.toml`
inválido, e o defeito só apareceria no render — longe do gesto que o causou.

O conserto já existe no repo. `balance()` (`web/templates.js:3124-3142`) faz
exatamente isto: redistribui, arredonda, e **joga o resíduo na maior zona**, onde
é imperceptível. O arraste de divisória faz o mesmo passo, e só ele.

O teste correspondente afirma a soma **depois** do `round4`, não antes.

### `captions` fica fora da aritmética

A zona `captions` tem `fraction = 0.0` e é posicionada de forma absoluta pelo
libass — não ocupa pilha, e `validate()` exige que seja a última
(`template.py:295-305`). As divisórias são calculadas só sobre as zonas não
`captions`, na ordem em que aparecem.

A lista do painel mistura os tipos, então o editor mantém um índice de "faixa
empilhável" separado do índice da zona, e toda conversão entre os dois passa por
ele. `planBands()` (`web/templates.js:419-457`) já resolve a geometria em pixels
com `evenFloor` (`web/templates.js:415-417`) e o clamp de `bandDy`; o novo código
consome essa resolução em vez de recalcular.

### Defeito pré-existente: `reframe_*` não chega ao render

O painel oferece zoom e pan do vídeo (`rf-zoom`, `rf-panx`, `rf-pany`,
`web/templates.html:742-747`), e a prévia aplica os três valores por `transform`
sobre a faixa de vídeo (`web/templates.js:1768-1774`).

O motor não faz isso. `render.py:454-461` lê `config.reframe_*` apenas dentro de
`_layout_filter`, que é o caminho legado, usado quando não há composição. Com um
template de zonas, o vídeo passa por `compose()` → `scale_into`
(`template.py:645-771`, `593-618`), e `compose()` nunca olha `reframe_*`.

Consequência: com qualquer template de zonas, deslocar o vídeo no painel não
muda o clipe renderizado. Os sliders do Passo 4 mentem.

Isso é precondição do pan dentro da faixa, não um extra: sem a correção, o gesto
seria construído sobre um controle que não faz nada.

**Só metade do defeito é do painel.** `zoom`, `pan_x` e `pan_y` de zona **não
têm gate de kind**: `template.py:229-234` só checa o intervalo dos valores,
nunca o `kind`. `plate_image` é que tem gate, via `PLATE_KINDS`
(`template.py:214-218`). E uma zona `video` com essas três chaves já passa na
validação e já entra no grafo — verificado:

```
COMPOSE com zone.zoom na zona de video:
  crop=1080:1152:x=(in_w-out_w)*0.2:y=(in_h-out_h)*0.8
```

Isso sugeria a correção mais barata: o painel passa a escrever
`zone.zoom`/`pan_x`/`pan_y` na zona de vídeo, e `template.py` fica intocado.
**A sugestão está errada, e o motivo está no roteamento, não na validação.**

`render_clip` (`render.py:784`, `render.py:812-816`) decide entre dois caminhos
disjuntos:

```python
layout = _layout_filter(config, source_width, source_height, crop_x)   # 784
...
if zones_need_composing:      # len_for_compose(template) > 1
    compose_graph, compose_label = template_mod.compose(...)           # 827
    graph_parts.append(compose_graph)
else:
    video_chain = f"{video_input}setpts=PTS-STARTPTS,{_apply(layout, overlay_chain)}[vout]"  # 811
```

`layout` — que é onde `reframe_*` é lido — é **calculado na linha 784 e usado só
na linha 811**, no ramo `else`. Com composição, ele é descartado. E
`zone.zoom` é lido por `scale_into`, que só `compose()` chama. **Nenhuma das
duas chaves chega ao outro caminho.** Medido nos dois templates embutidos:

| Template | `len_for_compose` | Compõe? | `reframe_*` lido? | `zone.zoom` lido? |
|---|---|---|---|---|
| `full-frame` | 1 | não | **sim** (via `_layout_filter`) | não |
| `split-card` | 2 | sim | **não** (`layout` descartado) | sim |

As duas rotas não são alternativas concorrentes — são **as duas metades de um
mesmo bug**:

- `full-frame` é o único template que é só vídeo. Ele não compõe, e por isso o
  `reframe_*` **funciona** hoje. É o único lugar onde o slider não mente.
- `split-card` é o único que compõe. É nele que o gesto morre.

Trocar o painel para `zone.zoom` consertaria `split-card` e **regressaria
`full-frame`**, que passaria a ler uma chave que ninguém lê no caminho dele. O
defeito ficaria do mesmo tamanho, só mudado de lugar — e mais difícil de ver,
porque o template que parou de funcionar é o mais simples.

**A correção é levar `reframe_*` até o `compose`, que é o caminho que hoje
perde o campo.** `scale_into` ganha um parâmetro opcional de ajuste; `compose()`
o preenche com `template.reframe_*` quando a zona é a de vídeo. Assim os dois
caminhos leem a mesma chave, e o painel volta a ter um único destino.

O painel **não** muda de chave, e `reframe_*` continua no TOML. Isso importa
para a suíte: `ApplyToConfigTests` (`test_template.py:1429-1455`) já cobre o
round-trip dessas três chaves na config, e ela continua válida porque a
superfície do arquivo não muda.

Custo real: `scale_into` ganha um parâmetro com default, então nenhum chamador
existente quebra. `compose()` é o único que o preenche, e só para a zona de
vídeo — que é o que `video_zone` (`template.py:357-362`) já expõe para isso.

#### `FULL_FRAME`: qual garantia vence

`compose()` curto-circuita em `template.py:733-738`: quando a primeira zona é um
vídeo de canvas inteiro, ele relabela a entrada e não monta filtro nenhum. O
docstring de `FULL_FRAME` (`template.py:372-373`) promete geometria
byte-comparável com renderizar sem template algum.

O curto-circuito está **depois** da chamada a `scale_into` (linha 725), então o
`crop` com pan é construído e depois descartado. Um `full-frame` com `reframe_*`
neutro não entra em nenhum dos dois ramos de ajuste do `scale_into`
(`template.py:606-618`) e produz o grafo de sempre — verificado:

```
FULL_FRAME: [0:v]scale=1080:1920:force_original_aspect_ratio=increase,
            crop=1080:1920,setsar=1[z0s];[z0s]setpts=PTS-STARTPTS[c0] | [c0]
```

A garantia não muda. O teste a escrever fixa isso: `full-frame` neutro continua
produzindo `scale=1080:1920,...,crop=1080:1920` sem expressão de pan. É o que já
existe em `test_template.py:200-201` e `617-618`, e a rota A não pode quebrá-los.

Um `full-frame` com reframe **não** neutro é a combinação que a spec não promete:
o gesto escreve `reframe_*`, o `compose` monta o `crop`, e o curto-circuito
descarta. O painel tem que dizer isso, ou esconder o gesto nos templates de
faixa única — o mesmo princípio que já rege a aba de cores (um controle visível
escrevendo num lugar que a prévia não lê é ajuste invisível).

## Interação

### Handle

Cada faixa empilhável ganha duas linhas de pegada — superior e inferior — de
8px de tela, com `cursor: ns-resize`. As pontas do quadro não têm handle. São
marcadas com `data-band-edge`, atributo distinto do `data-edit` que o editor de
texto já usa, para que o `closest("#canvas [data-edit]")` de `initTweetEditor`
(`web/templates.js:1085`) não as capture.

O alvo de 8px é em pixels de tela, não de quadro: o palco tem `max-width: 300px`
e `aspect-ratio: 9/16` (`web/templates.css:1663-1666`), então um pixel do quadro
cabe em menos de meio pixel de tela. Em toque, o alvo sobe para 16px.

Hoje não existe um único handle em `web/templates.css` — a pegada é nova.

### Durante o arraste, nada reconstrói a prévia

`renderPreview()` (`web/templates.js:1651-1843`) faz `canvas.innerHTML = ""` e
reconstrói tudo. Chamá-lo no meio do gesto trocaria o nó que detém a captura do
ponteiro. É a mesma razão que proíbe `renderPreview()` dentro de
`moveTweetDrag` — invariante travada em `test_web_server.py:3921-3934`, que
verifica o corpo da função.

Então o arraste toca apenas as duas faixas afetadas: `el.style.top` e
`el.style.height`, e o repaint do conteúdo delas pelos pintores que já existem
(`paintTextZone` em `web/templates.js:1557`, `plateStyle` em `web/templates.js:1145`).
O `applyPlateStyle` escreve o fundo propriedade a propriedade, nunca por
`style.cssText` — senão a geometria escrita logo antes é apagada
(`test_web_server.py:4474-4490`). As demais faixas não se movem.
`renderPreview()` roda só no `pointerup`.

O padrão de arraste a copiar é o de `initTweetEditor` (`web/templates.js:1076-1100`),
com `startTweetDrag` (750-771), `moveTweetDrag` (773-796) e `endTweetDrag`
(798-812): `setPointerCapture`, limiar de 3px antes de considerar arraste,
reancoragem da base, `getBoundingClientRect` do palco no início, e conversão de
delta de tela para px do quadro por `frameScaleY()` (`web/templates.js:647-651`).

### Seleção

Clicar numa faixa seleciona: acende os handles e abre a leitura numérica. Clicar
fora deseleciona. Arrastar uma divisória é sempre gesto de borda — nunca compete
com o arraste de texto, que é interior. `Esc` deseleciona.

### Dentro da faixa de mídia é pan

Arrastar dentro da faixa de vídeo move `reframe_pan_x` / `reframe_pan_y`. Dentro
de `image` ou `frame`, move `zone.pan_x` / `zone.pan_y`. O zoom vem da roda do
mouse sobre a faixa, com `Ctrl` para passo fino.

Duas fontes, porque são dois caminhos de render: o vídeo é o único template que
pode não compor, e `reframe_*` é a chave que ele lê.

Os sliders "Reenquadramento do vídeo" (Passo 4, `web/templates.html:742-747`) e
"Ajustar frame" (Passo 2, `web/templates.js:2388-2400`) saem do painel: o gesto
os substitui, e a prévia e o render passam a ler a mesma fonte.

Três consequências da remoção dos sliders, que o plano precisa cobrir e a
primeira redação omitia:

- **`web/templates.js:3220-3222` e `4003-4005`** escrevem
  `$("#rf-zoom").value = 1` e afins **sem guarda de null**. Removidos os
  elementos do HTML, o próximo "Carregar split-card" ou aplicação de formato da
  galeria joga `TypeError` e o painel morre. A linha `4192-4194` faz o mesmo
  percurso com `if (el)` — é o padrão a seguir, e é o que conserta.
- Os ouvintes de `input` em `web/templates.js:2871-2888`, atrelados aos ids
  `rf-zoom`/`rf-panx`/`rf-pany`, saem junto.
- `reframe_*` **fica** no TOML (`web/templates.js:1949-1951`) — é a chave que o
  caminho sem composição lê. A zona e a prévia passam a ler `state.reframe*` e
  `zone.pan*` de forma explícita por tipo de faixa, em vez de o `transform`
  atual (`web/templates.js:1768-1782`) adivinhar pelo `band.kind`.

Pan em `zoom = 1` é no-op, porque o `crop` de `scale_into` fica sem sobra para
recortar. A faixa mostra a dica "role para aproximar" quando o zoom é neutro.
Esse aviso é **uma mensagem nova**, não reuso: o que existe hoje em
`web/templates.js:1362-1372` é um atributo `data-edge` num ponto, sem texto
visível — serve de precedente de estilo, não de string.

Há uma divergência conhecida e aceita: em `zoom = 1` o `transformOrigin` da
prévia se move mesmo quando o render não cropa nada. Os dois painéis discordam
nesse caso, e a dica cobre isso.

### Mapeamento gesto → campo

| Gesto | Escreve | Vai para o `.toml` | Lido por |
|---|---|---|---|
| Arrastar divisória `k` | `zones[k].fraction`, `zones[k+1].fraction` | `fraction` | `plan_bands` |
| Arrastar dentro da faixa de vídeo | `reframe_pan_x/y` | `reframe_pan_x/y` | `_layout_filter` e, após o passo 1, `compose` |
| Roda sobre a faixa de vídeo | `reframe_zoom` | `reframe_zoom` | idem |
| Arrastar dentro de `image`/`frame` | `zone.pan_x/y` | `pan_x/y` | `scale_into` via `compose` |
| Roda sobre `image`/`frame` | `zone.zoom` | `zoom` | idem |

O painel apaga a chave no neutro — `reframe_*` já faz isso por comparação direta
contra o neutro (`web/templates.js:1949-1951`), e `applyPlateFit`
(`web/templates.js:1318-1335`) é o precedente do mesmo padrão do lado da zona.

## Acabamento

### Snap

Alvos, em y de canvas, dentro de uma janela de 6px de tela: o centro (`0.5`); as
frações redondas de altura `0.25, 1/3, 0.5, 2/3, 0.75`; e as divisórias
vizinhas. Vence o mais próximo, e os dois eixos se resolvem numa passada só — a
mesma técnica de `snapTweetOffset` (`web/templates.js:726-748`), que lê a caixa
depois do deslocamento cru para os dois eixos acertarem juntos.

A janela e o nudge são constantes nomeadas no padrão do arquivo
(`TW_SNAP_PX = 6`, `TW_NUDGE_PX = 1`, `web/templates.js:549-552`).

### Guias

Uma linha horizontal na posição da divisória, na cor quente quando ela pegou num
alvo, com a leitura `y=998 px`. Vive fora do canvas, reusando o padrão de
`#edit-guides` (`web/templates.html:1068`), que já existe, já tem linha quente
(`.eg-line.is-center`, `web/templates.css:1180`) e já é testado quanto a morar
fora do canvas e a não capturar ponteiro (`test_web_server.py:3937-3958`).

### Leitura numérica

Barra flutuante presa à divisória arrastada: `Vídeo 52.0% · 998 px`. Durante o
arraste mostra fração e pixel; ao soltar, colapsa para o valor salvo. Substitui o
papel que a coluna "Resumo" (`renderGeometry`, `web/templates.js:1861+`) cumpre
hoje, que continua existindo.

### Teclado

Com a divisória selecionada: setas movem 1px do quadro, `Shift` move 10px,
`Esc` deseleciona. Mesmas regras de `onTweetKey` (`web/templates.js:902-923`),
inclusive a guarda que devolve as setas ao item quando um input tem foco
(`web/templates.js:906`).

### Undo/redo

Pilha de instantâneos de um recorte do estado: frações das zonas e
`zoom`/`pan_x`/`pan_y` das zonas de mídia. Um empurrão por gesto confirmado, no
`pointerup`. `Ctrl+Z` desfaz, `Ctrl+Shift+Z` refaz. Teto de 60 níveis.

O escopo é deliberadamente só a geometria. Envelopar cada input do painel para
cobrir texto e cor é uma reescrita, e não compra nada aqui.

## Casos de borda

- **O resíduo do arredondamento.** A soma só fecha depois do passo de
  `balance()`, e é lá que ela é conferida. Uma divisória cujo `round4` fecha em
  0.9999 ou 1.0001 é corrigida antes de entrar no arquivo — nunca depois.
- **Faixa deslocada por `band_dy`.** `Zone.bandDy` é escrito pelo painel
  (`web/templates.js:2009`) e `plan_bands` move a faixa na pilha sem mexer em
  fração alguma (`template.py:504-506`). Uma faixa deslocada **não é adjacente**
  à vizinha: não há aresta compartilhada entre as duas, e a "divisória" que o
  usuário está vendo não é a aresta que o motor empilha. **Os handles de
  divisória ficam suprimidos** quando qualquer zona empilhável tem `band_dy`
  diferente de zero, e o painel diz por quê. Definir o que um handle faz numa
  faixa deslocada é problema para quando o `band_dy` for um gesto também; hoje
  ele é um campo, e um handle que mente sobre a geometria é pior do que a
  ausência dele.
- **Exatamente duas faixas empilháveis.** A divisória 0 é ao mesmo tempo a base
  da faixa de cima e o topo da de baixo — a mesma linha física. O alvo "divisórias
  vizinhas" do snap degenera: uma divisória adjacente a si mesma. O snap
  descarta o alvo que é a própria divisória antes de comparar.
- **Squeeze simultâneo das faixas internas.** Com o piso de 2%, um template de
  cinco faixas deixa as três do meio espremíveis até o piso ao mesmo tempo. Não
  há caminho que produza fração inválida, mas também não sobra alvo válido
  depois. O gesto para no clamp e a leitura mostra o mínimo.
- **Menos de duas faixas empilháveis.** Nenhum handle. O painel diz por quê, em
  vez de sumir com o controle.
- **`pointercancel` e perda de captura.** O gesto é tratado como não
  confirmado: volta ao instantâneo anterior, nada entra no histórico.
- **Toque.** Mesmos `PointerEvent`, alvo de 16px em vez de 8px.
- **Grade de croma.** `planBands` arredonda para par via `evenFloor`
  (`web/templates.js:415-417`), então uma divisória em pixel ímpar é absorvida
  pelo arredondamento. A leitura numérica mostra o valor antes do arredondamento,
  porque é o que a prévia desenha.
- **Última zona com sobra de arredondamento.** `plan_bands` joga o resto para a
  última faixa (`template.py:543-548`). Arrastar a divisória imediatamente acima
  dela move o resto junto, sem caso especial.

## Testes

A suíte do painel é de contrato sobre o texto-fonte, sem browser.
`page_source` concatena `.html` + `.css` + `.js` (`test_web_server.py:191`);
`fn_body` recorta o corpo de uma função (`test_web_server.py:208-219`).

**Limite de `fn_body` que planeja o contrato:** o corte é em `"function NOME("`
seguido de `"\n  function "`. Ele acha declaração de topo indentada com dois
espaços, e **não** acha função aninhada, arrow function nem método. Um contrato
que exija um literal numérico no corpo (o `0.02` do clamp, por exemplo) reprova
uma implementação que iça a constante para o escopo do módulo — que é o padrão
do arquivo (`web/templates.js:549-552`). Por isso os contratos abaixo apontam
para comportamento observável no texto, não para literais.

`tests/test_template.py`:

- `full-frame` com ajuste neutro continua produzindo o grafo de identidade
  (protege a garantia de `template.py:372-373` sob o novo parâmetro).
- `scale_into` com o ajuste da zona de vídeo gera o `crop` com
  `x=(in_w-out_w)*pan_x:y=(in_h-out_h)*pan_y` — o teste que fixa o passo 1, e
  ele **não** depende de nada do painel.
- Fração de zona de vídeo acima de 1.0 e pan fora de `[0,1]` continuam recusados
  (`template.py:229-234`).
- `captions` fora da pilha continua sendo a última e continua não contando.
- A aritmética da divisória espelhada em Python: uma função de teste que simula
  `fractions[k] += d; fractions[k+1] -= d`, aplica o clamp de 2%, e afirma que
  a soma **depois de um `round4` equivalente** fica dentro de 1e-6. É uma
  espelhagem, não o código do painel — mas é o que prova que a invariante
  sobrevive à serialização, que é onde ela quebrava.

`tests/test_web_server.py`:

- Existe `data-band-edge`, distinto de `data-edit`.
- O corpo da função de arraste de divisória não contém `renderPreview(` — o
  mesmo formato de `test_web_server.py:3928`, e a razão é a mesma.
- O corpo do arraste escreve as duas frações em sentidos opostos (`+=` e `-=`),
  preservando a soma por construção. É proxy legítimo: sem runtime de JS, a
  forma do texto é o contrato.
- O clamp é exercitado, não literalizado: o corpo referencia a constante nomeada
  do piso de 2%, e um teste vizinho confirma que ela vale 0.02 e bate com
  `MIN_ZONE_FRACTION` do Python.
- O passo de resíduo existe: o corpo da função que fecha o gesto referencia o
  mesmo absorver de resíduo que `balance` usa.
- `captions` não entra na aritmética das divisórias.
- Handles ficam suprimidos quando alguma zona empilhável tem `band_dy != 0`.
- A prévia e o `toToml` leem a mesma fonte de zoom/pan — o `transform` da prévia
  e a serialização apontam para a mesma chave, e a escolha entre `reframe_*` e
  `zone.pan*` é feita pelo `kind` da faixa, não por `if (el)`.
- `Ctrl+Z` e `Ctrl+Shift+Z` estão ligados.
- Os sliders `rf-zoom`/`rf-panx`/`rf-pany` saíram do HTML, e **nenhum**
  `$("#rf-…")` ficou sem guarda — o teste varre o `templates.js` atrás de
  acessos diretos a esses ids.

## Arquivos tocados

| Arquivo | Mudança |
|---|---|
| `viralclipper/template.py` | `scale_into` ganha um parâmetro opcional de ajuste; `compose()` o preenche com `reframe_*` quando a zona é a de vídeo |
| `web/templates.js` | Fronteiras, handles, arraste, pan, snap, guias, leitura, teclado, undo/redo; guardas de null em `3220-3222` e `4003-4005` |
| `web/templates.css` | Handles, guias, leitura flutuante, estados de seleção |
| `web/templates.html` | Remoção dos sliders de reframe; barra de leitura |
| `tests/test_template.py` | Contrato de `scale_into` com ajuste na zona de vídeo, identidade do `full-frame`, aritmética de divisória espelhada |
| `tests/test_web_server.py` | Contratos do editor de divisórias, varredura dos ids `rf-*` |

`web/server.py` não muda: nenhum endpoint toca geometria de zona nem `reframe_*`.

## Ordem de implementação

1. **Levar `reframe_*` até o `compose`.** `scale_into` ganha o parâmetro
   opcional de ajuste; `compose()` o preenche para a zona de vídeo.
   `split-card` volta a obedecer ao slider — hoje é o único template onde o
   gesto morre, e ele morre sem aviso. Entrega valor sozinho e é precondição do
   passo 4.
2. **Modelo de divisórias em JS**, com a soma preservada em memória, o clamp, e
   o passo de resíduo do `round4` antes de fechar o gesto. Teste de contrato da
   função pura e a aritmética espelhada em Python.
3. **Handles e arraste de divisória, com leitura numérica e guias.** As duas
   metades entram juntas porque reescrevem o mesmo handler de arraste: separá-las
   faria o mesmo código de pointer-capture ser escrito duas vezes.
4. **Pan interno e roda de zoom**, com a remoção dos sliders e a guarda de null
   nos dois loaders. Agora que o passo 1 fechou o `split-card`, o gesto tem um
   destino real nos dois caminhos.
5. **Snap e ajuste fino por teclado.**
6. **Undo/redo.**

Cada passo é verificável sozinho, e o passo 1 já entrega valor mesmo que o
resto demore.

## Commits

Um commit por passo, no padrão do repo — Conventional Commits, descrição em
português no imperativo, com o corpo explicando o porquê (é o que os 10 commits
à frente do `origin/master` fazem).

```
fix(template): o reframe do painel nao chega no render com template de zonas
feat(painel): a divisoria entre faixas arrasta as duas, com a leitura e a guia
feat(painel): pan dentro da faixa substitui o slider de reenquadramento
feat(painel): o encaixe da divisoria e o ajuste fino por teclado
feat(painel): undo/redo da geometria das faixas
```

O passo 1 é `fix` e não `feat`: ele não adiciona capacidade, conserta um controle
que não fazia nada. O `scope` é `template` porque o defeito é do motor não
ler o campo, ainda que a correção seja toda no painel — o mesmo critério que o
commit `6373c57` ("o post vira contexto e o video ganha o quadro") usou.
