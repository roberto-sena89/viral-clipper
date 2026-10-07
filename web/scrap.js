(function () {
  "use strict";

  // O rail (lista de destinos, marcacao da pagina atual e o menu do header)
  // mora em /comum.js: RAIL_PAGES + renderRail. Ele se monta sozinho no
  // load, entao nao ha nada para ligar aqui.

  // ---------- estado da pagina ----------
  //: Caminho relativo de proposito, e o prefixo entra aqui: todo dado e
  //: acao vive sob /api/. As PAGINAS nao passam por estas funcoes.
  var API = "/api";

  // ---------- helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  let toastTimer = null;
  function toast(message, kind) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.className = "toast " + (kind || "");
    el.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  function fmtDuration(seconds) {
    if (seconds == null) return "—";
    const s = Math.round(Number(seconds));
    if (!isFinite(s)) return "—";
    const m = Math.floor(s / 60);
    const r = s % 60;
    if (m >= 60) {
      const h = Math.floor(m / 60);
      return h + "h " + String(m % 60).padStart(2, "0") + "m";
    }
    return m + ":" + String(r).padStart(2, "0");
  }

  // Contagem curta em pt-BR: 16, 16k, 16,5k, 16mi. O sufixo e o que o publico
  // brasileiro le em qualquer plataforma ("mi" de milhao, nao "M") e a casa
  // decimal usa virgula, como manda o idioma.
  function fmtCount(n) {
    if (n == null) return "—";
    if (n >= 1e6) return fmtShort(n / 1e6) + "mi";
    if (n >= 1e3) {
      const thousands = fmtShort(n / 1e3);
      // 999.999 arredonda para "1000k", que ninguem escreve: vira "1mi".
      return thousands === "1000" ? "1mi" : thousands + "k";
    }
    return String(n);
  }

  // Uma casa decimal, sem ",0" no fim: 16.000 vira "16k", 16.500 vira "16,5k",
  // 16.000.000 vira "16mi". O corte e ancorado no fim porque ".0" solto tambem
  // aparece no meio de outros valores.
  function fmtShort(value) {
    return value.toFixed(1).replace(/\.0$/, "").replace(".", ",");
  }

  // ---------- backend ----------
  // Caminho relativo de proposito: a pagina pode estar sob /scrap e o mesmo
  // host serve a API. Sem isto o fetch viraria cross-origin quando o painel
  // muda de porta.
  async function post(path, body) {
    try {
      const res = await fetch(API + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return { error: data.error || ("HTTP " + res.status), code: res.status };
      return data;
    } catch (e) {
      return { error: "Servidor local não respondeu. Inicie web/server.py.", offline: true };
    }
  }

  // Leitura simples, para acompanhar o progresso. O codigo HTTP vem junto porque
  // o chamador precisa distinguir "o servidor nao respondeu" de "aquele job ja
  // estava rodando" (409): no segundo caso o certo e continuar acompanhando.
  async function get(path) {
    try {
      const res = await fetch(API + path);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return { error: data.error || ("HTTP " + res.status), code: res.status };
      return data;
    } catch (e) {
      return { error: "Servidor local não respondeu. Inicie web/server.py.", offline: true };
    }
  }

  // ---------- abas Link / Perfil ----------
  var mode = "link";

  function renderMode() {
    $$("[data-mode]").forEach((tab) => {
      const active = tab.getAttribute("data-mode") === mode;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    const opts = $("#perfil-opts");
    if (opts) opts.hidden = mode !== "profile";
    const viralHint = $("#viral-hint");
    if (viralHint) viralHint.hidden = mode !== "profile";
    const hint = $("#scrap-hint");
    if (hint) {
      hint.innerHTML = mode === "profile"
        ? "Modo <strong>Perfil</strong>: varre a lista de vídeos da conta. " +
          "Instagram costuma exigir cookies."
        : "Modo <strong>Link</strong>: aceita um post, reel ou vídeo. " +
          "Reels do Instagram também exigem cookies.";
    }
  }

  function setMode(next) {
    mode = next === "profile" ? "profile" : "link";
    renderMode();
    renderPlatform();
  }

  // Arrow keys move between tabs; that is what role=tablist promises.
  $$("[data-mode]").forEach((tab) => {
    tab.addEventListener("click", () => setMode(tab.getAttribute("data-mode")));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      setMode(mode === "link" ? "profile" : "link");
      const active = $('[data-mode="' + mode + '"]');
      if (active) active.focus();
    });
  });

  // ---------- abas de plataforma (Instagram / TikTok / YouTube) ----------
  // O arquivamento de perfil (reels/ + posts/ via GraphQL) só existe no
  // Instagram: a caixa some nas outras plataformas em vez de oferecer um
  // botão que falharia. A busca e o "Baixar selecionados" valem para as três.
  var platform = "instagram";

  function updateArchiveVisibility() {
    const arqBox = $("#arquivar-box");
    if (arqBox) arqBox.hidden = platform !== "instagram" || results.length === 0;
  }

  function renderPlatform() {
    $$("[data-platform]").forEach((tab) => {
      const active = tab.getAttribute("data-platform") === platform;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    const placeholders = {
      "instagram|link": "https://www.instagram.com/reel/... ou https://www.instagram.com/p/...",
      "instagram|profile": "https://www.instagram.com/<perfil>/",
      "tiktok|link": "https://www.tiktok.com/@.../video/...",
      "tiktok|profile": "https://www.tiktok.com/@perfil",
      "youtube|link": "https://www.youtube.com/watch?v=... ou https://youtu.be/...",
      "youtube|profile": "https://www.youtube.com/@canal",
    };
    const input = $("#scrap-url");
    if (input) input.placeholder = placeholders[platform + "|" + mode] || "";
    updateArchiveVisibility();
  }

  function setPlatform(next) {
    if (next !== "instagram" && next !== "tiktok" && next !== "youtube") return;
    platform = next;
    renderPlatform();
  }

  // Arrow keys move between tabs; that is what role=tablist promises.
  $$("[data-platform]").forEach((tab) => {
    tab.addEventListener("click", () => setPlatform(tab.getAttribute("data-platform")));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      const order = ["instagram", "tiktok", "youtube"];
      const at = order.indexOf(platform) + (e.key === "ArrowRight" ? 1 : -1);
      setPlatform(order[(at + order.length) % order.length]);
      const active = $('[data-platform="' + platform + '"]');
      if (active) active.focus();
    });
  });

  // ---------- "Mais viralizados" (botões de alternar) ----------
  // Mesma cara do Buscar: apagado = ordem do feed, aceso (btn-primary) =
  // ranking por views/engajamento. aria-pressed é a fonte da verdade.
  var viralOn = false;
  var arqViralOn = false;

  function renderViralButtons() {
    const searchBtn = $("#btn-viral");
    if (searchBtn) {
      searchBtn.classList.toggle("btn-primary", viralOn);
      searchBtn.setAttribute("aria-pressed", String(viralOn));
    }
    const archBtn = $("#btn-arq-viral");
    if (archBtn) {
      archBtn.classList.toggle("btn-primary", arqViralOn);
      archBtn.setAttribute("aria-pressed", String(arqViralOn));
    }
  }

  // ---------- resultados ----------
  var results = [];
  var picked = null;
  // Indices marcados para baixar. E um Set e nao um campo de cada item porque a
  // lista inteira e re-renderizada a cada busca: o estado tem de viver fora do
  // DOM, ou a marcacao some junto com os cards antigos.
  var selected = new Set();
  // Titulo da lista em tela ("ANCAPSU - Vídeos"), usado para nomear a pasta do
  // download. Guardado no render porque o `title` so existe dentro dele.
  var collectionTitle = "";
  // Cresce a cada busca. Vai na URL da thumbnail para o browser nao servir a
  // imagem da busca anterior do cache quando o indice do card se repete — os
  // indices 0,1,2... sao sempre os mesmos, entao sem isto o item 2 da busca
  // nova apareceria com a cara do item 2 da busca velha.
  var searchSeq = 0;

  // Os cards sao HTML montado a mao (nao ha template engine nem build nesta
  // pagina), e o titulo de um video e texto de terceiro. Tudo que entra no
  // markup passa por `esc`, que mora em `comum.js` — inclusive o que vai
  // dentro de um atributo, onde uma aspa fecharia o valor e o resto viraria
  // markup. Ver o comentario de la.

  // O src e uma URL que veio de fora (CDN do Instagram/YouTube) OU um caminho
  // desta origem. As duas formas entram: absoluta http(s), ou relativa
  // comecando com "/" (nunca "//", que e outro host). `javascript:` e `data:`
  // ficam de fora — num src nao executam, mas o mesmo valor serve o href do
  // link do titulo, onde executariam.
  function safeUrl(value) {
    const url = String(value == null ? "" : value).trim();
    if (url.startsWith("//")) return "";
    if (url.startsWith("/")) return url;
    return /^https?:\/\//i.test(url) ? url : "";
  }

  // O card NUNCA usa ``item.thumb`` no src. Esse campo e a URL do CDN alheio
  // (Instagram, YouTube), e o CSP desta pagina aceita imagem so da propria
  // origem — usa-la ali nao pinta nada, apenas gera um erro de policy no
  // console que parece bug de rede. Quem pinta e a fila, com /scrap/thumb.
  function thumbMarkup() {
    return '<div class="result-thumb" data-state="loading">' +
             '<span class="thumb-fallback" aria-hidden="true">▶</span>' +
             '<span class="result-play" aria-hidden="true">▶</span>' +
           "</div>";
  }

  function resultCard(item, index) {
    const duration = fmtDuration(item.duration);
    const parts = [];
    if (item.uploader) parts.push('<span class="result-uploader">' + esc(item.uploader) + "</span>");
    if (item.view_count != null) {
      parts.push('<span class="result-views">' + esc(fmtCount(item.view_count)) + " views</span>");
    }
    const meta = parts.join('<span class="dot">·</span>');

    const title = item.title || "(sem título)";
    const href = safeUrl(item.url);

    return '<article class="result" data-index="' + index + '"' +
        (selected.has(index) ? ' data-selected="1"' : "") + ">" +
      '<label class="result-check">' +
        '<input type="checkbox" data-act="check" data-index="' + index + '"' +
        (selected.has(index) ? " checked" : "") +
        ' aria-label="Selecionar para baixar">' +
      "</label>" +
      thumbMarkup() +
      '<div class="result-main">' +
        (href
          ? '<a class="result-title" href="' + esc(href) + '" target="_blank" ' +
            'rel="noopener noreferrer" title="' + esc(title) + '">' + esc(title) + "</a>"
          : '<span class="result-title" title="' + esc(title) + '">' + esc(title) + "</span>") +
        '<div class="result-meta">' +
          (meta ? meta + '<span class="dot">·</span>' : "") +
          '<span class="result-badge">' + esc(duration) + "</span>" +
        "</div>" +
      "</div>" +
      '<div class="result-actions">' +
        '<button class="btn btn-sm pressable" type="button" data-act="use" ' +
        'data-index="' + index + '">Usar</button>' +
      "</div>" +
    "</article>";
  }

  // A thumbnail custa uma extracao de metadados por item (o feed de um perfil
  // nao traz imagem nenhuma), entao ela vem DEPOIS da lista: a lista aparece
  // inteira e utilizavel primeiro, e as imagens chegam em fila. Em serie, de
  // proposito — paralelizar aqui e o jeito mais rapido de o Instagram
  // responder 429 e nenhuma imagem aparecer.
  //
  // A URL que o servidor devolve em ``thumb`` e a do CDN alheio, e o CSP da
  // pagina so aceita imagem da propria origem. Entao o que se usa no <img> e
  // SEMPRE ``/scrap/thumb``, que baixa e serve os bytes daqui: o ``thumb`` do
  // item serve so como sinal de "ja foi resolvido, nao precisa esperar a fila".
  var thumbQueue = [];
  var thumbRunning = false;

  function queueThumbs() {
    thumbQueue = results.map((_, i) => i);
    if (!thumbRunning) drainThumbs();
  }

  async function drainThumbs() {
    thumbRunning = true;
    while (thumbQueue.length) {
      const index = thumbQueue.shift();
      const item = results[index];
      if (!item) continue;
      // Resolver de novo e barato: o servidor guarda os bytes em disco e
      // responde da cache. Um GET direto ja entrega a imagem, sem extracao.
      paintThumb(index, "/api/scrap/thumb?i=" + index + "&s=" + searchSeq);
    }
    thumbRunning = false;
  }

  function cardFor(index) {
    return $('.result[data-index="' + index + '"]');
  }

  function paintThumb(index, src) {
    const box = cardFor(index);
    if (!box) return;
    const slot = box.querySelector(".result-thumb");
    if (!slot) return;
    const url = safeUrl(src);
    if (!url) { markThumbEmpty(index); return; }
    const img = document.createElement("img");
    img.src = url;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => markThumbEmpty(index), { once: true });
    slot.innerHTML = "";
    slot.appendChild(img);
    const play = document.createElement("span");
    play.className = "result-play";
    play.setAttribute("aria-hidden", "true");
    play.textContent = "▶";
    slot.appendChild(play);
    slot.setAttribute("data-state", "done");
  }

  function markThumbEmpty(index) {
    const box = cardFor(index);
    const slot = box && box.querySelector(".result-thumb");
    if (!slot) return;
    slot.setAttribute("data-state", "empty");
  }

  function markPicked(index) {
    $$(".result").forEach((card) => {
      const on = Number(card.getAttribute("data-index")) === index;
      if (on) card.setAttribute("data-picked", "1");
      else card.removeAttribute("data-picked");
    });
  }

  // ---------- selecao para baixar ----------
  // "Usar" escolhe UM video para cortar; a marcacao escolhe QUANTOS videos
  // baixar. Sao estados diferentes de proposito, e por isso tem cores
  // diferentes no card: uma escolha e destrutiva (muda o alvo dos cortes), a
  // outra e cumulativa.
  function toggleSelection(index, on) {
    if (on) selected.add(index);
    else selected.delete(index);
    const card = cardFor(index);
    if (card) {
      if (on) card.setAttribute("data-selected", "1");
      else card.removeAttribute("data-selected");
    }
    renderSelection();
  }

  // Sincroniza o DOM inteiro com o Set. Usado pelo "Selecionar todos", que
  // marca N cards de uma vez: re-renderizar a lista faria as thumbnails
  // recarregarem todas a toa, e elas ja estao certas.
  function paintSelection() {
    $$(".result").forEach((card) => {
      const index = Number(card.getAttribute("data-index"));
      const on = selected.has(index);
      const input = card.querySelector("input[data-act='check']");
      if (input) input.checked = on;
      if (on) card.setAttribute("data-selected", "1");
      else card.removeAttribute("data-selected");
    });
    renderSelection();
  }

  function renderSelection() {
    const total = results.length;
    const chosen = selected.size;
    const count = $("#select-count");
    if (count) {
      count.textContent = chosen
        ? chosen + " de " + total + " selecionado(s). O download pula o que já está em disco."
        : "Nada selecionado. Marque os vídeos ou use Selecionar todos.";
    }
    const all = $("#btn-select-all");
    if (all) {
      all.textContent = total && chosen === total ? "Limpar seleção" : "Selecionar todos";
      all.disabled = total === 0;
    }
    const button = $("#btn-download");
    if (button) {
      button.disabled = busy || chosen === 0;
      button.textContent = busy
        ? "Baixando…"
        : (chosen ? "Baixar selecionados (" + chosen + ")" : "Baixar selecionados");
    }
  }

  function toggleAll() {
    if (!results.length) return;
    // Tudo marcado e o unico momento em que o mesmo botao significa o
    // contrario; o rotulo diz qual dos dois vai acontecer antes do clique.
    const clearing = selected.size === results.length;
    selected.clear();
    if (!clearing) results.forEach((_, index) => selected.add(index));
    paintSelection();
  }

  // ---------- progresso do download ----------
  // O POST so devolve o aceite: o trabalho roda numa thread no servidor, porque
  // uma selecao de vinte videos leva minutos e uma resposta que so volta no fim
  // nao teria como mostrar progresso. Quem mostra e este poll, duas vezes por
  // segundo, lendo o record que o servidor publica.
  var downloadTimer = null;
  var busy = false;

  function downloadPhaseLabel(record) {
    if (record.active) {
      const total = Number(record.total) || 0;
      const index = Math.min(Number(record.index) || 0, total || 0);
      const code = record.title ? " · " + String(record.title).slice(0, 32) : "";
      return total ? ("Baixando " + index + " de " + total + code) : "Baixando…";
    }
    if (record.state === "concluido") return "Download concluído";
    if (record.state === "erro") return "Não foi possível baixar";
    return "Preparando…";
  }

  function paintDownload(record) {
    const wrap = $("#download-progress");
    const bar = $("#download-bar");
    const fill = $("#download-fill");
    const phase = $("#download-phase");
    const percentEl = $("#download-percent");
    const meta = $("#download-meta");
    const count = $("#select-count");
    if (record.state === "ocioso" || !record.state) return;

    const total = Number(record.total) || 0;
    const index = Number(record.index) || 0;
    // A fracao soma os itens ja fechados com o pedaco do item em maos. Sem
    // isso a barra ficaria parada em "1 de 20" durante os minutos de um video
    // longo, que e justamente quando ela e util.
    const done = Math.max(0, Math.min(index, total) - (record.active ? 1 : 0));
    const current = record.active ? (Number(record.percent) || 0) / 100 : 0;
    const fraction = total ? Math.min(1, (done + current) / total) : 0;
    const pct = Math.round(fraction * 100);
    const visual = record.active ? "running" : (record.state === "erro" ? "error" : "done");

    if (wrap) wrap.hidden = false;
    if (bar && fill) {
      bar.setAttribute("data-state", visual);
      bar.setAttribute("aria-valuenow", String(pct));
      fill.style.width = pct + "%";
    }
    if (phase) {
      phase.innerHTML = "";
      if (record.active) {
        const spin = document.createElement("span");
        spin.className = "dl-spin";
        spin.setAttribute("aria-hidden", "true");
        phase.appendChild(spin);
      }
      phase.appendChild(document.createTextNode(downloadPhaseLabel(record)));
    }
    if (percentEl) {
      percentEl.textContent = pct + "%";
      percentEl.setAttribute("data-state", visual);
    }
    if (meta) {
      if (record.active) {
        const parts = [];
        if (total) parts.push(index + "/" + total);
        if (record.title) parts.push(String(record.title).slice(0, 48));
        if (record.root) parts.push(record.root);
        meta.textContent = parts.join(" · ");
      } else if (record.state === "erro") {
        meta.textContent = record.error || "erro";
      } else {
        const parts = [
          record.downloaded + " baixado(s)",
          record.skipped + " já tinha",
          record.failed + " falha(s)",
        ];
        if (record.root) parts.push(record.root);
        meta.textContent = parts.join(" · ");
      }
    }

    if (count) {
      if (record.active) {
        const parts = ["Baixando " + Math.min(Number(record.index) || 1, Number(record.total) || 1) +
                       " de " + (Number(record.total) || 0)];
        if (record.percent) parts.push(Math.round(Number(record.percent)) + "%");
        if (record.title) parts.push(record.title.slice(0, 48));
        count.textContent = parts.join(" · ");
      } else if (record.state === "erro") {
        count.textContent = "O download falhou: " + (record.error || "erro");
      } else {
        const parts = [
          record.downloaded + " baixado(s)",
          record.skipped + " já tinha",
          record.failed + " falha(s)",
        ];
        if (record.root) parts.push(record.root);
        count.textContent = parts.join(" · ");
      }
    }
  }

  function stopPolling() {
    if (downloadTimer) {
      clearInterval(downloadTimer);
      downloadTimer = null;
    }
  }

  function setBusy(on) {
    busy = on;
    const all = $("#btn-select-all");
    if (all) all.disabled = on;
    renderSelection();
  }

  function followDownload() {
    stopPolling();
    downloadTimer = setInterval(pollDownload, 700);
  }

  async function pollDownload() {
    const record = await get("/scrap/download/progress");
    if (record.error) {
      // O servidor caiu no meio: para de perguntar e devolve o controle.
      stopPolling();
      setBusy(false);
      toast(String(record.error).slice(0, 120), "bad");
      return;
    }
    paintDownload(record);
    if (record.active) return;

    stopPolling();
    setBusy(false);
    if (record.state === "erro") {
      toast(("Falhou: " + (record.error || "")).slice(0, 120), "bad");
      return;
    }
    const summary = record.downloaded + " baixado(s), " + record.skipped +
                    " já tinha, " + record.failed + " falha(s)";
    toast(summary + (record.root ? " em " + record.root : ""), "ok");
  }

  // Se a pagina for recarregada no meio de um lote, o trabalho continua no
  // servidor: aqui ele volta a ser acompanhado em vez de virar um fantasma.
  async function resumeDownload() {
    const record = await get("/scrap/download/progress");
    if (record.error || !record.active) return;
    setBusy(true);
    paintDownload(record);
    followDownload();
  }


  // Diferente de "Baixar para o computador" (que le o catalogo de um perfil do
  // Instagram a partir de cookies), aqui a lista ja esta na tela: o que vai para
  // o servidor e a selecao exata, uma URL por item, e cada site responde por si.
  // Por isso funciona igual em YouTube, TikTok e Instagram.
  async function downloadSelected(event) {
    if (event) event.preventDefault();
    if (busy) return;
    const items = Array.from(selected)
      .sort((a, b) => a - b)
      .map((index) => results[index])
      .filter(Boolean)
      .map((item) => ({ url: item.url, id: item.id, title: item.title }));
    if (!items.length) {
      toast("Nada selecionado.", "bad");
      return;
    }

    const cookiesFile = $("#scrap-cookies-file");
    const r = await post("/scrap/download", {
      items: items,
      collection: collectionTitle,
      cookies_file: cookiesFile ? cookiesFile.value.trim() : "",
      ig_session: igSessionValue(),
    });

    if (r.error) {
      // 409 e "ja existe um lote rodando": nao e erro para mostrar e parar, e
      // motivo para voltar a acompanhar o que ja esta em andamento.
      if (r.code === 409) {
        setBusy(true);
        await resumeDownload();
        return;
      }
      toast(String(r.error).slice(0, 120), "bad");
      return;
    }

    setBusy(true);
    paintDownload({
      active: true, state: "baixando", phase: "item", index: 0,
      total: r.total, percent: 0, title: "",
      downloaded: 0, skipped: 0, failed: 0, root: r.root,
    });
    followDownload();
  }

  function renderResults(title, removed) {
    const box = $("#resultados");
    const sub = $("#resultados-sub");
    if (!box) return;
    // Lista nova, selecao nova: indices marcados na busca anterior apontariam
    // para outros videos agora.
    selected.clear();
    collectionTitle = title || "";
    box.innerHTML = "";
    if (sub) {
      let text = title
        ? title + " · " + results.length + (results.length === 1 ? " item" : " itens")
        : "Nada buscado ainda.";
      // Repetidos que o servidor tirou da lista: visível, para "sumiu um
      // vídeo" nunca parecer bug.
      if (removed > 0) text += " · " + removed + " repetido(s) oculto(s)";
      sub.textContent = text;
    }
    const selectBox = $("#select-box");
    if (selectBox) selectBox.hidden = results.length === 0;
    renderSelection();
    if (!results.length) {
      box.innerHTML = title
        ? '<div class="empty-state"><span class="empty-state-mark" aria-hidden="true">0</span>' +
          '<strong>Nenhum resultado encontrado</strong>' +
          '<span>Confira o endereço ou a sessão de cookies e tente buscar novamente.</span></div>'
        : '<div class="empty-state"><span class="empty-state-mark" aria-hidden="true">＋</span>' +
          '<strong>Sua biblioteca começa com uma busca</strong>' +
          '<span>Cole um link acima ou explore um perfil para revisar os vídeos disponíveis.</span></div>';
      return;
    }
    box.innerHTML = results.map(resultCard).join("");
    queueThumbs();
  }

  // Um listener no container, e nao um por card: a lista e substituida inteira
  // a cada busca, e os listeners dos cards antigos iriam embora junto —
  // religar um por card a cada render e a fonte classica de "o botao parou de
  // funcionar depois da segunda busca".
  const resultsBox = $("#resultados");
  if (resultsBox) {
    resultsBox.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-act='use']");
      if (!btn) return;
      const index = Number(btn.getAttribute("data-index"));
      pick(index);
      markPicked(index);
    });
    // ``change`` e nao ``click``: clicar no rotulo do checkbox dispara tambem o
    // clique do proprio input, entao um handler de clique leria o estado duas
    // vezes por toque. O ``change`` acontece uma vez, depois da alternancia.
    resultsBox.addEventListener("change", (event) => {
      const input = event.target.closest("input[data-act='check']");
      if (!input) return;
      toggleSelection(Number(input.getAttribute("data-index")), input.checked);
    });
  }

  // ---------- escolha ----------
  function pick(index) {
    picked = results[index] || null;
    renderPick();
    if (picked) toast("Selecionado: " + (picked.title || "").slice(0, 40), "ok");
  }

  function renderPick() {
    const box = $("#pick-body");
    if (!box) return;
    if (!picked) {
      box.innerHTML =
        '<p class="pick-empty">Escolha um item da lista. Ele vira o vídeo de ' +
        "origem dos cortes — transcrição, nota e render já existentes.</p>";
      return;
    }
    box.innerHTML = "";

    const eyebrow = document.createElement("p");
    eyebrow.className = "pick-eyebrow";
    eyebrow.textContent = "VÍDEO SELECIONADO";
    box.appendChild(eyebrow);

    const h = document.createElement("p");
    h.className = "pick-title";
    h.textContent = picked.title || "(sem título)";
    box.appendChild(h);

    const dl = document.createElement("dl");
    dl.className = "kv";
    const rows = [
      ["Duração", fmtDuration(picked.duration)],
      ["Canal", picked.uploader || "—"],
      ["Visualizações", picked.view_count != null ? fmtCount(picked.view_count) : "—"],
      ["ID", picked.id || "—"],
    ];
    rows.forEach(([k, v]) => {
      const item = document.createElement("div");
      item.className = "pick-meta-item";
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      item.appendChild(dt);
      item.appendChild(dd);
      dl.appendChild(item);
    });
    box.appendChild(dl);

    const actions = document.createElement("div");
    actions.className = "pick-actions";

    const cortes = document.createElement("button");
    cortes.type = "button";
    cortes.className = "btn pressable btn-primary";
    cortes.textContent = "Levar para o Estúdio";
    // A pagina do Estudio le ?url= do endereco: e o unico canal de hand-off
    // que existe hoje (nenhuma das paginas guarda estado entre navegacoes).
    cortes.addEventListener("click", () => {
      window.location.href = "/?url=" + encodeURIComponent(picked.url);
    });
    actions.appendChild(cortes);

    const secondary = document.createElement("div");
    secondary.className = "pick-secondary-actions";

    const open = document.createElement("a");
    open.className = "btn pressable";
    open.href = picked.url;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    open.textContent = "Abrir no site";
    secondary.appendChild(open);

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "btn pressable";
    copy.textContent = "Copiar link";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(picked.url);
        toast("Link copiado.", "ok");
      } catch (e) {
        toast("Não foi possível copiar.", "bad");
      }
    });
    secondary.appendChild(copy);

    actions.appendChild(secondary);
    box.appendChild(actions);
  }

  // ---------- busca ----------
  // Estado de busca: skeleton animado no lugar do "Buscando…" estático.
  // Mesma grade dos cards de resultado, para a lista não "pular" quando os
  // dados chegam; some por completo quando a resposta (ou o erro) pinta.
  function renderSearching() {
    const box = $("#resultados");
    if (!box) return;
    let cards = "";
    for (let n = 0; n < 4; n++) {
      cards += '<div class="skel-card" aria-hidden="true">' +
        '<div class="skel-thumb skel-shimmer"></div>' +
        '<div class="skel-main">' +
          '<span class="skel-line skel-shimmer w80"></span>' +
          '<span class="skel-line skel-shimmer w55"></span>' +
          '<span class="skel-badge skel-shimmer"></span>' +
        "</div></div>";
    }
    box.innerHTML = '<div class="search-loading" role="status" aria-label="Buscando vídeos">' +
      '<div class="search-loading-top">' +
        '<span class="search-loading-label">Buscando vídeos</span>' +
        '<span class="search-loading-track"><span></span></span>' +
      "</div>" + cards + "</div>";
  }

  async function search(event) {
    if (event) event.preventDefault();
    const input = $("#scrap-url");
    const url = (input && input.value || "").trim();
    if (!url) {
      toast("Informe o endereço.", "bad");
      if (input) input.focus();
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      toast("O endereço precisa começar com http:// ou https://", "bad");
      if (input) input.focus();
      return;
    }

    const btn = $("#btn-scrap");
    if (btn) { btn.disabled = true; btn.textContent = "Buscando…"; }
    renderSearching();

    const payload = { url: url, mode: mode };
    if (mode === "profile") {
      const limit = $("#scrap-limit");
      if (limit) payload.limit = Number(limit.value) || 20;
      // "Mais viralizados": o servidor lista até o teto e ordena por views
      // antes de cortar no limite — sem isso viriam os N primeiros do feed.
      if (viralOn) payload.viral = true;
    }
    // Cookies are offered in both modes: Instagram refuses an isolated reel as
    // readily as it refuses a profile, so gating this on "profile" left the
    // link mode with no way to authenticate at all.
    //
    // The file wins over the browser picker. On Chrome/Edge 127+ the browser
    // route is sealed with App-Bound Encryption and always fails, so a file the
    // user exported by hand is the only thing that can actually work there.
    const cookiesFile = $("#scrap-cookies-file");
    const cookies = $("#scrap-cookies");
    if (cookiesFile && cookiesFile.value.trim()) {
      payload.extra_ytdlp_args = ["--cookies", cookiesFile.value.trim()];
    } else if (cookies && cookies.value) {
      payload.extra_ytdlp_args = ["--cookies-from-browser", cookies.value];
    }
    // O sessionid viaja separado do argv: nao e flag do yt-dlp, e o servidor o
    // grava no jar antes de listar.
    const session = igSessionValue();
    if (session) payload.ig_session = session;

    const r = await post("/scrap", payload);

    if (btn) { btn.disabled = false; btn.textContent = "Buscar"; }

    if (r.error) {
      results = [];
      picked = null;
      renderResults("");
      const sub = $("#resultados-sub");
      if (sub) sub.textContent = "Falhou.";
      const box = $("#resultados");
      if (box) box.innerHTML = '<p class="empty"><strong>A busca falhou.</strong></p>';
      renderPick();
      toast(r.error.slice(0, 120), "bad");
      const errBox = document.createElement("p");
      errBox.className = "empty";
      errBox.textContent = r.error;
      if (box) box.appendChild(errBox);
      return;
    }

    results = Array.isArray(r.results) ? r.results : [];
    picked = null;
    searchSeq += 1;
    // A busca mudou: a fila de thumbnails da busca anterior nao vale mais e
    // precisa ser descartada, senao ela pintaria os cards novos com as imagens
    // do resultado antigo (os indices coincidem).
    thumbQueue = [];
    renderResults(r.title || "", Number(r.removed) || 0);
    renderPick();
    // O bloco de arquivamento é do Instagram e só aparece quando há uma
    // lista: oferecer "baixar tudo" sem nada na tela é um botão que não faz
    // nada — e fora do Instagram é um botão que só falharia.
    updateArchiveVisibility();
    toast(results.length + " item(ns) encontrado(s).", results.length ? "ok" : "bad");
  }

  // ---------- progresso do arquivamento ----------
  // O POST devolve só o aceite ({started:true}): o catálogo leva minutos e
  // uma resposta que só volta no fim não teria como mostrar progresso. Quem
  // mostra é este poll em /scrap/archive/progress, no mesmo ritmo do download.
  var archiveTimer = null;
  var archBusy = false;

  function setArchBusy(on) {
    archBusy = on;
    const btn = $("#btn-arquivar");
    if (btn) {
      btn.disabled = on;
      if (on) btn.textContent = "Baixando…";
    }
    const only = $("#arq-only");
    const max = $("#arq-max");
    const archViral = $("#btn-arq-viral");
    if (only) only.disabled = on;
    if (max) max.disabled = on;
    if (archViral) archViral.disabled = on;
  }

  function stopArchivePolling() {
    if (archiveTimer) { clearInterval(archiveTimer); archiveTimer = null; }
  }

  function followArchive() {
    stopArchivePolling();
    archiveTimer = setInterval(pollArchive, 700);
  }

  function phaseLabel(record) {
    if (record.state === "listando") return "Listando o perfil…";
    if (record.state === "baixando") {
      const total = Number(record.total) || 0;
      const index = Math.min(Number(record.index) || 0, total || 0);
      const parts = ["Baixando " + (total ? (index + " de " + total) : "…")];
      // A % de trabalho em andamento (bytes do item atual) é o que torna a
      // barra viva: sem ela o indicator para em "1 de 20" enquanto um reel
      // longo baixa. A linha do yt-dlp é o detalhe de baixo nível.
      const pct = Number(record.percent);
      if (pct > 0) parts.push(pct.toFixed(1) + "%");
      const code = record.title ? " · " + String(record.title).slice(0, 32) : "";
      return parts.join(" ") + code;
    }
    if (record.state === "concluido") return "Download concluído";
    if (record.state === "erro") return "Não foi possível baixar";
    return "Preparando…";
  }

  // Fração de trabalho executado, 0..1. O servidor é a fonte única: ele soma
  // os itens já fechados com o pedaço do item na mão (lido das linhas do
  // yt-dlp) e publica em `percent`. Sem isso a barra congelaria num reel longo
  // e andaria aos saltos de 1/total a cada item.
  function archiveFraction(record, total, index) {
    if (!total) return 0;
    const serverPct = Number(record.percent);
    if (Number.isFinite(serverPct) && serverPct > 0) {
      return Math.max(0, Math.min(1, serverPct / 100));
    }
    // Servidor antigo (sem percent): cai no cálculo por itens fechados.
    const closed = record.state === "baixando" ? Math.max(0, index - 1) : index;
    return Math.max(0, Math.min(1, closed / total));
  }

  function paintArchive(record) {
    const wrap = $("#archive-progress");
    const bar = $("#archive-bar");
    const fill = $("#archive-fill");
    const phase = $("#archive-phase");
    const percentEl = $("#archive-percent");
    const meta = $("#archive-meta");
    if (!wrap || !bar || !fill) return;
    if (record.state === "ocioso" || !record.state) { wrap.hidden = true; return; }
    wrap.hidden = false;

    const total = Number(record.total) || 0;
    const index = Number(record.index) || 0;
    // Fase de listagem não tem total ainda: barra indeterminada (CSS anima).
    // Na baixa, a fração é "itens fechados + o pedaço do item atual": o yt-dlp
    // imprime "12.3% of 45MiB" a cada linha, e sem isso a barra congelaria num
    // item longo até o próximo fechar (com 20 itens, saltos de 5%).
    const fraction = record.state === "listando"
      ? 0
      : archiveFraction(record, total, index);
    const pct = Math.round(fraction * 100);

    const visual = record.state === "erro" ? "erro"
      : record.state === "concluido" ? "concluido"
      : record.state === "listando" ? "listando" : "baixando";
    bar.setAttribute("data-state", visual);
    bar.setAttribute("aria-valuenow", String(record.state === "listando" ? 0 : pct));
    fill.style.width = record.state === "listando" ? "" : (pct + "%");

    if (phase) {
      const spinning = (record.state === "listando" || record.state === "baixando");
      phase.innerHTML = "";
      if (spinning) {
        const spin = document.createElement("span");
        spin.className = "arq-spin";
        spin.setAttribute("aria-hidden", "true");
        phase.appendChild(spin);
      }
      phase.appendChild(document.createTextNode(phaseLabel(record)));
    }
    if (percentEl) {
      percentEl.textContent = record.state === "listando" ? "…" : (pct + "%");
      percentEl.setAttribute("data-state", visual);
    }
    const stepList = $("#arq-step-list");
    const stepDl = $("#arq-step-dl");
    if (stepList) {
      const listing = record.state === "listando";
      const pastListing = ["baixando", "concluido", "erro"].includes(record.state);
      stepList.setAttribute("data-on", listing ? "1" : "0");
      stepList.setAttribute("data-done", pastListing ? "1" : "0");
    }
    if (stepDl) {
      const downloading = record.state === "baixando";
      stepDl.setAttribute("data-on", downloading ? "1" : "0");
      stepDl.setAttribute("data-done", record.state === "concluido" ? "1" : "0");
    }
    const logWrap = $("#archive-log");
    const logBody = $("#archive-log-body");
    const lines = Array.isArray(record.archive_lines) ? record.archive_lines : [];
    if (logWrap) {
      if (record.state === "ocioso" || !record.state) {
        logWrap.hidden = true;
      } else {
        logWrap.hidden = false;
        // Durante o trabalho o painel fica aberto: e a unica maneira de ver
        // o que o yt-dlp esta fazendo. Quando termina, recolhe pra dar lugar
        // ao resumo, mas a gente mantem os logs acessiveis.
        // Durante o download o painel abre sozinho (e a unica janela pro
        // yt-dlp). Se o usuario recolher, paramos de reabrir: a escolha dele
        // vence. Ao terminar, mantemos o estado atual em vez de esconder tudo.
        const finished = (record.state === "concluido" || record.state === "erro");
        if (!finished && !archiveLogUserToggled) setArchiveLogOpen(true);
        const wrapOpen = !finished || archiveLogOpen;
        showArchiveLog(wrapOpen);
      }
    }
    if (logBody) renderArchiveLog(lines);
    paintArchiveCards(record);

    if (meta) {
      if (record.state === "listando") {
        meta.textContent = "Lendo o catálogo do Instagram… isto leva alguns segundos.";
      } else if (record.state === "baixando") {
        const parts = [];
        if (total) parts.push(index + "/" + total);
        if (record.username) parts.push("@" + record.username);
        if (record.root) parts.push(record.root);
        meta.textContent = parts.join(" · ");
        // A última linha do yt-dlp (progresso em bytes, velocidade, etc.) é o
        // detalhe de andamento da requisição que o usuário quer ver.
        if (record.current_line) {
          const extra = document.createElement("span");
          extra.className = "arq-current";
          extra.style.marginLeft = "8px";
          extra.style.color = "var(--text-muted)";
          extra.style.fontFamily = "ui-monospace, monospace";
          extra.style.fontSize = "var(--text-2xs)";
          extra.textContent = " · " + String(record.current_line).slice(0, 60);
          meta.appendChild(extra);
        }
      } else if (record.state === "concluido") {
        meta.textContent = (record.downloaded + " baixado(s) · " + record.skipped +
          " já tinha · " + record.photos + " sem vídeo · " + record.failed +
          " falha(s)" + (record.root ? " · " + record.root : ""));
      } else if (record.state === "erro") {
        meta.textContent = record.error || "erro";
      }
    }
  }

  // Linhas do yt-dlp em tempo real: o painel de logs do arquivamento mostra
  // cada linha conforme chega (progresso em bytes, nomes de arquivo, erros).
  // Durante o download ele nasce aberto pra Competir com o resumo final; quando
  // o trabalho acaba ele se recolhe e deixa só a última linha na barra.
  var archiveLogOpen = true;
  var archiveLogUserToggled = false;

  function archiveLineClass(line) {
    const m = line.match(/^\s*\[?\s*(!|\*)\s*\]?\s*/);
    const low = line.toLowerCase();
    if (low.includes("falhou") || low.includes("error") || low.trim().startsWith("!")) return "ln-err";
    if (low.includes("ja existe") || low.includes("baixado") || low.includes("skip")) return "ln-ok";
    if (low.includes("aviso") || low.includes("warn")) return "ln-warn";
    if (line.trim().startsWith("[")) return "ln-step";
    return "ln-sys";
  }

  function renderArchiveLog(lines) {
    const body = $("#archive-log-body");
    const head = $("#archive-log-head");
    const count = $("#archive-log-count");
    if (!body) return;
    const list = Array.isArray(lines) ? lines : [];
    body.innerHTML = "";
    if (!list.length) {
      const empty = document.createElement("p");
      empty.className = "arq-log-empty";
      empty.textContent = "Nenhuma linha ainda.";
      body.appendChild(empty);
    } else {
      list.slice(-300).forEach((line) => {
        const div = document.createElement("div");
        div.className = "arq-log-line " + archiveLineClass(line);
        const prefix = line.match(/^\s*(\[[^\]]*\]|\*|!)\s*/);
        if (prefix) {
          const sp = document.createElement("span");
          sp.className = "ln-prefix";
          sp.textContent = prefix[0];
          div.appendChild(sp);
          div.appendChild(document.createTextNode(line.slice(prefix[0].length)));
        } else {
          div.appendChild(document.createTextNode(line));
        }
        body.appendChild(div);
      });
      body.scrollTop = body.scrollHeight;
    }
    if (count) count.textContent = list.length + (list.length === 1 ? " linha" : " linhas");
    if (head) head.setAttribute("aria-expanded", archiveLogOpen ? "true" : "false");
  }

  // Recolher o painel so esconde o corpo; o cabecalho continua visivel e
  // clicavel, entao o log nunca fica inacessivel depois do download acabar.
  function toggleArchiveLog() {
    archiveLogUserToggled = true;
    setArchiveLogOpen(!archiveLogOpen);
  }

  function setArchiveLogOpen(open) {
    archiveLogOpen = !!open;
    const body = $("#archive-log-body");
    const head = $("#archive-log-head");
    if (body) body.hidden = !archiveLogOpen;
    if (head) head.setAttribute("aria-expanded", archiveLogOpen ? "true" : "false");
  }

  // show=false recolhe o corpo, nao esconde o painel inteiro.
  function showArchiveLog(open) {
    const wrap = $("#archive-log");
    if (!wrap) return;
    wrap.hidden = false;
    setArchiveLogOpen(open);
  }

  // Cards dos itens do arquivamento: grade de retratos com capa, shortcode
  // e estado de cada video. Reconstrói só quando a lista troca (novo run);
  // a cada poll só atualiza os estados — recriar 200 <img> por poll
  // recarregaria as capas e quebraria a rolagem.
  var archiveCardsSig = "";
  var archiveCardsIndex = -1;
  var archiveThumbSeq = 0;

  function archiveCardStatus(state, isCurrent) {
    if (isCurrent) return ["doing", "Baixando"];
    if (state === "done") return ["done", "Baixado"];
    if (state === "skipped") return ["skipped", "Já tinha"];
    if (state === "photo") return ["photo", "Sem vídeo"];
    if (state === "failed") return ["failed", "Falha"];
    return ["waiting", "Na fila"];
  }

  function paintArchiveCards(record) {
    const box = $("#archive-cards");
    if (!box) return;
    const items = Array.isArray(record.items) ? record.items : [];
    const states = Array.isArray(record.item_states) ? record.item_states : [];
    if (!items.length || !record.state || record.state === "ocioso" || record.state === "listando") {
      box.hidden = true;
      archiveCardsSig = "";
      archiveCardsIndex = -1;
      return;
    }
    const sig = (record.username || "") + "|" + items.length + "|" +
      (items[0] && items[0].code ? items[0].code : "");
    if (sig !== archiveCardsSig) {
      archiveCardsSig = sig;
      archiveCardsIndex = -1;
      archiveThumbSeq += 1;
      box.innerHTML = "";
      items.forEach((item, n) => {
        const card = document.createElement("article");
        card.className = "arq-card";
        card.setAttribute("data-status", "waiting");
        const thumb = document.createElement("div");
        thumb.className = "arq-card-thumb";
        thumb.textContent = "▶";
        if (item.thumb) {
          const img = document.createElement("img");
          img.src = "/api/scrap/archive/thumb?i=" + n + "&s=" + archiveThumbSeq;
          img.alt = "";
          img.loading = "lazy";
          img.decoding = "async";
          img.addEventListener("error", () => thumb.setAttribute("data-empty", "1"), { once: true });
          thumb.appendChild(img);
        } else {
          thumb.setAttribute("data-empty", "1");
        }
        const kind = document.createElement("span");
        kind.className = "arq-card-kind";
        kind.textContent = item.folder === "reels" ? "reel" : "post";
        thumb.appendChild(kind);
        const foot = document.createElement("div");
        foot.className = "arq-card-foot";
        const code = document.createElement("div");
        code.className = "arq-card-code";
        code.title = item.code || "";
        code.textContent = item.code || ("#" + (n + 1));
        const st = document.createElement("div");
        st.className = "arq-card-st";
        st.textContent = "Na fila";
        foot.appendChild(code);
        foot.appendChild(st);
        card.appendChild(thumb);
        card.appendChild(foot);
        box.appendChild(card);
      });
      box.hidden = false;
    }
    const cards = box.children;
    const current = record.active ? (Number(record.index) || 0) : 0;
    for (let n = 0; n < cards.length; n++) {
      const isCurrent = record.active && (n + 1) === current &&
        (states[n] === "item" || !states[n]);
      const pair = archiveCardStatus(states[n] || "", isCurrent);
      cards[n].setAttribute("data-status", pair[0]);
      const label = cards[n].querySelector(".arq-card-st");
      if (label && label.textContent !== pair[1]) label.textContent = pair[1];
    }
    if (record.active && current !== archiveCardsIndex &&
        current >= 1 && current <= cards.length) {
      archiveCardsIndex = current;
      const el = cards[current - 1];
      if (el && typeof el.scrollIntoView === "function") {
        el.scrollIntoView({ block: "nearest" });
      }
    }
  }

  async function pollArchive() {
    const record = await get("/scrap/archive/progress");
    if (record.error) {
      stopArchivePolling();
      setArchBusy(false);
      restoreArchiveButton();
      toast(String(record.error).slice(0, 120), "bad");
      return;
    }
    paintArchive(record);
    if (record.active) return;

    stopArchivePolling();
    setArchBusy(false);
    restoreArchiveButton();
    const sub = $("#resultados-sub");
    if (record.state === "erro") {
      if (sub) sub.textContent = "O download do perfil falhou.";
      renderArchive(null, record.error || "erro");
      toast(String(record.error || "falhou").slice(0, 120), "bad");
      return;
    }
    if (sub && record.username) {
      sub.textContent = "@" + record.username + ": " + record.downloaded +
        " baixado(s), " + record.skipped + " já tinha, " + record.photos +
        " sem vídeo, " + record.failed + " falha(s)";
    }
    renderArchive({
      username: record.username,
      lines: record.lines,
      errors: record.errors,
    }, "");
    toast(record.downloaded + " vídeo(s) baixado(s) em " + (record.root || ""), "ok");
  }

  async function resumeArchive() {
    const record = await get("/scrap/archive/progress");
    if (record.error || !record.active) return;
    setArchBusy(true);
    lockArchiveButton();
    paintArchive(record);
    followArchive();
  }

  function lockArchiveButton() {
    const btn = $("#btn-arquivar");
    if (btn) { btn.disabled = true; btn.textContent = "Baixando…"; }
  }

  function restoreArchiveButton() {
    const btn = $("#btn-arquivar");
    if (btn && !archBusy) {
      btn.disabled = false;
      btn.textContent = "Baixar para o computador";
    }
  }

  // ---------- arquivar o perfil ----------
  // Escolhe o cookies.txt do formulario do mesmo jeito que a busca, para nao
  // existirem duas verdades sobre qual sessao esta a ser usada. Diferente da
  // busca, um seletor de navegador NAO serve aqui: a rota do Instagram exige o
  // `sessionid` num header, e o yt-dlp nao intermediaria essa chamada.
  async function archive(event) {
    if (event) event.preventDefault();
    if (archBusy) return;
    const url = ($("#scrap-url") && $("#scrap-url").value || "").trim();
    if (!url) {
      toast("Faça uma busca de perfil primeiro.", "bad");
      return;
    }
    const cookiesFile = $("#scrap-cookies-file");
    const cookiesPath = cookiesFile ? cookiesFile.value.trim() : "";
    if (!cookiesPath) {
      toast("Informe o arquivo cookies.txt — o Instagram exige sessão.", "bad");
      if (cookiesFile) cookiesFile.focus();
      return;
    }

    const only = $("#arq-only");
    const max = $("#arq-max");
    const payload = {
      profile: url,
      cookies_file: cookiesPath,
      ig_session: igSessionValue(),
      only: only ? only.value : "",
      max_items: max ? Number(max.value) || 0 : 0,
      // "Mais viralizados": ordena por plays/likes antes do corte por tipo.
      order: arqViralOn ? "viral" : "recent",
    };

    const sub = $("#resultados-sub");
    if (sub) sub.textContent = "Baixando o perfil… (isto pode levar alguns minutos)";
    toast("Baixando… não feche a página.", "ok");

    const r = await post("/scrap/archive", payload);

    if (r.error) {
      // 409 = já existe um arquivamento rodando: volta a acompanhar em vez de
      // empilhar outro em cima.
      if (r.code === 409) {
        setArchBusy(true);
        lockArchiveButton();
        await resumeArchive();
        return;
      }
      if (sub) sub.textContent = "O download do perfil falhou.";
      renderArchive(null, String(r.error));
      toast(String(r.error).slice(0, 120), "bad");
      return;
    }

    // Resposta legada (servidor antigo, síncrono): já traz o resultado pronto.
    if (r.username !== undefined && r.started === undefined) {
      if (sub) {
        sub.textContent = "@" + r.username + ": " + r.downloaded + " baixado(s), " +
                          r.skipped + " já tinha, " + r.photos + " sem vídeo, " +
                          r.failed + " falha(s)";
      }
      paintArchive({
        active: false, state: "concluido", phase: "done",
        total: r.downloaded + r.skipped + r.failed + (r.photos || 0),
        index: r.downloaded + r.skipped + r.failed + (r.photos || 0),
        downloaded: r.downloaded, skipped: r.skipped, failed: r.failed,
        photos: r.photos || 0, username: r.username, root: r.root || "",
      });
      renderArchive(r, "");
      toast(r.downloaded + " vídeo(s) baixado(s) em " + r.root, "ok");
      return;
    }

    setArchBusy(true);
    lockArchiveButton();
    paintArchive({
      active: true, state: "listando", phase: "listando",
      index: 0, total: 0, title: payload.profile,
    });
    followArchive();
  }

  // Painel do resultado do arquivamento. Substitui os cards: depois de baixar,
  // a lista de resultados deixa de ser a informacao mais util da tela.
  function renderArchive(report, error) {
    const box = $("#resultados");
    if (!box) return;
    box.innerHTML = "";

    const wrap = document.createElement("div");
    wrap.style.cssText =
      "padding:14px;border-radius:var(--radius-md);background:var(--well-bg);" +
      "border:1px solid var(--border-subtle);";

    const title = document.createElement("p");
    title.style.cssText = "margin:0 0 10px;font-weight:600;";
    title.textContent = error ? "Não foi possível baixar" : "Download concluído";
    wrap.appendChild(title);

    if (error) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = error;
      wrap.appendChild(p);
      box.appendChild(wrap);
      return;
    }

    const list = document.createElement("ul");
    list.style.cssText = "margin:0;padding-left:18px;";
    (report.lines || []).forEach((line) => {
      const li = document.createElement("li");
      li.textContent = line.trim();
      list.appendChild(li);
    });
    wrap.appendChild(list);

    if (report.errors && report.errors.length) {
      const head = document.createElement("p");
      head.style.cssText = "margin:12px 0 6px;font-weight:600;";
      head.textContent = "Itens que falharam";
      wrap.appendChild(head);
      const errs = document.createElement("ul");
      errs.style.cssText = "margin:0;padding-left:18px;";
      report.errors.slice(0, 20).forEach((entry) => {
        const li = document.createElement("li");
        li.textContent = entry.item + ": " + entry.error;
        errs.appendChild(li);
      });
      wrap.appendChild(errs);
      const tip = document.createElement("p");
      tip.className = "field-hint";
      tip.style.marginTop = "10px";
      tip.textContent =
        "Repetir o download tenta só o que falhou — o que já está em disco é pulado.";
      wrap.appendChild(tip);
    }

    box.appendChild(wrap);
  }

  // ---------- fio de ligacao com a pagina do Estudio ----------
  // Nada aqui le ?url=; quem consome e o index.html. O botao do header so
  // leva para la, porque a unica acao util desta pagina e ir para o Estudio.
  const go = $("#btn-open-cortes");
  if (go) {
    go.addEventListener("click", () => {
      window.location.href = picked
        ? "/?url=" + encodeURIComponent(picked.url)
        : "/";
    });
  }

  // ---------- sessionid: o cookie que o export nao traz ----------
  // O valor e HttpOnly, entao nenhum export que leia `document.cookie` o
  // enxerga — e sem ele o Instagram responde como se ninguem estivesse logado.
  // Vai no payload e o servidor grava no cookies.txt: a busca e o download
  // passam a usar a MESMA sessao, e por isso o campo se preenche uma vez so.
  function igSessionValue() {
    const field = $("#scrap-ig-session");
    return field ? field.value.trim() : "";
  }

  // ---------- sessão / cookies (sub-card do Buscar) ----------
  // O select de cookies continua sendo a fonte da verdade do
  // payload — o segmentado só espelha o valor dele. O arquivo, quando
  // preenchido, vence o navegador (mesma regra do search()).
  function paintAuthStatus() {
    const badge = $("#auth-status");
    const sel = $("#scrap-cookies");
    const file = $("#scrap-cookies-file");
    if (!badge) return;
    const fileVal = file ? file.value.trim() : "";
    const selVal = sel ? sel.value : "";
    // O selo responde a pergunta que importa — "da para autenticar?" —, e o
    // sessionid colado e metade dela: sem o jar nao ha `csrftoken` para ecoar no
    // `X-CSRFToken`, e o Instagram devolve a casca HTML em vez do JSON.
    const suffix = igSessionValue() ? " · sessão" : "";
    if (fileVal) {
      badge.dataset.state = "file";
      const base = fileVal.split(/[\\/]/).pop() || fileVal;
      badge.textContent = "Arquivo · " + base.slice(0, 28) + suffix;
    } else if (selVal) {
      badge.dataset.state = "browser";
      badge.textContent = "Navegador · " + selVal + suffix;
    } else if (suffix) {
      badge.dataset.state = "session";
      badge.textContent = "sessionid sem arquivo";
    } else {
      badge.dataset.state = "none";
      badge.textContent = "Sem sessão";
    }
  }

  function paintSeg() {
    const sel = $("#scrap-cookies");
    const seg = $("#cookies-seg");
    if (!sel || !seg) return;
    const btns = Array.from(seg.querySelectorAll(".seg-btn"));
    btns.forEach((btn) => {
      const on = btn.getAttribute("data-value") === sel.value;
      btn.setAttribute("aria-checked", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
    });
  }

  function setBrowser(value) {
    const sel = $("#scrap-cookies");
    if (!sel) return;
    sel.value = value;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    paintSeg();
    paintAuthStatus();
  }

  (function initAuthCard() {
    const seg = $("#cookies-seg");
    const sel = $("#scrap-cookies");
    const file = $("#scrap-cookies-file");
    const browse = $("#btn-cookies-browse");
    const clear = $("#btn-cookies-clear");
    const picker = $("#cookies-file-picker");
    if (!seg || !sel) return;
    seg.addEventListener("click", (event) => {
      const btn = event.target.closest(".seg-btn");
      if (btn) setBrowser(btn.getAttribute("data-value") || "");
    });
    // Roving tabindex: setas movem entre as pílulas como num radiogroup.
    seg.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      const btns = Array.from(seg.querySelectorAll(".seg-btn"));
      let i = btns.indexOf(document.activeElement);
      if (i < 0) i = 0;
      i = (i + (event.key === "ArrowRight" ? 1 : -1) + btns.length) % btns.length;
      btns[i].focus();
      setBrowser(btns[i].getAttribute("data-value") || "");
    });
    if (file) {
      // O valor nao ve mais hardcoded no HTML: aquele default era o caminho da
      // maquina de quem commitou (vazava o usuario em print/snapshot e nao
      // existia em outra maquina). O caminho vale por navegador, entao ele
      // e daqui: restaurado ao abrir, gravado a cada edicao. O sessionid NAO
      // passa por aqui de proposito — e segredo, fica so na memoria.
      try {
        const saved = window.localStorage.getItem("vc-cookies-file");
        if (saved && !file.value) file.value = saved;
        file.addEventListener("input", () => {
          try { window.localStorage.setItem("vc-cookies-file", file.value); } catch (_) {}
        });
      } catch (_) { /* storage bloqueado: segue sem memoria, sem quebrar */ }
      file.addEventListener("input", paintAuthStatus);
      file.addEventListener("change", paintAuthStatus);
    }
    if (browse && picker && file) {
      browse.addEventListener("click", () => picker.click());
      picker.addEventListener("change", () => {
        const picked = picker.files && picker.files[0];
        if (!picked) return;
        // O backend recebe um caminho em disco; o picker só revela o nome,
        // então preenchemos o nome e avisamos — o usuário ajusta a pasta.
        file.value = picked.name;
        file.dispatchEvent(new Event("input", { bubbles: true }));
        toast("Arquivo: " + picked.name + " — confira a pasta.", "ok");
        file.focus();
      });
    }
    if (clear && file) {
      clear.addEventListener("click", () => {
        file.value = "";
        file.dispatchEvent(new Event("input", { bubbles: true }));
        file.focus();
      });
    }
    const session = $("#scrap-ig-session");
    const reveal = $("#btn-session-reveal");
    const sessionClear = $("#btn-session-clear");
    if (session) {
      session.addEventListener("input", paintAuthStatus);
      session.addEventListener("change", paintAuthStatus);
    }
    if (reveal && session) {
      // `type` e o unico jeito de revelar um campo de senha — e o valor e longo
      // e cheio de `%3A`, entao conferir o que foi colado importa.
      reveal.addEventListener("click", () => {
        const shown = session.type === "text";
        session.type = shown ? "password" : "text";
        reveal.setAttribute("aria-pressed", shown ? "false" : "true");
        reveal.setAttribute("aria-label", shown ? "Mostrar o valor" : "Ocultar o valor");
        session.focus();
      });
    }
    if (sessionClear && session) {
      sessionClear.addEventListener("click", () => {
        session.value = "";
        session.dispatchEvent(new Event("input", { bubbles: true }));
        session.focus();
      });
    }
    paintSeg();
    paintAuthStatus();
  })();

  const form = $("#scrap-form");
  if (form) form.addEventListener("submit", search);

  const arq = $("#btn-arquivar");
  if (arq) arq.addEventListener("click", archive);

  const viralBtn = $("#btn-viral");
  if (viralBtn) viralBtn.addEventListener("click", () => {
    viralOn = !viralOn;
    renderViralButtons();
  });

  const arqViralBtn = $("#btn-arq-viral");
  if (arqViralBtn) arqViralBtn.addEventListener("click", () => {
    arqViralOn = !arqViralOn;
    renderViralButtons();
  });

  const logHead = $("#archive-log-head");
  if (logHead) {
    logHead.addEventListener("click", toggleArchiveLog);
    logHead.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleArchiveLog(); }
    });
  }

  const selectAll = $("#btn-select-all");
  if (selectAll) selectAll.addEventListener("click", toggleAll);

  const download = $("#btn-download");
  if (download) download.addEventListener("click", downloadSelected);

  renderMode();
  renderPlatform();
  renderViralButtons();
  renderResults("");
  renderPick();
  resumeDownload();
  resumeArchive();
})();