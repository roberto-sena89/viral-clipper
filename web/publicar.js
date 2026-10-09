(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);

  // O servidor local. Aqui a origem e' IMPLICITA de proposito: a pagina e'
  // servida por ele, entao '' ja aponta para o host e a porta certos. As duas
  // paginas antigas escrevem 'http://127.0.0.1:7755' a mao, e por isso
  // quebram quando o servidor sobe em outra porta (--port 7756) -- o CSP
  // (`connect-src 'self'`) recusa o destino. Este arquivo nao repete isso.
  const API = '';
  //: Mesmo contrato do index.js: todo dado e acao vive sob /api/, e o prefixo
  //: entra no helper para nao depender de cada call site lembrar dele.
  const API_BASE = API + '/api';

  //: O caminho publico de um arquivo de clip, e o UNICO lugar que o monta --
  //: mesma regra do index.js. O servidor devolve o caminho RELATIVO a
  //: `output/`; o prefixo da rota entra so aqui.
  const CLIP_URL_BASE = '/api/clips/';

  function clipsPath(rel) {
    return CLIP_URL_BASE
      + String(rel == null ? '' : rel).split('/').map(encodeURIComponent).join('/');
  }

  const esc = window.esc;

  // ---------- helpers ----------

  function toast(msg, kind) {
    const zone = $('#toast-zone');
    if (!zone) return;
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = msg;
    zone.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transition = 'opacity 300ms';
      setTimeout(() => el.remove(), 320);
    }, 4200);
  }

  // O aviso tambem vai para a regiao viva: quem usa leitor de tela aperta o
  // botao e nao ve o toast, que e' um elemento puramente visual.
  function aviso(msg, kind) {
    const vivo = $('#publicar-status');
    if (vivo) vivo.textContent = msg;
    toast(msg, kind);
  }

  function segundos(valor) {
    const n = Number(valor);
    if (!Number.isFinite(n) || n <= 0) return '—';
    return n.toFixed(1).replace('.', ',') + 's';
  }

  function nota(valor) {
    const n = Number(valor);
    if (!Number.isFinite(n)) return '—';
    return String(Math.round(n * 10) / 10).replace('.', ',');
  }

  // A legenda e' o texto do POST: headline primeiro, hashtags depois. A linha
  // em branco entre os dois nao e' decoracao -- sem ela a ultima palavra da
  // headline cola na primeira hashtag e o campo de descricao recebe
  // "...#viral" como se fosse uma palavra so.
  function legendaDe(clip) {
    const partes = [String(clip.headline || '').trim()];
    const tags = String(clip.hashtags || '').trim();
    if (tags) partes.push('', tags);
    return partes.join('\n').trim();
  }

  // ---------- render ----------

  //: Os clips da ultima leitura, guardados para o listener de copia achar pelo
  //: indice. O DOM nao serve como fonte: ele guarda o texto ja escapado, e
  //: reconstruir a legenda a partir dele seria uma segunda montagem do mesmo
  //: texto -- exatamente o que sai de sincronia quando o layout muda.
  let clipsLidos = [];

  function fichaHtml(data) {
    // A duracao da FONTE e' leitura de minutos, nao medida de clipe: 713 s
    // vira "11:53". O `segundos()` com decimal continua sendo o dos TRECHOS,
    // onde 30,0s importa; em 11 minutos o decimal e' ruido.
    const duracao = (valor) => {
      const n = Number(valor);
      if (!Number.isFinite(n) || n <= 0) return '';
      const total = Math.round(n);
      const h = Math.floor(total / 3600);
      const m = Math.floor((total % 3600) / 60);
      const s = total % 60;
      return h
        ? h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0')
        : m + ':' + String(s).padStart(2, '0');
    };
    // O chip do endereco mostra so' o que se reconhece -- host + caminho. O
    // URL COMPLETO viaja no href e no `title`, entao nada se perde, e uma
    // query longa nao empurra o resto da ficha para fora da coluna.
    const endereco = (valor) => {
      try {
        const u = new URL(valor);
        return u.hostname.replace(/^www\./, '')
          + (u.pathname === '/' ? '' : u.pathname) + u.search;
      } catch (e) {
        return String(valor);
      }
    };
    const celulas = [];
    const add = (rotulo, valor, op) => {
      if (!valor) return;
      const o = op || {};
      let conteudo;
      if (o.href) {
        conteudo = '<a class="publicar-ficha-link" href="' + esc(o.href) + '"'
          + ' title="' + esc(o.href) + '" target="_blank" rel="noopener">'
          + '<span class="publicar-ficha-link-texto">' + esc(valor) + '</span>'
          + '<svg class="publicar-ficha-seta" viewBox="0 0 12 12" aria-hidden="true" focusable="false">'
          + '<path d="M3.75 8.25 8.25 3.75M4.5 3.75h3.75V7.5" fill="none" stroke="currentColor"'
          + ' stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg></a>';
      } else if (o.chip) {
        conteudo = '<span class="publicar-ficha-chip">' + esc(valor) + '</span>';
      } else {
        conteudo = esc(valor);
        if (o.caminho) conteudo = '<code class="publicar-ficha-caminho">' + conteudo + '</code>';
      }
      celulas.push('<div class="publicar-ficha-cell' + (o.classe ? ' ' + o.classe : '') + '">'
        + '<dt>' + esc(rotulo) + '</dt><dd>' + conteudo + '</dd></div>');
    };
    add('Título', data.title, { classe: 'publicar-ficha-cell--lead' });
    add('Canal', data.uploader);
    add('Duração da fonte', duracao(data.source_duration), { classe: 'publicar-ficha-cell--stat' });
    add('Trechos', (data.clips || []).length ? String(data.clips.length) : '',
        { classe: 'publicar-ficha-cell--stat' });
    add('Curador', data.model, { classe: 'publicar-ficha-cell--chip', chip: true });
    add('Endereço', data.url ? endereco(data.url) : '',
        { classe: 'publicar-ficha-cell--link', href: data.url });
    add('Relatório', data.path, { classe: 'publicar-ficha-cell--caminho', caminho: true });
    if (!celulas.length) {
      return '<div class="publicar-ficha-cell publicar-ficha-cell--lead"><dt>—</dt>'
        + '<dd>Sem ficha: o relatório não trouxe os dados da origem.</dd></div>';
    }
    return celulas.join('');
  }

  function cartaoHtml(clip, indice) {
    const id = 'publicar-legenda-' + indice;
    const legenda = legendaDe(clip);
    const temArquivo = !!clip.rel;
    const ganchos = (clip.hook_terms || []).filter(Boolean);

    const thumb = clip.poster
      ? '<img src="' + clipsPath(clip.poster) + '" alt="" loading="lazy" decoding="async">'
      : '<span class="publicar-thumb-vazio" aria-hidden="true">9:16</span>';

    // Sem headline e sem hashtags o campo fica vazio, e um campo vazio com um
    // botao "Copiar" ao lado e' um botao que nao faz nada. Nesse caso o cartao
    // diz o que houve e so oferece o arquivo.
    const campo = legenda
      ? '<div class="field">'
        + '<label for="' + id + '">Legenda do clip ' + esc(clip.index) + '</label>'
        + '<textarea id="' + id + '" readonly rows="3" spellcheck="false">'
        + esc(legenda) + '</textarea>'
        + '</div>'
      : '<p class="hint">O curador não escreveu headline nem hashtags para este '
        + 'trecho. Ou o modelo não respondeu, ou o prompt atual não pede esse '
        + 'texto — veja o <a href="/ajustes">Prompt do curador</a>.</p>';

    const acoes = [];
    if (legenda) {
      acoes.push('<button class="btn btn-primary pressable" type="button"'
        + ' data-copiar="legenda" data-indice="' + esc(indice) + '">Copiar legenda</button>');
    }
    if (String(clip.hashtags || '').trim()) {
      acoes.push('<button class="btn pressable" type="button"'
        + ' data-copiar="hashtags" data-indice="' + esc(indice) + '">Copiar hashtags</button>');
    }
    if (temArquivo) {
      acoes.push('<a class="btn pressable" href="' + clipsPath(clip.rel) + '"'
        + ' target="_blank" rel="noopener">Abrir o vídeo</a>');
    } else {
      acoes.push('<span class="hint">Sem arquivo: a execução parou na análise.</span>');
    }

    const trecho = String(clip.text || '').trim()
      ? '<details class="publicar-trecho"><summary>Trecho transcrito</summary>'
        + '<p>' + esc(clip.text) + '</p></details>'
      : '';

    return '<article class="card clip-card publicar-clip">'
      + '<div class="publicar-topo">'
      +   '<div class="publicar-thumb">' + thumb + '</div>'
      +   '<div class="publicar-meta">'
      +     '<p class="publicar-index">Clip ' + esc(clip.index) + '</p>'
      +     (clip.headline
              ? '<strong class="publicar-titulo">' + esc(clip.headline) + '</strong>'
              : '<strong class="publicar-titulo publicar-titulo-vazio">sem headline</strong>')
      +     '<p class="hint">nota ' + nota(clip.score)
      +       ' · ' + esc(clip.start_label || '—') + ' → ' + esc(clip.end_label || '—')
      +       ' · ' + segundos(clip.duration) + '</p>'
      +     (ganchos.length
              ? '<p class="publicar-ganchos">ganchos: ' + esc(ganchos.join(' · ')) + '</p>'
              : '')
      +   '</div>'
      + '</div>'
      + campo
      + '<div class="row-actions">' + acoes.join('') + '</div>'
      + trecho
      + '</article>';
  }

  // O card da Origem e o atalho que aponta para ele sobem e descem JUNTOS.
  //
  // Um atalho para uma secao escondida e' um clique que nao leva a lugar
  // nenhum, e um card sem ficha e' moldura de dado que nao existe — era o
  // motivo de o `mostrarFalha` ja esconder o card. Aqui a regra vira UMA so,
  // para os dois caminhos que escondem (sem relatorio e servidor fora do ar)
  // nao divergirem: quando um escondia e o outro nao, o atalho ficava apontando
  // para o vazio.
  function mostrarOrigem(visivel) {
    const card = $('#publicar-origem');
    const atalho = $('#publicar-link-origem');
    if (card) card.hidden = !visivel;
    if (atalho) atalho.hidden = !visivel;
  }

  function render(data) {
    const lista = $('#publicar-lista');
    const vazio = $('#publicar-vazio');
    const erro = $('#publicar-erro');
    const resumo = $('#publicar-resumo');
    const clips = data.clips || [];
    clipsLidos = clips;

    // Uma leitura que da' certo depois de uma que falhou tem de trazer a ficha
    // da Origem de volta: `mostrarFalha` a esconde, e sem isto ela ficaria
    // escondida para sempre -- o estado do DOM sobrevivendo ao erro.
    //
    // `exists === false` e' o outro lado: nao ha relatorio, entao nao ha ficha.
    // O card mostraria so' o caminho do arquivo, e o subtitulo "De onde sairam
    // estes trechos" falaria de trechos que nao existem. O card de Clips ja diz,
    // no resumo, em que arquivo a pagina esta' olhando — o card da Origem seria
    // a mesma frase dentro de outra moldura.
    mostrarOrigem(data.exists === true);

    erro.hidden = true;
    erro.textContent = '';
    $('#publicar-ficha').innerHTML = fichaHtml(data);

    if (data.error) {
      erro.textContent = 'O relatório existe mas não pôde ser lido: ' + data.error
        + '. Rode a execução de novo para reescrever ' + (data.path || 'clips.json') + '.';
      erro.hidden = false;
    }

    const semArquivo = clips.filter((c) => !c.rel).length;

    if (!clips.length) {
      lista.innerHTML = '';
      vazio.hidden = false;
      resumo.textContent = data.exists
        ? 'O relatório existe, mas não lista nenhum clip.'
        : 'Ainda não há relatório em ' + (data.path || 'output/clips.json') + '.';
      return;
    }

    vazio.hidden = true;
    resumo.textContent = clips.length + (clips.length === 1 ? ' clip' : ' clips')
      + ' no relatório'
      + (semArquivo ? ' (' + semArquivo + ' sem arquivo renderizado)' : '')
      + '. Copie a legenda e cole no campo de descrição da plataforma.';
    lista.innerHTML = clips.map((clip, i) => cartaoHtml(clip, i)).join('');
  }

  // ---------- dados ----------

  function mostrarFalha(msg) {
    const resumo = $('#publicar-resumo');
    if (resumo) resumo.textContent = msg;
    $('#publicar-vazio').hidden = true;
    $('#publicar-lista').innerHTML = '';
    $('#publicar-ficha').innerHTML = '';
    // O card da Origem e o atalho dele saem junto: sem relatorio o card e' um
    // titulo e uma dica apontando para uma ficha vazia -- moldura de dado que
    // nao existe -- e o atalho levaria a uma secao escondida.
    mostrarOrigem(false);
  }

  async function carregar() {
    const botao = $('#btn-recarregar');
    botao.disabled = true;
    try {
      const r = await fetch(API_BASE + '/publicacao', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      render(await r.json());
    } catch (err) {
      // Sem servidor nao ha o que ler: esta pagina e' so a leitura de um
      // arquivo que ele conhece. O aviso diz o que fazer em vez de acusar.
      mostrarFalha('Não foi possível ler o relatório pelo servidor (' + err.message
        + '). Inicie o painel com `python web/server.py` e recarregue.');
      aviso('Servidor fora do ar: o relatório não pôde ser lido.', 'err');
    } finally {
      botao.disabled = false;
    }
  }

  // ---------- copiar ----------

  //: A frase que o usuario le depois de copiar. Concordancia pronta em vez de
  //: montada: "Hashtags copiada" seria o resultado de colar o rotulo num molde
  //: unico, e hashtag e' feminino plural.
  const COPIOU = { legenda: 'Legenda copiada.', hashtags: 'Hashtags copiadas.' };

  async function copiar(texto, tipo, botao) {
    if (!texto) {
      aviso('Não há ' + tipo + ' neste clip.', 'err');
      return;
    }
    // `isSecureContext` antes de tentar: em http puro a `navigator.clipboard`
    // existe mas rejeita, e o aviso generico esconderia o motivo real. Mesma
    // escolha (e mesma razao) do rodape em comum.js.
    if (!window.isSecureContext || !navigator.clipboard || !navigator.clipboard.writeText) {
      aviso('Cópia automática indisponível. Selecione o texto e copie.', 'err');
      return;
    }
    // Desabilitar durante a copia impede o clique duplo de disparar duas
    // escritas concorrentes no clipboard; o `finally` devolve o botao mesmo
    // quando a copia falha.
    botao.disabled = true;
    try {
      await navigator.clipboard.writeText(texto);
      aviso(COPIOU[tipo] || 'Copiado.', 'ok');
    } catch (err) {
      aviso('Não foi possível copiar. Selecione o texto e copie.', 'err');
    } finally {
      botao.disabled = false;
    }
  }

  // Delegacao: um listener cobre todos os cartoes. Sem um id por botao, a
  // lista pode crescer sem que este arquivo ganhe mais uma linha por clip.
  document.addEventListener('click', (ev) => {
    const botao = ev.target.closest('[data-copiar]');
    if (!botao) return;
    const clip = clipsLidos[Number(botao.getAttribute('data-indice'))];
    if (!clip) return;
    const tipo = botao.getAttribute('data-copiar') === 'hashtags' ? 'hashtags' : 'legenda';
    copiar(tipo === 'hashtags' ? String(clip.hashtags || '').trim() : legendaDe(clip),
      tipo, botao);
  });

  const recarregar = $('#btn-recarregar');
  if (recarregar) recarregar.addEventListener('click', carregar);

  carregar();
})();
