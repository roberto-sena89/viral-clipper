(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const state = { running: false, jobs: [], clips: [], viral: [] };

  // ---------- rail lateral (menu principal) ----------
  // A lista de destinos vive no HTML: uma vez no rail fixo, e uma copia no
  // menu do header que aparece abaixo de 920px. Aqui so marcamos qual e a
  // pagina atual — derivado de location.pathname, nunca escrito a mao, para
  // que adicionar uma pagina nova nao exija mexer em estado.
  const PAGES = {
    '/':          'Cortes',
    '/scrap':     'Scrap',
  };

  (function initRail() {
    const path = window.location.pathname.replace(/\/index\.html$/, '/');
    const norm = path.endsWith('/') ? path : path + '/';
    const key = PAGES[path] ? path : (PAGES[norm] ? norm : '/');
    const title = PAGES[key];

    document.title = `${title} · Viral Clipper`;

    // Cada instancia da lista (rail + menu do header) marca o seu proprio item.
    $$('[data-rail-page]').forEach((item) => {
      if (item.getAttribute('data-rail-page') === key) {
        item.setAttribute('aria-current', 'page');
      }
    });

    // O menu do header e o unico componente com estado aqui: abre e fecha.
    const menu = $('[data-rail-menu]');
    const btn = $('[data-rail-picker] .menu-btn');
    if (!menu || !btn) return;

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

    // O painel fica sobre o conteudo; sem isto um clique nele fecharia o menu
    // E dispararia a acao do elemento por baixo.
    menu.addEventListener('click', (e) => {
      if (e.target.closest('.rail-item')) setOpen(false);
    });

    document.addEventListener('click', () => setOpen(false));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const wasOpen = !menu.hidden;
        setOpen(false);
        // Devolve o foco ao botao, senao ele some para o body e o usuario de
        // teclado perde a posicao.
        if (wasOpen) btn.focus({ preventScroll: true });
      }
    });
  })();

  // ---------- toggles ----------
  $$('.toggle').forEach((t) => {
    t.addEventListener('click', () => {
      const on = t.classList.toggle('active');
      t.setAttribute('aria-checked', String(on));
    });
  });

  // ---------- selects ricos (dropdown custom) ----------
  // Progressive enhancement: o <select> nativo continua no form como fonte de
  // verdade (collectOptions lê .value), mas a interação visual acontece no
  // componente rico. Teclado: setas trocam a opção, Enter/Espaço abre/fecha,
  // Esc fecha.

  // Espelho visual dos presets do backend (viralclipper/caption_presets.py) em
  // CSS: o contorno do ASS vira text-shadow em 4 direcoes e a caixa vira um
  // background arredondado.
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
  }
  $$('select[id]').forEach(enhanceSelect);

  // ---------- helpers ----------
  function toast(msg, kind) {
    const zone = $('#toast-zone');
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = msg;
    zone.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 300ms'; setTimeout(() => el.remove(), 320); }, 4200);
  }

  function log(line, cls) {
    const box = $('#log-box');
    const span = document.createElement('span');
    if (cls) span.className = cls;
    span.textContent = line + '\n';
    box.appendChild(span);
    box.scrollTop = box.scrollHeight;
  }

  function setStatus(kind, text) {
    const pill = $('#status-pill');
    pill.className = 'status-pill' + (kind ? ' ' + kind : '');
    $('#status-text').textContent = text;
  }

  function setProgress(pct, label) {
    const bar = $('#progress-bar');
    bar.style.width = pct + '%';
    $('#progress-track').setAttribute('aria-valuenow', String(Math.round(pct)));
    if (label) $('#progress-label').textContent = label;
    if (pct >= 100) bar.classList.add('done'); else bar.classList.remove('done');
  }

  // ---------- etapas da execucao ----------
  // A barra responde "quanto falta?", e num job de minutos ela nao tem o que
  // responder: o pipeline reporta o fim de cada fase, nao um percentual. A
  // escada responde "o que esta acontecendo agora?" — a pergunta que o usuario
  // faz quando a barra esta parada. A ordem das etapas vem do servidor
  // (`/run/progress.stages`), entao a tela nao inventa fases que o pipeline
  // nao tem.
  function renderSteps(stages) {
    const list = $('#step-list');
    if (!list) return;
    list.textContent = '';
    (stages || []).forEach((stage) => {
      const li = document.createElement('li');
      li.setAttribute('data-state', stage.state || 'pendente');
      li.textContent = stage.label || stage.key || '';
      list.appendChild(li);
    });
  }

  // O log durante o job e SUBSTITUIDO pelo que o poll traz, e nao acrescentado:
  // o endpoint devolve a cauda da lista a cada segundo, e um `append` mostraria
  // a mesma linha uma vez por segundo. Substituir tambem conserta o caso de uma
  // linha perdida — o proximo poll a traz de volta.
  function renderLog(lines) {
    const box = $('#log-box');
    if (!box) return;
    box.textContent = '';
    (lines || []).forEach((line) => {
      const span = document.createElement('span');
      span.textContent = line + '\n';
      box.appendChild(span);
    });
    box.scrollTop = box.scrollHeight;
  }

  const runProgress = { timer: null, header: '' };

  function stopFollowingRun() {
    if (runProgress.timer) { clearInterval(runProgress.timer); runProgress.timer = null; }
  }

  async function followRun() {
    const r = await api('/run/progress');
    if (r.offline || r.error) return;
    renderSteps(r.stages);
    // `stage_label` cobre a janela entre o POST e a primeira fase: o servidor
    // publica "Preparando…" antes de anunciar qualquer etapa.
    if (r.stage_label) setProgress(0, r.stage_label);
    if (Array.isArray(r.lines)) renderLog([runProgress.header, ...r.lines]);
  }

  function startFollowingRun(header) {
    runProgress.header = header;
    stopFollowingRun();
    followRun();
    runProgress.timer = setInterval(followRun, 1000);
  }

  // ---------- skeleton ----------
  // O render leva minutos. Durante a espera as duas regioes de resultado sao
  // trocadas por um esqueleto na forma do conteudo que vai chegar: o usuario
  // le "ocupado" sem ler o log. O guard conta chamadas concorrentes porque a
  // galeria e re-renderizada pelo poll de /status.
  const skeletonCount = { gallery: 0, queue: 0 };

  function gallerySkeleton(n) {
    let html = '';
    for (let i = 0; i < n; i++) {
      html += '<div class="skeleton-card">' +
                '<div class="shimmer skeleton-thumb"></div>' +
                '<div class="shimmer skeleton-line w-80"></div>' +
                '<div class="shimmer skeleton-line w-60"></div>' +
              '</div>';
    }
    return html;
  }

  function queueSkeleton(n) {
    let html = '';
    for (let i = 0; i < n; i++) {
      html += '<div class="skeleton-card" style="padding:14px 0;">' +
                '<div class="shimmer skeleton-line w-60"></div>' +
                '<div class="shimmer skeleton-line w-40"></div>' +
              '</div>';
    }
    return html;
  }

  function showSkeletons() {
    const g = $('#gallery');
    if (g) {
      skeletonCount.gallery++;
      g.innerHTML = '<div class="skeleton-card" style="grid-column: 1 / -1;">' +
                    gallerySkeleton(4) + '</div>';
    }
    const q = $('#queue-list');
    if (q) {
      skeletonCount.queue++;
      q.innerHTML = queueSkeleton(2);
    }
  }

  function hideSkeletons() {
    skeletonCount.gallery = 0;
    skeletonCount.queue = 0;
  }

  // ---------- coleta do form ----------
  function toggleOn(id) { return $(id).classList.contains('active'); }

  function collectOptions() {
    const o = {
      url: $('#url').value.trim(),
      output: $('#output').value.trim() || 'output',
      count: parseInt($('#count').value, 10) || 5,
      download_mode: $('#download-mode').value,
      cookies_from_browser: $('#cookies-from-browser').value || null,
      cookies_file: $('#cookies-file').value.trim() || null,
      min_duration: parseFloat($('#min-duration').value) || 30,
      max_duration: parseFloat($('#max-duration').value) || 60,
      target_duration: parseFloat($('#target-duration').value) || 42,
      min_score: parseFloat($('#min-score').value) || 0,
      min_gap: parseFloat($('#min-gap').value) || 6,
      engine: $('#engine').value,
      whisper_model: $('#whisper-model').value,
      language: $('#language').value || null,
      beam_size: parseInt($('#beam-size').value, 10) || 1,
      vad_filter: toggleOn('#vad-filter'),
      transcript_cache: toggleOn('#transcript-cache'),
      layout: $('#layout').value,
      caption_style: $('#caption-style').value,
      caption_preset: $('#caption-preset').value,
      font_size: parseFloat($('#font-size').value) || null,
      crf: parseInt($('#crf').value, 10) || 20,
      lufs: parseFloat($('#lufs').value) || -14,
      workers: parseInt($('#workers').value, 10) || 2,
      jump_cut: toggleOn('#jump-cut'),
      loudnorm: toggleOn('#loudnorm'),
      ranker: toggleOn('#ranker-llm') ? 'llm' : 'none',
      ranker_model: $('#ranker-model').value,
      ranker_base_url: $('#ranker-base-url').value,
      ranker_top_n: parseInt($('#ranker-top-n').value, 10) || 24,
      ranker_weight: parseFloat($('#ranker-weight').value) || 0.6,
      cache_dir: $('#cache-dir').value.trim() || null,
      transcript_text: $('#transcript').value.trim() || null,
      // Headline only when the toggle is on; otherwise captions are the whole
      // burned overlay.
      headline_seconds: toggleOn('#headline-on')
        ? (parseFloat($('#headline-seconds').value) || 3)
        : 0,
      progress_bar: toggleOn('#progress-bar-on'),
      };
    return o;
  }

  // ---------- backend ----------
  // A UI conversa com um servidor local opcional (web/server.py).
  // Sem servidor, ela mostra os parâmetros e o comando CLI equivalente.
  const API = 'http://127.0.0.1:7755';

  async function api(path, opts) {
    try {
      const res = await fetch(API + path, opts);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) {
      return { error: e.message, offline: true };
    }
  }

  function cliCommand(o) {
    const parts = ['python', '-m', 'viralclipper', JSON.stringify(o.url)];
    parts.push('-o', o.output);
    parts.push('-n', String(o.count));
    parts.push('--min', String(o.min_duration), '--max', String(o.max_duration));
    parts.push('--target', String(o.target_duration));
    parts.push('--min-score', String(o.min_score));
    parts.push('--min-gap', String(o.min_gap));
    parts.push('--engine', o.engine);
    parts.push('--model', o.whisper_model);
    if (o.language) parts.push('--language', o.language);
    parts.push('--beam-size', String(o.beam_size));
    if (!o.vad_filter) parts.push('--no-vad');
    if (!o.transcript_cache) parts.push('--no-transcript-cache');
    parts.push('--download-mode', o.download_mode);
    if (o.cookies_file) parts.push('--ytdlp-arg', '--cookies', o.cookies_file);
    else if (o.cookies_from_browser) parts.push('--cookies-from-browser', o.cookies_from_browser);
    parts.push('--layout', o.layout);
    if (o.transcript_text) parts.push('--transcript caminho/da/transcricao.srt');
    parts.push('--caption-style', o.caption_style);
    parts.push('--font-size', String(o.font_size));
    parts.push('--crf', String(o.crf));
    parts.push('--lufs', String(o.lufs));
    parts.push('--workers', String(o.workers));
    if (o.jump_cut) parts.push('--jump-cut');
    if (!o.loudnorm) parts.push('--no-loudnorm');
    if (o.ranker === 'llm') {
      parts.push('--ranker', 'llm');
      parts.push('--ranker-model', o.ranker_model);
      parts.push('--ranker-base-url', o.ranker_base_url);
      parts.push('--ranker-top-n', String(o.ranker_top_n));
      parts.push('--ranker-weight', String(o.ranker_weight));
    }
    return parts.join(' ');
  }

  // ---------- fila ----------
  function renderQueue() {
    const list = $('#queue-list');
    list.innerHTML = '';
    if (!state.jobs.length) {
      list.innerHTML = '<div class="empty-state">Nenhum job na fila ainda.<br>Gere clips para vê-lo aqui.</div>';
      return;
    }
    state.jobs.forEach((j) => {
      const el = document.createElement('div');
      el.className = 'job';
      el.innerHTML =
        '<span class="job-dot ' + j.status + '"></span>' +
        '<div class="job-info"><div class="job-url"></div><div class="job-meta"></div></div>';
      el.querySelector('.job-url').textContent = j.url || '(sem url)';
      el.querySelector('.job-meta').textContent = j.meta || '';
      list.appendChild(el);
    });
  }

  // ---------- relatorio de viralizacao ----------
  // `esc` mora em `comum.js`: as duas paginas precisam do mesmo escapamento
  // e divergentes entre si, e a versao fraca (que nao escapa aspas) era o
  // risco embutido. Ver o comentario de la.

  function potentialClass(value) {
    if (value >= 70) return '';
    return value >= 45 ? 'mid' : 'low';
  }

  function metricRow(label, value) {
    const pct = Math.max(0, Math.min(100, Number(value) || 0));
    return '<div class="metric"><div class="m-top"><span>' + esc(label) + '</span>' +
      '<strong>' + pct + '/100</strong></div>' +
      '<div class="metric-track"><div class="metric-bar" style="width:' + pct + '%"></div></div></div>';
  }

  function renderViral(list, title) {
    const section = $('#viral-section');
    const box = $('#viral-list');
    if (!list || !list.length) {
      section.hidden = true;
      box.innerHTML = '';
      return;
    }
    if (title) $('#viral-title').textContent = 'Relatório de viralização · ' + title;
    box.innerHTML = '';
    list.forEach((v) => {
      const el = document.createElement('div');
      el.className = 'viral-item';
      el.innerHTML =
        '<div class="viral-head"><h3><span class="vn">#' + esc(v.index) + '</span> ' + esc(v.headline) + '</h3>' +
        '<span class="viral-potential ' + potentialClass(v.viral_potential) + '">Potencial ' + esc(v.viral_potential) + '%</span></div>' +
        '<div class="viral-time">' + esc(v.start_label) + ' → ' + esc(v.end_label) + ' · ' +
        Math.round(v.duration) + 's · score técnico ' + Number(v.score).toFixed(1) + '</div>' +
        '<div class="viral-field"><strong>Assunto:</strong> ' + esc(v.subject) + '</div>' +
        '<div class="viral-field"><strong>Por que pode viralizar:</strong> ' + esc(v.why) + '</div>' +
        '<div class="viral-quote">' + esc(v.hook) + '</div>' +
        '<div class="viral-field"><strong>Momento mais forte:</strong> ' + esc(v.peak) + '</div>' +
        '<div class="viral-field"><strong>Conclusão:</strong> ' + esc(v.conclusion) + '</div>' +
        '<div class="viral-metrics">' +
        metricRow('Retenção', v.retention) + metricRow('Comentários', v.comments) +
        metricRow('Compartilhamentos', v.shares) + metricRow('Polêmica', v.controversy) +
        '</div>';
      box.appendChild(el);
    });
    section.hidden = false;
  }

  // ---------- transcricao organizada ----------
  function renderCueTable(data) {
    const stats = data.stats || {};
    const parts = [
      stats.cues + ' falas',
      stats.words + ' palavras',
    ];
    if (stats.duplicates_removed) parts.push(stats.duplicates_removed + ' repetição(ões) removida(s)');
    if (stats.fragments_merged) parts.push(stats.fragments_merged + ' fragmento(s) unido(s)');
    if (stats.reordered) parts.push(stats.reordered + ' fala(s) reordenada(s)');
    $('#cue-stats').textContent = parts.join(' · ');

    const body = $('#cue-body');
    body.innerHTML = '';
    (data.cues || []).forEach((cue) => {
      const row = document.createElement('tr');
      const time = document.createElement('td');
      time.className = 'cue-time';
      time.textContent = cue.label;
      const duration = document.createElement('td');
      duration.className = 'cue-dur';
      duration.textContent = Math.round(cue.duration) + 's';
      const text = document.createElement('td');
      text.className = 'cue-text';
      text.textContent = cue.text;
      row.append(time, duration, text);
      body.appendChild(row);
    });
    $('#cue-preview').hidden = !(data.cues || []).length;
  }

  async function organizeTranscript(silent) {
    const raw = $('#transcript').value.trim();
    if (!raw) {
      if (!silent) {
        toast('Cole a transcrição primeiro.', 'err');
        $('#transcript').focus();
      }
      return;
    }
    const r = await api('/transcript/normalize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript: raw }),
    });
    if (r.offline) {
      if (!silent) toast('Backend offline: inicie web/server.py para organizar.', 'err');
      return;
    }
    if (r.error) {
      if (!silent) toast('Não foi possível organizar: ' + r.error, 'err');
      return;
    }
    $('#transcript').value = r.normalized || '';
    renderCueTable(r);
    const stats = r.stats || {};
    if (!silent || stats.duplicates_removed || stats.fragments_merged || stats.reordered) {
      toast('Transcrição organizada: ' + (stats.cues || 0) + ' falas alinhadas.', 'ok');
    }
  }

  function renderClips() {
    const g = $('#gallery');
    g.innerHTML = '';
    if (!state.clips.length) {
      g.innerHTML = '<div class="empty-state" style="grid-column: 1 / -1;">Ainda não há clips.<br>Rodou um job e nada apareceu? Verifique o log acima.</div>';
      return;
    }
    state.clips.forEach((c) => {
      const el = document.createElement('div');
      const playable = !!(c.video && c.rendered !== false);
      el.className = 'clip-item' + (playable ? '' : ' analysis-only');
      el.setAttribute('tabindex', '0');
      el.setAttribute('role', 'button');
      el.setAttribute('aria-label',
        'Clip ' + (c.title || '') + ', nota ' + c.score + (playable ? '' : ', ainda não renderizado'));
      const videoSrc = playable ? ('clips/' + c.video) : null;
      el.innerHTML =
        (videoSrc ? '<video src="' + videoSrc + '" muted loop preload="metadata" aria-hidden="true"></video>' : '') +
        '<div class="overlay"></div>' +
        '<span class="play-hint" aria-hidden="true">' + (playable ? '▶' : '⚙') + '</span>' +
        '<div class="clip-meta">' +
          '<span class="score">score ' + c.score + '</span><br>' +
          '<span class="time"></span>' +
          (playable ? '' : '<br><span class="pending">análise · sem arquivo</span>') +
        '</div>';
      el.querySelector('.time').textContent = c.start + ' → ' + c.end;
      el.addEventListener('click', () => {
        const v = el.querySelector('video');
        if (v) {
          if (v.paused) { v.play().catch(function () {}); } else { v.pause(); }
        } else if (playable) {
          window.open('clips/' + c.video, '_blank');
        } else {
          toast('Este corte ainda não foi renderizado. Use "Gerar clips" para produzir o arquivo.', 'err');
          setStatus('done', 'Análise pronta');
          $('#btn-run-side').focus();
        }
      });
      el.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); el.click(); } });
      g.appendChild(el);
    });
  }

  // ---------- pasta de saida (biblioteca) ----------
  function formatSize(bytes) {
    const mb = 1024 * 1024;
    if (bytes >= mb) return (bytes / mb).toFixed(1) + ' MB';
    return Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }

  function renderLibrary(files) {
    const g = $('#gallery');
    g.innerHTML = '';
    if (!files.length) {
      g.innerHTML = '<div class="empty-state" style="grid-column: 1 / -1;">Nenhum vídeo na pasta de saída ainda.</div>';
      return;
    }
    files.forEach((f) => {
      const el = document.createElement('div');
      el.className = 'clip-item library';
      el.setAttribute('tabindex', '0');
      el.setAttribute('role', 'button');
      el.setAttribute('aria-label', f.name);
      el.innerHTML =
        '<video src="clips/' + encodeURI(f.rel) + '" muted loop preload="metadata" aria-hidden="true"></video>' +
        '<div class="overlay"></div>' +
        '<span class="play-hint" aria-hidden="true">▶</span>' +
        '<div class="clip-meta"><span class="file-name"></span><span class="time"></span></div>';
      el.querySelector('.file-name').textContent = f.name;
      el.querySelector('.time').textContent = formatSize(f.size);
      el.addEventListener('click', () => {
        const v = el.querySelector('video');
        if (v.paused) { v.play().catch(function () {}); } else { v.pause(); }
      });
      el.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); el.click(); } });
      g.appendChild(el);
    });
  }

  async function showLibrary() {
    const r = await api('/library');
    if (r.offline) { toast('Backend offline: inicie web/server.py.', 'err'); return; }
    if (r.error) { toast('Não foi possível listar a pasta: ' + r.error, 'err'); return; }
    renderLibrary(r.files || []);
    $('#btn-library').textContent = 'Ver cortes deste job';
    $('#gallery-sub').textContent =
      'Todos os vídeos em output/ — inclusive renomeados e de execuções anteriores. Clique para reproduzir.';
  }

  function showJobClips() {
    renderClips();
    $('#btn-library').textContent = 'Ver pasta de saída';
    $('#gallery-sub').textContent = 'Pré-visualização 9:16 dos cortes renderizados. Clique para reproduzir.';
  }

  // ---------- execução ----------
  let timer = null, startedAt = 0;

  function startTimer() {
    startedAt = Date.now();
    clearInterval(timer);
    timer = setInterval(() => {
      const s = Math.floor((Date.now() - startedAt) / 1000);
      const mm = String(Math.floor(s / 60)).padStart(2, '0');
      const ss = String(s % 60).padStart(2, '0');
      $('#elapsed').textContent = mm + ':' + ss;
    }, 1000);
  }
  function stopTimer() { clearInterval(timer); }

  async function run(planOnly) {
    const o = collectOptions();
    if (!o.url) {
      toast('Cole a URL do vídeo primeiro.', 'err');
      $('#url').focus();
      return;
    }
    if (state.running) { toast('Já existe uma execução em andamento.', 'err'); return; }

    state.running = true;
    $$('#btn-run, #btn-run-side, #btn-plan').forEach((b) => b.disabled = true);
    setStatus('running', planOnly ? 'Simulando…' : 'Renderizando…');
    startTimer();
    const comando = '$ ' + cliCommand(o);
    log(comando);
    // A partir daqui quem escreve o log e o poll: o `log()` acima ja cumpriu o
    // papel de mostrar o comando antes do primeiro request, e o `startFollowing`
    // reescreve a caixa com a mesma linha na frente.
    startFollowingRun(comando);

    const job = { url: o.url, status: 'running', meta: (planOnly ? 'plan-only · ' : '') + o.whisper_model + ' · ' + o.engine };
    state.jobs.push(job);
    renderQueue();
    // A espera transforma a galeria no esqueleto do que vai chegar: e a
    // diferenca entre "travado" e "trabalhando" durante a transcricao.
    showSkeletons();

    const body = JSON.stringify({ options: o, plan_only: !!planOnly });
    const r = await api('/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
    // O job acabou: para o poll e passa a caixa para a lista COMPLETA que a
    // resposta traz. O poll so tinha a cauda, e a ultima leitura pode ter
    // acontecido antes da ultima linha ser emitida.
    stopFollowingRun();
    renderLog([comando, ...((r.log_lines) || [])]);
    // O resultado chegou (ou falhou): os esqueletos saem antes de qualquer
    // ramo de erro, senao uma falha deixaria a galeria shimmerando para sempre.
    hideSkeletons();

    job.status = r.error ? 'fail' : 'done';
    job.meta = r.error ? ('erro: ' + r.error) : ((r.clips || []).length + ' clips');
    renderQueue();

    if (r.offline) {
      // sem backend: mostra o comando equivalente e simula progresso
      log('Servidor local não encontrado em ' + API, 'ln-warn');
      log('Rode o comando acima no terminal, ou inicie web/server.py');
      setProgress(100, 'Comando pronto — execute no terminal.');
      setStatus('error', 'Offline');
      toast('Backend offline. Comando CLI copiado para o log.', 'err');
    } else if (r.error) {
      log('Erro: ' + r.error, 'ln-err');
      setStatus('error', 'Falhou');
      setProgress(0, 'Falhou: ' + r.error);
      toast('Execução falhou: ' + r.error, 'err');
    } else {
      (r.log_lines || []).forEach((l) => log(l));
      state.clips = r.clips || [];
      state.viral = r.viral || [];
      renderClips();
      renderViral(state.viral, r.title);
      if (state.viral.length) {
        document.getElementById('viral-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      setProgress(100, state.clips.length + ' clip(s) gerado(s).');
      setStatus('done', 'Concluído');
      if (r.plan_only) {
        toast('Análise pronta (sem render). Clique em "Gerar clips" para produzir os vídeos.', 'ok');
      } else {
        toast(state.clips.length + ' clip(s) gerado(s).', 'ok');
      }
      if (state.clips.length) {
        const first = state.clips[0];
        log('Relatorio: ' + (first.report_json || 'clips.json'), 'ln-ok');
      }
    }

    stopTimer();
    state.running = false;
    $$('#btn-run, #btn-run-side, #btn-plan').forEach((b) => b.disabled = false);
  }

  // ---------- previa do hero (videos reais, quando existem) ----------
  // Cada card marcado com data-live toca o proprio clip: sozinho, mudo, em
  // loop, sem controle nenhum. O estado visual e do CSS (data-live); aqui so se
  // pede o play, em todo evento que indica que o arquivo esta pronto. Autoplay
  // mudo e permitido, mas nao e garantido (aba em segundo plano, politica do
  // navegador, economia de energia), e video parado parece imagem quebrada —
  // entao a primeira interacao do usuario tenta de novo, que e exatamente o
  // momento em que a politica de autoplay deixa de valer.
  //
  // O card TOCA sempre, sem excecao, e e uma decisao, nao um esquecimento.
  // Duas versoes anteriores paravam o video — uma quando a maquina pedia
  // movimento reduzido, outra quando o card saia do viewport — e as duas
  // falhavam do mesmo jeito: com `data-live` em 0 o CSS deixa o video com
  // `opacity: 0`, entao "parar" nao deixava um quadro parado, deixava o
  // ESQUEMA. Quem tem a animacao do Windows desligada (MinAnimate=0, que e o
  // que o Chromium le como `prefers-reduced-motion: reduce`) via os tres cards
  // vazios, com o ▶ e o gradiente, e nenhuma pista do motivo.
  //
  // A economia de CPU de pausar fora da tela era real, e foi descartada de
  // proposito: o elemento e decorativo (`aria-hidden`), mudo e pequeno, e o
  // custo de o card nao mostrar o produto e maior que o de decodificar tres
  // loops curtos enquanto a pagina esta aberta. Quem trava isso e
  // `test_the_clip_is_never_paused`, e o nome dele e literal.
  (function initHeroPreviews() {
    const gestures = ['pointerdown', 'keydown', 'touchstart', 'scroll'];

    $$('.preview-card[data-live]').forEach((card) => {
      const video = card.querySelector('.preview-video');
      if (!video) return;

      const resume = () => {
        const attempt = video.play();
        if (attempt && attempt.catch) attempt.catch(() => {});
      };

      // Os gestos so ficam escutando enquanto o video esta parado: sem
      // interacao o navegador recusa o autoplay, e depois de aceito nao ha
      // nada a destruar. Sao rearmados no pause — economia de energia, aba em
      // segundo plano e falta de foco pausam midia, e um card que parou por
      // conta propria nao pode voltar por conta propria: ele recebe uma nova
      // chance no proximo toque, em vez de ficar estatico para sempre.
      const arm = () => gestures.forEach((type) => document.addEventListener(type, resume, true));
      const disarm = () => gestures.forEach((type) => document.removeEventListener(type, resume, true));

      video.addEventListener('loadeddata', () => {
        card.setAttribute('data-live', '1');
        resume();
      });
      video.addEventListener('canplay', resume);
      // Tocou: nao ha mais nada a destravar.
      video.addEventListener('playing', () => {
        card.setAttribute('data-live', '1');
        disarm();
      });
      video.addEventListener('pause', arm);
      arm();
      // Ao voltar de uma aba em segundo plano o navegador pode ter pausado.
      document.addEventListener('visibilitychange', () => {
        if (!document.hidden) resume();
      });

      // Arquivo ausente ou codec recusado: volta ao esquema, sem icone quebrado.
      video.addEventListener('error', () => card.setAttribute('data-live', '0'));

      resume();
    });
  })();

  // ---------- seletor de pasta de saída ----------
  // <input type="file" webkitdirectory> não serve: o navegador esconde o
  // caminho real, e quem escreve os clips é o servidor. Então o clique abre
  // o diálogo nativo na máquina do servidor (tkinter) e o caminho escolhido
  // cai direto no campo.
  async function browseNative() {
    const btn = $('#btn-browse');
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Aguardando…';
    // Sem abort: o request vive enquanto o diálogo está aberto, e uma escolha
    // demorada é legítima — matar o fetch no meio deixaria o servidor com o
    // diálogo aberto e o caminho perdido. O que o estado precisa é mostrar
    // que está vivo: o contador tica, e aos 45s a dica diz onde procurar a
    // janela (o caso real de "travou" foi o diálogo aberto atrás do navegador).
    let secs = 0;
    const tick = setInterval(() => {
      secs += 1;
      btn.textContent = 'Aguardando… ' + secs + 's';
      if (secs === 45) {
        toast('O diálogo segue aberto no servidor. Se a janela não apareceu, ela deve estar atrás de outra — confira também outro monitor.');
      }
    }, 1000);
    try {
      const r = await api('/browse/native');
      if (r.error) {
        toast(r.offline ? 'Backend offline: inicie web/server.py.' : r.error, 'err');
        return;
      }
      if (r.cancelled || !r.path) {
        toast('Nenhuma pasta escolhida.');
        return;
      }
      $('#output').value = r.path;
      toast('Pasta de saída: ' + r.path);
    } finally {
      clearInterval(tick);
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  // ---------- eventos ----------
  $('#btn-run').addEventListener('click', () => run(false));
  $('#btn-run-side').addEventListener('click', () => run(false));
  $('#btn-plan').addEventListener('click', () => run(true));
  $('#btn-analyze').addEventListener('click', () => {
    const text = $('#transcript').value.trim();
    if (!text) {
      toast('Cole a transcrição do vídeo para analisar.', 'err');
      $('#transcript').focus();
      return;
    }
    if (!$('#url').value.trim()) {
      toast('A URL ainda é necessária: o áudio é baixado para medir energia e pausas.', 'err');
      $('#url').focus();
      return;
    }
    log('# analise com transcricao fornecida (sem whisper, sem render)');
    run(true);
  });
  $('#btn-clear-transcript').addEventListener('click', () => {
    $('#transcript').value = '';
    state.viral = [];
    renderViral([], '');
    $('#cue-preview').hidden = true;
    toast('Transcrição limpa.');
  });
  $('#btn-organize').addEventListener('click', () => organizeTranscript(false));
  $('#btn-render').addEventListener('click', () => run(false));
  $('#btn-library').addEventListener('click', () => {
    const showingLibrary = $('#btn-library').textContent.indexOf('cortes deste job') >= 0;
    if (showingLibrary) { showJobClips(); } else { showLibrary(); }
  });
  // Pasta = organiza na hora: a transcrição entra limpa, alinhada e ordenada.
  $('#transcript').addEventListener('paste', () => setTimeout(() => organizeTranscript(true), 0));
  $('#btn-batch').addEventListener('click', () => {
    toast('Modo lote: use python -m viralclipper --batch urls.txt -o output');
    log('# modo lote (CLI):\npython -m viralclipper --batch urls.txt -o output --retry-failed');
  });
  const abrirDocs = () => {
    // Local: o README do repo renderizado pelo próprio servidor. O link pro
    // GitHub dava 404 (repo privado/renomeado) — a única ajuda do produto
    // não pode depender de endereço externo.
    window.open('/docs', '_blank');
  };
  $('#btn-docs').addEventListener('click', abrirDocs);
  // O rodape repete os mesmos destinos do rail: quem chega ao fim da pagina
  // (ou esta no celular, onde o rail virou hamburguer) precisa de uma saida
  // ali. Os elementos vivem no HTML com id proprio; o JS so liga a acao, para
  // a lista de rotas continuar num lugar so.
  const irPara = (rota) => { window.location.href = rota; };
  const liga = (id, acao) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('click', acao);
  };
  liga('btn-docs-foot', abrirDocs);
  liga('btn-rail-cortes', () => irPara('/'));
  liga('btn-rail-scrap', () => irPara('/scrap'));
  $('#hero-cta').addEventListener('click', () => {
    document.getElementById('config').scrollIntoView({ behavior: 'smooth' });
    setTimeout(() => $('#url').focus(), 500);
  });
  $('#btn-browse').addEventListener('click', browseNative);

  // ---------- entrada vinda da pagina Scrap ----------
  // A pagina de Scrap nao guarda estado (nenhuma pagina guarda), entao o item
  // escolhido chega pelo endereco. Ler ?url= aqui e o que fecha o ciclo:
  // procurar -> escolher -> gerar, sem redigitar o link. Limpa o parametro
  // depois de consumir para um recarregar/F5 nao remexer no que ja foi digitado.
  (function acceptHandoff() {
    const params = new URLSearchParams(window.location.search);
    const incoming = (params.get('url') || '').trim();
    if (!incoming) return;
    const field = $('#url');
    if (!field) return;
    field.value = incoming;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    params.delete('url');
    const query = params.toString();
    history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
    toast('Vídeo recebido da página Scrap.', 'ok');
  })();

  // ---------- sondagem de estado (se houver servidor) ----------
  //
  // O `poll` do scrap e ligado a um `stop`/`follow` porque ele SÓ existe durante
  // um download. Aqui e diferente: a sondagem e permanente, a pagina mostra o
  // estado da fila e da galeria o tempo todo. Mesmo assim ela para quando a aba
  // nao esta a vista — um `setInterval` de 4s em segundo plano e uma requisicao
  // a cada 4s para sempre, e o `visibilitychange` e o que corta isso. Ao voltar
  // a aba, um `poll` imediato traz o estado novo em vez de esperar o proximo
  // tique.
  let pollTimer = null;
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
  function startPolling() {
    stopPolling();
    pollTimer = setInterval(poll, 4000);
  }
  async function poll() {
    const r = await api('/status');
    if (r.offline || r.error) return;
    if (Array.isArray(r.clips) && r.clips.length !== state.clips.length) {
      state.clips = r.clips;
      renderClips();
    }
    if (Array.isArray(r.jobs) && r.jobs.length !== state.jobs.length) {
      state.jobs = r.jobs;
      renderQueue();
    }
  }
  document.addEventListener('visibilitychange', () => {
    // Tab escondida: para de pedir. Tab de volta: retoma e traz o estado agora,
    // para a fila nao ficar desatualizada ate o proximo tique.
    if (document.hidden) stopPolling(); else { startPolling(); poll(); }
  });
  startPolling();

  renderQueue();
  renderClips();
})();