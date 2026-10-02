(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // O servidor local opcional. Sem ele a pagina continua util: mostra os
  // padroes do proprio formulario e diz que nao esta salvando, em vez de
  // fingir que salvou.
  const API = 'http://127.0.0.1:7755';

  const state = {
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
      // A transcricao colada. Vazia vira null: string vazia e um valor
      // legitimo para `language` (auto-detectar), mas para o texto nao existe
      // "transcricao vazia" -- o motor tem de cair no Whisper.
      transcript_text: $('#transcript').value.trim() || null,

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
      // O Curador com IA saiu desta pagina: o card inteiro mora so em Cortes
      // (que ja o tinha). As chaves ranker_* deixaram AJUSTES_KEYS junto --
      // elas continuam no ClipConfig e na CLI, so nao sao mais um ajuste
      // persistido pelo painel. A Cortes carrega os seus proprios valores e os
      // manda no POST /run.
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

    // O texto colado e o unico campo em que `null` LIMPA em vez de "nao mexe".
    // A regra geral (`null` nunca sobrescreve) existe para um ajustes.toml
    // parcial nao apagar a tela; aqui ela seria um bug, porque nao haveria como
    // remover uma transcricao ja salva -- o campo ficaria preso no arquivo e
    // todo run seguinte usaria aquele texto. Quem apaga e a chave AUSENTE (nao
    // salva ainda), nao a chave nula (salva como "sem transcricao").
    if ('transcript_text' in s) {
      $('#transcript').value = s.transcript_text === null || s.transcript_text === undefined
        ? ''
        : String(s.transcript_text);
    }
    // O carimbo do video, que chega fora de `settings` mas na mesma resposta.
    // Ausente (arquivo antigo, escrito antes do carimbo existir) vira campo
    // vazio -- e campo vazio significa "nao da para provar que o texto e deste
    // video", que e o lado seguro do erro.
    if (typeof s.transcript_source_url === 'string') {
      $('#transcript-url').value = s.transcript_source_url;
    }
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
      // Sem linha de "Curador": o card saiu desta pagina, entao `readSettings()`
      // nao devolve mais ranker/ranker_provider. A ressalva vai no proprio
      // resumo em vez de sumir, senao quem le o painel acha que o curador
      // simplesmente nao existe -- ele existe, so e configurado em Cortes.
      ['Curador', 'configurado na página Cortes'],
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
      // `transcript_source_url` vai ao lado de `settings`, nao dentro: ele nao
      // e uma chave de AJUSTES_KEYS e o servidor recusaria (ou pior, gravaria
      // no ajustes.toml e o arquivo deixaria de ser um --config valido). O
      // servidor o trata como o carimbo que acompanha a transcricao.
      body: JSON.stringify({
        settings: settings,
        transcript_source_url: settings.transcript_text
          ? $('#transcript-url').value.trim()
          : '',
      }),
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

  // ---------- prompt do curador ----------
  // O texto, os rotulos e os estados desta secao sao os MESMOS da pagina
  // Cortes: o card existe nas duas e um usuario que alterna entre elas nao
  // pode ver dois textos diferentes para o mesmo botao. A Ajustes e a via
  // canonica (e onde se salva em disco pelo botao); a Cortes le o mesmo
  // arquivo e mostra as mesmas frases.
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
      r.exists ? 'Prompt carregado do arquivo.' : 'O arquivo ainda não existe — salve para criá-lo.',
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
    setPromptStatus('Salvo em ' + (r.path || 'arquivo') + '.', 'ok');
    toast('Prompt do curador salvo.', 'ok');
  }

  // ---------- transcricao colada ----------
  // A tabela de conferencia mora aqui desde que o campo migrou da pagina
  // Cortes. Sem ela o texto salvo vira uma caixa preta: a pessoa cola 40
  // minutos de fala e nao tem como ver se os minutos ficaram em ordem antes de
  // gastar um render inteiro descobrindo que nao.
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
    // O texto mudou: o autosave grava sozinho, como em qualquer outro campo.
    renderSummary();
    scheduleSave();
  }

  // ---------- ligacao dos controles ----------
  const form = $('#ajustes-form');
  form.addEventListener('change', () => { renderSummary(); scheduleSave(); });
  form.addEventListener('input', () => { renderSummary(); scheduleSave(); });

  $$('.toggle').forEach((t) => {
    t.addEventListener('click', () => { renderSummary(); scheduleSave(); });
  });

  // Os selects nativos viram o componente rico do comum.js. A ressalva
  // `data-defer-enhance` continua aqui mesmo sem o select de provedor nesta
  // pagina: e o marcador que diz "as <option> chegam depois", e um select novo
  // que nasca com so uma opcao precisa do mesmo tratamento. Sem ele o
  // componente copiaria uma lista vazia e o painel nasceria sem opcao nenhuma.
  $$('select[id]:not([data-defer-enhance])').forEach(enhanceSelect);

  $('#btn-save').addEventListener('click', () => saveSettings(false));
  $('#btn-save-side').addEventListener('click', () => saveSettings(false));

  $('#btn-prompt-load').addEventListener('click', loadCuratorPrompt);
  $('#btn-prompt-save').addEventListener('click', saveCuratorPrompt);

  // O colar dispara a organizacao sozinho, num setTimeout(0) para o valor ja
  // estar no textarea quando o handler roda -- o evento `paste` acontece ANTES
  // do navegador inserir o texto. O `silent` evita o toast de "organizada" a
  // cada colagem sem mudanca real.
  $('#transcript').addEventListener('paste', () => setTimeout(() => organizeTranscript(true), 0));

  $('#btn-organize').addEventListener('click', () => organizeTranscript(false));

  $('#btn-analyze').addEventListener('click', () => {
    const text = $('#transcript').value.trim();
    if (!text) {
      toast('Cole a transcrição primeiro.', 'err');
      $('#transcript').focus();
      return;
    }
    organizeTranscript(false);
  });

  $('#btn-clear-transcript').addEventListener('click', () => {
    $('#transcript').value = '';
    $('#cue-preview').hidden = true;
    renderSummary();
    scheduleSave();
    toast('Campo limpo. Salve para gravar a remoção.', 'ok');
  });

  // ---------- partida ----------
  renderSummary();
  loadSettings();
  loadCuratorPrompt();
})();
