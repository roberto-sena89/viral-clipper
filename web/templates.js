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
    text:     { label: "Texto",    color: "#38bdf8" },
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
      desc: "Faixa de texto POV em fundo preto no topo, vídeo reduzido e barra de identidade embaixo.",
      zones: [
        // A faixa de texto vem PRIMEIRO e fora do video: ela e altura propria, e
        // por isso o video encolhe para 58%. Antes o POV era sobreposto ao video
        // (74%) e so existia como desenho na pagina — o render nao o queimava.
        { kind: "text", fraction: 0.16, fit: "cover", frameAt: 0, source: "",
          marginTop: 0.8, marginBottom: 0.8, marginLeft: 5, marginRight: 5, radius: 0, color: "black",
          text: "POV: Você usou o formato de meme e VIRALIZOU com 3x mais!",
          textSize: 3.8, textColor: "#ffffff", textAlign: "center", textValign: "middle",
          textBold: true, textUppercase: false, textOutline: 0, textOff: { x: 0, y: 0 } },
        { kind: "video", fraction: 0.58, fit: "cover", frameAt: 0, source: "",
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
    // Texto do tweet no formato X (vazio = texto de exemplo do mock). Escrito
    // por DUAS portas: o passo Aparencia (campo proprio) e o passo Frases, onde
    // "escrever frase" vira headline + texto do tweet. As duas leem o mesmo
    // estado, e paintTweetFields() repinta os campos quando a outra escreve.
    tweetText: "",
    // Titulo e @handle do cartao (vazio = exemplo do modelo).
    tweetName: "",
    tweetHandle: "",
    // Posicao fina de cada item do cartao, em px do QUADRO (negativo sobe e vai
    // para a esquerda). UMA fonte para os seis sliders do passo Aparencia e para o
    // arraste na previa: os dois escrevem aqui, e o `paintTweetFields` devolve o
    // numero para os campos. `body` so existe no formato X (o Meme nao desenha
    // texto de tweet); avatar e titulo valem nos dois.
    tweetOffset: {
      avatar: { x: 0, y: 0 },
      name: { x: 0, y: 0 },
      body: { x: 0, y: 0 }
    },
    // Foto do avatar do cartao: SO previa. Guarda o object URL do arquivo
    // escolhido no computador — mesma ideia dos `previewVideos` e pelo mesmo
    // motivo: o objeto vive na sessao da pagina e o arquivo nao sobe para
    // servidor nenhum. Nao e data URL (o .toml e o estado da pagina nao devem
    // carregar megabyte de base64) e nao e caminho de disco (o navegador nao
    // le o disco). Vazio = a letra do titulo.
    tweetAvatar: "",
    tweetAvatarName: "",
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

  // Tweet da prévia no formato X: mesmo componente do mock da galeria, com o
  // título, o @handle e o texto do estado (passo Aparencia) e o ajuste fino de
  // posição de cada item (arraste na prévia ou os sliders H/V do passo).
  function tweetMock() {
    var tweet = document.createElement("div");
    tweet.className = "pv-tweet";
    paintTweetOffsets(tweet, ["avatar", "name", "body"]);
    tweet.innerHTML =
      "<div class='pv-tweet-row'>" + avatarMarkup("pv") +
      "<span class='pv-id' data-edit='name'><strong data-txt='name'>" +
        esc(tweetName()) + " " + VERIFIED_BADGE + "</strong>" +
      "<small data-txt='handle'>" + esc(tweetHandle()) + "</small></span></div>" +
      "<p class='pv-body' data-edit='body' data-txt='text'>" + esc(tweetText()) + "</p>";
    return tweet;
  }

  // Avatar do perfil, em QUALQUER formato que desenhe o cartao (X e Meme, na
  // janela e na vitrine): a foto escolhida no passo Aparencia, se houver; senao a
  // letra do titulo. `prefix` e "pv" (janela) ou "gal" (vitrine) — os dois
  // desenham o mesmo perfil, com nomes de classe diferentes. A letra continua no
  // HTML mesmo com a imagem: ela e o estado do modelo e o que reaparece quando o
  // arquivo e removido; o CSS esconde (`color: transparent`), nao apaga.
  function avatarMarkup(prefix, extra) {
    var base = prefix === "gal" ? "gal-avatar" : "pv-avatar";
    var cls = base + (extra ? " " + extra : "");
    // `data-edit` so na janela: e o que o arraste pega. Na vitrine o cartao e
    // esquema, nao area de edicao.
    var drag = prefix === "gal" ? "" : " data-edit='avatar'";
    if (!state.tweetAvatar) {
      return "<span class='" + cls + "'" + drag + ">" + esc(tweetAvatarLetter()) + "</span>";
    }
    var withImage = prefix === "gal" ? "gal-avatar--img" : "pv-avatar--img";
    return "<span class='" + cls + " " + withImage + "'" + drag + " role='img'" +
      " aria-label='Foto do avatar'" +
      " style='background-image:url(" + esc(state.tweetAvatar) + ")'>" +
      esc(tweetAvatarLetter()) + "</span>";
  }

  // px do QUADRO -> cqh do palco (mesma conversao do headline). Zero nao escreve
  // unidade nenhuma: a posicao fica com o padrao do modelo.
  function twOffsetY(px) {
    var value = Number(px) || 0;
    if (!value) return "0px";
    return (value / state.height * 100).toFixed(3) + "cqh";
  }

  // px do QUADRO -> numero puro: o CSS multiplica por `--framepx` (1 px do quadro
  // em px de TELA, medido em `paintFrameScale`).
  function twOffsetX(px) {
    return String(Math.round(Number(px) || 0));
  }

  function clampOffset(px) {
    var value = Math.round(Number(px) || 0);
    return Math.max(-TW_OFF_MAX, Math.min(TW_OFF_MAX, value));
  }

  // As seis variaveis do cartao de uma vez: mudar so uma deixaria o item com o
  // X antigo quando o outro eixo chega novo.
  function paintTweetOffsets(host, items) {
    items.forEach(function (item) {
      var off = tweetOffsetOf(item);
      host.style.setProperty("--tw-off-x-" + item, twOffsetX(off.x));
      host.style.setProperty("--tw-off-y-" + item, twOffsetY(off.y));
    });
  }

  // 1 px do QUADRO em px de TELA. O CSS precisa disso para o X: `cqw` nao serve
  // dentro do cartao (ele e `container-type: inline-size`, e mediria o conteudo do
  // cartao, nao a largura do quadro — o mesmo numero valeria outra coisa em cada
  // formato). O Y continua em `cqh` porque a tela do palco e `container-type: size`.
  // Reescrito no render E no resize: encolher a janela com um offset aplicado
  // deixaria o item fora de escala se o numero ficasse congelado.
  function paintFrameScale() {
    var screen = $(".phone-screen");
    if (!screen || !state.width) return;
    screen.style.setProperty("--framepx", (screen.clientWidth / state.width).toFixed(5) + "px");
  }

  // ---------- EDITOR DA PREVIA (passo Aparencia) ----------
  // Arrastar o item na janela solta ele em qualquer ponto; clique duplo edita o
  // texto ali mesmo; as setas ajustam de 1 em 1 px (com Shift, 10); o ponto cruz
  // marca o centro do item e prende no centro da tela.
  //
  // Duas regras que o codigo respeita para nao perder o trabalho de quem edita:
  //   * durante o ARRASTE nada e redesenhado — so as variaveis `--tw-off-*` do
  //     cartao mudam. Um `renderPreview()` aqui trocaria o no que esta com a
  //     captura do ponteiro, e o arraste morreria no primeiro pixel;
  //   * durante a DIGITACAO tambem nao: o cursor iria embora a cada tecla. Os
  //     campos do passo seguem por `paintTweetFields` (que so escreve em inputs) e
  //     a vitrine por `renderGallery` — nenhum dos dois toca no canvas.
  var TW_OFF_MAX = 480;    // px do quadro: o limite do slider e do arraste
  var TW_SNAP_PX = 6;      // px de TELA: distancia em que o centro prende na guia
  var TW_NUDGE_PX = 1;     // setas: 1 px do quadro (com Shift, 10)
  var TW_NUDGE_FAST = 10;
  // `pov` entra na mesma lista dos itens do cartao: o POV e um texto de uma zona
  // e o cartao e um texto de uma imagem, mas para quem edita os dois sao a mesma
  // coisa — arrastar, empurrar com as setas e prender no ponto cruz.
  var TW_ITEMS = ["avatar", "name", "body", "pov"];

  // Texto que da para editar NO LUGAR: onde mora no estado e o limite de
  // caracteres. Os limites sao os do formulario (o `maxlength` do textarea);
  // `contenteditable` nao tem `maxlength`, entao quem corta e `inlineValue`.
  //
  // `key` le e escreve `state[key]`; `get`/`set` existem para o texto que NAO mora
  // no estado de topo — o do POV mora na zona, porque e a zona que o motor
  // queima. Sem esta indirecao, o texto editado na previa nao chegaria ao .toml.
  var TW_INLINE = {
    name: { key: "tweetName", max: 60, single: true },
    handle: { key: "tweetHandle", max: 40, single: true },
    text: { key: "tweetText", max: 280, single: false },
    pov: {
      max: 160, single: false,
      get: function () { var z = povZone(); return z ? (z.text || "") : ""; },
      set: function (value) { var z = povZone(); if (z) z.text = value; }
    }
  };

  function inlineGet(spec) {
    if (spec.get) return spec.get();
    return state[spec.key] || "";
  }

  function inlineSet(spec, value) {
    if (spec.set) spec.set(value);
    else state[spec.key] = value;
  }

  // A zona de texto do formato Meme. E o alvo de tudo que se refere ao POV: o
  // arraste, os campos e o proprio desenho. Devolver a ZONA (e nao o no do DOM)
  // e o que mantem uma fonte so — o canvas e reescrito a cada render e o no
  // morreria junto.
  function povZone() {
    for (var i = 0; i < state.zones.length; i++) {
      if (state.zones[i].kind === "text") return state.zones[i];
    }
    return null;
  }

  var twSelected = "";   // item selecionado na janela: "avatar" | "name" | "body" | "pov"
  var twDrag = null;     // arraste em curso
  var twEditBase = "";   // texto de antes da edicao (o Escape devolve)

  function tweetOffsetOf(item) {
    // O POV guarda o deslocamento NA PROPRIA ZONA (o `text_dx`/`text_dy` do
    // motor sai daqui). Devolver o objeto aninhado e nao uma copia e o que faz
    // o arraste, as setas e os sliders escreverem no mesmo lugar — todos mutam
    // o retorno. `updateZone` troca o objeto da zona por um raso, mas o aninhado
    // sobrevive, entao a referencia continua valendo.
    if (item === "pov") {
      var zone = povZone();
      if (!zone) return { x: 0, y: 0 };
      if (!zone.textOff) zone.textOff = { x: 0, y: 0 };
      return zone.textOff;
    }
    if (!state.tweetOffset[item]) state.tweetOffset[item] = { x: 0, y: 0 };
    return state.tweetOffset[item];
  }

  function tweetHost() { return $(".pv-tweet") || $(".pv-idbar"); }

  function tweetItemNode(item) {
    return document.querySelector("#canvas [data-edit='" + item + "']");
  }

  // Onde as variaveis `--tw-off-*` do item sao escritas. No cartao e o HOST (as
  // regras de transform vivem em `.pv-avatar`/`.pv-id`/`.pv-body`, que sao filhos
  // dele); no POV o proprio no, porque nao ha cartao em volta.
  function offsetHostFor(item) {
    return item === "pov" ? tweetItemNode("pov") : tweetHost();
  }

  function frameScale() {
    var screen = $(".phone-screen");
    var scale = screen && screen.clientWidth ? screen.clientWidth / state.width : 1;
    return scale || 1;
  }

  // Escala do eixo VERTICAL: o X do arraste e em px do quadro convertido pela
  // LARGURA (é o que o CSS multiplica por `--framepx`), mas o V vira `cqh` e se
  // resolve contra a ALTURA do palco. Com uma escala só, o eixo vertical andava
  // na proporção errada (o palco tem moldura: 280/1080 ≠ 589/1920).
  function frameScaleY() {
    var screen = $(".phone-screen");
    var scale = screen && screen.clientHeight ? screen.clientHeight / state.height : 1;
    return scale || 1;
  }

  // Escreve SO as variaveis do item — e o que o arraste faz a cada movimento.
  function applyTweetOffset(item) {
    var host = offsetHostFor(item);
    if (!host) return;
    var off = tweetOffsetOf(item);
    host.style.setProperty("--tw-off-x-" + item, twOffsetX(off.x));
    host.style.setProperty("--tw-off-y-" + item, twOffsetY(off.y));
  }

  // Selecao + ponto cruz. A selecao e guardada pela CHAVE, nao pelo no: o canvas e
  // reescrito a cada render, e um no guardado morreria junto com o desenho.
  function paintTweetSelection() {
    $$("#canvas [data-edit]").forEach(function (node) {
      node.classList.toggle("is-selected", node.getAttribute("data-edit") === twSelected);
    });
    if (!twSelected) return hideEditGuides();
    showEditGuides(tweetItemNode(twSelected));
  }

  function selectTweetItem(item) {
    twSelected = item || "";
    // O campo que estava com o foco fica com as SETAS (os sliders usam as mesmas
    // teclas): tirar o foco e o que entrega as setas para o item selecionado.
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    paintTweetSelection();
  }

  function hideEditGuides() {
    var guides = $("#edit-guides");
    if (guides) guides.hidden = true;
  }

  // O ponto cruz segue o CENTRO do item: "esta no centro" passa a ser uma
  // afirmacao sobre o que o olho ve, e nao sobre um numero do estado.
  function showEditGuides(node) {
    var screen = $(".phone-screen");
    var guides = $("#edit-guides");
    if (!screen || !guides || !node || !node.getBoundingClientRect) return hideEditGuides();
    var s = screen.getBoundingClientRect();
    var box = node.getBoundingClientRect();
    if (!box.width && !box.height) return hideEditGuides();
    var cx = box.left + box.width / 2 - s.left;
    var cy = box.top + box.height / 2 - s.top;
    var off = twSelected ? tweetOffsetOf(twSelected) : { x: 0, y: 0 };
    var v = guides.querySelector(".eg-v"), h = guides.querySelector(".eg-h");
    var dot = guides.querySelector(".eg-dot"), read = $("#eg-readout");
    guides.hidden = false;
    // Cada linha recebe SÓ o eixo que a posiciona: `.eg-v` estica de topo a base
    // (escrever `top` nela cortaria a linha na metade) e `.eg-h` de borda a borda
    // (escrever `left` a transformaria no pedaço da direita). O ponto e a leitura
    // ficam no cruzamento, com os dois eixos.
    if (v) v.style.left = cx.toFixed(1) + "px";
    if (h) h.style.top = cy.toFixed(1) + "px";
    [dot, read].forEach(function (el) {
      if (!el) return;
      el.style.left = cx.toFixed(1) + "px";
      el.style.top = cy.toFixed(1) + "px";
    });
    if (read) read.textContent = "H " + off.x + " · V " + off.y + " px";
    // Linha quente = o item esta EXATAMENTE no eixo central da tela (o snap leva
    // ate la; a cor confirma sem precisar ler o numero).
    if (v) v.classList.toggle("is-center", Math.abs(cx - s.width / 2) <= 1);
    if (h) h.classList.toggle("is-center", Math.abs(cy - s.height / 2) <= 1);
  }

  // Snap: chegando perto do centro da tela, o centro do item prende nele. A caixa
  // e lida DEPOIS do deslocamento cru, entao os dois eixos se resolvem na mesma
  // passada — e o snap de um nao desfaz o do outro.
  //
  // No eixo V o alvo nao e so o centro da tela: a faixa que CONTEM o item tambem
  // oferece o proprio centro, e vence o mais proximo. E o alinhamento que importa
  // num banner — "no meio da faixa de texto" — e sem ele um POV numa faixa do topo
  // so poderia ser centrado na tela inteira, que ali quer dizer fora do lugar.
  function snapTweetOffset(drag) {
    var off = tweetOffsetOf(drag.item);
    var box = drag.node.getBoundingClientRect();
    var centreY = box.top + box.height / 2 - drag.screen.top;
    var dx = drag.screen.width / 2 - (box.left + box.width / 2 - drag.screen.left);
    if (Math.abs(dx) <= TW_SNAP_PX) off.x = clampOffset(off.x + dx / drag.scaleX);
    var targets = [drag.screen.height / 2];
    var band = drag.node.closest ? drag.node.closest(".band") : null;
    if (band) {
      var rect = band.getBoundingClientRect();
      targets.push(rect.top + rect.height / 2 - drag.screen.top);
    }
    var best = null;
    targets.forEach(function (target) {
      var delta = target - centreY;
      if (Math.abs(delta) <= TW_SNAP_PX && (best === null || Math.abs(delta) < Math.abs(best))) {
        best = delta;
      }
    });
    if (best !== null) off.y = clampOffset(off.y + best / drag.scaleY);
    applyTweetOffset(drag.item);
  }

  // ---------- arraste ----------
  function startTweetDrag(event) {
    if (event.button !== 0) return;
    var node = event.target.closest ? event.target.closest("#canvas [data-edit]") : null;
    if (!node) { selectTweetItem(""); return; }
    // Texto em edicao: o ponteiro pertence ao cursor (colocar o caret, selecionar
    // palavra). Sem esta guarda o primeiro clique no texto moveria o item.
    var leaf = event.target.closest("[data-txt]");
    if (leaf && leaf.isContentEditable) return;
    var item = node.getAttribute("data-edit");
    selectTweetItem(item);
    twDrag = {
      item: item, node: node, pointerId: event.pointerId,
      scaleX: frameScale(), scaleY: frameScaleY(),
      startX: event.clientX, startY: event.clientY,
      baseX: tweetOffsetOf(item).x, baseY: tweetOffsetOf(item).y,
      screen: $(".phone-screen").getBoundingClientRect(), moved: false
    };
    if (node.setPointerCapture) {
      try { node.setPointerCapture(event.pointerId); } catch (e) {}
    }
    event.preventDefault();
  }

  function moveTweetDrag(event) {
    if (!twDrag || event.pointerId !== twDrag.pointerId) return;
    if (!twDrag.moved) {
      // 3 px de tela antes de considerar arraste: um clique para selecionar nao
      // pode mexer o item. Ao passar o limiar a base e REANCORADA — senao o item
      // pularia esses 3 px de uma vez.
      if (Math.abs(event.clientX - twDrag.startX) < 3 &&
          Math.abs(event.clientY - twDrag.startY) < 3) return;
      twDrag.moved = true;
      twDrag.startX = event.clientX;
      twDrag.startY = event.clientY;
      twDrag.baseX = tweetOffsetOf(twDrag.item).x;
      twDrag.baseY = tweetOffsetOf(twDrag.item).y;
      return;
    }
    var off = tweetOffsetOf(twDrag.item);
    off.x = clampOffset(twDrag.baseX + (event.clientX - twDrag.startX) / twDrag.scaleX);
    off.y = clampOffset(twDrag.baseY + (event.clientY - twDrag.startY) / twDrag.scaleY);
    applyTweetOffset(twDrag.item);
    snapTweetOffset(twDrag);
    paintTweetFields();
    showEditGuides(twDrag.node);
    event.preventDefault();
  }

  function endTweetDrag(event) {
    if (!twDrag) return;
    if (event && event.pointerId !== twDrag.pointerId) return;
    var node = twDrag.node;
    var item = twDrag.item;
    if (node.releasePointerCapture) {
      try { node.releasePointerCapture(twDrag.pointerId); } catch (e) {}
    }
    twDrag = null;
    // Nao redesenha a PREVIA aqui: as variaveis ja estao certas e o no segue
    // selecionado. Os OUTPUTS, sim, quando o item e o POV — o deslocamento dele
    // vai para o .toml (`text_dx`/`text_dy`), e sem esta linha o usuario arrastava,
    // baixava o template e o arquivo saia sem o ajuste que ele acabou de fazer.
    if (item === "pov") renderOutputs();
  }

  // ---------- texto no lugar ----------
  // O que sai do `contenteditable` e `textContent`, nunca `innerHTML`: o texto
  // volta para o HTML do cartao por `esc()`, e deixar markup do usuario entrar ali
  // seria a unica porta de HTML da pagina.
  function inlineValue(text, spec) {
    var value = String(text == null ? "" : text).replace(/[^\S\n]+/g, " ");
    if (spec.single) value = value.replace(/\s+/g, " ");
    else value = value.replace(/\n{3,}/g, "\n\n");
    return value.trim().slice(0, spec.max);
  }

  function placeCaretAtEnd(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    var sel = window.getSelection();
    if (!sel) return;
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function startTweetEdit(node) {
    var leaf = node && node.matches("[data-txt]") ? node : (node ? node.querySelector("[data-txt]") : null);
    if (!leaf) return;
    var spec = TW_INLINE[leaf.getAttribute("data-txt")];
    if (!spec) return;
    twEditBase = inlineGet(spec);
    leaf.setAttribute("contenteditable", "true");
    leaf.setAttribute("spellcheck", "false");
    leaf.focus();
    // Tudo selecionado: digitar substitui, como no campo do passo. O clique duplo
    // ja marcou uma palavra; sem isto a selecao parcial ficaria e a primeira tecla
    // so trocaria a palavra.
    var range = document.createRange();
    range.selectNodeContents(leaf);
    var sel = window.getSelection();
    if (!sel) return;
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function stopTweetEdit(leaf, commit) {
    if (!leaf || !leaf.isContentEditable) return;
    var spec = TW_INLINE[leaf.getAttribute("data-txt")];
    if (spec && commit) inlineSet(spec, inlineValue(leaf.textContent, spec));
    leaf.removeAttribute("contenteditable");
    leaf.removeAttribute("spellcheck");
    paintTweetFields();
    renderGallery();
    renderPreview();   // normaliza: letra do avatar, limite de linhas, `esc()`
  }

  function onTweetEditInput(event) {
    var leaf = event.target;
    if (!leaf || !leaf.getAttribute || leaf.getAttribute("contenteditable") !== "true") return;
    var spec = TW_INLINE[leaf.getAttribute("data-txt")];
    if (!spec) return;
    if ((leaf.textContent || "").length > spec.max) {
      // `contenteditable` nao tem `maxlength`: o corte acontece aqui, e recolocar o
      // caret no fim e o que impede de travar no limite com uma letra.
      leaf.textContent = inlineValue(leaf.textContent, spec);
      placeCaretAtEnd(leaf);
    }
    inlineSet(spec, inlineValue(leaf.textContent, spec));
    // Campo do passo e vitrine acompanham tecla a tecla; o canvas NAO e redesenhado
    // (o cursor ficaria num no que acabou de sair do DOM).
    paintTweetFields();
    renderGallery();
  }

  function onTweetEditKey(event) {
    var leaf = document.activeElement;
    if (!leaf || !leaf.isContentEditable || !leaf.getAttribute) return;
    var spec = TW_INLINE[leaf.getAttribute("data-txt")];
    if (!spec) return;
    if (event.key === "Enter" && spec.single) {
      stopTweetEdit(leaf, true);
      event.preventDefault();
    } else if (event.key === "Escape") {
      // Escape devolve o texto como estava: e o que se espera de um editor, e
      // `contenteditable` nao tem desfazer proprio quando o valor mora no estado.
      inlineSet(spec, twEditBase);
      stopTweetEdit(leaf, false);
      event.preventDefault();
    }
  }

  // ---------- teclado ----------
  function onTweetKey(event) {
    if (!twSelected || twDrag) return;
    var active = document.activeElement;
    // Campo em foco manda nas setas (os sliders do passo usam as mesmas teclas).
    if (active && active.closest && active.closest("input, textarea, select, [contenteditable='true']")) return;
    if (event.key === "Escape") { selectTweetItem(""); return; }
    if (event.key === "Enter") { startTweetEdit(tweetItemNode(twSelected)); event.preventDefault(); return; }
    if (!event.key || event.key.indexOf("Arrow") !== 0) return;
    var step = event.shiftKey ? TW_NUDGE_FAST : TW_NUDGE_PX;
    var off = tweetOffsetOf(twSelected);
    if (event.key === "ArrowLeft") off.x = clampOffset(off.x - step);
    else if (event.key === "ArrowRight") off.x = clampOffset(off.x + step);
    else if (event.key === "ArrowUp") off.y = clampOffset(off.y - step);
    else if (event.key === "ArrowDown") off.y = clampOffset(off.y + step);
    applyTweetOffset(twSelected);
    paintTweetFields();
    showEditGuides(tweetItemNode(twSelected));
    // Mesma regra do slider e do arraste: so o POV tem deslocamento no .toml.
    if (twSelected === "pov") renderOutputs();
    event.preventDefault();
  }

  function resetTweetOffsets() {
    TW_ITEMS.forEach(function (item) {
      // O POV guarda o deslocamento na zona; os outros tres, no estado do cartao.
      var off = tweetOffsetOf(item);
      off.x = 0;
      off.y = 0;
    });
    paintTweetFields();
    renderPreview();
    renderOutputs();
    toast("Posições de volta ao modelo.", "");
  }

  function initTweetEditor() {
    var canvas = $("#canvas");
    if (!canvas) return;
    canvas.addEventListener("pointerdown", startTweetDrag);
    canvas.addEventListener("pointermove", moveTweetDrag);
    canvas.addEventListener("pointerup", endTweetDrag);
    canvas.addEventListener("pointercancel", endTweetDrag);
    canvas.addEventListener("dblclick", function (event) {
      var node = event.target.closest ? event.target.closest("#canvas [data-edit]") : null;
      if (!node) return;
      startTweetEdit(node);
      event.preventDefault();
    });
    canvas.addEventListener("input", onTweetEditInput);
    // `blur` nao borbulha: captura para ouvir a saida do texto em edicao. O commit
    // e o que grava — sem isto, editar e clicar fora perderia o texto.
    canvas.addEventListener("blur", function (event) {
      if (event.target && event.target.getAttribute &&
          event.target.getAttribute("contenteditable") === "true") {
        stopTweetEdit(event.target, true);
      }
    }, true);
    document.addEventListener("keydown", onTweetEditKey);
    document.addEventListener("keydown", onTweetKey);
    var reset = $("#tw-reset-pos");
    if (reset) reset.addEventListener("click", resetTweetOffsets);
    paintFrameScale();
    // `--framepx` e um comprimento em px de TELA: sem reescrever no resize, um item
    // deslocado sairia fora de escala quando o palco encolhesse.
    if (window.ResizeObserver) {
      new ResizeObserver(function () { paintFrameScale(); }).observe($(".phone-screen"));
    }
  }

  // Faixa de texto na prévia: placa + texto. Espelha o que o motor faz — a zona
  // pinta a placa (o `color` dela, preto por padrão) e o libass queima o texto
  // ancorado na área interna.
  //
  // Ela pinta a faixa JÁ POSICIONADA pelo laço, não cria uma segunda: duas
  // `.band` aninhadas davam DUAS alturas para a mesma zona — a de fora com a
  // geometria do motor e a de dentro com a altura do conteúdo — e a placa preta
  // vazava sobre o vídeo, que era exatamente o que o usuário pediu para não
  // acontecer.
  //
  // Medidas verticais em `cqh` e horizontais em `cqw`, nunca em porcentagem de
  // padding: porcentagem de padding resolve contra a LARGURA do bloco contentor,
  // então `padding-top: 5%` daria 5% da largura e a área interna deixaria de ser a
  // que `plan_bands` calcula. É a mesma armadilha que já tinha aparecido na
  // margem da legenda.
  function paintTextZone(el, band) {
    var zone = band.zone;
    el.className = "band band--text";
    el.style.background = zone.color || "#000";
    el.style.paddingTop = ((band.innerY - band.y) / state.height * 100) + "cqh";
    el.style.paddingBottom =
      ((band.height - (band.innerY - band.y) - band.innerH) / state.height * 100) + "cqh";
    el.style.paddingLeft = (band.innerX / state.width * 100) + "cqw";
    el.style.paddingRight =
      ((state.width - band.innerX - band.innerW) / state.width * 100) + "cqw";
    // `border-box`: o padding é a área interna, não altura a mais. Sem isto a
    // faixa mediria margem + altura e o texto sairia fora da zona declarada.
    el.style.boxSizing = "border-box";
    // O alinhamento é o do motor: a área interna é o quadrante e o texto se ancora
    // nele. `flex` em vez do `place-items: center` do `.band` porque aqui o
    // alinhamento é dado, não fixo.
    el.style.display = "flex";
    el.style.justifyContent = zone.textAlign === "left" ? "flex-start" :
      zone.textAlign === "right" ? "flex-end" : "center";
    el.style.alignItems = zone.textValign === "top" ? "flex-start" :
      zone.textValign === "bottom" ? "flex-end" : "center";

    var pov = document.createElement("p");
    pov.className = "pv-pov";
    // `data-edit` é o que o arraste pega; `data-txt` é o que o duplo clique edita.
    // Sem os dois o texto seria só desenho — foi o que ele foi até agora.
    pov.setAttribute("data-edit", "pov");
    pov.style.textAlign = zone.textAlign || "center";
    // `textSize` é porcentagem da ALTURA do quadro — a unidade do motor
    // (`text_size`). Na prévia ele vira `cqw` pela razão de aspecto do QUADRO, e
    // não `cqh` direto, porque o palco não tem a mesma razão do quadro (o mock do
    // celular é 9:18,4, o quadro 9:16): em `cqh` o texto fica ~18% maior em
    // relação à largura disponível e quebra em QUATRO linhas onde o motor queima
    // duas — o usuário olha a prévia, vê o texto estourando a faixa preta e não
    // tem como saber que o render sai certo.
    //
    // O que a prévia precisa acertar aqui é a CONTAGEM DE LINHAS, que é uma razão
    // entre o corpo da fonte e a largura. A legenda usa `cqh` porque o alvo dela é
    // outro (o corpo em si, com duas palavras que nunca quebram).
    pov.style.fontSize = (zone.textSize * (state.height / state.width)) + "cqw";
    pov.style.color = zone.textColor || "#ffffff";
    pov.style.fontWeight = zone.textBold === false ? "500" : "800";
    pov.style.textTransform = zone.textUppercase ? "uppercase" : "none";
    if (zone.textOutline > 0) {
      var halo = zone.textOutline + "cqh";
      pov.style.textShadow =
        "0 0 " + halo + " #000, 0 0 " + halo + " #000, 0 0 " + halo + " #000";
    }
    // O deslocamento fino vai no PRÓPRIO nó (não há cartão em volta), e é escrito
    // aqui e não por `applyTweetOffset`: o nó ainda não está no DOM, então a busca
    // por `#canvas [data-edit='pov']` acharia o anterior.
    var off = tweetOffsetOf("pov");
    pov.style.setProperty("--tw-off-x-pov", twOffsetX(off.x));
    pov.style.setProperty("--tw-off-y-pov", twOffsetY(off.y));

    var leaf = document.createElement("span");
    leaf.setAttribute("data-txt", "pov");
    leaf.textContent = zone.text || "";
    pov.appendChild(leaf);
    el.appendChild(pov);
    return el;
  }

  // Barra de identidade do formato Meme na janela: o mesmo perfil do cartao do X
  // (avatar + nome + @handle), com os MESMOS campos do passo Aparencia. Antes era
  // "Seu Nome" fixo e a secao nao editava este template.
  function memeIdBar() {
    var bar = document.createElement("div");
    bar.className = "pv-idbar";
    // Avatar e nome respondem aos sliders e ao arraste, como no cartao do X — os
    // dois itens que este formato desenha (nao ha texto de tweet aqui: os campos
    // de Texto valem so para o X).
    paintTweetOffsets(bar, ["avatar", "name"]);
    bar.innerHTML =
      avatarMarkup("pv", "pv-avatar--dark") +
      "<span class='pv-id' data-edit='name'><strong data-txt='name'>" +
        esc(tweetName()) + "</strong>" +
      "<small data-txt='handle'>" + esc(tweetHandle()) + "</small></span>";
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
        // Zona de texto: a faixa e a placa (o `color` da zona, preto por padrao) e
        // o texto vive dentro da AREA INTERNA dela. Nao passa pelo caminho de
        // midia: nao ha fonte de pixel nenhuma aqui — e por isso ela pinta `el`,
        // a faixa que o laco ja posicionou, em vez de criar outra.
        if (band.kind === "text") {
          paintTextZone(el, band);
        } else
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
        // Formato Viral: gancho na base do vídeo. (O POV do Meme deixou de ser
        // sobreposição: ele é uma zona de texto com faixa própria, desenhada por
        // `paintTextZone` — a mesma que o motor queima.)
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

    // `--framepx` e a escala da tela (o X dos offsets depende dela) e a selecao
    // e guardada pela chave: o canvas novo recebe o mesmo item selecionado, com o
    // ponto cruz reposicionado no no acabou de nascer.
    paintFrameScale();
    paintTweetSelection();
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
      if (zone.kind === "solid" || zone.kind === "text")
        lines.push('color = "' + (zone.color || "black") + '"');
      if (zone.kind !== "captions" && zone.kind !== "solid" && zone.kind !== "text") {
        if (zone.fit !== "cover") lines.push('fit = "' + zone.fit + '"');
      }
      if (zone.kind === "text") {
        // O texto vai entre aspas com as internas escapadas; o `\\n` do campo vira
        // quebra de linha de verdade no motor (`\\N` do ASS), entao ele e preservado.
        lines.push('text = "' + String(zone.text || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n") + '"');
        lines.push("text_size = " + fmtNum(round4((zone.textSize || 3.8) / 100)));
        if ((zone.textColor || "#ffffff").toLowerCase() !== "#ffffff")
          lines.push('text_color = "' + zone.textColor + '"');
        if ((zone.textAlign || "center") !== "center")
          lines.push('text_align = "' + zone.textAlign + '"');
        if ((zone.textValign || "middle") !== "middle")
          lines.push('text_valign = "' + zone.textValign + '"');
        if (zone.textBold === false) lines.push("text_bold = false");
        if (zone.textUppercase === true) lines.push("text_uppercase = true");
        if (zone.textOutline) lines.push("text_outline = " + fmtNum(round4(zone.textOutline / 100)));
        // Os deslocamentos sao px do QUADRO na pagina e fracao do canvas no motor:
        // a conversao tem de ser a mesma dos dois eixos que o motor separa (x pela
        // largura, y pela altura).
        var offX = textOffsetOf(zone, "x"), offY = textOffsetOf(zone, "y");
        if (offX) lines.push("text_dx = " + fmtNum(round4(offX / state.width)));
        if (offY) lines.push("text_dy = " + fmtNum(round4(offY / state.height)));
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

  // Padroes de uma zona de texto. Existem porque os campos abaixo precisam de um
  // valor para MOSTRAR quando a zona acabou de nascer: sem isto o painel mostraria
  // vazio e o .toml sairia com o padrao do MOTOR — o usuario veria uma coisa e
  // renderizaria outra.
  //
  // `textSize` e o unico que difere do motor de proposito: `Zone.text_size` default
  // e 5%, e 3,8% e o corpo que o modelo Meme pede. Como o wizard SEMPRE grava
  // `text_size` explicito, o default do motor so aparece em .toml escrito a mao.
  var TEXT_ZONE_DEFAULTS = {
    text: "POV: o texto que aparece na faixa",
    textSize: 3.8, textColor: "#ffffff", textAlign: "center", textValign: "middle",
    textBold: true, textUppercase: false, textOutline: 0, textOff: { x: 0, y: 0 }
  };

  // Os dois liga/desliga tem padrao DIFERENTE (negrito ligado, maiusculas
  // desligado) e a ausencia do campo significa o padrao, nao "falso".
  function textFlag(zone, key) {
    if (key === "textBold") return zone.textBold !== false;
    return zone.textUppercase === true;
  }

  function textOffsetOf(zone, axis) {
    return zone.textOff ? (zone.textOff[axis] || 0) : 0;
  }

  // Chips de um conjunto fechado: alinhamento (string) e os dois liga/desliga
  // (booleano). `flagKey` distingue os casos — sem ele o valor e comparado como
  // string, com ele o estado vem de `textFlag`.
  function textAlignChips(zone, index, act, options, flagKey) {
    return options.map(function (pair) {
      var value = pair[0];
      var on = flagKey ? textFlag(zone, act) === (value === "1") : (zone[act] || "") === value;
      return "<button type='button' class='chip pressable' role='radio' data-act='" + act +
        "' data-value='" + value + "' data-i='" + index + "' aria-checked='" + (on ? "true" : "false") + "'" +
        (on ? "" : " tabindex='-1'") + ">" + pair[1] + "</button>";
    }).join("");
  }

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
          ((zone.kind === "solid" || zone.kind === "text") ?
            "<div class='full'>" +
              "<div class='mini-label'>" + (zone.kind === "text" ? "Cor da faixa" : "Cor") + "</div>" +
              "<input type='text' value='" + esc(zone.color) + "' data-act='color' data-i='" + index + "'>" +
            "</div>" : "") +
          (zone.kind === "text" ?
            "<div class='full'>" +
              "<div class='mini-label'>Texto da faixa</div>" +
              "<textarea rows='2' maxlength='160' data-act='text' data-i='" + index + "'" +
                " placeholder='POV: ...'>" + esc(zone.text || "") + "</textarea>" +
            "</div>" +
            "<div class='full'>" +
              "<div class='mini-label'>Tamanho <span>" + (zone.textSize || 0).toFixed(1) + "% da altura</span></div>" +
              "<input type='range' min='1' max='12' step='0.1' value='" + (zone.textSize || 3.8) + "'" +
                " style='--p:" + (((zone.textSize || 3.8) - 1) / 11 * 100) + "%'" +
                " data-act='textSize' data-i='" + index + "'>" +
            "</div>" +
            "<div><div class='mini-label'>Cor do texto</div>" +
              "<input type='color' value='" + esc(zone.textColor || "#ffffff") + "'" +
                " data-act='textColor' data-i='" + index + "' aria-label='Cor do texto'></div>" +
            "<div><div class='mini-label'>Contorno <span>" + (zone.textOutline || 0).toFixed(1) + "%</span></div>" +
              "<input type='range' min='0' max='1.5' step='0.05' value='" + (zone.textOutline || 0) + "'" +
                " style='--p:" + ((zone.textOutline || 0) / 1.5 * 100) + "%'" +
                " data-act='textOutline' data-i='" + index + "'></div>" +
            "<div class='full'>" +
              "<div class='mini-label'>Alinhamento na faixa</div>" +
              "<div class='chips' role='radiogroup' aria-label='Alinhamento horizontal do texto'>" +
                textAlignChips(zone, index, "textAlign",
                  [["left", "Esq"], ["center", "Centro"], ["right", "Dir"]]) +
              "</div>" +
            "</div>" +
            "<div class='full'>" +
              "<div class='mini-label'>Altura na faixa</div>" +
              "<div class='chips' role='radiogroup' aria-label='Alinhamento vertical do texto'>" +
                textAlignChips(zone, index, "textValign",
                  [["top", "Topo"], ["middle", "Meio"], ["bottom", "Base"]]) +
              "</div>" +
            "</div>" +
            "<div class='full'>" +
              "<div class='mini-label'>Peso</div>" +
              "<div class='chips' role='radiogroup' aria-label='Peso e caixa do texto'>" +
                textAlignChips(zone, index, "textBold", [["1", "Negrito"], ["0", "Normal"]], "textBold") +
                textAlignChips(zone, index, "textUppercase", [["1", "MAIÚSC."], ["0", "Como escrito"]], "textUppercase") +
              "</div>" +
            "</div>" +
            "<div><div class='mini-label'>Desloc. H</div><div class='num-wrap'>" +
              "<input type='number' min='-480' max='480' step='1' value='" + textOffsetOf(zone, "x") + "'" +
                " data-act='textOffX' data-i='" + index + "' aria-label='Deslocamento horizontal do texto em px do quadro'><span>px</span></div></div>" +
            "<div><div class='mini-label'>Desloc. V</div><div class='num-wrap'>" +
              "<input type='number' min='-480' max='480' step='1' value='" + textOffsetOf(zone, "y") + "'" +
                " data-act='textOffY' data-i='" + index + "' aria-label='Deslocamento vertical do texto em px do quadro'><span>px</span></div></div>" : "") +
          (isPixel && zone.kind !== "solid" && zone.kind !== "text" ?
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
    // O tamanho do texto tem controle em DOIS passos (aqui, no editor de zonas, e
    // na secao da faixa de texto). Sem esta repintura, mexer no slider de Zonas
    // deixaria o slider e os chips do POV mostrando o tamanho anterior.
    paintPovFields();
  }

  // ---------- interacoes ----------
  function updateZone(index, patch) {
    state.zones[index] = Object.assign({}, state.zones[index], patch);
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-act], [data-goto], [data-axis], [data-copy], [data-gallery], [data-pov-size]");
    if (!target) return;

    // Tamanhos prontos da faixa de texto (Pequeno/Medio/Grande): um clique reduz o
    // corpo sem cacar o slider. Escreve o MESMO `zone.textSize` que o slider do POV
    // e o do passo de Zonas, entao os tres nunca divergem.
    if (target.dataset.povSize !== undefined) {
      var sizeZone = povZone();
      if (sizeZone) {
        sizeZone.textSize = Number(target.dataset.povSize);
        paintPovFields();
        renderPreview();
        renderOutputs();
      }
      return;
    }

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
    } else if ((act === "kind" || act === "fit" || act === "textAlign" || act === "textValign") &&
               target.dataset.value !== undefined) {
      // Chips de tipo/encaixe/alinhamento: o valor vem no data-value (clique), nao
      // num select. O change handler continua existindo para compatibilidade.
      var patch = {};
      patch[act] = target.dataset.value;
      if (act === "kind" && target.dataset.value === "text") {
        // Uma zona que acabou de virar texto precisa dos campos preenchidos: os
        // controles abaixo leem `zone.textSize` etc., e sem isto o painel mostraria
        // vazio enquanto o motor aplicaria o proprio default.
        Object.keys(TEXT_ZONE_DEFAULTS).forEach(function (key) {
          if (state.zones[index][key] !== undefined) return;
          var value = TEXT_ZONE_DEFAULTS[key];
          // `textOff` e um objeto: copiar a referencia faria todas as zonas de
          // texto dividirem o mesmo deslocamento.
          patch[key] = value && typeof value === "object" ? { x: value.x, y: value.y } : value;
        });
      }
      updateZone(index, patch);
      if (act === "kind") state.previewMock = "";
    } else if (act === "textBold" || act === "textUppercase") {
      // Liga/desliga: chip com data-value 1 ou 0, nao um checkbox — o resto da
      // lista de zonas usa chips e um controle de outro tipo destoaria.
      var flags = {};
      flags[act] = target.dataset.value === "1";
      updateZone(index, flags);
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
    else if (act === "text") updateZone(index, { text: target.value });
    else if (act === "textSize") {
      updateZone(index, { textSize: Number(target.value) });
      // O tamanho do texto tem controle em dois passos. O `renderAll` daqui nao
      // roda neste caminho (o listener de `input` repinta so o necessario para nao
      // recriar o slider no meio do arrasto), entao a secao do POV e espelhada
      // aqui — sem isto o slider e os chips dela ficariam no valor anterior.
      paintPovFields();
    }
    else if (act === "textColor") updateZone(index, { textColor: target.value });
    else if (act === "textOutline") updateZone(index, { textOutline: Number(target.value) });
    else if (act === "marginTop") updateZone(index, { marginTop: Number(target.value) });
    else if (act === "marginBottom") updateZone(index, { marginBottom: Number(target.value) });
    else if (act === "marginLeft") updateZone(index, { marginLeft: Number(target.value) });
    else if (act === "marginRight") updateZone(index, { marginRight: Number(target.value) });
    else if (act === "source") updateZone(index, { source: target.value });
    else if (act === "color") updateZone(index, { color: target.value });
    else if (act === "textOffX" || act === "textOffY") {
      // O numero escreve o MESMO objeto que o arraste move (o `textOff` da zona),
      // entao os dois controles nunca ficam fora de sincronia. Mutar o aninhado em
      // vez de passar por `updateZone` e o que preserva a referencia que
      // `tweetOffsetOf("pov")` devolve.
      var textZone = state.zones[index];
      if (!textZone.textOff) textZone.textOff = { x: 0, y: 0 };
      textZone.textOff[act === "textOffX" ? "x" : "y"] = clampOffset(target.value);
      paintTweetFields();
    }

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
    } else if (target.id === "tw-name") {
      state.tweetName = target.value;
      renderPreview();
      // A vitrine tambem desenha o perfil (cartao do X e barra do Meme): sem
      // isto o card da galeria ficava com o nome anterior enquanto a janela ja
      // mostrava o novo.
      renderGallery();
    } else if (target.id === "tw-handle") {
      state.tweetHandle = target.value;
      renderPreview();
      renderGallery();
    } else if (target.id === "tw-text") {
      state.tweetText = target.value;
      renderPreview();
      renderGallery();
    } else if (target.id === "pov-text") {
      // Escreve na ZONA, que e de onde o motor le o texto no render. O campo e o
      // passo de Zonas leem o mesmo lugar, entao os dois nunca divergem.
      var povTarget = povZone();
      if (povTarget) {
        povTarget.text = target.value;
        renderPreview();
        renderGallery();
        renderOutputs();
      }
    } else if (target.id === "pov-size") {
      // Mesmo caminho do texto: escreve `zone.textSize`, que e de onde o motor
      // tira o `text_size`. Vai ao `.toml` (`renderOutputs`), senao o usuario
      // reduzia o texto, baixava o arquivo e ele saia no tamanho antigo.
      var sizeTarget = povZone();
      if (sizeTarget) {
        sizeTarget.textSize = Number(target.value);
        // Repinta os DOIS controles (slider e chips) daqui, e nao so o slider que
        // recebeu o evento: um chip aceso com o tamanho antigo mentiria.
        paintPovFields();
        renderPreview();
        renderOutputs();
      }
    } else if (target.id.indexOf("tw-pos-") === 0) {
      // Slider do grid de posicao: `tw-pos-<item>-<eixo>`. O arraste e esta rota
      // escrevem o MESMO `state.tweetOffset`, entao os dois controles nunca ficam
      // fora de sincronia — `paintTweetFields` repinta o outro lado.
      var posParts = target.id.split("-");   // tw-pos-<item>-<axis>
      var posItem = posParts[2], posAxis = posParts[3];
      if ((posAxis === "x" || posAxis === "y")) {
        // `tweetOffsetOf` e a porta unica: para o POV ele devolve o objeto guardado
        // na ZONA, para os itens do cartao o do estado. Escrever em
        // `state.tweetOffset[posItem]` deixaria o POV sem controle por slider.
        tweetOffsetOf(posItem)[posAxis] = clampOffset(target.value);
        var posHint = document.getElementById(target.id + "-hint");
        if (posHint) posHint.textContent = tweetOffsetOf(posItem)[posAxis] + "px";
        paintSlideFill(target);
        renderPreview();
        // O POV é o único item cujo deslocamento CHEGA ao .toml (`text_dx`/
        // `text_dy`); os do cartão são só da prévia. Sem esta linha o usuário
        // ajustava o slider e baixava um arquivo sem o ajuste — e o slider dispara
        // a cada `input`, então a repintura fica restrita a quem ela muda.
        if (posItem === "pov") renderOutputs();
      }
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
    // O TEXTO e conteudo do projeto e nao vaza para o template novo. O perfil
    // (titulo, @handle, foto) fica: os campos do passo Aparencia editam TODOS os
    // formatos que desenham o cartao, entao trocar de formato nao pode devolver
    // "Seu Nome" para quem acabou de escrever o seu.
    state.tweetText = "";
    paintTweetFields();

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
  // Exemplos do modelo: campo vazio cai neles. Ficam ao lado do `tweetText`
  // porque e o mesmo padrao que o cartao da vitrine desenha — quem edita o tweet
  // da janela no passo Aparencia esta editando a SUA previa, nao o modelo.
  var TWEET_DEFAULT = "Olha só o que rolou nesse servidor 👀";
  var TWEET_NAME_DEFAULT = "Seu Nome";
  var TWEET_HANDLE_DEFAULT = "@seuhandle";
  var VERIFIED_BADGE =
    "<svg viewBox='0 0 24 24' width='10' height='10' aria-hidden='true'>" +
    "<path fill='#1d9bf0' d='M12 2l2.4 2.4 3.4-.5 1 3.3 3.2 1.2-1.4 3.1 1.4 3.1-3.2 1.2-1 3.3-3.4-.5L12 22l-2.4-2.4-3.4.5-1-3.3-3.2-1.2L3.4 12 2 8.9l3.2-1.2 1-3.3 3.4.5z'/>" +
    "<path fill='none' stroke='#fff' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round' d='M8.5 12.5l2.5 2.5 4.5-5'/></svg>";

  function tweetText() { return state.tweetText || TWEET_DEFAULT; }
  function tweetName() { return state.tweetName || TWEET_NAME_DEFAULT; }
  function tweetHandle() { return state.tweetHandle || TWEET_HANDLE_DEFAULT; }

  // A letra do avatar segue o titulo: "Cortes do Sena" deixa "C".
  function tweetAvatarLetter() {
    var letter = tweetName().replace(/[^0-9A-Za-z\u00C0-\u017F]/g, "").charAt(0);
    return letter ? letter.toUpperCase() : "S";
  }

  // ---------- foto do avatar (passo Aparencia) ----------
  // Arquivo local, so previa: o render desenha a imagem que a zona de imagem
  // aponta, entao esta foto nao viaja para o motor nem para o .toml — ela existe
  // para a janela mostrar o cartao como ele vai sair, com a sua cara.
  //
  // A foto entra REDUZIDA a TWEET_AVATAR_PX de lado e guardada como data URL, por
  // duas razoes que se somam:
  //   * o CSP da pagina e `img-src 'self' data:`, SEM `blob:`. Object URL nao
  //     serve nem para pintar a janela nem como ponte para o canvas: `img-src`
  //     vale para TODA imagem, inclusive `new Image()`, e o bloqueio e silencioso
  //     (o circulo do avatar fica vazio e o console nao acusa). `media-src` tem
  //     `blob:`, e so por isso o video de referencia pode usar object URL.
  //     `data:` esta no img-src: o caminho e FileReader -> Image -> canvas.
  //   * o avatar aparece com ~48px no cartao: guardar o original (foto de 5 MB)
  //     seria carregar megabyte de base64 no estado e recopia-lo para dentro do
  //     HTML a cada tecla digitada no titulo. 256px da 4x de sobra na tela.
  var TWEET_AVATAR_PX = 256;

  function addTweetAvatar(file) {
    if (!file) return;
    if (!file.type || file.type.indexOf("image/") !== 0) {
      toast("Escolha um arquivo de imagem (PNG, JPEG, WebP ou GIF).", "bad");
      return;
    }
    var reader = new FileReader();
    reader.onerror = function () { toast("Não consegui ler o arquivo.", "bad"); };
    reader.onload = function () {
      var img = new Image();
      img.onerror = function () {
        toast("O arquivo não é uma imagem que eu consiga abrir.", "bad");
      };
      img.onload = function () {
        var small = downscaleAvatar(img);
        if (!small) {
          toast("Não consegui preparar a imagem.", "bad");
          return;
        }
        state.tweetAvatar = small;
        state.tweetAvatarName = file.name || "imagem";
        paintTweetFields();
        renderPreview();
        // A foto vale para o cartao da janela E para os cards da vitrine (X e
        // Meme desenham perfil): a galeria e reescrita, senao o card continuava
        // com a letra antiga.
        renderGallery();
        toast("Avatar da prévia: " + state.tweetAvatarName, "ok");
      };
      img.src = String(reader.result || "");
    };
    reader.readAsDataURL(file);
  }

  // Reduz a foto ao quadrado de TWEET_AVATAR_PX, com o mesmo enquadramento do CSS
  // (`cover`: o maior quadrado central), e devolve um data URL JPEG. O estado
  // guarda alguns KB, nao a foto inteira.
  function downscaleAvatar(img) {
    if (!img.naturalWidth || !img.naturalHeight) return "";
    var side = TWEET_AVATAR_PX;
    var canvas = document.createElement("canvas");
    canvas.width = side;
    canvas.height = side;
    var ctx = canvas.getContext("2d");
    var scale = Math.max(side / img.naturalWidth, side / img.naturalHeight);
    var w = img.naturalWidth * scale;
    var h = img.naturalHeight * scale;
    ctx.drawImage(img, (side - w) / 2, (side - h) / 2, w, h);
    try { return canvas.toDataURL("image/jpeg", 0.86); } catch (e) { return ""; }
  }

  // Volta ao modelo. Silencioso de proposito: os resets (carregar o split-card,
  // trocar o mock da galeria) chamam esta, sem toast de "removi o seu arquivo".
  function clearTweetAvatar() {
    state.tweetAvatar = "";
    state.tweetAvatarName = "";
  }

  function removeTweetAvatar() {
    if (!state.tweetAvatar) return;
    clearTweetAvatar();
    paintTweetFields();
    renderPreview();
    renderGallery();
    toast("Avatar do modelo restaurado (letra do título).", "");
  }

  // ---------- campos do tweet (passo Aparencia) ----------
  // Uma porta de entrada para o estado. O texto tem DUAS portas — este campo e o
  // passo Frases, onde "escrever frase" vira headline + texto do tweet —, entao
  // quem escreve por la repinta os campos com esta funcao: sem isso a janela
  // ficava com o texto antigo enquanto o cartao da galeria ja mostrava o novo.
  // A foto do avatar tambem e repintada aqui: o chip do campo, o nome do arquivo
  // e o botao Remover existem so para dizer o que a janela esta desenhando.
  function paintTweetFields() {
    [["tw-name", "tweetName"], ["tw-handle", "tweetHandle"], ["tw-text", "tweetText"]]
      .forEach(function (pair) {
        var el = document.getElementById(pair[0]);
        if (el) el.value = state[pair[1]] || "";
      });
    [["tw-pos-avatar-x", "avatar", "x"], ["tw-pos-avatar-y", "avatar", "y"],
     ["tw-pos-name-x", "name", "x"], ["tw-pos-name-y", "name", "y"],
     ["tw-pos-body-x", "body", "x"], ["tw-pos-body-y", "body", "y"],
     ["tw-pos-pov-x", "pov", "x"], ["tw-pos-pov-y", "pov", "y"]]
      .forEach(function (trip) {
        var el = document.getElementById(trip[0]);
        var value = clampOffset(tweetOffsetOf(trip[1])[trip[2]]);
        if (el) { el.value = value; paintSlideFill(el); }
        var hint = document.getElementById(trip[0] + "-hint");
        if (hint) hint.textContent = value + "px";
      });
    paintTweetAvatar();
    paintPovFields();
  }

  // O painel da faixa de texto so existe quando ha uma zona de texto. Escondido
  // nao basta: um campo visivel escrevendo num lugar que o motor nao le e o defeito
  // que o usuario nao consegue diagnosticar.
  function paintPovFields() {
    var zone = povZone();
    var box = document.getElementById("pov-box");
    if (box) box.hidden = !zone;
    if (!zone) return;
    var text = document.getElementById("pov-text");
    // So escreve quando o valor MUDOU. Durante a digitacao os dois sao iguais, e
    // atribuir o mesmo texto ao textarea joga o cursor para o fim da linha — o
    // usuario digitaria e o cursor saltaria a cada tecla.
    if (text && text.value !== (zone.text || "")) text.value = zone.text || "";
    paintPovSize(zone);
  }

  // Tamanho do texto: um valor so (`zone.textSize`, porcentagem da altura do
  // quadro — a unidade do motor), desenhado em tres controles: o slider e os chips
  // prontos daqui e o slider do passo de Zonas. Todos escrevem no mesmo lugar, e
  // este pintor e o que os mantem de acordo — venha a mudanca de onde vier.
  function paintPovSize(zone) {
    var size = povSizeOf(zone);
    var slider = document.getElementById("pov-size");
    if (slider) { slider.value = size; paintSlideFill(slider); }
    var hint = document.getElementById("pov-size-hint");
    if (hint) hint.textContent = size.toFixed(1) + "%";
    var unit = document.getElementById("pov-size-unit");
    if (unit) unit.textContent = Math.round(size / 100 * state.height) + " px de altura";
    var chips = document.querySelectorAll("#pov-size-chips [data-pov-size]");
    Array.prototype.forEach.call(chips, function (chip) {
      // Igualdade exata: o slider vive fora dos tres valores prontos, e nesse caso
      // nenhum chip esta ligado — o conjunto e o mesmo, mas nao ha "o escolhido".
      var on = Math.abs(Number(chip.dataset.povSize) - size) < 0.05;
      chip.setAttribute("aria-checked", on ? "true" : "false");
      if (on) chip.removeAttribute("tabindex"); else chip.setAttribute("tabindex", "-1");
    });
    // O slider do passo de Zonas edita a MESMA zona, e aquele passo nao e
    // reconstruido a cada tique (nem quando se troca de passo, veja `showStep`).
    // Espelhar o valor aqui — como o `frameAt` faz entre os seus dois sliders — e o
    // que impede um controle aceso mostrando o tamanho anterior.
    Array.prototype.forEach.call(
      document.querySelectorAll("input[data-act='textSize']"),
      function (el) {
        el.value = size;
        paintSlideFill(el);
        var head = el.closest(".field, .full");
        head = head ? head.querySelector(".mini-label span") : null;
        if (head) head.textContent = size.toFixed(1) + "% da altura";
      }
    );
  }

  // O mesmo default do editor de zonas, para uma zona de texto recem-criada nao
  // aparecer com o slider em branco.
  function povSizeOf(zone) {
    var size = Number(zone && zone.textSize);
    return Number.isFinite(size) && size > 0 ? size : TEXT_ZONE_DEFAULTS.textSize;
  }

  function paintTweetAvatar() {
    var has = !!state.tweetAvatar;
    var chip = document.getElementById("tw-avatar-chip");
    if (chip) {
      chip.textContent = tweetAvatarLetter();
      chip.classList.toggle("tw-avatar-chip--img", has);
      chip.style.backgroundImage = has ? "url(" + state.tweetAvatar + ")" : "";
    }
    var state_label = document.getElementById("tw-avatar-state");
    if (state_label) {
      state_label.textContent = has ? state.tweetAvatarName : "letra do título";
    }
    var hint = document.getElementById("tw-avatar-hint");
    if (hint) {
      hint.textContent = has
        ? "A prévia desenha esta imagem no cartão. O arquivo não sobe para o servidor."
        : "Sem arquivo, a prévia desenha a letra do título.";
    }
    var clear = document.getElementById("tw-avatar-clear");
    if (clear) clear.hidden = !has;
  }

  // Ícones do placeholder de mídia (traço, estilo lucide): câmera para
  // video/frame, moldura para imagem, quadrado para cor sólida.
  var GAL_ICONS = {
    video: "<path d='m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5'/><rect x='2' y='6' width='14' height='12' rx='2'/>",
    frame: "<path d='m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5'/><rect x='2' y='6' width='14' height='12' rx='2'/>",
    image: "<rect width='18' height='18' x='3' y='3' rx='2' ry='2'/><circle cx='9' cy='9' r='2'/><path d='m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21'/>",
    solid: "<rect x='4' y='4' width='16' height='16' rx='4'/>",
    text: "<path d='M4 7V4h16v3'/><path d='M9 20h6'/><path d='M12 4v16'/>"
  };
  var GAL_SHORT = { video: "Vídeo", frame: "Frame", image: "Imagem", solid: "Cor", text: "Texto" };

  // POV do card Meme: agora e uma FAIXA, como no modelo e como no motor. Antes era
  // uma sobreposição na tela porque o POV não era zona nenhuma — existia só como
  // desenho da página, e o render não o queimava. Virou zona de texto, então o card
  // desenha a faixa dela com a fração declarada e o texto do modelo.
  //
  // A escala tipográfica do card continua fixa (`0.6rem`): `.gal-screen` não é
  // `container-type`, então `cqh` aqui mediria o container de fora. A proporção das
  // faixas é o contrato do card; o tamanho exato do texto é o contrato da prévia.
  function galTextBand(z) {
    var align = z.textAlign || "center";
    return "<div class='gal-band gal-text-band' style='flex-grow:" + (z.fraction * 100).toFixed(1) +
        ";background:" + (z.color || "#000") + "'>" +
        "<span class='gal-pov' style='text-align:" + align + ";color:" +
          (z.textColor || "#ffffff") + ";font-weight:" + (z.textBold === false ? "500" : "800") +
          ";text-transform:" + (z.textUppercase ? "uppercase" : "none") + "'>" +
          esc(z.text || "") + "</span>" +
        "<span class='gal-pct gal-pct--corner'>" + Math.round(z.fraction * 100) + "%</span></div>";
  }

  function galleryCard(key, g) {
    // As frações das zonas mandam em tudo: cada zona vira UMA faixa. Nenhuma
    // decoração de formato é mais sobreposta na tela — o que existe é a zona.
    var bands = g.mock === "meme" ? memeBands(g) :
      g.mock === "viral" ? viralBands(g) : g.zones.map(function (z) {
      if (z.mock === "tweet") return tweetBand(z);
      return mediaBand(z);
    }).join("");
    var overlay = "";

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

  // Composição Meme: faixa de texto no topo (fundo preto), vídeo reduzido e a barra
  // de identidade escura embaixo. Cada faixa sai da zona declarada, na ordem — é o
  // que faz o card prometer exatamente o layout que o motor monta.
  function memeBands(g) {
    return g.zones.map(function (z) {
      if (z.kind === "text") return galTextBand(z);
      if (z.kind === "image") return galIdBand(z);
      return galVideoBand(z);
    }).join("");
  }

  function galVideoBand(z) {
    return "<div class='gal-band' style='flex-grow:" + (z.fraction * 100).toFixed(1) + "'>" +
        "<svg viewBox='0 0 24 24' fill='none' stroke='#6366f1' stroke-width='1.8'" +
          " stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>" + GAL_ICONS.video + "</svg>" +
        "<span class='gal-kind'>Vídeo</span><span class='gal-cap'>Legenda</span>" +
        "<span class='gal-pct gal-pct--corner'>" + Math.round(z.fraction * 100) + "%</span></div>";
  }

  function galIdBand(z) {
    return "<div class='gal-band gal-meme-id' style='flex-grow:" + (z.fraction * 100).toFixed(1) + "'>" +
        avatarMarkup("gal", "gal-avatar--dark") +
        "<span class='gal-id-text'><strong>" + esc(tweetName()) + "</strong>" +
        "<small>" + esc(tweetHandle()) + "</small></span>" +
        "<span class='gal-pct gal-pct--corner'>" + Math.round(z.fraction * 100) + "%</span></div>";
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
  // handle + texto — o que a zona de imagem do formato X representa. O perfil
  // (avatar, nome, @handle) vem do passo Aparencia: os campos da secao editam
  // este cartao tambem, e nao so a janela de previa.
  function tweetBand(z) {
    return "<div class='gal-band gal-tweet-band' style='flex-grow:" + (z.fraction * 100).toFixed(1) + "'>" +
      "<div class='gal-tweet'>" +
        "<div class='gal-tweet-row'>" +
          avatarMarkup("gal") +
          "<span class='gal-tweet-id'><strong>" + esc(tweetName()) + " " + VERIFIED_BADGE +
          "</strong><small>" + esc(tweetHandle()) + "</small></span>" +
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
      // O clone acima e RASO: "textOff" e um objeto aninhado e continuaria
      // sendo o MESMO objeto do catalogo. Arrastar o POV no editor mexeria no
      // card da galeria (que se redesenha a partir de GALLERY). Clona fundo.
      if (clean.textOff) {
        clean.textOff = { x: clean.textOff.x || 0, y: clean.textOff.y || 0 };
      }
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
    // Igual ao Carregar split-card: o texto sai, o perfil fica (a secao Aparencia
    // edita todos os formatos, e o perfil e do usuario, nao do template).
    state.tweetText = "";
    paintTweetFields();

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
    paintTweetFields();
    addProjectPhrase(text);
    renderGallery();
    // A janela tambem mostra o texto: sem isto a frase entrava no cartao da
    // galeria e a previa continuava com o texto antigo.
    renderPreview();
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
    // A foto do avatar e da PREVIA: o botao so existe na pagina (a tile de upload
    // e estatica), entao o ouvinte entra no init junto dos outros.
    var clearAvatar = $("#tw-avatar-clear");
    if (clearAvatar) clearAvatar.addEventListener("click", removeTweetAvatar);
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
    paintTweetFields();

    // Editor da previa: arraste, clique duplo, setas e `--framepx`. Depois do
    // boot porque ele mede a tela e liga ouvintes no #canvas ja renderizado.
    initTweetEditor();

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
      if (!t.classList) return;
      // A foto do avatar e um arquivo local do cartao, nao um video de referencia:
      // mesma delegacao (o passo Aparencia nao e recriado, mas o handler que ja
      // existe evita um segundo listener para a mesma coisa), outro destino.
      if (t.classList.contains("tw-avatar-input")) {
        addTweetAvatar(t.files && t.files[0]);
        t.value = "";
      } else if (t.classList.contains("pv-upload-input")) {
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