(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const state = {
    running: false, jobs: [], clips: [], viral: [], jobSignature: null,
    // Caminho do arquivo de prompt do curador e se ele ja existe em disco. Os
    // dois vem do servidor: o painel nunca inventa o caminho, porque quem
    // decide onde o arquivo mora e o servidor (CURATOR_PROMPT_PATH).
    curatorPromptPath: '',
    curatorPromptExists: false,
    // As secoes "Selecao", "Transcricao" e "Renderizacao" nao vivem mais nesta
    // pagina: as tres moram so em /ajustes. O que fica aqui e a copia do que
    // esta em ajustes.toml, lida de /ajustes.json no load. Os valores abaixo sao
    // os defaults do formulario de Ajustes e valem enquanto ninguem salvou nada.
    ajustes: {
      // Selecao
      min_duration: 30,
      max_duration: 60,
      target_duration: 42,
      min_score: 0,
      min_gap: 6,
      engine: 'hybrid',
      // Transcricao. `language: null` e o "auto-detectar" do formulario (o
      // select usa value="" para isso) - e o que o motor entende como deixa
      // o Whisper decidir.
      whisper_model: 'small',
      language: null,
      beam_size: 1,
      cache_dir: 'output/cache/transcripts',
      vad_filter: true,
      transcript_cache: true,
      // Renderizacao. Mesmos defaults do formulario de Ajustes.
      layout: 'focus',
      caption_preset: 'karaoke',
      caption_style: 'karaoke',
      font_size: null,
      crf: 20,
      target_lufs: -14,
      workers: 2,
      headline_seconds: 0,
      progress_bar: false,
      jump_cut: false,
      loudnorm: true,
    },
  };

  // O rail (lista de destinos, marcacao da pagina atual e o menu do header)
  // mora em /comum.js: RAIL_PAGES + renderRail. Ele se monta sozinho no
  // load, entao nao ha nada para ligar aqui.

  // ---------- toggles ----------
  $$('.toggle').forEach((t) => {
    t.addEventListener('click', () => {
      const on = t.classList.toggle('active');
      t.setAttribute('aria-checked', String(on));
    });
  });

  // Os selects ricos (dropdown custom) e o visual das legendas moram em
  // /comum.js: CAPTION_LOOKS + enhanceSelect. O que resta aqui e a chamada
  // que aprimora os selects da pagina no load.

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
    const empty = box.querySelector('.execution-log-empty');
    if (empty) empty.remove();
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
    const progressLabel = $('#progress-label');
    if (label && progressLabel.textContent !== label) progressLabel.textContent = label;
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

  function queueClock(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const minutes = String(Math.floor(total / 60)).padStart(2, '0');
    const remainder = String(total % 60).padStart(2, '0');
    return minutes + ':' + remainder;
  }

  async function followRun() {
    const r = await api('/run/progress');
    if (r.offline || r.error) return;
    renderSteps(r.stages);
    // `stage_label` cobre a janela entre o POST e a primeira fase: o servidor
    // publica "Preparando…" antes de anunciar qualquer etapa.
    if (r.stage_label) setProgress(0, r.stage_label);
    if (Array.isArray(r.lines)) renderLog([runProgress.header, ...r.lines]);

    let jobIndex = -1;
    for (let index = state.jobs.length - 1; index >= 0; index--) {
      if (state.jobs[index].status === 'running') {
        jobIndex = index;
        break;
      }
    }
    if (jobIndex < 0) return;

    const job = state.jobs[jobIndex];
    if (r.active && (!r.url || r.url === job.url)) {
      const activeStage = (r.stages || []).find((stage) => stage.state === 'agora');
      job.progress = r.stage_label || (activeStage && activeStage.label) || job.progress;
      job.elapsed = Number(r.elapsed) || 0;
      const card = $('#queue-list .job-card[data-job-index="' + jobIndex + '"]');
      if (card) {
        const phase = card.querySelector('.job-phase');
        const elapsed = card.querySelector('.job-elapsed');
        if (phase && job.progress) phase.textContent = job.progress;
        if (elapsed) elapsed.textContent = queueClock(job.elapsed);
      }
    }

    if (!(job.baselineFiles instanceof Map)) return;
    const library = await api('/library');
    if (library.offline || library.error || !Array.isArray(library.files)) return;

    const observed = job.observedFiles || (job.observedFiles = new Map());
    const ready = [];
    library.files.forEach((file) => {
      if (!isRenderedClipFile(file, job)) return;
      const fingerprint = String(file.size || 0) + ':' + String(file.modified || 0);
      if (job.baselineFiles.get(file.rel) === fingerprint) return;
      const previous = observed.get(file.rel);
      observed.set(file.rel, fingerprint);
      if (previous === fingerprint) {
        ready.push({ rel: file.rel, name: file.name, size: file.size });
      }
    });

    ready.sort((a, b) => renderedClipSequence(a) - renderedClipSequence(b));

    const nextPreviews = ready;
    const previousKeys = JSON.stringify((job.previewFiles || []).map((file) => file.rel));
    const nextKeys = JSON.stringify(nextPreviews.map((file) => file.rel));
    if (nextKeys !== previousKeys) {
      job.previewFiles = nextPreviews;
      renderQueue();
    }
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
      // Selecao, Transcricao e Renderizacao vem dos Ajustes, nao desta pagina:
      // as tres secoes existem so em /ajustes, que grava ajustes.toml. Aqui so
      // lemos o que foi salvo (state.ajustes) e mandamos no payload do run. Os
      // literais sao os mesmos defaults do formulario de Ajustes, para uma
      // instalacao nova - sem ajustes.toml ainda - rodar como antes.
      min_duration: state.ajustes.min_duration,
      max_duration: state.ajustes.max_duration,
      target_duration: state.ajustes.target_duration,
      min_score: state.ajustes.min_score,
      min_gap: state.ajustes.min_gap,
      engine: state.ajustes.engine,
      whisper_model: state.ajustes.whisper_model,
      language: state.ajustes.language,
      beam_size: state.ajustes.beam_size,
      vad_filter: state.ajustes.vad_filter,
      transcript_cache: state.ajustes.transcript_cache,
      layout: state.ajustes.layout,
      caption_style: state.ajustes.caption_style,
      caption_preset: state.ajustes.caption_preset,
      font_size: state.ajustes.font_size,
      crf: state.ajustes.crf,
      // `target_lufs`, nao `lufs`: e o nome do campo em ClipConfig. O alias
      // antigo sobrevive no servidor so para uma pagina em cache, nao para nos.
      target_lufs: state.ajustes.target_lufs,
      workers: state.ajustes.workers,
      jump_cut: state.ajustes.jump_cut,
      loudnorm: state.ajustes.loudnorm,
      ranker: toggleOn('#ranker-llm') ? 'llm' : 'none',
      // Vazio = "personalizado": o motor entao usa o modelo e o endpoint
      // digitados abaixo. Preenchido, o provedor manda nesses dois campos.
      ranker_provider: $('#ranker-provider').value,
      ranker_model: $('#ranker-model').value,
      ranker_base_url: $('#ranker-base-url').value,
      ranker_api_key_env: $('#ranker-api-key-env').value.trim() || 'OPENAI_API_KEY',
      ranker_top_n: parseInt($('#ranker-top-n').value, 10) || 24,
      ranker_weight: parseFloat($('#ranker-weight').value) || 0.6,
      // So vai quando o ranker esta ligado E o arquivo existe. Um caminho
      // apontando para arquivo inexistente e erro de configuracao no motor
      // (validate() recusa), entao mandar assim seria derrubar o run inteiro
      // por causa de um campo que a pessoa nem tocou.
      curator_prompt_file: (toggleOn('#ranker-llm') && state.curatorPromptExists)
        ? state.curatorPromptPath
        : null,
      cache_dir: state.ajustes.cache_dir,
      transcript_text: $('#transcript').value.trim() || null,
      // O titulo so e queimado com duracao > 0; o formulario de Ajustes ja
      // grava 0 quando o toggle esta desligado, entao o valor salvo e o que
      // vale. Nao ha mais um toggle aqui para reinterpretar.
      headline_seconds: state.ajustes.headline_seconds,
      progress_bar: state.ajustes.progress_bar,
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
      // `sem_conexao` separa "o fetch nem saiu" de "o servidor respondeu com
      // erro". Os dois cegam o mesmo aqui, mas so o primeiro se resolve
      // subindo o servidor: um 500 quer outro conselho, e a nota que a pagina
      // mostra nao pode mandar reiniciar algo que esta de pe.
      const http = String(e.message).startsWith('HTTP ');
      return { error: e.message, offline: true, sem_conexao: !http };
    }
  }

  // ---------- ajustes de selecao (vindos da pagina Ajustes) ----------
  // A secao "Selecao" foi removida desta pagina de propósito: ela e ajuste de
  // uma vez, nao parametro de cada rodada, e ter os mesmos campos nos dois
  // lugares criava duas fontes de verdade para o mesmo valor. Agora quem
  // escreve e a pagina Ajustes (POST /ajustes -> ajustes.toml) e quem le e
  // esta, no load. Sem chave no arquivo, o valor atual de state.ajustes fica.
  function applyAjustes(settings) {
    if (!settings || typeof settings !== 'object') return;

    // Agrupado por tipo de valor, e nao chave a chave: sao duas duzias de campos
    // de 4 tipos, e a regra de cada tipo e o que importa. `undefined`/`null`
    // nunca sobrescrevem - um ajustes.toml parcial (ou uma versao futura que
    // ainda nao conhece um campo) nao pode apagar o que ja esta aqui.
    const NUMERICOS = ['min_duration', 'max_duration', 'target_duration',
                       'min_score', 'min_gap', 'beam_size',
                       'crf', 'target_lufs', 'workers', 'headline_seconds'];
    const TEXTOS = ['engine', 'whisper_model', 'layout', 'caption_preset',
                    'caption_style'];
    // Vazio e um valor legitimo aqui: `language: ""` no formulario e
    // "auto-detectar", entao nao se pode descartar string vazia. O motor
    // recebe `null` nesse caso.
    const TEXTO_OU_NULO = ['language', 'cache_dir'];
    const BOOLEANOS = ['vad_filter', 'transcript_cache', 'progress_bar',
                       'jump_cut', 'loudnorm'];

    NUMERICOS.forEach((chave) => {
      const valor = settings[chave];
      if (valor !== undefined && valor !== null && isFinite(Number(valor))) {
        state.ajustes[chave] = Number(valor);
      }
    });
    TEXTOS.forEach((chave) => {
      const valor = settings[chave];
      if (typeof valor === 'string' && valor) state.ajustes[chave] = valor;
    });
    TEXTO_OU_NULO.forEach((chave) => {
      const valor = settings[chave];
      if (typeof valor === 'string') state.ajustes[chave] = valor || null;
    });
    BOOLEANOS.forEach((chave) => {
      if (typeof settings[chave] === 'boolean') {
        state.ajustes[chave] = settings[chave];
      }
    });
    // `font_size` tem tres estados: numero (o usuario fixou um tamanho), null
    // (herda do preset) e ausente (arquivo antigo, nao mexe). Tratar junto com
    // NUMERICOS faria o null ser descartado e o preset nunca voltar a valer.
    if ('font_size' in settings && settings.font_size !== undefined) {
      const tamanho = Number(settings.font_size);
      state.ajustes.font_size = (settings.font_size === null || !isFinite(tamanho))
        ? null
        : tamanho;
    }
  }

  // O motor de analise e um valor tecnico; na tela ele aparece com o mesmo
  // rotulo do formulario de Ajustes, senao a pessoa le "hybrid" aqui e
  // "Hybrid" la e nao sabe que e a mesma coisa.
  const ENGINE_LABELS = { hybrid: 'híbrido', audio: 'áudio', transcript: 'transcrição' };

  // Numero sem casa decimal inutil: 30 sai "30", 42.5 sai "42,5". O resumo e
  // para bater o olho, nao para justapor zeros.
  function fmtSeconds(valor) {
    return Number(valor).toLocaleString('pt-BR', { maximumFractionDigits: 1 });
  }

  function renderSelectionSummary() {
    const el = $('#selection-summary-value');
    if (!el) return;
    const a = state.ajustes;
    const partes = [fmtSeconds(a.min_duration) + '–' + fmtSeconds(a.max_duration) + ' s'];
    partes.push('alvo ' + fmtSeconds(a.target_duration) + ' s');
    partes.push(ENGINE_LABELS[a.engine] || a.engine);
    // O portao de nota so aparece quando esta ligado: um "nota ≥ 0" seria
    // ruido, porque 0 significa "sem portao" e nao uma exigencia.
    if (Number(a.min_score) > 0) partes.push('nota ≥ ' + fmtSeconds(a.min_score));
    el.textContent = partes.join(' · ');
  }

  async function loadAjustes() {
    const r = await api('/ajustes.json');
    // Servidor fora do ar ou rota ausente: os defaults de state.ajustes ja
    // cobrem isso, e a pagina nao tem onde mostrar um erro so por causa de
    // um valor que ela nem edita mais. Segue calada com os defaults.
    if (r.offline || r.error) return;
    applyAjustes(r.settings || {});
    renderSelectionSummary();
  }

  // ---------- curador: provedores e prompt ----------
  // Os dois vivem no servidor: a lista de provedores sai de
  // `viralclipper/providers.py` e o prompt de um arquivo versionado no git. O
  // painel so mostra e edita - ele nao decide nem o caminho do arquivo nem o
  // catalogo de modelos, porque quem decide isso e o motor.
  function setPromptStatus(message) {
    const el = $('#curator-prompt-status');
    if (el) el.textContent = message || '';
  }

  function applyProvider(provider) {
    if (!provider) return;
    if (provider.base_url) $('#ranker-base-url').value = provider.base_url;
    if (provider.model) $('#ranker-model').value = provider.model;
    if (provider.api_key_env) $('#ranker-api-key-env').value = provider.api_key_env;
  }

  function populateProviders(data) {
    const select = $('#ranker-provider');
    if (!select) return;
    const providers = (data && data.providers) || [];
    select.innerHTML = '';

    const custom = document.createElement('option');
    custom.value = '';
    custom.textContent = 'Personalizado';
    custom.setAttribute('data-title', 'Personalizado');
    custom.setAttribute('data-desc', 'Usa o endpoint e o modelo digitados abaixo');
    select.appendChild(custom);

    providers.forEach((p) => {
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = p.label;
      opt.setAttribute('data-title', p.label);
      opt.setAttribute('data-desc', p.note || p.model);
      select.appendChild(opt);
    });

    select.value = (data && data.default) || '';
    applyProvider(providers.find((p) => p.name === select.value));

    // Agora sim da para montar o dropdown rico, com a lista de verdade. No
    // init este select estava vazio - foi por isso que ele nasceu com
    // data-defer-enhance e ficou fora da varredura.
    select.removeAttribute('data-defer-enhance');
    enhanceSelect(select);

    select.addEventListener('change', () => {
      const chosen = providers.find((p) => p.name === select.value);
      if (chosen) applyProvider(chosen);
    });
  }

  async function loadCuratorPrompt() {
    const data = await api('/prompts/curador');
    if (!data || data.error) {
      setPromptStatus('Servidor fora do ar: o prompt não pôde ser carregado.');
      return;
    }
    state.curatorPromptPath = data.path || '';
    state.curatorPromptExists = Boolean(data.exists);
    const pathEl = $('#curator-prompt-path');
    if (pathEl) pathEl.value = data.path || '';
    const area = $('#curator-prompt');
    // Nao sobrescreve o que a pessoa ja digitou se o arquivo estiver vazio: o
    // "Carregar" e uma acao dela, e apagar texto alheio sem pedir e pior do
    // que nao fazer nada.
    if (area && data.text) area.value = data.text;
    setPromptStatus(
      data.exists ? '' : 'O arquivo ainda não existe — salve para criá-lo.'
    );
  }

  async function saveCuratorPrompt() {
    const area = $('#curator-prompt');
    if (!area) return;
    const text = area.value;
    if (!text.trim()) {
      setPromptStatus('O prompt está vazio: nada foi salvo.');
      return;
    }
    setPromptStatus('Salvando...');
    const data = await api('/prompts/curador', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (data && data.ok) {
      state.curatorPromptExists = true;
      if (data.path) {
        state.curatorPromptPath = data.path;
        const pathEl = $('#curator-prompt-path');
        if (pathEl) pathEl.value = data.path;
      }
      setPromptStatus('Salvo em ' + (data.path || 'arquivo') + '.');
    } else {
      setPromptStatus((data && data.error) || 'Não consegui salvar.');
    }
  }

  const btnPromptLoad = $('#btn-prompt-load');
  if (btnPromptLoad) btnPromptLoad.addEventListener('click', loadCuratorPrompt);
  const btnPromptSave = $('#btn-prompt-save');
  if (btnPromptSave) btnPromptSave.addEventListener('click', saveCuratorPrompt);

  (async function initCurator() {
    const providers = await api('/providers');
    if (providers && !providers.error) populateProviders(providers);
    await loadCuratorPrompt();
  })();

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
      // Um provedor nomeado ja carrega endpoint e modelo: imprimir os dois
      // junto com --ranker-provider sugeriria que eles tambem valem, e o
      // provedor sobrescreve os dois no motor.
      if (o.ranker_provider) {
        parts.push('--ranker-provider', o.ranker_provider);
      } else {
        parts.push('--ranker-model', o.ranker_model);
        parts.push('--ranker-base-url', o.ranker_base_url);
      }
      if (o.curator_prompt_file) parts.push('--curator-prompt', o.curator_prompt_file);
      parts.push('--ranker-top-n', String(o.ranker_top_n));
      parts.push('--ranker-weight', String(o.ranker_weight));
    }
    return parts.join(' ');
  }

  // ---------- fila ----------
  function videoAddress(rel) {
    return API + '/clips/' + String(rel || '').split('/').map(encodeURIComponent).join('/');
  }

  function youtubeVideoId(value) {
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase();
      if (host === 'youtu.be') return url.pathname.split('/').filter(Boolean)[0] || '';
      if (host !== 'youtube.com' && !host.endsWith('.youtube.com')) return '';
      const pathId = (url.pathname.match(/^\/(?:shorts|embed|live)\/([^/?]+)/) || [])[1];
      return url.searchParams.get('v') || pathId || '';
    } catch (_) {
      return '';
    }
  }

  function renderedClipSequence(file) {
    const name = String(file.name || file.rel || '').split(/[\\/]/).pop();
    return Number((name.match(/^[^_]+_(\d{2})_/) || [])[1]) || Number.MAX_SAFE_INTEGER;
  }

  function isRenderedClipFile(file, job) {
    const rel = String(file.rel || '').replace(/\\/g, '/');
    const name = String(file.name || rel).split(/[\\/]/).pop();
    if (!rel || /(^|\/)_(?:work|tmp)(\/|$)/i.test(rel) || !/\.mp4$/i.test(name)) return false;

    const videoId = youtubeVideoId(job.url);
    if (videoId) {
      const prefix = videoId + '_';
      return name.startsWith(prefix) && /^\d{2}_/.test(name.slice(prefix.length));
    }
    return /^[^_]+_\d{2}_/.test(name);
  }

  async function hydrateQueuePreviews() {
    const now = Date.now();
    const pending = state.jobs.filter((job) => {
      if (job.status !== 'done' || job.previewLookupDone) return false;
      if (/(sem render|plan[- ]only)/i.test(String(job.meta || ''))) return false;
      if (!youtubeVideoId(job.url) || now - Number(job.previewLookupAt || 0) < 15000) return false;
      job.previewLookupAt = now;
      return true;
    });
    if (!pending.length) return;

    const library = await api('/library');
    if (library.offline || library.error || !Array.isArray(library.files)) return;
    let changed = false;
    pending.forEach((job) => {
      const files = library.files.filter((file) => isRenderedClipFile(file, job));
      files.sort((a, b) => renderedClipSequence(a) - renderedClipSequence(b));
      if (files.length) {
        const currentKeys = JSON.stringify((job.previewFiles || []).map((file) => file.rel));
        const libraryKeys = JSON.stringify(files.map((file) => file.rel));
        if (currentKeys !== libraryKeys) {
          job.previewFiles = files;
          changed = true;
        }
        job.previewLookupDone = true;
      }
    });
    if (changed) renderQueue();
  }

  function renderQueue() {
    const list = $('#queue-list');
    const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const runningPreviewSpeed = reduceMotion ? 1 : 2;
    list.innerHTML = '';
    if (!state.jobs.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.innerHTML = 'Nenhum job na fila ainda.<br>Cole a URL acima e clique em <strong>Gerar clips</strong>.';
      list.appendChild(empty);
      return;
    }

    state.jobs.forEach((job, index) => {
      const card = document.createElement('article');
      card.className = 'job-card ' + (job.status || 'queued');
      card.dataset.jobIndex = String(index);

      const poster = document.createElement('div');
      poster.className = 'job-poster';
      poster.setAttribute('aria-hidden', 'true');
      const posterMark = document.createElement('span');
      posterMark.className = 'job-poster-mark';
      posterMark.textContent = 'VC';
      const posterCaption = document.createElement('span');
      posterCaption.className = 'job-poster-caption';
      posterCaption.textContent = job.status === 'running'
        ? 'Corte em produção · prévia ' + runningPreviewSpeed + '×'
        : job.previewFiles && job.previewFiles.length
          ? (job.previewFiles.length + ' prévia' + (job.previewFiles.length > 1 ? 's' : '') + ' pronta' + (job.previewFiles.length > 1 ? 's' : ''))
          : 'Video clipper';
      poster.append(posterMark, posterCaption);

      const content = document.createElement('div');
      content.className = 'job-content';
      const head = document.createElement('div');
      head.className = 'job-head';
      const info = document.createElement('div');
      info.className = 'job-info';
      const title = document.createElement('div');
      title.className = 'job-title';
      title.textContent = job.title || job.url || '(sem URL)';
      const url = document.createElement('div');
      url.className = 'job-url';
      url.textContent = job.url || '';
      const badge = document.createElement('span');
      badge.className = 'job-badge ' + (job.status || 'queued');
      badge.textContent = job.status === 'running' ? 'Renderizando'
        : job.status === 'done' ? 'Concluído'
          : job.status === 'fail' ? 'Falhou' : 'Na fila';
      info.append(title, url);
      head.append(info, badge);

      const details = document.createElement('div');
      details.className = 'job-details';
      const phase = document.createElement('span');
      phase.className = 'job-phase';
      phase.textContent = job.progress || job.meta || (job.status === 'running' ? 'Preparando processamento…' : '');
      const elapsed = document.createElement('span');
      elapsed.className = 'job-elapsed';
      elapsed.textContent = job.elapsed ? queueClock(job.elapsed) : '';
      details.append(phase, elapsed);

      const track = document.createElement('div');
      track.className = 'job-progress ' + (job.status === 'done' ? 'complete' : job.status === 'fail' ? 'failed' : '');
      track.setAttribute('role', 'progressbar');
      track.setAttribute('aria-label', 'Progresso do processamento');
      track.setAttribute('aria-valuemin', '0');
      track.setAttribute('aria-valuemax', '100');
      if (job.status === 'done') track.setAttribute('aria-valuenow', '100');
      else track.setAttribute('aria-valuetext', job.progress || 'Em processamento');
      const fill = document.createElement('span');
      fill.className = 'job-progress-fill';
      track.appendChild(fill);

      content.append(head, details, track);
      const previews = document.createElement('div');
      previews.className = 'job-previews';
      previews.setAttribute('aria-label', 'Pré-visualização dos cortes');
      (job.previewFiles || []).forEach((file, clipIndex) => {
        const figure = document.createElement('figure');
        const livePreview = job.status === 'running' && clipIndex === 0;
        figure.className = 'job-preview' + (livePreview ? ' is-live-preview' : '');
        figure.tabIndex = 0;
        figure.setAttribute('role', 'button');
        figure.setAttribute('aria-label', (livePreview ? 'Prévia acelerada em produção' : 'Reproduzir ou pausar corte ' + (clipIndex + 1)));
        const video = document.createElement('video');
        video.muted = true;
        video.loop = true;
        video.autoplay = true;
        video.playsInline = true;
        video.preload = 'auto';
        video.setAttribute('aria-hidden', 'true');
        video.defaultPlaybackRate = livePreview ? runningPreviewSpeed : 1;
        video.playbackRate = livePreview ? runningPreviewSpeed : 1;
        video.src = videoAddress(file.rel);
        const startPreview = () => {
          video.playbackRate = livePreview ? runningPreviewSpeed : 1;
          if (video.paused) video.play().catch(() => {});
        };
        video.addEventListener('loadedmetadata', startPreview, { once: true });
        video.addEventListener('loadeddata', startPreview, { once: true });
        video.addEventListener('canplay', startPreview, { once: true });
        const caption = document.createElement('figcaption');
        caption.textContent = livePreview
          ? 'AO VIVO · ' + runningPreviewSpeed + '×'
          : 'Corte ' + String(clipIndex + 1).padStart(2, '0');
        figure.append(video, caption);
        figure.addEventListener('click', () => {
          if (video.paused) video.play().catch(() => {});
          else video.pause();
        });
        figure.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            figure.click();
          }
        });
        previews.appendChild(figure);
      });
      if (!(job.previewFiles || []).length && job.status === 'running') {
        const stage = document.createElement('div');
        stage.className = 'job-render-stage';
        stage.setAttribute('role', 'status');
        stage.setAttribute('aria-label', 'Preparando a prévia do corte');
        stage.innerHTML = '<div class="job-render-visual" aria-hidden="true">' +
          '<span class="job-render-glow"></span><span class="job-render-frame"></span>' +
          '<span class="job-render-scan"></span><span class="job-render-core">VC</span>' +
          '<span class="job-render-corner">RENDER</span></div>' +
          '<div class="job-render-copy"><span class="job-render-live"><i></i> EM PRODUÇÃO</span>' +
          '<strong>Preparando sua prévia</strong><span>O primeiro corte aparece aqui em ' + runningPreviewSpeed + '× assim que ficar pronto.</span></div>' +
          '<span class="job-render-meter" aria-hidden="true"><i></i></span>';
        previews.appendChild(stage);
      }
      if (job.meta && job.status !== 'running' && job.status !== 'done') {
        const error = document.createElement('p');
        error.className = 'job-error';
        error.textContent = job.meta;
        content.appendChild(error);
      }

      card.append(poster, content, previews);
      list.appendChild(card);
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
      const card = document.createElement('div');
      card.className = 'clip-card';
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
      card.appendChild(el);
      if (playable && c.quality) {
        const panel = document.createElement('div');
        panel.className = 'clip-quality ' + (c.quality.status || 'ok');
        const heading = document.createElement('strong');
        const issues = (c.quality.checks || []).filter((check) => check.status === 'warning' || check.status === 'error');
        const skipped = (c.quality.checks || []).filter((check) => check.status === 'skipped');
        heading.textContent = issues.length ? `${issues.length} ponto(s) para revisar` : (skipped.length ? 'Revisão parcial' : 'Revisão automática concluída');
        panel.appendChild(heading);
        const visibleChecks = issues.length
          ? [...issues, ...skipped]
          : (skipped.length ? [...skipped, ...(c.quality.checks || []).filter((check) => check.status === 'ok').slice(0, 1)] : (c.quality.checks || []).filter((check) => check.status === 'ok').slice(0, 2));
        visibleChecks.forEach((check) => {
          const row = document.createElement('p');
          row.textContent = `${check.status === 'skipped' ? 'Não verificado · ' : ''}${check.label}: ${check.message}`;
          panel.appendChild(row);
        });
        if ((c.quality.checks || []).some((check) => check.key === 'face' && (check.status === 'warning' || check.status === 'skipped'))) {
          const fix = document.createElement('button');
          fix.type = 'button';
          fix.className = 'quality-fix';
          fix.textContent = 'Refazer todos com quadro completo';
          fix.addEventListener('click', (ev) => {
            ev.stopPropagation();
            run(false, {
              ...(state.lastRunOptions || collectOptions()),
              url: c.source_url || (state.lastRunOptions || {}).url || $('#url').value.trim(),
              layout: 'blur',
            });
          });
          panel.appendChild(fix);
        }
        card.appendChild(panel);
      }
      g.appendChild(card);
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

  async function run(planOnly, overrides) {
    const o = Object.assign(collectOptions(), overrides || {});
    if (!o.url) {
      toast('Cole a URL do vídeo primeiro.', 'err');
      $('#url').focus();
      return;
    }
    if (state.running) { toast('Já existe uma execução em andamento.', 'err'); return; }

    state.lastRunOptions = { ...o };

    state.running = true;
    $$('#btn-run, #btn-run-side, #btn-plan').forEach((b) => b.disabled = true);
    setStatus('running', planOnly ? 'Simulando…' : 'Renderizando…');
    startTimer();
    const comando = '$ ' + cliCommand(o);
    log(comando);
    // Registra o estado atual da pasta para separar os cortes deste job dos
    // vídeos que já existiam antes da renderização.
    const baseline = await api('/library');
    const baselineFiles = new Map(
      (Array.isArray(baseline.files) ? baseline.files : [])
        .filter((file) => file.rel)
        .map((file) => [file.rel, String(file.size || 0) + ':' + String(file.modified || 0)])
    );
    const job = {
      url: o.url,
      status: 'running',
      meta: (planOnly ? 'plan-only · ' : '') + o.whisper_model + ' · ' + o.engine,
      progress: planOnly ? 'Analisando vídeo…' : 'Preparando processamento…',
      elapsed: 0,
      baselineFiles,
      observedFiles: new Map(),
      previewFiles: [],
    };
    state.jobs.push(job);
    // A galeria vira esqueleto, mas a fila continua mostrando o card real.
    showSkeletons();
    renderQueue();
    // O job já existe na fila antes do primeiro poll de progresso.
    startFollowingRun(comando);

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
    job.title = r.title || '';
    job.clips = Array.isArray(r.clips) ? r.clips : [];
    job.meta = r.error ? ('erro: ' + r.error) : (job.clips.length + ' clips');
    if (!r.error && job.clips.length) {
      const completedPreviews = job.clips
        .filter((clip) => clip.video && clip.rendered !== false)
        .slice(0, 3)
        .map((clip) => ({ rel: clip.video, name: clip.title || 'Corte', size: 0 }));
      if (completedPreviews.length) job.previewFiles = completedPreviews;
    }
    job.progress = r.error ? 'Renderização interrompida' : (job.meta || 'Renderização concluída');
    job.elapsed = Number(r.elapsed) || job.elapsed || 0;
    renderQueue();

    if (r.offline) {
      // sem backend: mostra o comando equivalente e simula progresso
      log('Servidor local não encontrado em ' + API, 'ln-warn');
      log('Rode o comando acima no terminal, ou inicie web/server.py');
      setProgress(100, 'Comando pronto — execute no terminal.');
      // A nota de prontidao fala o mesmo que aqui, e ela sabe se o problema
      // e o arquivo aberto direto (nesse caso mandar subir o servidor e
      // matar o servidor, que pode estar no ar).
      prontidao(r);
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

  // ---------- prontidao do backend ----------
  //
  // A pagina abre sem servidor o tempo todo: metade das pessoas chega pelo
  // arquivo direto, sem ter nunca subido o `web/server.py`. Antes, a unica
  // dica era o pill virando "Offline" depois de um botao ser apertado — ja
  // tarde, depois de um clique que parecia ter dado errado.
  //
  // Ha dois casos, e o conselho de cada um e diferente:
  //   * `location.protocol === 'file:'` — o navegador bloqueia o fetch para
  //     `http://127.0.0.1:7755` porque nao ha cabecalho CORS, entao a nota
  //     diz para abrir pelo endereço. Matar o servidor aqui nao ajuda nada,
  //     ele pode estar no ar.
  //   * servidor de verdade e sem resposta — a nota diz o comando a rodar.
  let ateve_pill_offline = false;
  function prontidao(r) {
    const el = $('#backend-state');
    if (!el) return;
    const tem_erro = !!(r && (r.offline || r.error));
    if (!tem_erro) {
      el.hidden = true;
      // So desfaz se a gente foi quem marcou: o pill tambem carrega "Em
      // execucao" e "Concluido", e zera-los aqui apagaria a resposta do
      // trabalho que acabou de rodar.
      if (ateve_pill_offline && !state.running) {
        setStatus(null, 'Pronto');
        ateve_pill_offline = false;
      }
      return;
    }
    el.hidden = false;
    // A chave e a ORIGEM, nao o protocolo. Medido no navegador: a pagina
    // servida em outra porta que 7755 tambem e bloqueada (nao ha cabecalho
    // CORS), e a nota chegava a mandar "rode o servidor" enquanto o
    // servidor estava de pe — conselho errado, e pior: parece que a pessoa
    // tem que reiniciar algo que nunca parou.
    //
    // Isso inverte a conta: a pagina so recebeu a propria pagina do
    // servidor, entao quem e servido pela 7755 ja tem servidor. O ramo
    // "sem resposta" so existe para a pagina ter caido do ar depois.
    if (location.origin !== API) {
      el.textContent = 'Esta página não está sendo servida pelo painel '
        + '(vinda de ' + location.origin + '), então o navegador recusa a '
        + 'chamada para 127.0.0.1:7755 mesmo com o servidor no ar. Abra '
        + 'http://127.0.0.1:7755/ e carregue de lá.';
      setStatus('error', 'Endereço errado');
    } else if (r.sem_conexao) {
      el.textContent = 'O painel parou de responder em 127.0.0.1:7755. '
        + 'Na pasta do projeto, rode "python web/server.py" e recarregue '
        + 'esta página.';
      setStatus('error', 'Sem servidor');
    } else {
      el.textContent = 'O servidor respondeu com erro (' + r.error + '). '
        + 'A fila e a galeria podem estar desatualizadas.';
      setStatus('error', 'Erro');
    }
    ateve_pill_offline = true;
  }

  async function poll() {
    const r = await api('/status');
    prontidao(r);
    if (r.offline || r.error) return;
    if (Array.isArray(r.clips) && r.clips.length !== state.clips.length) {
      state.clips = r.clips;
      renderClips();
    }
    if (Array.isArray(r.jobs)) {
      const signature = JSON.stringify(r.jobs.map((job) => [
        job.url, job.status, job.meta, job.title,
        (job.clips || []).map((clip) => clip.video || clip.title || ''),
      ]));
      if (signature !== state.jobSignature) {
        const used = new Set();
        const merged = r.jobs.map((remoteJob, index) => {
          let localIndex = index;
          if (!state.jobs[localIndex] || state.jobs[localIndex].url !== remoteJob.url || used.has(localIndex)) {
            localIndex = state.jobs.findIndex((job, candidate) =>
              !used.has(candidate) && job.url === remoteJob.url
            );
          }
          if (localIndex < 0) return remoteJob;
          used.add(localIndex);
          const localJob = state.jobs[localIndex];
          const baselineFiles = localJob.baselineFiles;
          const observedFiles = localJob.observedFiles;
          const previewFiles = localJob.previewFiles;
          Object.assign(localJob, remoteJob);
          if (baselineFiles) localJob.baselineFiles = baselineFiles;
          if (observedFiles) localJob.observedFiles = observedFiles;
          if (previewFiles && previewFiles.length) localJob.previewFiles = previewFiles;
          else if (Array.isArray(remoteJob.clips)) {
            localJob.previewFiles = remoteJob.clips
              .filter((clip) => clip.video && clip.rendered !== false)
              .map((clip) => ({ rel: clip.video, name: clip.title || 'Corte', size: 0 }));
          }
          return localJob;
        });
        if (state.running) {
          state.jobs.forEach((job, index) => {
            if (job.status === 'running' && !used.has(index)) merged.push(job);
          });
        }
        state.jobs = merged;
        state.jobSignature = signature;
        renderQueue();
      }
      await hydrateQueuePreviews();
    }
  }
  document.addEventListener('visibilitychange', () => {
    // Tab escondida: para de pedir. Tab de volta: retoma e traz o estado agora,
    // para a fila nao ficar desatualizada ate o proximo tique.
    if (document.hidden) stopPolling(); else { startPolling(); poll(); }
  });
  // Um `poll` imediato em vez de esperar o primeiro tique de 4s: a nota de
  // "sem servidor" e a primeira coisa que um visitante sem backend quer ver,
  // e ela nao pode esperar quatro segundos para aparecer.
  startPolling();
  poll();

  // Os 6 parametros de selecao vem de /ajustes.json; sem esperar por eles, um
  // clique em Rodar logo apos o load mandaria os defaults do HTML em vez do
  // que esta salvo. A leitura e rapida (arquivo local) e nao bloqueia a tela.
  renderSelectionSummary();
  loadAjustes();

  renderQueue();
  renderClips();
})();
