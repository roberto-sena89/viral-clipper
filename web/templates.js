(function () {
  "use strict";

  // ---------- rail lateral (menu principal) ----------
  // A lista de destinos vive no HTML: uma vez no rail fixo, e uma copia no
  // menu do header que aparece abaixo de 920px. Aqui so marcamos qual e a
  // pagina atual — derivado de location.pathname, nunca escrito a mao, para
  // que adicionar uma pagina nova nao exija mexer em estado.
  const PAGES = {
    "/":          "Cortes",
    "/templates": "Templates",
    "/scrap":     "Scrap",
  };

  (function initRail() {
    // Nao usa os helpers $ / $$ do wizard: eles sao declarados mais abaixo
    // neste arquivo, e `var` sofre hoisting como undefined — a chamada
    // estouraria aqui. O rail busca o que precisa direto.
    const q = function (sel) { return document.querySelector(sel); };

    const path = window.location.pathname.replace(/\/index\.html$/, "/");
    const norm = path.endsWith("/") ? path : path + "/";
    const key = PAGES[path] ? path : (PAGES[norm] ? norm : "/");
    const title = PAGES[key];

    document.title = `${title} · Viral Clipper`;

    // Cada instancia da lista (rail + menu do header) marca o seu proprio item.
    document.querySelectorAll("[data-rail-page]").forEach((item) => {
      if (item.getAttribute("data-rail-page") === key) {
        item.setAttribute("aria-current", "page");
      }
    });

    // O menu do header e o unico componente com estado aqui: abre e fecha.
    const menu = q("[data-rail-menu]");
    const btn = q("[data-rail-picker] .menu-btn");
    if (!menu || !btn) return;

    const setOpen = (open) => {
      menu.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    };

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = menu.hidden;
      setOpen(willOpen);
      if (willOpen) {
        const first = menu.querySelector(".rail-item");
        if (first) first.focus({ preventScroll: true });
      }
    });

    // O painel fica sobre o conteudo; sem isto um clique nele fecharia o menu
    // E dispararia a acao do elemento por baixo.
    menu.addEventListener("click", (e) => {
      if (e.target.closest(".rail-item")) setOpen(false);
    });

    document.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        const wasOpen = !menu.hidden;
        setOpen(false);
        // Devolve o foco ao botao, senao ele some para o body e o usuario de
        // teclado perde a posicao.
        if (wasOpen) btn.focus({ preventScroll: true });
      }
    });
  })();


  // ---------- catalogo ----------
  // Espelha viralclipper/caption_presets.py. Manter em sincronia e responsabilidade
  // do teste tests/test_web_server.py, que compara as chaves.
  var PRESETS = {
    "karaoke":      { desc: "Karaoke classico — branco com destaque amarelo", font: "Arial Black", size: 84,  color: "#ffffff", accent: "#ffff00", box: null,      margin: 640 },
    "block-dark":   { desc: "Bloco escuro, texto branco",                      font: "Arial Black", size: 80,  color: "#ffffff", accent: "#ffe066", box: "#141414", margin: 620 },
    "blood":        { desc: "Vermelho sangue, impacto maximo",                 font: "Impact",      size: 92,  color: "#ff2b2b", accent: "#ffffff", box: null,      margin: 650 },
    "bold-box":     { desc: "Texto branco sobre caixa escura",                 font: "Arial Black", size: 80,  color: "#ffffff", accent: "#ffd400", box: "#1a1a1a", margin: 620 },
    "bubble":       { desc: "Caixa clara arredondada",                         font: "Arial Black", size: 78,  color: "#101010", accent: "#7c3aed", box: "#f2f2f2", margin: 600 },
    "candy":        { desc: "Caixa clara com texto escuro",                    font: "Arial Black", size: 78,  color: "#141414", accent: "#ff4fae", box: "#f4f4f4", margin: 600 },
    "cobalt":       { desc: "Azul cobalto, tom corporativo",                   font: "Arial Black", size: 82,  color: "#ffffff", accent: "#4da3ff", box: "#0b2a5b", margin: 630 },
    "cyan-pop":     { desc: "Ciano neon sobre escuro",                         font: "Arial Black", size: 84,  color: "#00f0ff", accent: "#ffffff", box: null,      margin: 640 },
    "fire":         { desc: "Laranja fogo, energia alta",                      font: "Impact",      size: 90,  color: "#ff8a00", accent: "#ffd400", box: null,      margin: 645 },
    "gold-box":     { desc: "Caixa dourada, ar premium",                       font: "Arial Black", size: 78,  color: "#1c1c1c", accent: "#8a6a00", box: "#ffd700", margin: 610 },
    "ice-blue":     { desc: "Azul gelo, tom calmo",                            font: "Arial Black", size: 82,  color: "#dff3ff", accent: "#7fd4ff", box: null,      margin: 635 },
    "lime-hit":     { desc: "Verde limao, alto contraste",                     font: "Arial Black", size: 86,  color: "#c6ff00", accent: "#ffffff", box: null,      margin: 640 },
    "magenta-pop":  { desc: "Magenta vibrante",                                font: "Arial Black", size: 84,  color: "#ff4fae", accent: "#ffffff", box: null,      margin: 640 },
    "minimal":      { desc: "Minimalista, caixa discreta",                     font: "Helvetica",   size: 66,  color: "#ffffff", accent: "#ffffff", box: "#000000", margin: 580 },
    "mono":         { desc: "Monoespacado, tom tecnico",                       font: "Courier New", size: 70,  color: "#ffffff", accent: "#00ff9d", box: "#0d0d0d", margin: 600 },
    "neon":         { desc: "Verde neon, glow de rua",                         font: "Arial Black", size: 84,  color: "#39ff14", accent: "#ffffff", box: null,      margin: 640 },
    "pop-box":      { desc: "Caixa magenta, pop maximo",                       font: "Arial Black", size: 80,  color: "#ffffff", accent: "#ffd400", box: "#ff0090", margin: 620 },
    "slim":         { desc: "Magra e discreta",                                font: "Helvetica",   size: 62,  color: "#ffffff", accent: "#ffd400", box: null,      margin: 560 },
    "social":       { desc: "Social — padrão TikTok/Reels, destaque amarelo",   font: "Arial Black", size: 88,  color: "#ffffff", accent: "#ffff00", box: null,      margin: 560 },
    "sunset":       { desc: "Por do sol, quente e suave",                      font: "Arial Black", size: 82,  color: "#ffd9a0", accent: "#ff5e62", box: null,      margin: 635 },
    "ultra-impact": { desc: "Impact gigante, duas palavras",                   font: "Impact",      size: 108, color: "#ffffff", accent: "#ff2b2b", box: null,      margin: 660 },
    "violet-vibe":  { desc: "Violeta moderno",                                 font: "Arial Black", size: 84,  color: "#c9b6ff", accent: "#ffffff", box: null,      margin: 640 },

    // --- familia por FONTE (espelho dos presets de fonte do backend) ---
    // Mesmo formato dos acima; o que muda de um para o outro e a familia
    // tipografica. As cores vem do &HAABBGGRR do ASS, ja convertidas.
    "roboto-bold":        { desc: "Roboto — padrão dos shorts e do YouTube, destaque ciano", font: "Roboto", size: 80, color: "#ffffff", accent: "#ffe500", box: null, margin: 640 },
    "inter-bold":         { desc: "Inter — legibilidade máxima em tela pequena, destaque lima", font: "Inter", size: 78, color: "#ffffff", accent: "#00ffcc", box: null, margin: 640 },
    "poppins-bold":       { desc: "Poppins — geométrica e redonda, destaque magenta", font: "Poppins", size: 80, color: "#ffffff", accent: "#cc00ff", box: null, margin: 640 },
    "montserrat-bold":    { desc: "Montserrat — estilo TikTok, destaque dourado", font: "Montserrat", size: 80, color: "#ffffff", accent: "#00d7ff", box: null, margin: 640 },
    "dm-sans":            { desc: "DM Sans — minimalismo suíço, destaque laranja", font: "DM Sans", size: 76, color: "#ffffff", accent: "#0055ff", box: null, margin: 640 },
    "cabin-bold":         { desc: "Cabin — aberta e amigável, destaque ciano claro", font: "Cabin", size: 78, color: "#ffffff", accent: "#ffe500", box: null, margin: 640 },
    "verdana-bold":       { desc: "Verdana — x-height gigante, destaque amarelo", font: "Verdana", size: 74, color: "#ffffff", accent: "#00ffff", box: null, margin: 640 },
    "trebuchet-bold":     { desc: "Trebuchet MS — humanista e limpa, destaque vermelho", font: "Trebuchet MS", size: 78, color: "#ffffff", accent: "#2d2dff", box: null, margin: 640 },
    "tahoma-bold":        { desc: "Tahoma — estreita e legível, destaque ciano", font: "Tahoma", size: 78, color: "#ffffff", accent: "#ffe500", box: null, margin: 640 },
    "calibri-bold":       { desc: "Calibri — clara e moderna, destaque lima", font: "Calibri", size: 78, color: "#ffffff", accent: "#00ffcc", box: null, margin: 640 },
    "franklin-bold":      { desc: "Franklin Gothic — condensada clássica, destaque fogo", font: "Franklin Gothic Medium", size: 80, color: "#ffffff", accent: "#0055ff", box: null, margin: 640 },
    "segoe-black":        { desc: "Segoe UI — moderna nativa, destaque violeta", font: "Segoe UI", size: 80, color: "#ffffff", accent: "#ff6bb2", box: null, margin: 640 },
    "helvetica-classic":  { desc: "Helvetica — o amarelo clássico do cinema", font: "Helvetica", size: 80, color: "#ffffff", accent: "#00ffff", box: null, margin: 640 },
    "merriweather-black": { desc: "Merriweather — serifada editorial para ritmo lento", font: "Merriweather", size: 78, color: "#ffffff", accent: "#00d7ff", box: null, margin: 640 },
    "arvo-bold":          { desc: "Arvo — serifada de telão para entrevistas", font: "Arvo", size: 78, color: "#ffffff", accent: "#00ffff", box: null, margin: 640 }
  };

  // Onde o motor queima o headline: o estilo ASS de `render.py` fixa
  // `MarginV = 60` com alinhamento 7/8/9 (linha de CIMA do teclado numerico),
  // entao 60 px medidos do TOPO de um frame de 1920 de altura. E px do frame,
  // nao do palco: para virar posicao na previa tem de ser fracao da ALTURA
  // (cqh). Com cqw o texto subia so 3% da LARGURA — 32 px de 1080, metade do
  // que o motor usa — e a decoracao pousava sob o notch.
  var HEADLINE_TOP_PX = 60;

  // A area segura do motor comeca a 4% da altura do frame — o mesmo numero que o
  // guia (`.stage-guides .safe { top: 4% }`) desenha. Mas ela fica ACIMA do fundo
  // do recorte do aparelho, que pinta por cima do conteudo. Os dois numeros sao
  // reais e conflitam, entao quem manda nas decoracoes de formato (o POV do meme)
  // e o recorte: o headline mostra a posicao do motor, o POV e ilustracao do
  // modelo e precisa APARECER. O recuo vem do CSS (`--notch-top` + `--notch-h`),
  // nao daqui — em px de tela, sem conversao para fracao de zona.

  var KINDS = {
    video:    { label: "Video",    color: "#6366f1" },
    frame:    { label: "Frame do video", color: "#fbbf24" },
    image:    { label: "Imagem",   color: "#34d399" },
    solid:    { label: "Cor solida", color: "#f87171" },
    captions: { label: "Legenda",  color: "#ff4fae" }
  };

  // Galeria "Escolha um template": formatos prontos com zonas que somam 100%
  // (só kinds do motor) + preset de legenda. "Usar" carrega tudo e leva ao
  // Passo 1; as etapas seguintes personalizam.
  var GALLERY = {
    x: {
      label: "Twitter / X", name: "x-reacao", preset: "bold-box", layout: "",
      desc: "Print do post em cima, vídeo reagindo embaixo — formato reação.",
      zones: [
        { kind: "image", mock: "tweet", fraction: 0.34, fit: "contain", frameAt: 0, source: "",
          marginTop: 1.2, marginBottom: 1.2, marginLeft: 3, marginRight: 3, radius: 3.5, color: "black" },
        { kind: "video", fraction: 0.66, fit: "cover", frameAt: 0, source: "",
          marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0, radius: 0, color: "black" }
      ]
    },
    meme: {
      label: "Meme", name: "meme-pov", preset: "ultra-impact", layout: "", mock: "meme",
      desc: "Texto POV gigante sobre o vídeo + barra de identidade embaixo.",
      zones: [
        { kind: "video", fraction: 0.74, fit: "cover", frameAt: 0, source: "",
          marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0, radius: 0, color: "black" },
        { kind: "image", fraction: 0.26, fit: "cover", frameAt: 0, source: "",
          marginTop: 1.2, marginBottom: 1.2, marginLeft: 3, marginRight: 3, radius: 3.5, color: "black" }
      ]
    },
    viral: {
      label: "Vídeo Viral", name: "video-viral", preset: "fire", layout: "", mock: "viral",
      desc: "Vídeo em destaque + faixa de imagem com o gancho — energia alta.",
      hook: "Isso aqui vai viralizar e você ainda não sabe por quê",
      zones: [
        { kind: "video", fraction: 0.58, fit: "cover", frameAt: 0, source: "",
          marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0, radius: 0, color: "black" },
        { kind: "image", fraction: 0.42, fit: "cover", frameAt: 0, source: "",
          marginTop: 1.2, marginBottom: 1.2, marginLeft: 3, marginRight: 3, radius: 3.5, color: "black" }
      ]
    }
  };

  var STEPS = [
    { key: "aparencia",  label: "Aparencia" },
    { key: "zonas",      label: "Zonas" },
    { key: "frases",     label: "Frases" },
    { key: "midia",      label: "Midia" },
    { key: "estilo",     label: "Estilo" },
    { key: "variacoes",  label: "Variacoes" },
    { key: "gerar",      label: "Gerar" }
  ];

  // ---------- estado ----------
  var state = {
    step: 0,
    name: "meu-canal",
    width: 1080,
    height: 1920,
    preset: "social",
    background: "black",
    layout: "",
    headlineOn: false,
    headlineSeconds: 3,
    headlineText: "",
    headlineAlign: "center",
    headlineSize: 100,
    headlineMargin: 60,
    captionTheme: "",
    reframeZoom: 1,
    reframePanX: 0.5,
    reframePanY: 0.5,
    // Texto do tweet no formato X (vazio = texto de exemplo do mock).
    tweetText: "",
    // Vídeos de referência só para a prévia (object URLs locais).
    previewVideos: [],
    // Biblioteca de frases do projeto (Passo 3).
    phrases: [],
    // Qual mock da galeria a prévia espelha ("x", "meme", "viral" ou "").
    // Qualquer edição estrutural limpa e volta ao esquema abstrato.
    previewMock: "",
    progressOn: false,
    progressHeight: 10,
    progressColor: "yellow",
    variantPresets: [],
    variantLayouts: [],
    zones: [
      { kind: "video", fraction: 0.62, fit: "cover", frameAt: 0, source: "",
        marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0, radius: 3.5, color: "black" },
      { kind: "frame", fraction: 0.38, fit: "cover", frameAt: 0, source: "",
        marginTop: 1.2, marginBottom: 1.2, marginLeft: 3, marginRight: 3, radius: 3.5, color: "black" }
    ],
    hasCaptions: true
  };

  var $ = function (sel) { return document.querySelector(sel); };
  var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };

  function esc(text) {
    return String(text).replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
    });
  }

  function toast(message, kind) {
    var node = document.createElement("div");
    node.className = "toast " + (kind || "");
    node.textContent = message;
    $("#toast-zone").appendChild(node);
    setTimeout(function () { node.remove(); }, 2600);
  }

  // ---------- geometria (espelha plan_bands do motor) ----------
  // A previa tem que concordar com o render, entao isto e uma traducao das
  // mesmas regras: fracao -> pixel, sobras absorvidas na ultima faixa, e
  // margin_* como fracao do CANVAS (nao da faixa).
  function planBands() {
    var H = state.height, W = state.width;
    var pixel = state.zones.filter(function (z) { return z.kind !== "captions"; });
    var total = pixel.reduce(function (sum, z) { return sum + z.fraction; }, 0);
    var bands = [];
    var cursor = 0;

    state.zones.forEach(function (zone) {
      if (zone.kind === "captions") {
        bands.push({ zone: zone, kind: "captions", y: 0, height: H, innerX: 0, innerY: 0,
                     innerW: W, innerH: H, share: 0 });
        return;
      }
      var bandH = Math.round(H * zone.fraction);
      var innerX = Math.round(W * (zone.marginLeft / 100));
      var innerY = cursor + Math.round(H * (zone.marginTop / 100));
      var innerW = Math.max(2, W - innerX - Math.round(W * (zone.marginRight / 100)));
      var innerH = Math.max(2, bandH - Math.round(H * ((zone.marginTop + zone.marginBottom) / 100)));
      bands.push({ zone: zone, kind: zone.kind, y: cursor, height: bandH,
                   innerX: innerX, innerY: innerY, innerW: innerW, innerH: innerH,
                   share: total > 0 ? zone.fraction / total : 0 });
      cursor += bandH;
    });

    // A sobra de arredondamento vai para a ultima faixa com altura, senao vira
    // uma costura preta de 1px contra fundo claro.
    if (cursor !== H) {
      for (var i = bands.length - 1; i >= 0; i--) {
        if (bands[i].kind !== "captions") { bands[i].height += (H - cursor); break; }
      }
    }
    return bands;
  }

  // Tweet da prévia no formato X: mesmo conteúdo do mock da galeria,
  // escalado em cqw junto com o palco.
  function tweetMock() {
    var tweet = document.createElement("div");
    tweet.className = "pv-tweet";
    tweet.innerHTML =
      "<div class='pv-tweet-row'><span class='pv-avatar'>S</span>" +
      "<span class='pv-id'><strong>Seu Nome " +
      "<svg viewBox='0 0 24 24' width='10' height='10' aria-hidden='true'><path fill='#1d9bf0' d='M12 2l2.4 2.4 3.4-.5 1 3.3 3.2 1.2-1.4 3.1 1.4 3.1-3.2 1.2-1 3.3-3.4-.5L12 22l-2.4-2.4-3.4.5-1-3.3-3.2-1.2L3.4 12 2 8.9l3.2-1.2 1-3.3 3.4.5z'/><path fill='none' stroke='#fff' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round' d='M8.5 12.5l2.5 2.5 4.5-5'/></svg>" +
      "</strong><small>@seuhandle</small></span></div>" +
      "<p class='pv-body'>" + esc(tweetText()) + "</p>";
    return tweet;
  }

  // POV sobre o vídeo + barra de identidade (formato Meme na prévia).
  function memePov() {
    var pov = document.createElement("p");
    pov.className = "pv-pov";
    // Sem `top` daqui: o recuo e o fundo do recorte do aparelho, medido em px de
    // tela pelo CSS. O notch e moldura, pinta por cima do conteudo e engolia a
    // primeira linha do POV — enquanto a conta viveu aqui, cada mudanca de zona
    // movia o texto para dentro ou para fora do recorte.
    pov.textContent = "POV: Você usou o formato de meme e VIRALIZOU com 3x mais!";
    return pov;
  }

  function memeIdBar() {
    var bar = document.createElement("div");
    bar.className = "pv-idbar";
    bar.innerHTML =
      "<span class='pv-avatar pv-avatar--dark'>S</span>" +
      "<span class='pv-id'><strong>Seu Nome</strong><small>@seuhandle</small></span>";
    return bar;
  }

  // Gancho na base do vídeo (formato Vídeo Viral na prévia).
  function viralHook() {
    var hook = document.createElement("p");
    hook.className = "pv-hook";
    var entry = GALLERY[state.previewMock] || {};
    hook.textContent = entry.hook || "";
    return hook;
  }

  // ---------- render da previa ---------- //
  function renderPreview() {
    var bands = planBands();
    var canvas = $("#canvas");
    canvas.innerHTML = "";

    var bands = planBands();
    var canvas = $("#canvas");
    canvas.innerHTML = "";

    bands.forEach(function (band) {
      var el = document.createElement("div");
      el.className = "band";
      el.style.top = (band.y / state.height * 100) + "%";
      el.style.height = (band.height / state.height * 100) + "%";

      if (band.kind === "captions") {
        // A faixa de legenda e posicionada por margin_v a partir da BASE DO
        // CANVAS. Numa composicao de varias zonas ela cola na base da faixa
        // de video (12% da altura dela), no ponto viral — ver captionMargin.
        var preset = PRESETS[state.preset] || PRESETS.karaoke;
        var marginV = captionMargin(bands);
        el.className = "band caption-band";
        el.style.top = "auto";
        // A margem vem do motor em PIXELS A PARTIR DA BASE do frame. Tem de
        // virar porcentagem de `bottom`, nao `margin-bottom`: porcentagem de
        // margin resolve contra a LARGURA do bloco contentor, entao a legenda
        // subia so ~47% do que devia e pousava dentro da zona de baixo. Em
        // `bottom` a porcentagem resolve contra a ALTURA, que e o que o motor
        // mede.
        el.style.bottom = (marginV / state.height * 100) + "%";
        el.style.height = "auto";
        var text = document.createElement("div");
        text.className = "caption-text";
        text.style.fontFamily = "'" + preset.font + "', sans-serif";
        // Corpo da fonte: `preset.size` e um tamanho em px do frame de 1920 de
        // altura, entao a unidade certa e cqh (fracao da altura), nao cqw.
        text.style.fontSize = (preset.size / state.height * 100) + "cqh";
        // Tema claro/escuro força o par caixa+texto clássico; vazio = preset.
        var capColor = preset.color, capBox = preset.box;
        if (state.captionTheme === "light") { capColor = "#141414"; capBox = "#f4f4f4"; }
        else if (state.captionTheme === "dark") { capColor = "#ffffff"; capBox = "#141414"; }
        text.style.color = capColor;
        text.style.fontStyle = "normal";
        if (capBox) {
          text.style.background = capBox;
          text.style.borderRadius = "1.2cqw";
          text.style.padding = "0.8cqw 2.4cqw";
        } else {
          text.style.textShadow =
            "0 0 1.2cqw rgba(0,0,0,0.95), 0 0 0.4cqw rgba(0,0,0,0.95)";
        }
        text.innerHTML = "SEGREDO QUE NINGUEM <span style='color:" + preset.accent + "'>CONTA</span>";
        el.appendChild(text);
      } else {
        el.style.top = (band.y / state.height * 100) + "%";
        el.style.height = (band.height / state.height * 100) + "%";
        var mockKey = state.previewMock || "";
        // Formato X: a banda de imagem vira o tweet, igual ao mock da galeria.
        if (mockKey === "x" && band.kind === "image") {
          el.appendChild(tweetMock());
        } else if (mockKey === "meme" && band.kind === "image") {
          el.appendChild(memeIdBar());
        } else {
        var media = document.createElement("div");
        var thumb = state.zones.indexOf(band.zone) >= 0 ? band.zone.source : "";
        var isThumb = band.kind === "image" && thumb;
        media.className = "media " + (isThumb ? "img-thumb" : band.kind);
        media.style.left = (band.innerX / state.width * 100) + "%";
        media.style.right = ((state.width - band.innerX - band.innerW) / state.width * 100) + "%";
        media.style.top = ((band.innerY - band.y) / band.height * 100) + "%";
        media.style.bottom = ((band.height - (band.innerY - band.y) - band.innerH) / band.height * 100) + "%";
        if (band.zone.radius > 0) {
          media.style.borderRadius = (band.zone.radius / 100 * state.width / state.width * 100 * (state.width / 100)) + "px";
          media.style.borderRadius = (band.zone.radius * state.width / 100) + "px";
        }
        if (band.kind === "solid") {
          media.style.background = band.zone.color || "#333";
          media.className = "media";
        }
        // Frame principal: selo com o segundo + o quadro real quando há
        // vídeo de referência (pausado no instante escolhido).
        if (band.kind === "frame") {
          var tag = document.createElement("span");
          tag.className = "pv-frame-tag";
          tag.textContent = "FRAME · " + band.zone.frameAt.toFixed(1) + "s";
          media.appendChild(tag);
          if (state.previewVideos.length) {
            var fv = document.createElement("video");
            fv.src = state.previewVideos[0].url;
            fv.muted = true; fv.playsInline = true; fv.preload = "auto";
            fv.className = "pv-video";
            (function (at) {
              fv.addEventListener("loadedmetadata", function () {
                try { fv.currentTime = Math.min(at, Math.max(0, fv.duration - 0.1)); } catch (e) {}
              });
            })(band.zone.frameAt);
            media.appendChild(fv);
          }
        }
        if (isThumb) media.style.backgroundImage = "url('" + thumb.replace(/['"]/g, "") + "')";
        // Vídeo de referência: pinta a faixa no palco (o render usa o corte).
        if (band.kind === "video" && state.previewVideos.length) {
          var pv = document.createElement("video");
          pv.src = state.previewVideos[0].url;
          pv.muted = true; pv.loop = true; pv.autoplay = true; pv.playsInline = true;
          pv.className = "pv-video" + (band.zone.fit === "contain" ? " pv-contain" : "");
          pv.setAttribute("aria-label", "Prévia: " + state.previewVideos[0].name);
          media.appendChild(pv);
        }
        // Enquadramento manual: zoom sobre o ponto de pan, igual ao recorte
        // do render (scale + crop). A banda esconde a sobra (overflow hidden).
        if (band.kind === "video" &&
            (state.reframeZoom !== 1 || state.reframePanX !== 0.5 || state.reframePanY !== 0.5)) {
          media.style.transform = "scale(" + state.reframeZoom + ")";
          media.style.transformOrigin =
            (state.reframePanX * 100) + "% " + (state.reframePanY * 100) + "%";
        }
        // Zoom/pan da própria zona (frame/imagem): mesmo gesto do render.
        if (band.kind === "frame" || band.kind === "image") {
          var zzoom = band.zone.zoom || 1;
          var zpanx = band.zone.panX != null ? band.zone.panX : 0.5;
          var zpany = band.zone.panY != null ? band.zone.panY : 0.5;
          if (zzoom !== 1 || zpanx !== 0.5 || zpany !== 0.5) {
            media.style.transform = "scale(" + zzoom + ")";
            media.style.transformOrigin = (zpanx * 100) + "% " + (zpany * 100) + "%";
          }
        }
        el.appendChild(media);
        // Formato Meme: POV sobre o vídeo. Formato Viral: gancho na base.
        if (mockKey === "meme" && band.kind === "video") el.appendChild(memePov());
        if (mockKey === "viral" && band.kind === "video") el.appendChild(viralHook());
        }
      }
      canvas.appendChild(el);
    });

    if (state.progressOn) {
      var bar = document.createElement("div");
      bar.className = "progress-preview";
      bar.style.width = "38%";
      // Espessura tambem e uma medida vertical do frame: cqh.
      bar.style.height = (state.progressHeight / state.height * 100) + "cqh";
      bar.style.background = state.progressColor;
      canvas.appendChild(bar);
    }

    if (state.headlineOn) {
      var head = document.createElement("div");
      head.className = "headline-preview";
      head.style.color = (PRESETS[state.preset] || PRESETS.karaoke).accent;
      head.style.textAlign = state.headlineAlign || "center";
      // Margem do topo: px do frame de 1920, logo cqh — a mesma medida que
      // `render.py` escreve no estilo ASS. O `top: 3cqw` do CSS media a
      // LARGURA e punha o headline a 1,7% da altura, quase metade do motor.
      head.style.top = (HEADLINE_TOP_PX / state.height * 100) + "cqh";
      // `headline_size` e px do frame de 1920 de altura (render.py monta o
      // estilo ASS com PlayResY=height), logo cqh — nao o 5.2cqw fixo, que
      // ignorava o controle de tamanho do Passo 1.
      head.style.fontSize = ((state.headlineSize || 100) / state.height * 100) + "cqh";
      head.style.paddingLeft = head.style.paddingRight =
        "calc(4cqw + " + ((state.headlineMargin || 0) / state.width * 100) + "cqw)";
      head.textContent = (state.headlineText || "O ERRO QUE CUSTA CARO").toUpperCase();
      canvas.appendChild(head);
    }

    $("#preview-dims").textContent = state.width + " x " + state.height;

    // O rodape mostra quanta altura e conteudo vs legenda.
    var pixelShare = state.zones
      .filter(function (z) { return z.kind !== "captions"; })
      .reduce(function (s, z) { return s + z.fraction; }, 0);
    $("#preview-share").textContent = Math.round(pixelShare * 100) + "% conteudo";
  }

  // Mesma conta do motor (_caption_margin_for_band): em template dividido
  // a legenda cola na base da faixa de vídeo (12% da altura dela), nunca
  // abaixo do piso social (16% da altura — a UI das redes).
  function captionMargin(bands) {
    var preset = PRESETS[state.preset] || PRESETS.karaoke;
    var floor = Math.round(state.height * 0.16);
    var pixel = state.zones.filter(function (z) { return z.kind !== "captions"; });
    var idx = -1;
    for (var i = 0; i < pixel.length; i++) {
      if (pixel[i].kind === "video") { idx = i; break; }
    }
    if (idx < 0) return Math.max(preset.margin, floor);
    var video = pixel[idx];
    if (video.fraction >= 1) return Math.max(preset.margin, floor);
    var below = pixel.slice(idx + 1).reduce(function (s, z) { return s + z.fraction; }, 0);
    var belowPx = Math.round(state.height * below);
    var bandPx = Math.round(state.height * video.fraction);
    return Math.max(belowPx + Math.round(bandPx * 0.12), floor);
  }

  function renderGeometry() {
    var bands = planBands();
    var rows = bands.map(function (band) {
      if (band.kind === "captions") {
        return "<tr><td>" + KINDS.captions.label + "</td><td class='mono'>full canvas</td>" +
               "<td class='mono'>posicionado pelo libass</td></tr>";
      }
      return "<tr><td><span class='zone-swatch' style='background:" +
        (KINDS[band.kind] || {}).color + ";display:inline-block;margin-right:7px'></span>" +
        (KINDS[band.kind] || {}).label + "</td>" +
        "<td class='mono'>y=" + band.y + " h=" + band.height + "</td>" +
        "<td class='mono'>" + band.innerW + "x" + band.innerH + " @ (" + band.innerX + "," + band.innerY + ")</td></tr>";
    }).join("");

    $("#geom-out").innerHTML =
      "<div class='matrix'><table>" +
      "<thead><tr><th>Zona</th><th>Faixa</th><th>Area interna</th></tr></thead>" +
      "<tbody>" + rows + "</tbody></table></div>";
  }

  // ---------- validacao (as mesmas regras do Template.validate) ---------- //
  function validate() {
    var problems = [];
    var pixel = state.zones.filter(function (z) { return z.kind !== "captions"; });
    var total = pixel.reduce(function (s, z) { return s + z.fraction; }, 0);

    if (!state.name.trim()) problems.push("O template precisa de um nome.");
    if (!/^[A-Za-z0-9._-]+$/.test(state.name.trim()))
      problems.push("O nome so pode ter letras, numeros, ponto, hifen e underline.");
    if (!pixel.length) problems.push("O template precisa de pelo menos uma zona que ocupe altura.");
    if (pixel.length && Math.abs(total - 1) > 1e-6)
      problems.push("As zonas somam " + (total * 100).toFixed(1) +
        "% da altura; precisam somar exatamente 100%.");

    var videos = state.zones.filter(function (z) { return z.kind === "video"; });
    if (videos.length > 1) problems.push("No maximo uma zona de video.");

    var caps = state.zones.filter(function (z) { return z.kind === "captions"; });
    if (caps.length > 1) problems.push("No maximo uma zona de legenda.");
    if (caps.length && state.zones[state.zones.length - 1].kind !== "captions")
      problems.push("A zona de legenda precisa ser a ultima.");

    state.zones.forEach(function (zone, index) {
      var where = "Zona " + (index + 1) + " (" + (KINDS[zone.kind] || {}).label + ")";
      if (zone.kind !== "captions" && (zone.fraction < 0.02 || zone.fraction > 1))
        problems.push("Zona " + (index + 1) + ": a fracao precisa ficar entre 2% e 100%.");
      if (zone.kind === "image" && !zone.source.trim())
        problems.push(where + ": uma zona de imagem precisa de um caminho.");
      if (zone.marginTop + zone.marginBottom >= 100)
        problems.push(where + ": as margens verticais consomem a faixa inteira.");
    });

    return problems;
  }

  // ---------- geracao do arquivo ---------- //
  function toToml() {
    var lines = [];
    lines.push('name = "' + state.name.trim() + '"');
    lines.push('caption_preset = "' + state.preset + '"');
    if (state.layout) lines.push('layout = "' + state.layout + '"');
    if (state.background && state.background !== "black")
      lines.push('background = "' + state.background + '"');
    if (state.captionTheme)
      lines.push('caption_box_theme = "' + state.captionTheme + '"');
    if (state.headlineOn) {
      lines.push("headline_seconds = " + fmtNum(state.headlineSeconds));
      if (state.headlineText.trim())
        lines.push('headline_text = "' + state.headlineText.trim().replace(/"/g, '\\"') + '"');
      if (state.headlineAlign && state.headlineAlign !== "center")
        lines.push('headline_align = "' + state.headlineAlign + '"');
      if (state.headlineSize !== 100)
        lines.push("headline_font_size = " + state.headlineSize);
      if (state.headlineMargin !== 60)
        lines.push("headline_margin_side = " + state.headlineMargin);
    }
    if (state.progressOn) lines.push("progress_bar = true");
    if (state.reframeZoom !== 1) lines.push("reframe_zoom = " + fmtNum(state.reframeZoom));
    if (state.reframePanX !== 0.5) lines.push("reframe_pan_x = " + fmtNum(state.reframePanX));
    if (state.reframePanY !== 0.5) lines.push("reframe_pan_y = " + fmtNum(state.reframePanY));
    lines.push("");

    state.zones.forEach(function (zone) {
      lines.push("[[zones]]");
      lines.push('kind = "' + zone.kind + '"');
      lines.push("fraction = " + fmtNum(round4(zone.fraction)));
      if (zone.kind === "image") lines.push('source = "' + zone.source.trim() + '"');
      if (zone.kind === "frame" && zone.frameAt > 0)
        lines.push("frame_at = " + fmtNum(zone.frameAt));
      if (zone.kind === "solid") lines.push('color = "' + (zone.color || "black") + '"');
      if (zone.kind !== "captions" && zone.kind !== "solid") {
        if (zone.fit !== "cover") lines.push('fit = "' + zone.fit + '"');
      }
      if (zone.marginTop) lines.push("margin_top = " + fmtNum(zone.marginTop / 100));
      if (zone.marginBottom) lines.push("margin_bottom = " + fmtNum(zone.marginBottom / 100));
      if (zone.marginLeft) lines.push("margin_left = " + fmtNum(zone.marginLeft / 100));
      if (zone.marginRight) lines.push("margin_right = " + fmtNum(zone.marginRight / 100));
      if (zone.radius > 0 && (zone.kind === "image" || zone.kind === "frame"))
        lines.push("corner_radius = " + fmtNum(zone.radius / 100));
      if ((zone.kind === "image" || zone.kind === "frame") && zone.zoom && zone.zoom !== 1)
        lines.push("zoom = " + fmtNum(zone.zoom));
      if ((zone.kind === "image" || zone.kind === "frame") && zone.panX != null && zone.panX !== 0.5)
        lines.push("pan_x = " + fmtNum(zone.panX));
      if ((zone.kind === "image" || zone.kind === "frame") && zone.panY != null && zone.panY !== 0.5)
        lines.push("pan_y = " + fmtNum(zone.panY));
      lines.push("");
    });

    return lines.join("\n").replace(/\n+$/, "\n");
  }

  function round4(value) { return Math.round(value * 10000) / 10000; }
  function fmtNum(value) {
    if (Number.isInteger(value)) return String(value);
    return String(Number(value.toFixed(4)));
  }

  function variantCount() {
    var presets = state.variantPresets.length || 1;
    var layouts = state.variantLayouts.length || 1;
    return presets * layouts;
  }

  function toCommand() {
    var parts = ["python -m viralclipper", '"COLE_A_URL_AQUI"'];
    // Um template embutido vai pelo nome; um template salvo vai pelo caminho.
    // Mandar o nome de um arquivo que nao existe e o erro mais facil de cometer.
    parts.push("--template", "./templates/" + state.name.trim() + ".toml");
    if (state.variantPresets.length)
      parts.push("--variant-presets", state.variantPresets.join(","));
    if (state.variantLayouts.length)
      parts.push("--variant-layouts", state.variantLayouts.join(","));
    parts.push("--output", "output");
    return parts.join(" ");
  }

  function renderOutputs() {
    var problems = validate();
    var count = variantCount();

    var alertHtml;
    if (problems.length) {
      alertHtml = "<div class='alert bad'><span class='mark'>!</span><div>" +
        "<strong>O template ainda nao e valido.</strong><ul style='margin:6px 0 0 18px'>" +
        problems.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") +
        "</ul></div></div>";
    } else {
      var renders = count;
      alertHtml = "<div class='alert ok'><span class='mark'>v</span><div>" +
        "<strong>Pronto para renderizar.</strong> Cada corte sai em " +
        "<strong>" + renders + "</strong> arquivo" + (renders > 1 ? "s" : "") +
        (renders > 1 ? " — a secao e baixada uma vez por janela." : ".") +
        "</div></div>";
    }
    $("#validate-out").innerHTML = alertHtml;

    var toml = toToml();
    $("#out-toml").textContent = toml;
    $("#out-toml").className = "code" + (problems.length ? " bad" : "");
    $("#out-toml-title").textContent = "Template .toml  (templates/" + state.name.trim() + ".toml)";
    $("#out-cmd").textContent = toCommand();
    $("#btn-download").disabled = problems.length > 0;
  }

  // Preenchimento do trilho dos sliders de enquadramento (mesma técnica
  // do slider de altura das zonas).
  function paintSlideFill(input) {
    var min = Number(input.min || 0), max = Number(input.max || 100);
    var pct = max > min ? ((Number(input.value) - min) / (max - min)) * 100 : 0;
    input.style.setProperty("--p", pct + "%");
  }

  // ---------- editor de zonas ----------
  function renderZoneSummary() {
    var pixel = state.zones.filter(function (z) { return z.kind !== "captions"; });
    var total = pixel.reduce(function (s, z) { return s + z.fraction; }, 0);
    var ok = Math.abs(total - 1) <= 1e-6;
    var missing = Math.max(0, 1 - total);

    $("#zone-summary").innerHTML =
      "<div class='alert " + (ok ? "ok" : "warn") + "'>" +
      "<span class='mark'>" + (ok ? "v" : "!") + "</span><div>" +
      "Zonas com altura somam <strong>" + (total * 100).toFixed(1) + "%</strong>." +
      (ok ? " Fechado." :
        " Faltam <strong>" + (missing * 100).toFixed(1) + "%</strong> — use " +
        "<em>Balancear 100%</em> ou ajuste as fracoes.") +
      "</div></div>";
  }

  // Rótulo + trilho do slider que está sob o dedo (sem recriar nada).
  function paintZoneSliderLabel(input, text) {
    paintSlideFill(input);
    var box = input.closest(".field, .full");
    var label = box ? box.querySelector(".mini-label span") : null;
    if (label) label.textContent = text;
  }

  // Atualiza só o que a fração toca (rótulos + resumo), sem recriar o
  // slider: recriar no meio do arrasto mataria o gesto no primeiro tick.
  // Há dois sliders de altura por zona (Zonas e Mídia): todos espelham.
  function paintFractionUI(index) {
    var zone = state.zones[index];
    if (!zone) return;
    var pct = zone.fraction * 100;
    Array.prototype.forEach.call(
      document.querySelectorAll("input[data-act='fraction'][data-i='" + index + "']"),
      function (slider) {
        if (slider !== document.activeElement) slider.value = pct;
        slider.style.setProperty("--p", pct + "%");
        var box = slider.closest(".full, .field");
        var label = box ? box.querySelector(".mini-label span") : null;
        if (label) label.textContent = pct.toFixed(1) + "%";
      }
    );
    var zslider = document.querySelector("#zone-list input[data-act='fraction'][data-i='" + index + "']");
    var item = zslider ? zslider.closest(".zone-item") : null;
    var share = item ? item.querySelector(".zone-share") : null;
    if (share) share.textContent = pct.toFixed(0) + "%";
    renderZoneSummary();
  }

  function renderZones() {
    var list = $("#zone-list");
    list.innerHTML = "";

    renderZoneSummary();

    state.zones.forEach(function (zone, index) {
      var item = document.createElement("div");
      item.className = "zone-item";
      var isPixel = zone.kind !== "captions";
      var swatch = (KINDS[zone.kind] || {}).color || "#888";

      var kindChips = Object.keys(KINDS).map(function (key) {
        var on = key === zone.kind;
        return "<button type='button' class='chip pressable' role='radio' data-act='kind' data-value='" + key +
          "' data-i='" + index + "' aria-checked='" + (on ? "true" : "false") + "'" +
          (on ? "" : " tabindex='-1'") + " title='" + KINDS[key].label + "'>" +
          "<span class='chip-dot' style='background:" + KINDS[key].color + "'></span>" + KINDS[key].label + "</button>";
      }).join("");

      item.innerHTML =
        "<div class='zone-head'>" +
          "<div class='zone-kind'>" +
            "<span class='zone-swatch' style='background:" + swatch + "'></span>" +
            "Zona " + (index + 1) +
          "</div>" +
          "<div class='zone-tools'>" +
            "<span class='zone-share'>" +
              (isPixel ? (zone.fraction * 100).toFixed(0) + "%" : "—") + "</span>" +
            "<button class='icon-btn pressable' data-act='up' data-i='" + index + "'" +
              (index === 0 ? " disabled" : "") + " title='Subir'>&#8593;</button>" +
            "<button class='icon-btn pressable' data-act='down' data-i='" + index + "'" +
              (index === state.zones.length - 1 ? " disabled" : "") + " title='Descer'>&#8595;</button>" +
            "<button class='icon-btn pressable' data-act='del' data-i='" + index + "'" +
              " title='Remover'>&#10005;</button>" +
          "</div>" +
        "</div>" +
        "<div class='zone-fields'>" +
          "<div class='full'>" +
            "<div class='mini-label'>Tipo</div>" +
            "<div class='chips' role='radiogroup' aria-label='Tipo da zona " + (index + 1) + "'>" + kindChips + "</div>" +
          "</div>" +
          (isPixel ?
            "<div class='full'>" +
              "<div class='mini-label'>Altura <span>" + (zone.fraction * 100).toFixed(1) + "%</span></div>" +
              "<input type='range' min='2' max='100' step='0.5' value='" + (zone.fraction * 100) + "'" +
                " style='--p:" + (zone.fraction * 100) + "%'" +
                " data-act='fraction' data-i='" + index + "'>" +
            "</div>" : "") +
          (zone.kind === "image" ?
            "<div class='full'>" +
              "<div class='mini-label'>Arquivo</div>" +
              "<input type='text' value='" + esc(zone.source) + "' placeholder='logo.png'" +
                " data-act='source' data-i='" + index + "'>" +
            "</div>" : "") +
          (zone.kind === "frame" ?
            "<div class='full'>" +
              "<div class='mini-label'>Segundo do video <span>" + zone.frameAt.toFixed(1) + "s</span></div>" +
              "<input type='range' class='rf-slide' min='0' max='10' step='0.5' value='" + zone.frameAt + "'" +
                " style='--p:" + (zone.frameAt * 10) + "%'" +
                " data-act='frameAt' data-i='" + index + "'>" +
            "</div>" : "") +
          (zone.kind === "solid" ?
            "<div class='full'>" +
              "<div class='mini-label'>Cor</div>" +
              "<input type='text' value='" + esc(zone.color) + "' data-act='color' data-i='" + index + "'>" +
            "</div>" : "") +
          (isPixel && zone.kind !== "solid" ?
            "<div class='full'>" +
              "<div class='mini-label'>Encaixe</div>" +
              "<div class='chips' role='radiogroup' aria-label='Encaixe da zona " + (index + 1) + "'>" +
                "<button type='button' class='chip pressable' role='radio' data-act='fit' data-value='cover' data-i='" + index + "'" +
                  (zone.fit === "cover" ? " aria-checked='true'" : " aria-checked='false' tabindex='-1'") +
                  " title='Preenche e corta as sobras'><span class='chip-dot' style='background:#6366f1'></span>Preenche</button>" +
                "<button type='button' class='chip pressable' role='radio' data-act='fit' data-value='contain' data-i='" + index + "'" +
                  (zone.fit === "contain" ? " aria-checked='true'" : " aria-checked='false' tabindex='-1'") +
                  " title='Encaixa inteiro com letterbox'><span class='chip-dot' style='background:#34d399'></span>Encaixa</button>" +
              "</div>" +
            "</div>" : "") +
          (isPixel ?
            "<div><div class='mini-label'>Topo</div><div class='num-wrap'>" +
              "<input type='number' min='0' max='50' step='0.1' value='" + zone.marginTop + "' data-act='marginTop' data-i='" + index + "' aria-label='Margem do topo em por cento'><span>%</span></div></div>" +
            "<div><div class='mini-label'>Base</div><div class='num-wrap'>" +
              "<input type='number' min='0' max='50' step='0.1' value='" + zone.marginBottom + "' data-act='marginBottom' data-i='" + index + "' aria-label='Margem da base em por cento'><span>%</span></div></div>" +
            "<div><div class='mini-label'>Esq</div><div class='num-wrap'>" +
              "<input type='number' min='0' max='40' step='0.1' value='" + zone.marginLeft + "' data-act='marginLeft' data-i='" + index + "' aria-label='Margem da esquerda em por cento'><span>%</span></div></div>" +
            "<div><div class='mini-label'>Dir</div><div class='num-wrap'>" +
              "<input type='number' min='0' max='40' step='0.1' value='" + zone.marginRight + "' data-act='marginRight' data-i='" + index + "' aria-label='Margem da direita em por cento'><span>%</span></div></div>" : "") +
          ((zone.kind === "image" || zone.kind === "frame") ?
            "<div class='full'>" +
              "<div class='mini-label'>Raio de canto <span>" + zone.radius.toFixed(1) + "%</span></div>" +
              "<input type='range' min='0' max='12' step='0.5' value='" + zone.radius + "'" +
                " data-act='radius' data-i='" + index + "'>" +
            "</div>" : "") +
        "</div>" +
        "<div class='step-nav' style='margin-top:10px'>" +
          "<button class='btn pressable btn-sm btn-ghost' data-act='dup' data-i='" + index + "'>Duplicar tipo</button>" +
        "</div>";

      list.appendChild(item);
    });
  }

  function renderMedia() {
    var target = $("#media-list");
    target.innerHTML = "";
    var pixel = state.zones.filter(function (z) { return z.kind !== "captions"; });

    if (!pixel.length) {
      target.innerHTML = "<div class='alert warn'><span class='mark'>!</span>" +
        "<div>Nenhuma zona de midia ainda. Adicione uma no passo <strong>Zonas</strong>.</div></div>";
      return;
    }

    pixel.forEach(function (zone, index) {
      var realIndex = state.zones.indexOf(zone);
      var block = document.createElement("div");
      block.className = "field";
      var label = (KINDS[zone.kind] || {}).label;

      if (zone.kind === "video") {
        block.innerHTML =
          "<div class='mini-label'>Zona " + (realIndex + 1) + " — " + label + "</div>" +
          "<div class='pv-stack'>" +
            "<button type='button' class='pv-upload-btn pressable' aria-label='Adicionar vídeos à prévia'>" +
              "<input type='file' class='pv-upload-input' accept='video/*' multiple hidden>" +
              "<span class='pv-upload-ico' aria-hidden='true'>" +
                "<svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round'><path d='M12 3v12'></path><path d='m17 8-5-5-5 5'></path><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'></path></svg>" +
              "</span>Adicionar vídeo(s)</button>" +
            "<div class='pv-files'>" + previewVideoFiles(zone.fit) + "</div>" +
          "</div>";
      } else if (zone.kind === "frame") {
        var fz = zone.zoom || 1;
        var fpx = zone.panX != null ? zone.panX * 100 : 50;
        var fpy = zone.panY != null ? zone.panY * 100 : 50;
        block.innerHTML =
          "<div class='mini-label'>Zona " + (realIndex + 1) + " — " + label + "</div>" +
          "<div class='pv-stack'>" +
            "<div class='pv-framebox' data-pv-framebox='" + realIndex + "'>" +
              "<strong>" + zone.frameAt.toFixed(1) + "s</strong><small>do corte</small></div>" +
            "<div class='pv-files'>" +
              "<div class='field' style='margin:0'>" +
                "<div class='mini-label'>Frame principal <span>" + zone.frameAt.toFixed(1) + "s</span></div>" +
                "<input type='range' class='rf-slide' min='0' max='10' step='0.5' value='" + zone.frameAt + "'" +
                  " style='--p:" + (zone.frameAt * 10) + "%'" +
                  " data-act='frameAt' data-i='" + realIndex + "' aria-label='Segundo do vídeo para o frame'>" +
              "</div>" +
              "<div><button type='button' class='btn pressable btn-sm' data-frame-edit='" + realIndex + "'" +
                " aria-expanded='false'>Ajustar frame</button></div>" +
              "<div class='frame-edit' data-frame-panel='" + realIndex + "' hidden>" +
                "<div class='mini-label'>Zoom <span>" + fz.toFixed(2) + "x</span></div>" +
                "<input type='range' class='rf-slide' min='1' max='3' step='0.05' value='" + fz + "'" +
                  " style='--p:" + ((fz - 1) / 2 * 100) + "%'" +
                  " data-act='zoom' data-i='" + realIndex + "' aria-label='Zoom do frame'>" +
                "<div class='mini-label' style='margin-top:8px'>Horizontal <span>" + Math.round(fpx) + "%</span></div>" +
                "<input type='range' class='rf-slide' min='0' max='100' step='1' value='" + fpx + "'" +
                  " style='--p:" + fpx + "%'" +
                  " data-act='panx' data-i='" + realIndex + "' aria-label='Posição horizontal do frame'>" +
                "<div class='mini-label' style='margin-top:8px'>Vertical <span>" + Math.round(fpy) + "%</span></div>" +
                "<input type='range' class='rf-slide' min='0' max='100' step='1' value='" + fpy + "'" +
                  " style='--p:" + fpy + "%'" +
                  " data-act='pany' data-i='" + realIndex + "' aria-label='Posição vertical do frame'>" +
                "<div class='mini-label' style='margin-top:8px'>Altura <span>" + (zone.fraction * 100).toFixed(1) + "%</span></div>" +
                "<input type='range' class='rf-slide' min='2' max='100' step='0.5' value='" + (zone.fraction * 100) + "'" +
                  " style='--p:" + (zone.fraction * 100) + "%'" +
                  " data-act='fraction' data-i='" + realIndex + "' aria-label='Altura da zona de frame'>" +
              "</div>" +
              "<p class='hint' style='margin:0'>Quadro extraído do próprio vídeo. " +
              "<strong>Nenhum arquivo externo</strong> — se falhar, degrada para o vídeo.</p>" +
            "</div>" +
          "</div>";
      } else if (zone.kind === "image") {
        block.innerHTML =
          "<div class='mini-label'>Zona " + (realIndex + 1) + " — " + label + "</div>" +
          "<div class='zone-fields'><div class='full'>" +
          "<input type='text' value='" + esc(zone.source) + "' placeholder='logo.png ou caminho/para/logo.png'" +
          " data-act='source' data-i='" + realIndex + "'>" +
          "<div class='hint' style='margin-top:6px'>Relativo a pasta de saida, ou um caminho absoluto.</div>" +
          "</div></div>";
      } else {
        block.innerHTML =
          "<div class='mini-label'>Zona " + (realIndex + 1) + " — " + label + "</div>" +
          "<div class='zone-fields'><div class='full'>" +
          "<input type='text' value='" + esc(zone.color) + "' data-act='color' data-i='" + realIndex + "'>" +
          "</div></div>";
      }
      target.appendChild(block);
    });
  }

  function renderStyle() {
    var notes = state.zones.filter(function (z) { return z.kind !== "captions"; })
      .map(function (zone) {
        var index = state.zones.indexOf(zone);
        return "<tr><td>Zona " + (index + 1) + "</td><td>" + ((KINDS[zone.kind] || {}).label) +
          "</td><td class='mono'>" + (zone.kind === "solid" ? "—" :
          (zone.fit === "cover" ? "preenche" : "encaixa")) + "</td></tr>";
      }).join("");

    $("#style-notes").innerHTML =
      "<div class='mini-label' style='margin-bottom:10px'>Estado atual</div>" +
      "<div class='matrix' style='margin-top:0'><table><thead><tr><th>Zona</th><th>Tipo</th><th>Encaixe</th></tr></thead>" +
      "<tbody>" + notes + "</tbody></table></div>";
  }

  function renderVariants() {
    var presetKeys = Object.keys(PRESETS).sort();
    $("#variant-presets").innerHTML = presetKeys.map(function (key) {
      var on = state.variantPresets.indexOf(key) >= 0 ? "1" : "0";
      return "<button class='pick pressable' data-on='" + on + "' data-axis='preset' data-key='" +
        key + "'>" + key + "</button>";
    }).join("");

    var layouts = ["center", "blur", "fit", "focus"];
    $("#variant-layouts").innerHTML = layouts.map(function (key) {
      var on = state.variantLayouts.indexOf(key) >= 0 ? "1" : "0";
      return "<button class='pick pressable' data-on='" + on + "' data-axis='layout' data-key='" +
        key + "'>" + key + "</button>";
    }).join("");

    $("#preset-count").textContent = state.variantPresets.length;
    $("#layout-count").textContent = state.variantLayouts.length;

    var presets = state.variantPresets.length ? state.variantPresets : ["(preset do template)"];
    var layoutList = state.variantLayouts.length ? state.variantLayouts : ["(layout do template)"];
    var rows = [];
    presets.forEach(function (preset) {
      layoutList.forEach(function (layout) {
        rows.push("<tr><td class='mono'>" + esc(preset) + "</td><td class='mono'>" +
          esc(layout) + "</td><td class='mono'>" + state.name.trim() + "__" +
          (preset === "(preset do template)" ? "" : preset + "-") +
          (layout === "(layout do template)" ? "" : layout) + "_000010.mp4</td></tr>");
      });
    });

    $("#matrix-out").innerHTML =
      "<div class='matrix'><table>" +
      "<thead><tr><th>Preset</th><th>Layout</th><th>Arquivo por corte</th></tr></thead>" +
      "<tbody>" + rows.join("") +
      "<tr class='total'><td colspan='2'>Total por corte</td><td class='mono'>" +
      variantCount() + " arquivo" + (variantCount() > 1 ? "s" : "") + "</td></tr>" +
      "</tbody></table></div>";
  }

  function renderSteps() {
    $("#steps-bar").innerHTML = STEPS.map(function (step, index) {
      var stateName = index === state.step ? "active" : (index < state.step ? "done" : "todo");
      return "<button class='step-pip pressable' data-state='" + stateName + "' data-goto='" + index + "'" +
        (index === state.step ? " aria-current='step'" : "") + ">" +
        "<span class='pip-track'></span>" +
        "<span class='pip-num'>Passo " + (index + 1) + "</span>" +
        "<span class='pip-label'>" + step.label + "</span>" +
        "</button>";
    }).join("");
  }

  function showStep(index) {
    state.step = Math.max(0, Math.min(STEPS.length - 1, index));
    $$("[data-step]").forEach(function (card) {
      card.hidden = Number(card.dataset.step) !== state.step;
    });
    $("#btn-prev").disabled = state.step === 0;
    $("#btn-next").textContent = state.step === STEPS.length - 1 ? "Concluir" : "Proximo";
    renderSteps();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function renderAll() {
    renderZones();
    renderMedia();
    renderStyle();
    renderVariants();
    renderPreview();
    renderGeometry();
    renderOutputs();
  }

  // ---------- interacoes ----------
  function updateZone(index, patch) {
    state.zones[index] = Object.assign({}, state.zones[index], patch);
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-act], [data-goto], [data-axis], [data-copy], [data-gallery]");
    if (!target) return;

    if (target.dataset.goto !== undefined) {
      showStep(Number(target.dataset.goto));
      return;
    }

    if (target.dataset.axis) {
      var axis = target.dataset.axis === "preset" ? "variantPresets" : "variantLayouts";
      var key = target.dataset.key;
      var list = state[axis];
      var at = list.indexOf(key);
      if (at >= 0) list.splice(at, 1); else list.push(key);
      renderVariants();
      renderOutputs();
      return;
    }

    if (target.dataset.copy) {
      var which = target.dataset.copy === "cmd" ? "#out-cmd" : "#out-toml";
      navigator.clipboard.writeText($(which).textContent).then(function () {
        toast("Copiado.", "ok");
      }, function () {
        toast("O navegador bloqueou a area de transferencia.", "bad");
      });
      return;
    }

    // Galeria "Escolha um template": carrega o formato e abre o Passo 1.
    if (target.dataset.gallery !== undefined) {
      loadGallery(target.dataset.gallery);
      return;
    }

    var index = Number(target.dataset.i);
    var act = target.dataset.act;

    if (act === "up" && index > 0) {
      var above = state.zones[index - 1];
      state.zones[index - 1] = state.zones[index];
      state.zones[index] = above;
      // A legenda precisa continuar por ultimo; reordenar pode violar isso.
      var caps = state.zones.filter(function (z) { return z.kind === "captions"; });
      if (caps.length) {
        state.zones = state.zones.filter(function (z) { return z.kind !== "captions"; });
        state.zones.push(caps[0]);
      }
      state.previewMock = "";
    } else if (act === "down" && index < state.zones.length - 1) {
      var below = state.zones[index + 1];
      state.zones[index + 1] = state.zones[index];
      state.zones[index] = below;
      var caps2 = state.zones.filter(function (z) { return z.kind === "captions"; });
      if (caps2.length) {
        state.zones = state.zones.filter(function (z) { return z.kind !== "captions"; });
        state.zones.push(caps2[0]);
      }
      state.previewMock = "";
    } else if (act === "del") {
      state.zones.splice(index, 1);
      state.previewMock = "";
    } else if (act === "dup") {
      var copy = Object.assign({}, state.zones[index]);
      state.zones.splice(index + 1, 0, copy);
      state.previewMock = "";
    } else if ((act === "kind" || act === "fit") && target.dataset.value !== undefined) {
      // Chips de tipo/encaixe: o valor vem no data-value (clique), nao num
      // select. O change handler continua existindo para compatibilidade.
      var patch = {};
      patch[act] = target.dataset.value;
      updateZone(index, patch);
      if (act === "kind") state.previewMock = "";
    }

    renderAll();
  });

  // Os controles de faixa precisam de feedback ao vivo, por isso 'input' e nao
  // 'change': com 'change' a previa so atualizaria ao soltar o controle.
  document.addEventListener("input", function (event) {
    var target = event.target;
    var act = target.dataset.act;
    if (!act) return;
    var index = Number(target.dataset.i);

    if (act === "fraction") updateZone(index, { fraction: Number(target.value) / 100 });
    else if (act === "zoom") {
      updateZone(index, { zoom: Number(target.value) || 1 });
      paintZoneSliderLabel(target, (Number(target.value) || 1).toFixed(2) + "x");
    }
    else if (act === "panx" || act === "pany") {
      var patch = {};
      patch[act === "panx" ? "panX" : "panY"] = Number(target.value) / 100;
      updateZone(index, patch);
      paintZoneSliderLabel(target, Math.round(Number(target.value)) + "%");
    }
    else if (act === "frameAt") {
      updateZone(index, { frameAt: Number(target.value) });
      // Os dois sliders (Zonas e Mídia) espelham o mesmo estado.
      var label = Number(target.value).toFixed(1) + "s";
      Array.prototype.forEach.call(
        document.querySelectorAll("input[data-act='frameAt'][data-i='" + index + "']"),
        function (el) {
          if (el !== target) el.value = target.value;
          paintSlideFill(el);
          var head = el.closest(".field, .full");
          head = head ? head.querySelector(".mini-label span") : null;
          if (head) head.textContent = label;
        }
      );
      var box = document.querySelector("[data-pv-framebox='" + index + "'] strong");
      if (box) box.textContent = label;
    }
    else if (act === "radius") updateZone(index, { radius: Number(target.value) });
    else if (act === "marginTop") updateZone(index, { marginTop: Number(target.value) });
    else if (act === "marginBottom") updateZone(index, { marginBottom: Number(target.value) });
    else if (act === "marginLeft") updateZone(index, { marginLeft: Number(target.value) });
    else if (act === "marginRight") updateZone(index, { marginRight: Number(target.value) });
    else if (act === "source") updateZone(index, { source: target.value });
    else if (act === "color") updateZone(index, { color: target.value });

    renderPreview();
    renderGeometry();
    if (act === "fraction") {
      // Sem renderZones aqui: recriar o slider no meio do arrasto cancela o
      // gesto; os rótulos vão por paintFractionUI.
      paintFractionUI(index);
    } else if (act === "marginTop" || act === "marginBottom" ||
        act === "marginLeft" || act === "marginRight") {
      renderZones();
    }
    renderOutputs();
  });

  document.addEventListener("change", function (event) {
    var target = event.target;
    var act = target.dataset.act;
    // Os selects do Passo 1 (canvas/preset/layout) e os checkboxes nao tem
    // data-act: sem este guarda, os ramos por id abaixo nunca executavam.
    if (!act && !target.id) return;

    if (act === "kind") {
      var index = Number(target.dataset.i);
      var kind = target.value;
      updateZone(index, { kind: kind });
      renderAll();
    } else if (act === "fit") {
      updateZone(Number(target.dataset.i), { fit: target.value });
      renderAll();
    } else if (target.id === "tpl-canvas") {
      var parts = target.value.split("x");
      state.width = Number(parts[0]);
      state.height = Number(parts[1]);
      renderAll();
    } else if (target.id === "tpl-preset") {
      state.preset = target.value;
      renderPresetHint();
      renderAll();
    } else if (target.id === "tpl-layout") {
      state.layout = target.value;
      renderOutputs();
    } else if (target.id === "hl-on") {
      state.headlineOn = target.checked;
      $("#hl-fields").hidden = !target.checked;
      renderPreview();
      renderOutputs();
    } else if (target.id === "pb-on") {
      state.progressOn = target.checked;
      $("#pb-fields").hidden = !target.checked;
      renderPreview();
      renderOutputs();
    } else if (target.id === "show-guides") {
      $("#stage").classList.toggle("show-guides", target.checked);
    }
  });

  document.addEventListener("input", function (event) {
    var target = event.target;
    if (target.id === "tpl-name") {
      state.name = target.value;
      renderOutputs();
    } else if (target.id === "tpl-bg") {
      state.background = target.value;
      renderOutputs();
    } else if (target.id === "hl-secs") {
      state.headlineSeconds = Number(target.value);
      $("#hl-secs-hint").textContent = state.headlineSeconds.toFixed(1) + "s de headline";
    } else if (target.id === "hl-text") {
      state.headlineText = target.value;
      renderPreview();
      renderOutputs();
    } else if (target.id === "hl-size") {
      state.headlineSize = Number(target.value) || 100;
      $("#hl-size-hint").textContent = state.headlineSize + "px";
      renderPreview();
      renderOutputs();
    } else if (target.id === "hl-margin") {
      state.headlineMargin = Number(target.value);
      if (!Number.isFinite(state.headlineMargin)) state.headlineMargin = 60;
      $("#hl-margin-hint").textContent = state.headlineMargin + "px";
      renderPreview();
      renderOutputs();
    } else if (target.id === "rf-zoom") {
      state.reframeZoom = Number(target.value) || 1;
      $("#rf-zoom-hint").textContent = state.reframeZoom.toFixed(2) + "x";
      paintSlideFill(target);
      renderPreview();
      renderOutputs();
    } else if (target.id === "rf-panx") {
      state.reframePanX = Number(target.value);
      if (!Number.isFinite(state.reframePanX)) state.reframePanX = 0.5;
      $("#rf-panx-hint").textContent = Math.round(state.reframePanX * 100) + "%";
      paintSlideFill(target);
      renderPreview();
      renderOutputs();
    } else if (target.id === "rf-pany") {
      state.reframePanY = Number(target.value);
      if (!Number.isFinite(state.reframePanY)) state.reframePanY = 0.5;
      $("#rf-pany-hint").textContent = Math.round(state.reframePanY * 100) + "%";
      paintSlideFill(target);
      renderPreview();
      renderOutputs();
    } else if (target.id === "pb-height") {
      state.progressHeight = Number(target.value) || 10;
      renderPreview();
    } else if (target.id === "pb-color") {
      state.progressColor = target.value;
      renderPreview();
    } else if (target.id === "st-gap") {
      var gap = Number(target.value);
      $("#st-gap-hint").textContent = "Margem vertical de " + gap.toFixed(1) + "% em cada midia";
    } else if (target.id === "st-radius") {
      var radius = Number(target.value);
      $("#st-radius-hint").textContent = "Raio de " + radius.toFixed(1) + "% da largura (" +
        Math.round(radius * state.width / 100) + "px neste canvas)";
    }
  });

  function renderPresetHint() {
    var preset = PRESETS[state.preset] || PRESETS.karaoke;
    $("#preset-hint").textContent = preset.desc + " · " + preset.font + " " + preset.size +
      (preset.box ? " · caixa " + preset.box : " · contorno");
    paintPresetCombo();
  }

  // Vídeos de referência (só prévia): o render sempre usa o corte; um
  // arquivo aqui pinta a faixa de vídeo no palco para conferir o layout.
  function previewVideoFiles(fit) {
    var how = fit === "cover" ? "preenche e corta as sobras" : "encaixa inteiro com letterbox";
    if (!state.previewVideos.length) {
      return "<p class='hint' style='margin:0'>O corte selecionado, recortado para esta faixa " +
        "(" + how + "). Sem arquivo, a prévia mostra o esquema.</p>";
    }
    return state.previewVideos.map(function (f, i) {
      return "<div class='pv-card'>" +
        "<div class='pv-thumb'><video class='pv-thumb-video' src='" + f.url + "'" +
          " muted playsinline preload='metadata' data-pv-i='" + i + "'></video>" +
          "<span class='pv-dur' data-pv-dur='" + i + "'>…</span></div>" +
        "<div class='pv-meta'><span class='pv-name' title='" + esc(f.name) + "'>" + esc(f.name) + "</span>" +
          "<span class='pv-sub'>" + esc(f.type || "vídeo") + " · " + esc(fmtBytes(f.size)) + "</span></div>" +
        "<button type='button' class='btn pressable btn-sm btn-ghost' data-pv-del='" + i + "'" +
        " aria-label='Remover " + esc(f.name) + "'>✕</button></div>";
    }).join("");
  }

  function fmtBytes(bytes) {
    bytes = Number(bytes) || 0;
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
  }

  function fmtClock(seconds) {
    seconds = Math.max(0, Math.round(Number(seconds) || 0));
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }

  function addPreviewVideos(files) {
    var added = 0;
    Array.prototype.forEach.call(files || [], function (file) {
      if (state.previewVideos.length >= 5) return;
      if (!file.type || file.type.indexOf("video/") !== 0) return;
      try {
        state.previewVideos.push({
          name: file.name,
          url: URL.createObjectURL(file),
          size: file.size,
          type: (file.type.split("/")[1] || "vídeo").toUpperCase(),
        });
        added++;
      } catch (e) { /* sem object URL, sem prévia */ }
    });
    if (added) {
      renderMedia();
      renderPreview();
      toast(added + " vídeo(s) na prévia.", "ok");
    }
  }

  function clearPreviewVideos() {
    state.previewVideos.forEach(function (f) {
      try { URL.revokeObjectURL(f.url); } catch (e) {}
    });
    state.previewVideos = [];
  }

  // ---------- acoes ----------
  function addZone() {
    var hasVideo = state.zones.some(function (z) { return z.kind === "video"; });
    var kind = hasVideo ? "image" : "video";
    var insertAt = state.zones.length;
    for (var i = 0; i < state.zones.length; i++) {
      if (state.zones[i].kind === "captions") { insertAt = i; break; }
    }
    state.zones.splice(insertAt, 0, {
      kind: kind, fraction: 0.25, fit: "cover", frameAt: 0, source: "",
      marginTop: 1.2, marginBottom: 1.2, marginLeft: 3, marginRight: 3,
      radius: kind === "solid" ? 0 : 3.5, color: "black"
    });
    state.previewMock = "";
    renderAll();
    toast("Zona adicionada. Ajuste a fracao para fechar 100%.", "");
  }

  function balance() {
    var pixel = state.zones.filter(function (z) { return z.kind !== "captions"; });
    if (!pixel.length) return;
    // Redistribui proporcionalmente: dividir igualmente apagaria a intencao do
    // usuario (a zona de video quase nunca deve ter a mesma altura da de imagem).
    var total = pixel.reduce(function (s, z) { return s + z.fraction; }, 0);
    if (total <= 0) {
      pixel.forEach(function (z) { z.fraction = 1 / pixel.length; });
    } else {
      var factor = 1 / total;
      pixel.forEach(function (z) { z.fraction = round4(z.fraction * factor); });
      // A soma em ponto flutuante raramente fecha exata; o residuo vai para a
      // maior zona, onde e imperceptivel.
      var sum = pixel.reduce(function (s, z) { return s + z.fraction; }, 0);
      var biggest = pixel.reduce(function (a, b) { return a.fraction >= b.fraction ? a : b; });
      biggest.fraction = round4(biggest.fraction + (1 - sum));
    }
    state.previewMock = "";
    renderAll();
    toast("Fracoes balanceadas para 100%.", "ok");
  }

  function applyStyle() {
    var gap = Number($("#st-gap").value);
    var radius = Number($("#st-radius").value);
    state.zones.forEach(function (zone) {
      if (zone.kind === "captions") return;
      zone.marginTop = gap;
      zone.marginBottom = gap;
      if (zone.kind === "image" || zone.kind === "frame") zone.radius = radius;
    });
    renderAll();
    toast("Estilo aplicado a todas as midias.", "ok");
  }

  function addCaptionsZone() {
    if (state.zones.some(function (z) { return z.kind === "captions"; })) return;
    state.zones.push({
      kind: "captions", fraction: 0, fit: "cover", frameAt: 0, source: "",
      marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0,
      radius: 0, color: "black"
    });
  }

  function loadSplitCard() {
    state.name = "split-card";
    state.zones = [
      { kind: "video", fraction: 0.62, fit: "cover", frameAt: 0, source: "",
        marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0, radius: 0, color: "black" },
      { kind: "frame", fraction: 0.38, fit: "cover", frameAt: 0, source: "",
        marginTop: 1.2, marginBottom: 1.2, marginLeft: 3, marginRight: 3, radius: 3.5, color: "black" }
    ];
    state.preset = "social";
    state.variantPresets = [];
    state.variantLayouts = [];
    addCaptionsZone();
    $("#tpl-name").value = state.name;
    $("#tpl-preset").value = state.preset;
    $("#tpl-canvas").value = state.width + "x" + state.height;
    $("#hl-on").checked = false; $("#hl-fields").hidden = true;
    $("#pb-on").checked = false; $("#pb-fields").hidden = true;
    state.headlineOn = false; state.progressOn = false;
    state.headlineAlign = "center";
    state.captionTheme = "";
    state.tweetText = "";
    var phIdea = $("#ph-idea");
    if (phIdea) phIdea.value = "";
    var phCustom = $("#ph-custom");
    if (phCustom) phCustom.value = "";
    var phList = $("#phrase-list");
    if (phList) phList.innerHTML = "";
    state.phrases = [];
    renderProjectPhrases();
    clearPreviewVideos();
    state.headlineSize = 100;
    state.headlineMargin = 60;
    state.previewMock = "";
    $("#hl-size").value = 100; $("#hl-size-hint").textContent = "100px";
    $("#hl-margin").value = 60; $("#hl-margin-hint").textContent = "60px";
    state.reframeZoom = 1; state.reframePanX = 0.5; state.reframePanY = 0.5;
    $("#rf-zoom").value = 1; $("#rf-zoom-hint").textContent = "1.00x";
    $("#rf-panx").value = 0.5; $("#rf-panx-hint").textContent = "50%";
    $("#rf-pany").value = 0.5; $("#rf-pany-hint").textContent = "50%";
    ["rf-zoom", "rf-panx", "rf-pany"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) paintSlideFill(el);
    });
    renderPresetHint();
    renderAll();
    renderGallery();
    toast("Template split-card carregado.", "ok");
  }

  // ---------- galeria ----------
  var TWEET_DEFAULT = "Olha só o que rolou nesse servidor 👀";
  function tweetText() { return state.tweetText || TWEET_DEFAULT; }

  // Ícones do placeholder de mídia (traço, estilo lucide): câmera para
  // video/frame, moldura para imagem, quadrado para cor sólida.
  var GAL_ICONS = {
    video: "<path d='m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5'/><rect x='2' y='6' width='14' height='12' rx='2'/>",
    frame: "<path d='m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5'/><rect x='2' y='6' width='14' height='12' rx='2'/>",
    image: "<rect width='18' height='18' x='3' y='3' rx='2' ry='2'/><circle cx='9' cy='9' r='2'/><path d='m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21'/>",
    solid: "<rect x='4' y='4' width='16' height='16' rx='4'/>"
  };
  var GAL_SHORT = { video: "Vídeo", frame: "Frame", image: "Imagem", solid: "Cor" };

  // POV do card Meme: irmão das faixas, dentro da TELA. O motor queima o texto
  // em coordenadas do frame, não da zona, e o recorte do aparelho também é da
  // tela — como filho da faixa de vídeo o `top` era lido contra ela, e era
  // preciso converter a área segura em fração da faixa (a conta errava toda vez
  // que a zona de vídeo mudava de tamanho).
  function povOverlay() {
    return "<span class='gal-pov'>POV: Você usou o formato de meme e " +
      "VIRALIZOU com 3x mais!</span>";
  }

  function galleryCard(key, g) {
    // As frações das zonas mandam em tudo: cada zona vira UMA faixa, e as
    // decorações de formato (o POV) são sobrepostas na tela.
    var bands = g.mock === "meme" ? memeBands(g) :
      g.mock === "viral" ? viralBands(g) : g.zones.map(function (z) {
      if (z.mock === "tweet") return tweetBand(z);
      return mediaBand(z);
    }).join("");
    var overlay = g.mock === "meme" ? povOverlay() : "";

  // Placeholder genérico de mídia: ícone + nome + % na cor do tipo. A
  // legenda mora na faixa de vídeo: é ali que o motor a queima (margem do
  // preset, erguida até a borda da banda em template dividido).
  function mediaBand(z) {
      var meta = KINDS[z.kind] || { label: z.kind, color: "#888" };
      var icon = GAL_ICONS[z.kind] || GAL_ICONS.solid;
      var short = GAL_SHORT[z.kind] || meta.label;
      return "<div class='gal-band' style='flex-grow:" + (z.fraction * 100).toFixed(1) + "'>" +
        "<svg viewBox='0 0 24 24' fill='none' stroke='" + meta.color + "' stroke-width='1.8'" +
          " stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>" + icon + "</svg>" +
        "<span class='gal-kind'>" + short + "</span>" +
        "<span class='gal-pct'>" + Math.round(z.fraction * 100) + "%</span>" +
        (z.kind === "video" ? "<span class='gal-cap'>Legenda</span>" : "") + "</div>";
  }

  // Composição Meme: a zona de vídeo carrega o POV SOBREPOSTO — é assim que o
  // motor queima o texto e é assim que a prévia desenha. A zona de imagem vira
  // a barra de identidade escura.
  //
  // O POV era uma faixa irmã de 30% da zona de vídeo: como faixa ele consumia
  // altura, e o card passava a mostrar [30, 43, 26] onde as zonas declaradas são
  // [74, 26]. Sobreposto, o card passa a bater com o modelo. Quem emite a
  // sobreposição é `povOverlay`, na tela — aqui só saem as zonas.
  function memeBands(g) {
    var v = 0, img = 0;
    g.zones.forEach(function (z) {
      if (z.kind === "video") v = z.fraction * 100;
      if (z.kind === "image") img = z.fraction * 100;
    });
    return "<div class='gal-band' style='flex-grow:" + v.toFixed(1) + "'>" +
        "<svg viewBox='0 0 24 24' fill='none' stroke='#6366f1' stroke-width='1.8'" +
          " stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>" + GAL_ICONS.video + "</svg>" +
        "<span class='gal-kind'>Vídeo</span><span class='gal-cap'>Legenda</span></div>" +
      "<div class='gal-band gal-meme-id' style='flex-grow:" + img.toFixed(1) + "'>" +
        "<span class='gal-avatar gal-avatar--dark'>S</span>" +
        "<span class='gal-id-text'><strong>Seu Nome</strong><small>@seuhandle</small></span></div>";
  }
    return "<article class='gal-card'>" +
      "<div class='gal-prev' aria-hidden='true'><div class='gal-screen'>" + bands + overlay +
        "<span class='gal-notch'></span></div>" +
        "<span class='gal-home'></span></div>" +
      "<h3>" + g.label + "</h3><p>" + g.desc + "</p>" +
      "<button type='button' class='btn pressable btn-primary btn-sm' data-gallery='" + key + "'>" +
        "Usar este template <span aria-hidden='true'>→</span></button>" +
    "</article>";
  }

  // Composição Vídeo Viral: vídeo em cima, gancho na base DO VÍDEO e imagem
  // embaixo. O gancho é a legenda em contexto, e o motor a posiciona dentro da
  // faixa de vídeo (`captionMargin`); a prévia faz o mesmo.
  //
  // Ele era faixa irmã (`flex: none`): consumia 12% da altura e o card mostrava
  // [48, 12, 40] onde as zonas declaradas são [58, 42]. Sobreposto, o card passa
  // a bater com o modelo.
  function viralBands(g) {
    return g.zones.map(function (z) {
      var meta = KINDS[z.kind] || { label: z.kind, color: "#888" };
      var icon = GAL_ICONS[z.kind] || GAL_ICONS.solid;
      var short = GAL_SHORT[z.kind] || meta.label;
      var hook = z.kind === "video"
        ? "<span class='gal-hook'>" + esc(g.hook || "") + "</span>" : "";
      return "<div class='gal-band' style='flex-grow:" + (z.fraction * 100).toFixed(1) + "'>" +
        hook +
        "<svg viewBox='0 0 24 24' fill='none' stroke='" + meta.color + "' stroke-width='1.8'" +
          " stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>" + icon + "</svg>" +
        "<span class='gal-kind'>" + short + "</span>" +
        "<span class='gal-pct'>" + Math.round(z.fraction * 100) + "%</span></div>";
    }).join("");
  }

  // Faixa "print do tweet" (tema claro): avatar + nome + verificado +
  // handle + texto — o que a zona de imagem do formato X representa.
  function tweetBand(z) {
    return "<div class='gal-band gal-tweet-band' style='flex-grow:" + (z.fraction * 100).toFixed(1) + "'>" +
      "<div class='gal-tweet'>" +
        "<div class='gal-tweet-row'>" +
          "<span class='gal-avatar'>S</span>" +
          "<span class='gal-tweet-id'><strong>Seu Nome " +
            "<svg viewBox='0 0 24 24' width='10' height='10' aria-hidden='true'><path fill='#1d9bf0' d='M12 2l2.4 2.4 3.4-.5 1 3.3 3.2 1.2-1.4 3.1 1.4 3.1-3.2 1.2-1 3.3-3.4-.5L12 22l-2.4-2.4-3.4.5-1-3.3-3.2-1.2L3.4 12 2 8.9l3.2-1.2 1-3.3 3.4.5z'/><path fill='none' stroke='#fff' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round' d='M8.5 12.5l2.5 2.5 4.5-5'/></svg>" +
          "</strong><small>@seuhandle</small></span>" +
        "</div>" +
        "<p class='gal-tweet-body'>" + esc(tweetText()) + "</p>" +
      "</div>" +
      "<span class='gal-pct gal-pct--corner'>" + Math.round(z.fraction * 100) + "%</span></div>";
  }

  function renderGallery() {
    var grid = $("#gallery-grid");
    if (!grid) return;
    grid.innerHTML = Object.keys(GALLERY).map(function (key) {
      return galleryCard(key, GALLERY[key]);
    }).join("");
  }

  // "Usar" carrega nome, preset e zonas e leva ao Passo 1 (Aparência):
  // as etapas seguintes — Zonas, Frases, Mídia, Estilo, Variações, Gerar —
  // são a personalização, já preenchidas com o formato escolhido.
  function loadGallery(key) {
    var g = GALLERY[key];
    if (!g) return;
    state.name = g.name;
    state.preset = g.preset;
    state.layout = g.layout || "";
    state.zones = g.zones.map(function (z) {
      // "mock" é só visual da galeria: nunca entra no estado nem no .toml.
      var clean = Object.assign({}, z);
      delete clean.mock;
      return clean;
    });
    state.variantPresets = [];
    state.variantLayouts = [];
    state.previewMock = key;
    addCaptionsZone();
    $("#tpl-name").value = state.name;
    $("#tpl-preset").value = state.preset;
    $("#tpl-layout").value = state.layout;
    $("#tpl-canvas").value = state.width + "x" + state.height;
    // Formato limpo: headline/progresso anteriores não vazam para o novo
    // template (igual ao Carregar split-card).
    $("#hl-on").checked = false; $("#hl-fields").hidden = true;
    $("#pb-on").checked = false; $("#pb-fields").hidden = true;
    state.headlineOn = false; state.progressOn = false;
    state.headlineAlign = "center";
    state.captionTheme = "";
    state.tweetText = "";
    var phIdea = $("#ph-idea");
    if (phIdea) phIdea.value = "";
    var phCustom = $("#ph-custom");
    if (phCustom) phCustom.value = "";
    var phList = $("#phrase-list");
    if (phList) phList.innerHTML = "";
    state.phrases = [];
    renderProjectPhrases();
    clearPreviewVideos();
    state.headlineSize = 100;
    state.headlineMargin = 60;
    // previewMock NAO se zera aqui. Ele foi definido com a chave do card no
    // topo desta funcao, e e o que faz a previa desenhar as caracteristicas do
    // modelo (cartao de tweet, POV, gancho) em vez de retangulo generico.
    // Zera-lo neste ponto — no meio de um bloco que so devia limpar headline,
    // progresso e enquadramento — apagava a escolha 25 linhas depois de ela ter
    // sido feita, e a previa caia sempre no placeholder de midia.
    $("#hl-size").value = 100; $("#hl-size-hint").textContent = "100px";
    $("#hl-margin").value = 60; $("#hl-margin-hint").textContent = "60px";
    state.reframeZoom = 1; state.reframePanX = 0.5; state.reframePanY = 0.5;
    $("#rf-zoom").value = 1; $("#rf-zoom-hint").textContent = "1.00x";
    $("#rf-panx").value = 0.5; $("#rf-panx-hint").textContent = "50%";
    $("#rf-pany").value = 0.5; $("#rf-pany-hint").textContent = "50%";
    ["rf-zoom", "rf-panx", "rf-pany"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) paintSlideFill(el);
    });
    renderPresetHint();
    renderAll();
    renderGallery();
    paintSegs();
    paintPresetPreview();
    showStep(0);
    // O clique na galeria deve mostrar o resultado na hora: showStep(0) leva
    // ao Passo 1, mas rola para o topo — e a previa (sticky) sai da vista em
    // tela pequena. Traz a coluna de previa para a area visivel depois.
    var previewCol = document.querySelector(".preview-col");
    if (previewCol) previewCol.scrollIntoView({ behavior: "smooth", block: "start" });
    toast(g.label + " carregado — personalize nas etapas abaixo.", "ok");
  }

  // ---------- frases do tweet ----------
  function postJSON(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (res) { return res.json(); });
  }

  function setHeadlineText(text) {
    $("#hl-text").value = text;
    state.headlineText = text;
    renderPreview();
    renderOutputs();
  }

  function renderProjectPhrases() {
    var box = $("#project-phrases");
    var count = $("#phrase-count");
    var empty = $("#phrase-empty");
    if (!box) return;
    if (count) count.textContent = "(" + state.phrases.length + ")";
    if (empty) empty.hidden = state.phrases.length > 0;
    box.innerHTML = state.phrases.map(function (p, i) {
      return "<div class='ph-item'><span title='" + esc(p) + "'>" + esc(p) + "</span>" +
        "<button type='button' class='btn pressable btn-sm' data-ph-use='" + i + "'>Usar</button>" +
        "<button type='button' class='btn pressable btn-sm btn-ghost' data-ph-del='" + i + "'" +
        " aria-label='Remover frase'>✕</button></div>";
    }).join("");
  }

  function addProjectPhrase(text) {
    text = (text || "").trim().slice(0, 280);
    if (!text || state.phrases.indexOf(text) >= 0) return;
    state.phrases.push(text);
    renderProjectPhrases();
  }

  function generatePhrases(event) {
    if (event) event.preventDefault();
    var btn = $("#btn-phrases");
    var idea = ($("#ph-idea") && $("#ph-idea").value || "").trim();
    if (!idea) {
      toast("Descreva a ideia do vídeo primeiro.", "bad");
      return;
    }
    var count = Math.max(1, Math.min(10, Number($("#ph-count").value) || 5));
    if (btn) { btn.disabled = true; btn.textContent = "Gerando…"; }
    postJSON("/phrases", { idea: idea, count: count }).then(function (r) {
      if (btn) { btn.disabled = false; btn.textContent = "Gerar"; }
      if (r.error) {
        toast(String(r.error).slice(0, 120), "bad");
        return;
      }
      var list = $("#phrase-list");
      list.innerHTML = (r.phrases || []).map(function (p) {
        return "<button type='button' class='pick pressable' data-phrase='" + esc(p) + "'>" +
          esc(p) + "</button>";
      }).join("");
      if (!(r.phrases || []).length) toast("Nenhuma frase voltou.", "bad");
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = "Gerar"; }
      toast("Falha ao falar com o servidor.", "bad");
    });
  }

  function useCustomPhrase(event) {
    if (event) event.preventDefault();
    var text = ($("#ph-custom") && $("#ph-custom").value || "").trim().slice(0, 280);
    if (!text) {
      toast("Escreva a frase primeiro.", "bad");
      return;
    }
    setHeadlineText(text);
    state.tweetText = text;
    addProjectPhrase(text);
    renderGallery();
    toast("Frase aplicada ao headline e ao tweet.", "ok");
  }

  function download() {
    var problems = validate();
    if (problems.length) {
      toast("Corrija a validacao antes de baixar.", "bad");
      return;
    }
    var blob = new Blob([toToml()], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = state.name.trim() + ".toml";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("Baixado. Coloque em templates/ e rode o comando.", "ok");
  }

  // ---------- boot ----------
  function init() {
    $("#tpl-preset").innerHTML = Object.keys(PRESETS).sort().map(function (key) {
      return "<option value='" + key + "'>" + key + " — " + PRESETS[key].desc + "</option>";
    }).join("");
    $("#tpl-preset").value = state.preset;
    renderPresetHint();

    addCaptionsZone();

    $("#btn-add-zone").addEventListener("click", addZone);
    $("#btn-balance").addEventListener("click", balance);
    $("#btn-apply-style").addEventListener("click", applyStyle);
    $("#btn-prev").addEventListener("click", function () { showStep(state.step - 1); });
    $("#btn-next").addEventListener("click", function () { showStep(state.step + 1); });
    $("#btn-download").addEventListener("click", download);
    $("#btn-preset-split").addEventListener("click", loadSplitCard);
    $("#btn-phrases").addEventListener("click", generatePhrases);
    $("#btn-phrase-add").addEventListener("click", useCustomPhrase);
    $("#phrase-list").addEventListener("click", function (event) {
      var chip = event.target.closest("[data-phrase]");
      if (!chip) return;
      var text = chip.getAttribute("data-phrase") || "";
      setHeadlineText(text);
      addProjectPhrase(text);
      toast("Frase aplicada ao headline.", "ok");
    });
    var projectBox = $("#project-phrases");
    if (projectBox) {
      projectBox.addEventListener("click", function (event) {
        var use = event.target.closest("[data-ph-use]");
        var del = event.target.closest("[data-ph-del]");
        if (use) {
          var text = state.phrases[Number(use.getAttribute("data-ph-use"))] || "";
          if (text) {
            setHeadlineText(text);
            toast("Frase aplicada ao headline.", "ok");
          }
        } else if (del) {
          state.phrases.splice(Number(del.getAttribute("data-ph-del")), 1);
          renderProjectPhrases();
        }
      });
    }
    // "Salvar e continuar": avança o wizard; no último passo, baixa o .toml.
    $("#btn-save-continue").addEventListener("click", function () {
      if (state.step >= STEPS.length - 1) { download(); return; }
      showStep(state.step + 1);
    });

    $("#st-gap").dispatchEvent(new Event("input", { bubbles: true }));
    $("#st-radius").dispatchEvent(new Event("input", { bubbles: true }));
    $("#hl-secs").dispatchEvent(new Event("input", { bubbles: true }));
    $("#hl-size").dispatchEvent(new Event("input", { bubbles: true }));
    $("#hl-margin").dispatchEvent(new Event("input", { bubbles: true }));
    ["rf-zoom", "rf-panx", "rf-pany"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.dispatchEvent(new Event("input", { bubbles: true }));
    });

    showStep(0);
    renderGallery();
    renderAll();
  }

  // ---------- controles modernos (segmentados + previa) ----------
  // Os selects nativos continuam sendo a fonte da verdade; os segmentados
  // só espelham o valor e disparam change, então delegation e renderAll
  // seguem funcionando sem alteração.
  function paintSeg(segId, selectId) {
    var seg = document.getElementById(segId);
    var sel = document.getElementById(selectId);
    if (!seg || !sel) return;
    Array.prototype.forEach.call(seg.querySelectorAll("[data-value]"), function (btn) {
      var on = btn.getAttribute("data-value") === sel.value;
      btn.setAttribute("aria-checked", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
    });
  }

  function paintSegs() {
    paintSeg("canvas-seg", "tpl-canvas");
    paintSeg("layout-seg", "tpl-layout");
    paintAlignSeg();
    paintThemeSeg();
  }

  // O alinhamento mora no estado (sem select nativo): espelha o mesmo
  // comportamento visual dos segmentados de canvas/layout.
  function paintAlignSeg() {
    var seg = document.getElementById("hl-align-seg");
    if (!seg) return;
    Array.prototype.forEach.call(seg.querySelectorAll("[data-value]"), function (btn) {
      var on = btn.getAttribute("data-value") === (state.headlineAlign || "center");
      btn.setAttribute("aria-checked", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
    });
  }

  function setSegValue(selectId, value) {
    var sel = document.getElementById(selectId);
    if (!sel) return;
    sel.value = value;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    paintSegs();
  }

  function segKeys(seg, event) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    var btns = Array.prototype.slice.call(seg.querySelectorAll("[data-value]"));
    var i = btns.indexOf(document.activeElement);
    if (i < 0) i = 0;
    i = (i + (event.key === "ArrowRight" ? 1 : -1) + btns.length) % btns.length;
    btns[i].focus();
    btns[i].click();
  }

  function paintPresetPreview() {
    var text = document.getElementById("preset-prev-text");
    var sub = document.getElementById("preset-prev-sub");
    if (!text) return;
    var preset = PRESETS[state.preset] || PRESETS.karaoke;
    text.style.fontFamily = "'" + preset.font + "', sans-serif";
    text.style.color = preset.color;
    text.style.background = preset.box || "transparent";
    text.style.display = "inline-block";
    text.style.padding = preset.box ? "4px 14px" : "0";
    text.style.borderRadius = preset.box ? "6px" : "0";
    text.style.textShadow = preset.box ? "none" : "0 0 12px " + preset.accent;
    if (sub) {
      sub.innerHTML = "";
      sub.appendChild(document.createTextNode(preset.font + " · " + preset.size + "px · destaque "));
      var sw = document.createElement("span");
      sw.style.cssText = "display:inline-block;width:11px;height:11px;border-radius:3px;vertical-align:-1px;background:" + preset.accent;
      sub.appendChild(sw);
    }
  }

  // O tema mora no estado (sem select nativo). Reclicar o ativo limpa e
  // volta às cores do preset.
  function paintThemeSeg() {
    var seg = document.getElementById("theme-seg");
    if (!seg) return;
    Array.prototype.forEach.call(seg.querySelectorAll("[data-value]"), function (btn) {
      var on = btn.getAttribute("data-value") === state.captionTheme && state.captionTheme !== "";
      btn.setAttribute("aria-checked", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
    });
  }

  // ---------- combobox de preset ----------
  // Botão com amostra viva + painel com busca. Escreve no select nativo e
  // dispara change, então hint, prévia, renderAll e delegation não mudam.
  var comboActive = null;

  function presetKeys(filter) {
    var q = (filter || "").toLowerCase();
    return Object.keys(PRESETS).sort().filter(function (key) {
      return !q || key.indexOf(q) >= 0 ||
        PRESETS[key].desc.toLowerCase().indexOf(q) >= 0;
    });
  }

  function paintPresetCombo() {
    var btn = document.getElementById("preset-combo-btn");
    if (!btn) return;
    var preset = PRESETS[state.preset] || PRESETS.karaoke;
    var sw = document.getElementById("preset-combo-swatch");
    if (sw) {
      sw.style.background = preset.box || "rgba(255,255,255,0.05)";
      sw.style.color = preset.color;
      sw.style.borderColor = preset.accent;
      sw.style.fontFamily = "'" + preset.font + "', sans-serif";
      sw.style.textShadow = preset.box ? "none" : "0 0 8px " + preset.accent;
    }
    var name = document.getElementById("preset-combo-name");
    if (name) name.textContent = state.preset;
    var desc = document.getElementById("preset-combo-desc");
    if (desc) desc.textContent = preset.desc;
    renderPresetList();
  }

  function renderPresetList() {
    var list = document.getElementById("preset-combo-list");
    if (!list) return;
    var search = document.getElementById("preset-combo-search");
    var keys = presetKeys(search ? search.value : "");
    if (!keys.length) {
      list.innerHTML = "<div class='combo-empty'>Nenhum preset para esta busca.</div>";
      return;
    }
    list.innerHTML = keys.map(function (key) {
      var p = PRESETS[key];
      var on = key === state.preset;
      return "<div class='combo-item pressable' role='option' data-value='" + key + "'" +
        " aria-selected='" + (on ? "true" : "false") + "'" +
        (key === comboActive ? " data-active='true'" : "") + ">" +
        "<span class='combo-dot' style='background:" + (p.box || "rgba(255,255,255,0.05)") +
          ";border-color:" + p.accent + "'></span>" +
        "<span class='combo-item-text'><strong>" + key + "</strong><small>" + p.desc + "</small></span>" +
        (on ? "<span class='combo-check' aria-hidden='true'>✓</span>" : "") + "</div>";
    }).join("");
  }

  function markComboActive() {
    var list = document.getElementById("preset-combo-list");
    if (!list) return;
    Array.prototype.forEach.call(list.querySelectorAll(".combo-item"), function (item) {
      if (item.getAttribute("data-value") === comboActive) {
        item.setAttribute("data-active", "true");
        item.scrollIntoView({ block: "nearest" });
      } else {
        item.removeAttribute("data-active");
      }
    });
  }

  function setComboOpen(open, refocus) {
    var btn = document.getElementById("preset-combo-btn");
    var panel = document.getElementById("preset-combo-panel");
    if (!btn || !panel) return;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    panel.hidden = !open;
    if (open) {
      comboActive = state.preset;
      var search = document.getElementById("preset-combo-search");
      if (search) {
        search.value = "";
        renderPresetList();
        markComboActive();
        search.focus();
      }
    } else if (refocus) {
      btn.focus();
    }
  }

  function selectPresetCombo(key) {
    var sel = document.getElementById("tpl-preset");
    if (!sel || !PRESETS[key]) return;
    sel.value = key;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    setComboOpen(false, true);
  }

  (function initModernControls() {
    ["canvas-seg", "layout-seg", "hl-align-seg", "theme-seg"].forEach(function (id) {
      var seg = document.getElementById(id);
      if (!seg) return;
      seg.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-value]");
        if (!btn) return;
        if (seg.id === "hl-align-seg") {
          state.headlineAlign = btn.getAttribute("data-value") || "center";
          paintSegs();
          renderPreview();
          renderOutputs();
          return;
        }
        if (seg.id === "theme-seg") {
          var value = btn.getAttribute("data-value") || "";
          state.captionTheme = (value === state.captionTheme) ? "" : value;
          paintSegs();
          renderPreview();
          renderOutputs();
          return;
        }
        setSegValue(seg.id === "canvas-seg" ? "tpl-canvas" : "tpl-layout",
          btn.getAttribute("data-value") || "");
      });
      seg.addEventListener("keydown", function (event) { segKeys(seg, event); });
    });
    // Prévia por plataforma: só troca a moldura do palco (o render é o mesmo).
    var pfSeg = document.getElementById("platform-seg");
    if (pfSeg) {
      pfSeg.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-value]");
        if (!btn) return;
        var v = btn.getAttribute("data-value") || "tiktok";
        var stage = document.getElementById("stage");
        var name = document.getElementById("platform-name");
        if (stage) stage.setAttribute("data-platform", v);
        if (name) name.textContent = v === "instagram" ? "Instagram" : "TikTok";
        Array.prototype.forEach.call(pfSeg.querySelectorAll("[data-value]"), function (b) {
          var on = b === btn;
          b.setAttribute("aria-checked", on ? "true" : "false");
          b.tabIndex = on ? 0 : -1;
        });
      });
      pfSeg.addEventListener("keydown", function (event) { segKeys(pfSeg, event); });
    }
    // Combobox de preset: abre/fecha, busca, setas + Enter, clique fora.
    var comboBtn = document.getElementById("preset-combo-btn");
    var comboPanel = document.getElementById("preset-combo-panel");
    var comboSearch = document.getElementById("preset-combo-search");
    var comboList = document.getElementById("preset-combo-list");
    if (comboBtn && comboPanel) {
      comboBtn.addEventListener("click", function () {
        setComboOpen(comboPanel.hidden, false);
      });
      comboBtn.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setComboOpen(true, false);
        }
      });
    }
    if (comboSearch) {
      comboSearch.addEventListener("input", function () {
        renderPresetList();
        var first = comboList ? comboList.querySelector(".combo-item") : null;
        comboActive = first ? first.getAttribute("data-value") : null;
        markComboActive();
      });
      comboSearch.addEventListener("keydown", function (event) {
        var items = comboList ?
          Array.prototype.slice.call(comboList.querySelectorAll(".combo-item")) : [];
        if (event.key === "Escape") {
          event.preventDefault();
          setComboOpen(false, true);
        } else if (event.key === "Enter") {
          event.preventDefault();
          if (comboActive) selectPresetCombo(comboActive);
        } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (!items.length) return;
          var i = items.indexOf(comboList.querySelector("[data-active='true']"));
          if (i < 0) i = items.indexOf(comboList.querySelector("[aria-selected='true']"));
          i = (i + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
          comboActive = items[i].getAttribute("data-value");
          markComboActive();
        }
      });
    }
    if (comboList) {
      comboList.addEventListener("click", function (event) {
        var item = event.target.closest(".combo-item");
        if (item) selectPresetCombo(item.getAttribute("data-value"));
      });
    }
    document.addEventListener("click", function (event) {
      if (!comboPanel || comboPanel.hidden) return;
      if (event.target.closest && event.target.closest("#preset-combo")) return;
      setComboOpen(false, false);
    });
    // Upload de referência: o botão contém o input; o change é delegado
    // porque o Passo 4 é recriado a cada render.
    document.addEventListener("click", function (event) {
      if (!event.target.closest) return;
      // Painel dobrável de ajuste do frame.
      var fe = event.target.closest("[data-frame-edit]");
      if (fe) {
        var panel = document.querySelector(
          "[data-frame-panel='" + fe.getAttribute("data-frame-edit") + "']");
        if (panel) {
          panel.hidden = !panel.hidden;
          fe.setAttribute("aria-expanded", String(!panel.hidden));
          fe.textContent = panel.hidden ? "Ajustar frame" : "Fechar ajuste";
        }
        return;
      }
      var up = event.target.closest(".pv-upload-btn");
      if (up) {
        var inp = up.querySelector(".pv-upload-input");
        if (inp) inp.click();
        return;
      }
      var del = event.target.closest("[data-pv-del]");
      if (del) {
        var at = Number(del.getAttribute("data-pv-del"));
        var gone = state.previewVideos.splice(at, 1)[0];
        if (gone) { try { URL.revokeObjectURL(gone.url); } catch (e) {} }
        renderMedia();
        renderPreview();
        return;
      }
      // Toca/pausa a miniatura do card (mudo, só conferência visual).
      var thumb = event.target.closest(".pv-thumb-video");
      if (thumb) {
        if (thumb.paused) { thumb.play().catch(function () {}); }
        else { thumb.pause(); }
      }
    });
    document.addEventListener("change", function (event) {
      var t = event.target;
      if (t.classList && t.classList.contains("pv-upload-input")) {
        addPreviewVideos(t.files);
        t.value = "";
      }
    });
    // Duração da miniatura (loadedmetadata não borbulha: captura).
    document.addEventListener("loadedmetadata", function (event) {
      var v = event.target;
      if (!v.classList || !v.classList.contains("pv-thumb-video")) return;
      var thumb = v.closest(".pv-thumb");
      var badge = thumb ? thumb.querySelector("[data-pv-dur]") : null;
      if (badge && isFinite(v.duration)) badge.textContent = fmtClock(v.duration);
    }, true);
    // Setas nos chips das zonas (delegado: as zonas sao recriadas a cada render).
    document.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      if (!event.target.closest) return;
      var group = event.target.closest(".chips");
      if (!group) return;
      event.preventDefault();
      var btns = Array.prototype.slice.call(group.querySelectorAll(".chip"));
      var i = btns.indexOf(document.activeElement);
      if (i < 0) return;
      i = (i + (event.key === "ArrowRight" ? 1 : -1) + btns.length) % btns.length;
      btns[i].focus();
      btns[i].click();
    });
    // renderAll() e loadSplitCard() reescrevem os selects: repinta depois.
    document.addEventListener("change", function (event) {
      if (!event.target.id) return;
      if (event.target.id === "tpl-canvas" || event.target.id === "tpl-layout") paintSegs();
      if (event.target.id === "tpl-preset") paintPresetPreview();
    });
    paintSegs();
    paintPresetPreview();
  })();

  init();
})();