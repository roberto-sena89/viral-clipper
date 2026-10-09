/* ---------- ESCAPEMENTO DE HTML ----------
   Uma unica Implementacao de `esc`, usada pelas duas paginas.

   Havia duas, e elas nao eram iguais:

   - o `index.js` usava um `<div>` descartavel, escrevia o valor em
     `textContent` e lia de volta o `innerHTML`. Isso escapa `&`, `<` e `>`,
     e NAO escapa aspas: o `innerHTML` de um nodo de texto nao precisa disso,
     porque nao ha aspa para fechar.
   - o `scrap.js` fazia cinco `replace` e escapava tambem `"` e `'`.

   A do scrap e a que serve. O motivo esta no uso: o scrap passa `esc(href)`
   DENTRO de um atributo (`href="..."`, `title="..."`), e ai uma aspa sem
   escapar fecha o atributo e o resto da string vira markup — o
   `server.py` escapa com `quote=True` pelo mesmo motivo, e o comentario de
   la explica o ataque. O index so usava `esc` em conteudo de texto, entao a
   versao fraca nunca chegou a falhar ali; mas ela seria um bug no dia em que
   alguem precisasse de um atributo, e a falha seria silenciosa.

   Unificar na versao forte e o que mantem o index seguro pelo mesmo motivo que
   mantem o scrap. Nao e a versao mais simples: e a versao correta.

   A ordem dos `replace` importa: `&` primeiro. Escapar `&` depois de
   `<` e `>` transformaria o `&` de `&lt;` em `&amp;lt;`, e o browser leria
   `&lt;` como texto em vez de `<`. */
(function (global) {
  'use strict';

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Os dois scripts de pagina sao IIFE com `'use strict'`, entao nao veem
  // nada do escopo de outro arquivo. O global e o que os dois alcancam, e o
  // por que o `esc` fica em `window` e nao num modulo.
  global.esc = esc;

  const CAPTION_LOOKS = {
    'karaoke':    { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.55)', box: null, highlight: '#FFFF00', upper: true },
    'bold-box':   { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: null, shadow: 'none', box: 'rgba(20, 20, 23, 0.7)', highlight: '#FFFF00', upper: true },
    'minimal':    { font: "Arial, sans-serif", weight: 400, color: '#FFFFFF', outline: '#000000', shadow: '1px 1px 2px rgba(0,0,0,0.6)', box: null, highlight: '#FFFF00', upper: false },
    'neon':       { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.7)', box: null, highlight: '#00FF88', upper: true },
    'block-dark': { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: null, shadow: 'none', box: 'rgba(16, 16, 20, 0.9)', highlight: '#FFFF00', upper: true },
    'mono':       { font: "Consolas, 'Courier New', monospace", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '1px 1px 3px rgba(0,0,0,0.6)', box: null, highlight: '#00FF88', upper: true },
    // --- vibrantes de alto impacto (espelho dos presets novos do backend) ---
    'fire':        { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.6)', box: null, highlight: '#FF5500', upper: true },
    'magenta-pop': { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.7)', box: null, highlight: '#FF00CC', upper: true },
    'cyan-pop':    { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.6)', box: null, highlight: '#00E5FF', upper: true },
    'lime-hit':    { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.6)', box: null, highlight: '#CCFF00', upper: true },
    'blood':       { font: "Impact, 'Arial Black', sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.7)', box: null, highlight: '#FF2D2D', upper: true },
    'gold-box':    { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: null, shadow: 'none', box: 'rgba(20, 20, 23, 0.7)', highlight: '#FFD700', upper: true },
    'candy':       { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#141414', outline: null, shadow: 'none', box: 'rgba(244, 244, 244, 0.8)', highlight: '#FF6FA5', upper: true },
    'violet-vibe': { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.75)', box: null, highlight: '#B26BFF', upper: true },
    'ice-blue':    { font: "Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: null, shadow: 'none', box: 'rgba(20, 24, 31, 0.8)', highlight: '#66CCFF', upper: true },
    'sunset':      { font: "Impact, 'Arial Black', sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.6)', box: null, highlight: '#FF7A00', upper: true },
    'bubble':      { font: "'Segoe UI', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: null, shadow: 'none', box: 'rgba(26, 30, 51, 0.84)', highlight: '#FFF200', upper: true },
    'ultra-impact':{ font: "Impact, 'Arial Black', sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 5px rgba(0,0,0,0.8)', box: null, highlight: '#FFFF00', upper: true },
    'slim':        { font: "'Arial Narrow', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '1px 1px 2px rgba(0,0,0,0.55)', box: null, highlight: '#00E5FF', upper: true },
    'cobalt':      { font: "'Segoe UI', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: null, shadow: 'none', box: 'rgba(16, 18, 24, 0.9)', highlight: '#00E5FF', upper: true },
    'pop-box':     { font: "'Arial Black', Arial, sans-serif", weight: 700, color: '#141414', outline: null, shadow: 'none', box: 'rgba(255, 242, 0, 0.8)', highlight: '#FF2D2D', upper: true },
    // --- fontes do consenso editorial (espelho das novas do backend) ---
    'roboto-bold':       { font: "Roboto, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.6)', box: null, highlight: '#00E5FF', upper: true },
    'inter-bold':        { font: "Inter, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.6)', box: null, highlight: '#CCFF00', upper: true },
    'poppins-bold':      { font: "Poppins, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.6)', box: null, highlight: '#FF00CC', upper: true },
    'montserrat-bold':   { font: "Montserrat, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.6)', box: null, highlight: '#FFD700', upper: true },
    'dm-sans':           { font: "'DM Sans', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.6)', box: null, highlight: '#FF5500', upper: true },
    'cabin-bold':        { font: "Cabin, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '1px 1px 2px rgba(0,0,0,0.55)', box: null, highlight: '#00E5FF', upper: true },
    'verdana-bold':      { font: "Verdana, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.6)', box: null, highlight: '#FFFF00', upper: true },
    'trebuchet-bold':    { font: "'Trebuchet MS', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.6)', box: null, highlight: '#FF2D2D', upper: true },
    'tahoma-bold':       { font: "Tahoma, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '1px 1px 2px rgba(0,0,0,0.55)', box: null, highlight: '#00E5FF', upper: true },
    'calibri-bold':      { font: "Calibri, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 3px rgba(0,0,0,0.6)', box: null, highlight: '#CCFF00', upper: true },
    'franklin-bold':     { font: "'Franklin Gothic Medium', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.6)', box: null, highlight: '#FF5500', upper: true },
    'segoe-black':       { font: "'Segoe UI', Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '2px 2px 4px rgba(0,0,0,0.6)', box: null, highlight: '#B26BFF', upper: true },
    'helvetica-classic': { font: "Helvetica, Arial, sans-serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '1px 1px 2px rgba(0,0,0,0.5)', box: null, highlight: '#FFFF00', upper: true },
    'merriweather-black':{ font: "Merriweather, Georgia, serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '1px 1px 3px rgba(0,0,0,0.55)', box: null, highlight: '#FFD700', upper: true },
    'arvo-bold':         { font: "Arvo, Georgia, serif", weight: 700, color: '#FFFFFF', outline: '#000000', shadow: '1px 1px 3px rgba(0,0,0,0.55)', box: null, highlight: '#FFFF00', upper: true },
  };

  function captionOutlineShadow(color) {
    return '1px 0 0 ' + color + ', -1px 0 0 ' + color + ', 0 1px 0 ' + color + ', 0 -1px 0 ' + color +
           ', 1px 1px 0 ' + color + ', -1px 1px 0 ' + color + ', 1px -1px 0 ' + color + ', -1px -1px 0 ' + color;
  }

  function captionTextStyle(el, look) {
    el.style.fontFamily = look.font;
    el.style.fontWeight = String(look.weight);
    el.style.color = look.color;
    const shadows = [];
    if (look.outline) shadows.push(captionOutlineShadow(look.outline));
    if (look.shadow && look.shadow !== 'none') shadows.push(look.shadow);
    el.style.textShadow = shadows.join(', ');
    el.style.background = look.box || 'transparent';
    el.style.borderRadius = look.box ? '6px' : '0';
    el.style.padding = look.box ? '4px 10px' : '0';
  }

  function captionDemoHtml(look) {
    const text = look.upper ? 'EXEMPLO DE LEGENDA' : 'Exemplo de legenda';
    const marker = look.upper ? 'LEGENDA' : 'legenda';
    const at = text.lastIndexOf(marker);
    return text.slice(0, at) + '<em style="color:' + look.highlight + '">' + text.slice(at) + '</em>';
  }

  function captionDemoElement(look) {
    const stage = document.createElement('span');
    stage.className = 'cap-stage';
    const demo = document.createElement('span');
    demo.className = 'cap-demo';
    captionTextStyle(demo, look);
    demo.innerHTML = captionDemoHtml(look);
    stage.appendChild(demo);
    return stage;
  }

  function enhanceSelect(select) {
    const isCaptionPreset = select.id === 'caption-preset';
    const wrapper = document.createElement('div');
    wrapper.className = 'select-rich';

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.innerHTML =
      '<span class="sel-text"><span class="sel-title"></span><span class="sel-desc"></span></span>' +
      '<span class="sel-arrow" aria-hidden="true">▾</span>';

    const panel = document.createElement('div');
    panel.className = 'select-panel';
    panel.setAttribute('role', 'listbox');
    panel.setAttribute('aria-label', select.name || select.id);

    const optionEls = Array.from(select.options).map((opt) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'select-option pressable';
      item.setAttribute('role', 'option');
      const title = opt.getAttribute('data-title') || opt.textContent.trim();
      const desc = opt.getAttribute('data-desc') || '';
      const titleEl = document.createElement('span');
      titleEl.className = 'opt-title';
      titleEl.textContent = title;
      item.appendChild(titleEl);
      if (desc) {
        const descEl = document.createElement('span');
        descEl.className = 'opt-desc';
        descEl.textContent = desc;
        item.appendChild(descEl);
      }
      // O preset de legenda mostra a própria cara: a opção já renderiza a
      // legenda como ela vai sair no video (fonte, cor, caixa e destaque).
      if (isCaptionPreset && CAPTION_LOOKS[opt.value]) {
        item.appendChild(captionDemoElement(CAPTION_LOOKS[opt.value]));
      }
      item.addEventListener('click', () => {
        select.value = opt.value;
        // O painel rico e uma camada por cima: quem escuta o <select> - o
        // autosave da pagina de Ajustes, por exemplo - so fica sabendo se o
        // evento for emitido aqui. Atribuir .value por codigo nao emite nada,
        // entao sem esta linha o ajuste mudaria na tela e nunca seria salvo.
        select.dispatchEvent(new Event('change', { bubbles: true }));
        sync();
        close();
        trigger.focus();
      });
      panel.appendChild(item);
      return item;
    });

    function sync() {
      const active = select.options[select.selectedIndex];
      if (!active) return;
      const titleEl = trigger.querySelector('.sel-title');
      titleEl.textContent =
        active.getAttribute('data-title') || active.textContent.trim();
      // O select de preset carrega o visual da legenda no proprio trigger.
      if (isCaptionPreset && CAPTION_LOOKS[select.value]) {
        captionTextStyle(titleEl, CAPTION_LOOKS[select.value]);
        trigger.classList.add('cap-trigger');
      }
      const descEl = trigger.querySelector('.sel-desc');
      const desc = active.getAttribute('data-desc') || '';
      descEl.textContent = desc;
      descEl.style.display = desc ? '' : 'none';
      optionEls.forEach((el, i) => {
        const on = select.options[i].selected;
        el.classList.toggle('selected', on);
        el.setAttribute('aria-selected', String(on));
      });
    }

    function place() {
      const r = trigger.getBoundingClientRect();
      panel.style.top = (r.bottom + 6) + 'px';
      panel.style.left = r.left + 'px';
      panel.style.width = r.width + 'px';
    }
    function open() {
      place();
      wrapper.classList.add('open');
      panel.classList.add('open');
      trigger.setAttribute('aria-expanded', 'true');
      wrapper.closest('.card')?.classList.add('dropdown-open');
    }
    function close() {
      wrapper.classList.remove('open');
      panel.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
      wrapper.closest('.card')?.classList.remove('dropdown-open');
    }
    function isOpen() { return wrapper.classList.contains('open'); }

    // Reposiciona ao rolar/redimensionar: o painel é fixed no <body>, então a
    // âncora visual se move, mas o painel não.
    const reposition = () => { if (isOpen()) place(); };
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);

    trigger.addEventListener('click', () => {
      isOpen() ? close() : open();
    });
    document.addEventListener('click', (e) => {
      const inside = wrapper.contains(e.target) || panel.contains(e.target);
      if (!inside) close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && wrapper.classList.contains('open')) {
        close();
        trigger.focus();
      }
    });
    trigger.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const idx = select.selectedIndex;
        const next = e.key === 'ArrowDown'
          ? Math.min(select.options.length - 1, idx + 1)
          : Math.max(0, idx - 1);
        select.selectedIndex = next;
        sync();
      }
    });

    select.classList.add('select-native');
    select.setAttribute('tabindex', '-1');
    select.setAttribute('aria-hidden', 'true');
    select.addEventListener('change', sync);

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(trigger);
    // O painel vai direto para o <body>: como ele não herda nenhum stacking
    // context criado por backdrop-filter/transform dos cards, nenhum card
    // consegue pintar por cima.
    document.body.appendChild(panel);
    wrapper.appendChild(select);
    sync();

    // Escrever em select.value por codigo nao emite 'change', entao o trigger
    // rico continuaria mostrando o rotulo antigo - o que acontece toda vez que
    // a pagina de Ajustes aplica o que veio do servidor. O sync fica exposto
    // para quem escreve no select sem passar pelo usuario.
    select.syncRichSelect = sync;
  }
  /* ---------- RAIL DE NAVEGACAO ----------
     A lista de destinos das paginas. Antes ela existia SEIS vezes no HTML (rail
     fixo + menu do header, em cada uma das tres paginas) e mais TRES copias da
     logica em `initRail`, uma por arquivo. As copias ja tinham divergido:

     - o mapa `PAGES` do index.js e do scrap.js nao conhecia `/ajustes`;
     - a normalizacao do caminho era oposta: index/scrap ACRESCENTAVAM barra
       final, ajustes TIRAVA - nenhuma das duas cobria o formato da outra;
     - o fechamento do menu por clique de fora era diferente nos tres, e so o
       scrap chamava `stopPropagation` no clique do painel.

     Agora existe uma lista e um render. As paginas ficam so com os <ul> vazios
     marcados com `data-rail-list` - um no rail, um no menu do header - e o
     `renderRail` preenche os dois. Um destino novo entra aqui e aparece nas
     duas instancias e nas tres paginas, sem tocar em HTML.

     Sem JS o rail fica vazio. E' aceito de proposito: a interface ja depende de
     JS para tudo (o index desenha os cortes, o scrap desenha os resultados), e
     a alternativa - carimbar a lista no HTML - e' exatamente a duplicacao que
     este bloco existe para eliminar. */
  const RAIL_PAGES = [
    { path: '/',        ico: '▶', title: 'Estúdio', desc: 'Configurar e gerar os clips de um vídeo longo.' },
    { path: '/publicar', ico: '↥', title: 'Publicar', desc: 'A legenda e as hashtags de cada clip, prontas para o post.' },
    { path: '/ajustes', ico: '▦', title: 'Ajustes', desc: 'Durações, legendas, curador e prompt.' },
    { path: '/biblioteca', ico: '⤓', title: 'Biblioteca', desc: 'Buscar, revisar e importar vídeos.' },
  ];

  /* ---------- A MARCA E O CABECALHO DE CONTEXTO ----------

     Os dois moravam escritos a mao em cada um dos quatro HTML, e divergiram --
     nao por decisao, por copia:

       - a descricao da marca (`.rail-brand-copy small`) dizia "Editor de
         videos curtos" no index e no scrap, e o NOME DA PAGINA nas outras
         duas ("Ajustes", "Publicar"). E' o subtitulo do PRODUTO, entao nao
         pode mudar de pagina para pagina;
       - o botao do menu em tela pequena era um SVG no index/scrap e os
         caracteres `☰` / `▾` no ajustes/publicar, com dois rotulos
         diferentes ("Abrir navegacao" / "Menu de paginas"). O glifo depende
         da fonte instalada; o SVG, nao.

     Aqui a marca e' CONSTANTE e o cabecalho vem de uma tabela por pagina. Um
     `title`/`long`/`short` novo entra na tabela e o HTML nao muda. */

  //: O subtitulo do produto. Um so' para as quatro paginas, de proposito.
  const BRAND = {
    nome: 'Viral Clipper',
    desc: 'Editor de vídeos curtos',
    badge: 'LOCAL',
    badgeTitulo: 'Aplicativo local',
  };

  //: O icone da marca. O MESMO do favicon: um clipe com um play dentro.
  const BRAND_ICONE = '<img src="/favicon.svg" alt="" width="36" height="36">';

  /* O cabecalho de contexto de cada pagina, chaveado pela rota canonica.

     `raiz` e' a classe do elemento de FORA e `pre` o prefixo das de dentro.
     Sao dois campos, e nao um, porque a raiz NAO segue a regra das filhas: o
     cabecalho comum chama-se `.header-context` (a raiz) e suas filhas
     `.header-context-icon`, `.header-context-eyebrow`, ... -- ou seja, o
     prefixo das filhas E' o nome da raiz. Derivar a raiz de `pre + '-context'`
     produzia `header-context-context`, uma classe que nenhum CSS define: o
     wrapper perdia o `display:flex` e a altura ia de 38px para 102px. O bug
     so' nao apareceu na Biblioteca porque la' o prefixo (`scrap-header`) nao
     contem a palavra `context`, entao `scrap-header-context` saiu certo por
     sorte. Com dois campos explicitos nao ha' o que derivar.

     Nao da' para usar um prefixo so' nas quatro: a pagina da Biblioteca nao
     carrega o `index.css` (que define `.header-context-*`) e traz as proprias
     regras `.scrap-header-*` no `scrap.css`. As duas familias medem IDENTICO --
     icone 38x38, raio 11px, eyebrow mesma cor e tamanho, mesma altura --, mas o
     `scrap.css` tambem sobrescreve `.card` e `.btn` de proposito, entao trocar
     a folha que a Biblioteca carrega mexeria na cascata da pagina inteira por um
     ganho que e' so' de nomenclatura. Unificar o MARKUP (uma fonte, um render)
     e' o que tira a copia; unificar o nome da classe e' outra conversa.

     `eyebrow` e' o kicker em maiusculas; `long` e' o complemento depois da
     barra (o que sobra na tela larga) e `short` o rotulo que o substitui
     abaixo de 920px. O `<span aria-hidden="true">/</span>` que separa os dois
     vai no template, nao aqui: e' apresentacao, nao conteudo. */
  const HC = {
    '/': {
      raiz: 'header-context',
      pre: 'header-context',
      aria: 'Estúdio de cortes verticais',
      eyebrow: 'ESPAÇO DE TRABALHO',
      title: 'Estúdio',
      long: 'edição de cortes verticais',
      short: 'Editor',
      //: O ponto verde de "Processamento local". So' a raiz o exibe.
      status: 'Processamento local',
      icone: '<svg viewBox="0 0 24 24" fill="none"><rect x="3.5" y="4.5" width="17" height="15" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="m10 8.5 5 3.5-5 3.5v-7Z" fill="currentColor"/><path d="M7 2.75v3M17 2.75v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    },
    '/publicar': {
      raiz: 'header-context',
      pre: 'header-context',
      aria: 'Publicação dos clips gerados',
      eyebrow: 'PÓS-RENDER',
      title: 'Publicar',
      long: 'Legenda e tags',
      short: 'Publicar',
      icone: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 15.5V4m0 0L7.75 8.25M12 4l4.25 4.25" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M4.75 14.5v3.75A1.75 1.75 0 0 0 6.5 20h11a1.75 1.75 0 0 0 1.75-1.75V14.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    },
    '/ajustes': {
      raiz: 'header-context',
      pre: 'header-context',
      aria: 'Ajustes do aplicativo',
      eyebrow: 'CONFIGURAÇÕES',
      title: 'Ajustes',
      long: 'Preferências de edição',
      short: 'Ajustes',
      icone: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 8.25h5m4 0h5M5 15.75h9m4 0h1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="12" cy="8.25" r="2" stroke="currentColor" stroke-width="1.6"/><circle cx="16" cy="15.75" r="2" stroke="currentColor" stroke-width="1.6"/></svg>',
    },
    '/biblioteca': {
      raiz: 'scrap-header-context',
      pre: 'scrap-header',
      aria: 'Biblioteca de vídeos',
      eyebrow: 'BIBLIOTECA DE MÍDIA',
      title: '',
      long: 'Importação de vídeos',
      short: 'Biblioteca',
      //: O selo do cabeçalho. So' a Biblioteca o tem.
      tag: 'Downloads neste PC',
      tagIcone: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 2.8 16.5 5v4.6c0 3.5-2.7 6.2-6.5 7.7-3.8-1.5-6.5-4.2-6.5-7.7V5L10 2.8Z" stroke="currentColor" stroke-width="1.35"/><path d="m7.3 9.8 1.8 1.8 3.7-3.8" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      icone: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3.5v10m0 0 3.7-3.7M12 13.5l-3.7-3.7M5 15.5v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    },
  };

  //: O menu em tela pequena. SVG de proposito: o glifo `☰` depende da fonte
  //: instalada e some onde ela nao existe; a pagina ficaria sem o icone. Duas
  //: das quatro paginas usavam o caractere, duas usavam o SVG, e o rotulo
  //: tambem divergia ("Abrir navegacao" / "Menu de paginas").
  const MENU_BTN = {
    rotulo: 'Abrir navegação',
    barras: '<svg viewBox="0 0 24 24" fill="none"><path d="M4 6.5h16M4 12h16M4 17.5h16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    seta: '<svg viewBox="0 0 16 16" fill="none"><path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  function brandHtml() {
    return '<a class="rail-brand-link" href="/" aria-label="' +
        esc(BRAND.nome) + ' — abrir o Estúdio">' +
        '<span class="brand-mark" aria-hidden="true">' + BRAND_ICONE + '</span>' +
        '<span class="rail-brand-copy"><strong>' + esc(BRAND.nome) +
          '</strong><small>' + esc(BRAND.desc) + '</small></span>' +
      '</a>' +
      '<span class="rail-brand-badge" aria-label="' + esc(BRAND.badgeTitulo) +
        '">' + esc(BRAND.badge) + '</span>';
  }

  function headerContextHtml(ctx) {
    const p = ctx.pre;
    //: `ctx.raiz` e' o nome EXATO da classe de fora; `p` so' prefixa as filhas.
    //: Sao diferentes: a raiz comum chama-se `.header-context` e as filhas
    //: `.header-context-icon` -- ou seja, o prefixo das filhas E' o nome da
    //: raiz. Montar a raiz como `p + '-context'` dava `header-context-context`,
    //: que nenhum CSS define, e o wrapper perdia o `display:flex` (altura
    //: 38px -> 102px). Ver o comentario da tabela HC.
    const status = ctx.status
      ? '<span class="' + p + '-status"><span aria-hidden="true"></span>' +
        esc(ctx.status) + '</span>'
      : '';
    const tag = ctx.tag
      ? '<span class="' + p + '-tag">' + ctx.tagIcone + esc(ctx.tag) + '</span>'
      : '';
    // A raiz escreve "Estúdio / edicao..."; a Biblioteca nao tem o rotulo
    // antes da barra (o eyebrow ja' diz "BIBLIOTECA DE MIDIA" e o strong e' o
    // proprio `long`). Por isso `title` vazio nao deixa espaco solto.
    const antes = ctx.title ? esc(ctx.title) + ' ' : '';
    return '<div class="' + ctx.raiz + '" aria-label="' + esc(ctx.aria) + '">' +
      '<span class="' + p + '-icon" aria-hidden="true">' + ctx.icone + '</span>' +
      '<span class="' + p + '-copy">' +
        '<span class="' + p + '-eyebrow">' + esc(ctx.eyebrow) + '</span>' +
        '<strong>' + antes +
          '<span class="' + p + '-long">' +
            (ctx.title ? '<span aria-hidden="true">/</span> ' : '') +
            esc(ctx.long) + '</span>' +
          '<span class="' + p + '-short">' + esc(ctx.short) + '</span>' +
        '</strong>' +
      '</span>' + status + tag +
    '</div>';
  }

  function menuBtnHtml() {
    return '<button class="menu-btn pressable" type="button" id="rail-menu-btn"' +
        ' aria-haspopup="true" aria-expanded="false" aria-controls="rail-menu-sm">' +
        '<span class="mb-bars" aria-hidden="true">' + MENU_BTN.barras + '</span>' +
        '<span class="sr-only">' + esc(MENU_BTN.rotulo) + '</span>' +
        '<span class="mb-caret" aria-hidden="true">' + MENU_BTN.seta + '</span>' +
      '</button>' +
      '<ul class="rail-menu" id="rail-menu-sm" data-rail-menu data-rail-list hidden></ul>';
  }

  /* Preenche a marca, o cabecalho e o menu em tela pequena.

     Cada hospedeiro e' procurado por um `data-` proprio, entao uma pagina pode
     ter so' um dos tres sem quebrar -- e uma pagina que nao tenha nenhum
     simplesmente nao recebe nada. */
  function renderChrome() {
    const current = railKey(window.location.pathname);
    const ctx = HC[current] || HC['/'];

    const brandHosts = Array.prototype.slice.call(document.querySelectorAll('[data-brand]'));
    if (brandHosts.length) {
      const html = brandHtml();
      brandHosts.forEach((host) => { host.innerHTML = html; });
    }

    const ctxHosts = Array.prototype.slice.call(document.querySelectorAll('[data-header-context]'));
    if (ctxHosts.length) {
      const html = headerContextHtml(ctx);
      ctxHosts.forEach((host) => { host.innerHTML = html; });
    }

    const menuHosts = Array.prototype.slice.call(document.querySelectorAll('[data-menu-picker]'));
    if (menuHosts.length) {
      const html = menuBtnHtml();
      menuHosts.forEach((host) => { host.innerHTML = html; });
    }
  }

  // '/', '/index.html', '/biblioteca', '/ajustes/', '/qualquer' ->
  // a chave canonica da RAIL_PAGES. Desconhecido cai em '/'.
  function railKey(pathname) {
    let key = String(pathname == null ? '/' : pathname).replace(/\.html$/i, '');
    if (key === '/index') key = '/';
    if (key.length > 1) key = key.replace(/\/+$/, '');
    return RAIL_PAGES.some((page) => page.path === key) ? key : '/';
  }

  function railItemHtml(page, current) {
    const marked = page.path === current ? ' aria-current="page"' : '';
    return '<li>' +
        '<a class="rail-item pressable" href="' + esc(page.path) + '"' +
          ' data-rail-page="' + esc(page.path) + '"' + marked + '>' +
          '<span class="ri-ico" aria-hidden="true">' + esc(page.ico) + '</span>' +
          '<span>' +
            '<span class="ri-title">' + esc(page.title) + '</span>' +
            '<span class="ri-desc">' + esc(page.desc) + '</span>' +
          '</span>' +
        '</a>' +
      '</li>';
  }

  function renderRail() {
    const current = railKey(window.location.pathname);
    const here = RAIL_PAGES.filter((page) => page.path === current)[0] || RAIL_PAGES[0];

    document.title = here.title + ' · Viral Clipper';

    // Todas as instancias da lista recebem o MESMO HTML, ja com o item da
    // pagina atual marcado. Gerar a marca aqui, em vez de varrer o documento
    // depois, evita o instante em que a lista existe sem nenhum item marcado.
    const lists = Array.prototype.slice.call(document.querySelectorAll('[data-rail-list]'));
    const html = RAIL_PAGES.map((page) => railItemHtml(page, current)).join('');
    lists.forEach((list) => { list.innerHTML = html; });

    // O menu do header e o unico componente com estado aqui. A guarda impede
    // que uma segunda chamada (uma pagina que chame renderRail de novo) ligue
    // os listeners duas vezes - o toggle abriria e fecharia no mesmo clique.
    const menu = document.querySelector('[data-rail-menu]');
    const btn = document.querySelector('[data-rail-picker] .menu-btn');
    if (!menu || !btn || menu.dataset.railWired) return;
    menu.dataset.railWired = '1';

    const setOpen = (open) => {
      menu.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
    };

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = menu.hidden;
      setOpen(willOpen);
      if (willOpen) {
        const first = menu.querySelector('.rail-item');
        if (first) first.focus({ preventScroll: true });
      }
    });

    // O painel fica sobre o conteudo. Sem o stopPropagation o clique no item
    // fecharia o menu E dispararia o clique no elemento por baixo; e o
    // `contains` evita fechar quando o clique caiu dentro do painel, mas fora
    // de um item.
    menu.addEventListener('click', (e) => {
      if (e.target.closest('.rail-item')) setOpen(false);
      e.stopPropagation();
    });

    document.addEventListener('click', (e) => {
      if (!menu.hidden && !menu.contains(e.target)) setOpen(false);
    });

    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || menu.hidden) return;
      setOpen(false);
      // Devolve o foco ao botao, senao ele some para o body e o usuario de
      // teclado perde a posicao.
      btn.focus({ preventScroll: true });
    });
  }

  /* ---------- RODAPE ----------
     O rodape se monta sozinho, como o rail, e pelo mesmo motivo: a navegacao
     dele era uma SEGUNDA copia das rotas escrita a mao no HTML (`btn-rail-cortes`
     -> `/`, `btn-rail-scrap` -> `/scrap`). Duas listas da mesma coisa divergem --
     foi exatamente o que aconteceu com o rail, que chegou a ter tres nomes para
     o mesmo destino.

     Aqui a navegacao e' DERIVADA de RAIL_PAGES: acrescentar uma pagina ao menu
     a coloca no rodape sem tocar em HTML nenhum. O unico destino que nao vem do
     rail e' "Como usar" (`/docs`), que e' ajuda e nao uma etapa do fluxo.

     O rodape vive nas TRES paginas. Antes so o index o tinha -- e como ele era
     a unica entrada para `/docs`, a ajuda ficava inalcancavel de Ajustes e
     Biblioteca. */

  const ICONE_MARCA =
    '<svg viewBox="0 0 24 24" fill="none">' +
      '<path d="m9 6 9 6-9 6V6Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>' +
      '<path d="M5 5v14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' +
    '</svg>';

  const ICONE_COPIA =
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<rect x="8" y="8" width="12" height="12" rx="3" stroke="currentColor" stroke-width="1.7"/>' +
      '<path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" stroke="currentColor" stroke-width="1.7"/>' +
    '</svg>';

  // O comando da CLI mora aqui UMA vez. O botao de copiar le o texto deste
  // `<code>` em vez de guardar uma copia propria: duas copias do mesmo comando
  // divergem na primeira edicao.
  const COMANDO_CLI = 'python -m viralclipper';

  function footerNavHtml() {
    // "Como usar" primeiro: e' a saida de quem ainda nao sabe o que fazer, e o
    // rodape e' justamente onde essa pessoa chega.
    const ajuda =
      '<button class="footer-link" type="button" data-footer-action="docs">Como usar</button>';
    return ajuda + RAIL_PAGES.map((page) =>
      '<button class="footer-link" type="button" data-footer-page="' + esc(page.path) + '">' +
        esc(page.title) +
      '</button>'
    ).join('');
  }

  function footerHtml() {
    return '' +
      '<div class="footer-container">' +
        '<div class="footer-main">' +
          '<a class="footer-brand" href="/" aria-label="Viral Clipper — início">' +
            '<span class="footer-brand__icon" aria-hidden="true">' + ICONE_MARCA + '</span>' +
            '<span class="footer-brand__content">' +
              '<span class="footer-brand__name">viral-clipper</span>' +
              '<span class="footer-brand__description">Seu conteúdo. Novas possibilidades.</span>' +
            '</span>' +
          '</a>' +
          '<nav class="footer-nav" aria-label="Navegação do rodapé">' + footerNavHtml() + '</nav>' +
        '</div>' +
        '<div class="footer-bottom">' +
          '<p class="footer-caption">' +
            'Interface web' +
            '<span class="footer-caption__separator" aria-hidden="true"></span>' +
            'Também disponível via CLI' +
          '</p>' +
          '<div class="footer-cli">' +
            '<span class="footer-cli__prompt" aria-hidden="true">$</span>' +
            '<code id="footer-cli-command">' + COMANDO_CLI + '</code>' +
            '<button class="footer-copy" type="button" id="btn-copy-cli"' +
              ' aria-label="Copiar comando da CLI" aria-describedby="footer-cli-command">' +
              ICONE_COPIA +
            '</button>' +
          '</div>' +
        '</div>' +
        '<p class="footer-feedback" id="footer-feedback" role="status"' +
          ' aria-live="polite" aria-atomic="true"></p>' +
      '</div>';
  }

  function wireFooter(host) {
    const nav = host.querySelector('.footer-nav');
    if (nav) {
      // Delegacao: um listener cobre os destinos todos. Sem um id por botao, a
      // lista pode crescer (uma pagina nova em RAIL_PAGES) sem que este arquivo
      // ganhe mais uma linha por destino.
      nav.addEventListener('click', (ev) => {
        const destino = ev.target.closest('[data-footer-page]');
        if (destino) {
          window.location.href = destino.getAttribute('data-footer-page');
          return;
        }
        if (ev.target.closest('[data-footer-action="docs"]')) {
          // O README do repo renderizado pelo proprio servidor. O link para o
          // GitHub dava 404 (repo privado/renomeado) -- a unica ajuda do
          // produto nao pode depender de endereco externo.
          window.open('/docs', '_blank');
        }
      });
    }

    const cmdEl = host.querySelector('#footer-cli-command');
    const feedbackEl = host.querySelector('#footer-feedback');
    const copiar = host.querySelector('#btn-copy-cli');
    if (!cmdEl || !copiar) return;

    const timer = { id: 0 };
    // O aviso se limpa sozinho: um "copiado" permanente vira parte do rodape, e
    // quem olhasse depois nao saberia se a copia foi agora ou cinco minutos
    // atras. O timer e cancelado a cada anuncio para um aviso novo nao ser
    // apagado pelo anterior.
    const anunciar = (texto) => {
      if (!feedbackEl) return;
      window.clearTimeout(timer.id);
      feedbackEl.textContent = texto;
      timer.id = window.setTimeout(() => { feedbackEl.textContent = ''; }, 5000);
    };

    copiar.addEventListener('click', async (ev) => {
      const botao = ev.currentTarget;
      const comando = cmdEl.textContent.trim();
      // `isSecureContext` antes de tentar: em http puro a `navigator.clipboard`
      // existe mas rejeita, e o aviso generico de erro esconderia o motivo real
      // ("copie a mao") atras de um "nao foi possivel". O teste e' o que escolhe
      // a mensagem certa, nao o que evita a tentativa.
      if (!window.isSecureContext || !navigator.clipboard || !navigator.clipboard.writeText) {
        anunciar('Cópia automática indisponível. Selecione e copie o comando.');
        return;
      }
      // Desabilitar durante a copia impede o clique duplo de disparar duas
      // escritas concorrentes no clipboard, cujo resultado e indefinido. E o
      // `finally` devolve o botao mesmo quando a copia falha: sem ele um erro
      // unico deixaria o controle morto para sempre.
      botao.disabled = true;
      try {
        await navigator.clipboard.writeText(comando);
        anunciar('Comando copiado para a área de transferência.');
      } catch (err) {
        // Clipboard negado (permissao, iframe): o comando continua visivel na
        // tela, entao falhar em copiar nao e' um beco -- o aviso diz o que
        // fazer em vez de so acusar o erro.
        anunciar('Não foi possível copiar. Selecione e copie o comando.');
      } finally {
        botao.disabled = false;
      }
    });
  }

  function renderFooter() {
    const hosts = Array.prototype.slice.call(document.querySelectorAll('[data-footer]'));
    if (!hosts.length) return;
    const html = footerHtml();
    hosts.forEach((host) => {
      host.innerHTML = html;
      wireFooter(host);
    });
  }

  // Cada pagina so precisa incluir /comum.js: o rail, a marca, o cabecalho de
  // contexto e o rodape se montam sozinhos. O readyState cobre o caso de o
  // script ser movido para o <head>.
  //
  // A ORDEM importa: `renderChrome` escreve o botao do menu em tela pequena, e
  // o `renderRail` e' quem liga o listener nele. Ao contrario, o
  // `renderRail` procuraria um botao que ainda nao existe e a guarda
  // `if (!menu || !btn) return` sairia calada -- o menu nunca abriria.
  const monta = () => { renderChrome(); renderRail(); renderFooter(); };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', monta);
  } else {
    monta();
  }

  global.esc = esc;
  global.enhanceSelect = enhanceSelect;
  global.renderRail = renderRail;
  global.renderFooter = renderFooter;
  global.renderChrome = renderChrome;
  global.RAIL_PAGES = RAIL_PAGES;
  global.BRAND = BRAND;
  global.HC = HC;
})(window);
