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
    { path: '/',        ico: '▶', title: 'Cortes',  desc: 'Configurar e gerar os clips de um vídeo longo.' },
    { path: '/ajustes', ico: '▦', title: 'Ajustes', desc: 'Durações, legendas, curador e prompt.' },
    { path: '/scrap',   ico: '⤓', title: 'Biblioteca', desc: 'Buscar, revisar e importar vídeos.' },
  ];

  // '/', '/index.html', '/scrap', '/scrap.html', '/ajustes/', '/qualquer' ->
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

  // Cada pagina so precisa incluir /comum.js: o rail se monta sozinho. O
  // readyState cobre o caso de o script ser movido para o <head> um dia.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderRail);
  } else {
    renderRail();
  }

  global.esc = esc;
  global.enhanceSelect = enhanceSelect;
  global.renderRail = renderRail;
  global.RAIL_PAGES = RAIL_PAGES;
})(window);
