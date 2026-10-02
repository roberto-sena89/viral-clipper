(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // O servidor local opcional. Sem ele a pagina continua util: mostra os
  // padroes do proprio formulario e diz que nao esta salvando, em vez de
  // fingir que salvou.
  const API = 'http://127.0.0.1:7755';

  const state = {
    // Catalogo de provedores nomeados, como /providers devolve.
    providers: [],
    // true depois que o servidor confirmou que a rota /ajustes.json existe.
    // Enquanto for false, o autosave fica desligado: cada tecla digitada
    // viraria um POST 404.
    canSave: false,
    curatorPromptPath: '',
    saving: false,
  };

  // O rail (lista de destinos, marcacao da pagina atual e o menu do header)
  // mora em /comum.js: RAIL_PAGES + renderRail. Ele se monta sozinho no
  // load, entao nao ha nada para ligar aqui.

  // ---------- helpers de campo ----------
  function num(sel, fallback) {
    const raw = $(sel).value.trim();
    if (raw === '') return fallback;
    const value = parseFloat(raw);
    return Number.isFinite(value) ? value : fallback;
  }

  function toggleOn(sel) {
    return $(sel).classList.contains('active');
  }

  function setToggle(sel, on) {
    const el = $(sel);
    el.classList.toggle('active', !!on);
    el.setAttribute('aria-checked', String(!!on));
  }

  function setNum(sel, value) {
    const el = $(sel);
    if (el.type === 'number') el.value = value === null || value === undefined ? '' : String(value);
    else el.value = value === null || value === undefined ? '' : String(value);
  }

  function labelOf(sel) {
    const el = $(sel);
    const opt = el.options ? el.options[el.selectedIndex] : null;
    if (!opt) return el.value || '—';
    return opt.getAttribute('data-title') || opt.textContent.trim();
  }

  function toast(msg, kind) {
    const zone = $('#toast-zone');
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = msg;
    zone.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  // ---------- toggles ----------
  $$('.toggle').forEach((t) => {
    t.addEventListener('click', () => {
      const on = t.classList.toggle('active');
      t.setAttribute('aria-checked', String(on));
    });
  });

  // ---------- leitura do formulario ----------
  // As chaves sao exatamente as do payload do POST /run e as do --config da
  // CLI. Nao ha traducao no meio: o que esta aqui e o que o motor recebe.
  function readSettings() {
    return {
      min_duration: num('#min-duration', 30),
      max_duration: num('#max-duration', 60),
      target_duration: num('#target-duration', 42),
      min_score: num('#min-score', 0),
      min_gap: num('#min-gap', 6),
      engine: $('#engine').value,
      // Os tres numeros do modo automatico. Ficam aqui e nao na pagina Cortes
      // porque sao ajuste de uma vez: o que muda a cada execucao e o campo
      // "Quantidade de clips", que fica la.
      auto_margin: num('#auto-margin', 15),
      auto_ceiling: num('#auto-ceiling', 200),
      max_duration_grace: num('#max-grace', 30),

      whisper_model: $('#whisper-model').value,
      language: $('#language').value || null,
      beam_size: num('#beam-size', 1),
      cache_dir: $('#cache-dir').value.trim() || null,
      vad_filter: toggleOn('#vad-filter'),
      transcript_cache: toggleOn('#transcript-cache'),

      layout: $('#layout').value,
      caption_preset: $('#caption-preset').value,
      caption_style: $('#caption-style').value,
      font_size: num('#font-size', null),
      crf: num('#crf', 20),
      // target_lufs, nao lufs: o ClipConfig nao tem campo 'lufs', e mandar
      // o nome errado fazia o servidor descartar o valor em silencio.
      target_lufs: num('#target-lufs', -14),
      workers: num('#workers', 2),
      // Zero quando o titulo esta desligado, igual ao que o index manda hoje:
      // o campo so vale com o toggle ligado.
      headline_seconds: toggleOn('#headline-on') ? num('#headline-seconds', 3) : 0,
      progress_bar: toggleOn('#progress-bar-on'),
      jump_cut: toggleOn('#jump-cut'),
      loudnorm: toggleOn('#loudnorm'),

      ranker: toggleOn('#ranker-llm') ? 'llm' : 'none',
      ranker_provider: $('#ranker-provider').value,
      ranker_api_key_env: $('#ranker-api-key-env').value.trim() || 'OPENAI_API_KEY',
      ranker_model: $('#ranker-model').value,
      ranker_base_url: $('#ranker-base-url').value,
      ranker_top_n: num('#ranker-top-n', 24),
      ranker_weight: num('#ranker-weight', 0.6),
    };
  }

  // ---------- escrita no formulario ----------
  // Campo ausente nao e sobrescrito: um ajustes.toml parcial (ou uma versao
  // futura que ainda nao conhece um campo) nao apaga o que ja esta na tela.
  function applySettings(s) {
    if (!s || typeof s !== 'object') return;

    const numbers = [
      ['min_duration', '#min-duration'],
      ['max_duration', '#max-duration'],
      ['target_duration', '#target-duration'],
      ['min_score', '#min-score'],
      ['min_gap', '#min-gap'],
      ['auto_margin', '#auto-margin'],
      ['auto_ceiling', '#auto-ceiling'],
      ['max_duration_grace', '#max-grace'],
      ['beam_size', '#beam-size'],
      ['font_size', '#font-size'],
      ['crf', '#crf'],
      ['target_lufs', '#target-lufs'],
      ['workers', '#workers'],
      ['ranker_top_n', '#ranker-top-n'],
      ['ranker_weight', '#ranker-weight'],
    ];
    numbers.forEach(([key, sel]) => {
      if (s[key] !== undefined && s[key] !== null) setNum(sel, s[key]);
    });

    const texts = [
      ['engine', '#engine'],
      ['whisper_model', '#whisper-model'],
      ['language', '#language'],
      ['cache_dir', '#cache-dir'],
      ['layout', '#layout'],
      ['caption_preset', '#caption-preset'],
      ['caption_style', '#caption-style'],
      ['ranker_provider', '#ranker-provider'],
      ['ranker_api_key_env', '#ranker-api-key-env'],
      ['ranker_model', '#ranker-model'],
      ['ranker_base_url', '#ranker-base-url'],
    ];
    texts.forEach(([key, sel]) => {
      if (s[key] === undefined || s[key] === null) return;
      const el = $(sel);
      el.value = String(s[key]);
      // O select nativo esta escondido atras do componente rico: sem avisar,
      // o trigger continuaria exibindo a opcao anterior.
      if (typeof el.syncRichSelect === 'function') el.syncRichSelect();
    });

    const toggles = [
      ['vad_filter', '#vad-filter'],
      ['transcript_cache', '#transcript-cache'],
      ['progress_bar', '#progress-bar-on'],
      ['jump_cut', '#jump-cut'],
      ['loudnorm', '#loudnorm'],
    ];
    toggles.forEach(([key, sel]) => {
      if (s[key] !== undefined) setToggle(sel, s[key]);
    });

    // headline_seconds carrega duas coisas: se o titulo esta ligado (> 0) e
    // quanto ele dura. Zero desliga sem apagar a duracao que ja esta no campo.
    if (s.headline_seconds !== undefined && s.headline_seconds !== null) {
      const on = Number(s.headline_seconds) > 0;
      setToggle('#headline-on', on);
      if (on) setNum('#headline-seconds', s.headline_seconds);
    }

    if (s.ranker !== undefined) setToggle('#ranker-llm', s.ranker === 'llm');
  }

  // ---------- resumo lateral ----------
  function summaryRow(label, value) {
    const row = document.createElement('div');
    row.style.cssText =
      'display:flex; justify-content:space-between; gap:10px; padding:7px 0;' +
      ' border-top:0.5px solid var(--border-subtle);';
    const left = document.createElement('span');
    left.style.cssText = 'font-size:0.75rem; color:var(--text-muted); flex:none;';
    left.textContent = label;
    const right = document.createElement('span');
    right.style.cssText = 'font-size:0.78rem; text-align:right; min-width:0;';
    right.textContent = value;
    row.append(left, right);
    return row;
  }

  function renderSummary() {
    const s = readSettings();
    const box = $('#summary-list');
    box.innerHTML = '';

    const ceiling = s.max_duration + Math.max(0, s.max_duration_grace);
    const rows = [
      ['Duração', `${s.min_duration}–${s.max_duration} s · alvo ${s.target_duration} s`],
      ['Fechar contexto',
        s.max_duration_grace > 0 ? `pode ir até ${ceiling} s` : 'proibido passar do máximo'],
      ['Automático', `margem ${s.auto_margin} pts · teto ${s.auto_ceiling}`],
      ['Legendas', s.caption_style === 'none'
        ? 'nenhuma'
        : `${labelOf('#caption-preset')} · ${labelOf('#caption-style')}`],
      ['Reframe', labelOf('#layout')],
      ['Curador', s.ranker === 'llm'
        ? (s.ranker_provider || s.ranker_model || 'endpoint manual')
        : 'desligado'],
    ];
    rows.forEach(([label, value]) => box.appendChild(summaryRow(label, value)));
  }

  function setSaveState(kind, text, note) {
    const pill = $('#save-pill');
    pill.className = 'status-pill' + (kind ? ' ' + kind : '');
    $('#save-text').textContent = text;
    if (note !== undefined) $('#save-note').textContent = note;
  }

  function stampSaved() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    $('#save-when').textContent = 'salvo às ' + hh + ':' + mm;
  }

  // ---------- backend ----------
  async function api(path, opts) {
    try {
      const res = await fetch(API + path, opts);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) {
      return { error: e.message, offline: true };
    }
  }

  let saveTimer = null;
  function scheduleSave() {
    if (!state.canSave) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveSettings(true), 600);
  }

  async function saveSettings(silent) {
    if (state.saving) return;
    const settings = readSettings();

    if (!state.canSave) {
      setSaveState('error', 'Não salvo',
        'O servidor ainda não expõe /ajustes.json nem POST /ajustes. ' +
        'Nada está sendo gravado: os valores abaixo são só os padrões do formulário.');
      if (!silent) {
        toast('O servidor não tem a rota de ajustes ainda. Nada foi salvo.', 'err');
      }
      return;
    }

    state.saving = true;
    setSaveState('running', 'Salvando…');
    const r = await api('/ajustes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: settings }),
    });
    state.saving = false;

    if (r.offline || r.error) {
      setSaveState('error', 'Falha ao salvar', 'Não foi possível gravar: ' + (r.error || 'servidor fora do ar'));
      if (!silent) toast('Não foi possível salvar os ajustes.', 'err');
      return;
    }

    // O servidor devolve o que gravou. Reaplicar em vez de confiar no que
    // estava na tela e o que garante que a tela e o arquivo nao divirjam —
    // se o servidor normalizou um valor, quem manda e a resposta dele.
    if (r.settings) applySettings(r.settings);
    renderSummary();
    setSaveState('done', 'Salvo', r.path
      ? 'Gravado em ' + r.path
      : 'Gravado no servidor.');
    stampSaved();
    if (!silent) toast('Ajustes salvos.', 'ok');
  }

  async function loadSettings() {
    setSaveState('running', 'Carregando', 'Lendo os ajustes do servidor…');
    const r = await api('/ajustes.json');

    if (r.offline || r.error) {
      // Esperado enquanto a rota nao existe: a pagina fica com os padroes do
      // HTML e diz isso, em vez de mostrar um "salvo" que nunca aconteceu.
      state.canSave = false;
      setSaveState('error', 'Sem rota',
        'O servidor ainda não expõe /ajustes.json. Os valores abaixo são os padrões ' +
        'do formulário e nada será gravado até a rota existir.');
      renderSummary();
      return;
    }

    state.canSave = true;
    applySettings(r.settings || {});
    renderSummary();
    setSaveState('done', r.exists === false ? 'Padrões' : 'Carregado',
      r.path ? 'Gravado em ' + r.path : 'Ajustes vindos do servidor.');
  }

  // ---------- provedores do curador ----------
  function applyProvider() {
    const select = $('#ranker-provider');
    const note = $('#ranker-provider-hint');
    const provider = (state.providers || []).find((p) => p.name === select.value);
    if (!provider) {
      note.textContent = 'Escolher um provedor preenche endpoint, modelo, chave e timeout de uma vez.';
      return;
    }
    // Preencher os campos nao e so conveniencia: o que viaja no run e o NOME do
    // provedor, e o servidor reescreve os quatro campos de qualquer forma.
    $('#ranker-base-url').value = provider.base_url;
    $('#ranker-model').value = provider.model;
    if (provider.api_key_env) $('#ranker-api-key-env').value = provider.api_key_env;
    note.textContent = provider.note || provider.label;
  }

  async function initProviders() {
    const catalogue = await api('/providers');
    if (catalogue.offline || catalogue.error) return;
    state.providers = catalogue.providers || [];
    const select = $('#ranker-provider');
    state.providers.forEach((provider) => {
      const option = document.createElement('option');
      option.value = provider.name;
      option.textContent = provider.label;
      option.title = provider.note || '';
      select.appendChild(option);
    });
    // So agora o select tem opcoes: enfeitar depois de popular e o que faz o
    // painel nascer com o catalogo inteiro dentro.
    enhanceSelect(select);
    applyProvider();
    select.addEventListener('change', () => { applyProvider(); renderSummary(); scheduleSave(); });
  }

  // ---------- prompt do curador ----------
  function setPromptStatus(message, kind) {
    const el = $('#curator-prompt-status');
    el.textContent = message || '';
    el.style.color = kind === 'erro' ? 'var(--danger, #ff6b6b)'
      : kind === 'ok' ? 'var(--success, #4ade80)'
      : 'var(--text-muted)';
  }

  async function loadCuratorPrompt() {
    setPromptStatus('Lendo…');
    const r = await api('/prompts/curador');
    if (r.offline || r.error) {
      setPromptStatus('Servidor fora do ar: o prompt não pode ser lido nem salvo.', 'erro');
      return;
    }
    $('#curator-prompt').value = r.text || '';
    $('#curator-prompt-path').value = r.path || '';
    state.curatorPromptPath = r.path || '';
    setPromptStatus(
      r.exists ? 'Prompt carregado do arquivo.' : 'O arquivo ainda não existe; ele será criado ao salvar.',
      'ok',
    );
  }

  async function saveCuratorPrompt() {
    const text = $('#curator-prompt').value;
    if (!text.trim()) {
      setPromptStatus('O prompt não pode ficar vazio.', 'erro');
      return;
    }
    setPromptStatus('Salvando…');
    const r = await api('/prompts/curador', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text }),
    });
    if (r.offline || r.error) {
      setPromptStatus('Não foi possível salvar: ' + (r.error || 'servidor fora do ar'), 'erro');
      return;
    }
    state.curatorPromptPath = r.path || state.curatorPromptPath;
    $('#curator-prompt-path').value = state.curatorPromptPath;
    setPromptStatus('Prompt salvo. O cache de veredictos foi invalidado.', 'ok');
    toast('Prompt do curador salvo.', 'ok');
  }

  // ---------- ligacao dos controles ----------
  const form = $('#ajustes-form');
  form.addEventListener('change', () => { renderSummary(); scheduleSave(); });
  form.addEventListener('input', () => { renderSummary(); scheduleSave(); });

  $$('.toggle').forEach((t) => {
    t.addEventListener('click', () => { renderSummary(); scheduleSave(); });
  });

  // Os selects nativos viram o componente rico do comum.js. O de provedor fica
  // de fora (data-defer-enhance): ele nasce com uma opcao so e e preenchido por
  // /providers, que e assincrono - enfeitar antes de popular daria um painel
  // vazio, porque o componente copia as <option> no momento em que roda.
  $$('select[id]:not([data-defer-enhance])').forEach(enhanceSelect);

  $('#btn-save').addEventListener('click', () => saveSettings(false));
  $('#btn-save-side').addEventListener('click', () => saveSettings(false));

  $('#btn-prompt-load').addEventListener('click', loadCuratorPrompt);
  $('#btn-prompt-save').addEventListener('click', saveCuratorPrompt);

  // ---------- partida ----------
  renderSummary();
  initProviders();
  loadSettings();
  loadCuratorPrompt();
})();
