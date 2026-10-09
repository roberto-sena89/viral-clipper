/* ---------- DOM MINIMO PARA OS TESTES DO PAINEL ----------

   Por que existe: os testes de `web/*.html` liam o ARQUIVO e passavam regex
   nele. Isso prova o que esta escrito na fonte, nao o que o usuario ve. No dia
   em que o rail e o rodape sairam do HTML para o `comum.js`, os testes
   continuaram verdes medindo um HTML que nao montava mais nada -- e a unica
   forma de nao repetir isso e' medir o DOM depois do JS rodar.

   Por que nao Playwright/jsdom: nenhum dos dois e' dependencia do projeto.
   O `requirements.txt` nao tem nada de DOM, e os testes tem de rodar numa
   maquina limpa com o `pip install -r` e mais nada. O Playwright ainda exigiria
   baixar um browser inteiro no CI. Este arquivo cobre exatamente as APIs que o
   `comum.js` usa (medidas, nao supostas) e nada mais; quando o JS usar uma API
   nova, o teste falha com "nao implementado" em vez de medir errado em
   silencio.

   Como funciona: um parser de HTML reduzido que constroi a arvore, mais um
   elemento com `querySelector`/`querySelectorAll` por seletor de CSS simples.
   Nao e' um browser e nao finge ser: e' o suficiente para "o rail montou?" e
   "quantos destinos existem?".

   Limitacoes conhecidas, e por isso declaradas:
   - seletor suportado: tag, .classe, #id, [attr], [attr="valor"], combinacoes
     dos quatro e descendente ("a b"). Sem irmao (+ ~) nem pseudo (:hover).
     `querySelector` levanta erro em vez de devolver null se receber algo fora
     disso -- um teste que passa por nao achar nada e' pior que um que falha.
   - sem layout: `getBoundingClientRect` devolve zeros. Geometria se mede no
     browser de verdade, com os probes do `sticky-probe/`.
   - sem eventos de verdade: `addEventListener` guarda e `dispatchEvent`
     chama, mas nao ha bubbling nem captura. Da' para testar "o clique no
     botao chama o handler", nao "o clique na folha fecha o menu por
     propagacao". */

'use strict';

const fs = require('fs');

// ---------- parser ----------

// As tags que abrem e fecham sozinhas no HTML que este projeto escreve. Sem
// elas o parser aninharia meio documento dentro de um <img>.
const VAZIAS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
  'param', 'source', 'track', 'wbr',
]);

const RE_COMENTARIO = /<!--[\s\S]*?-->/g;
const RE_TAG = /<\/?([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^<>]*?)?)\/?\s*>/g;
const RE_ATRIBUTO = /([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

class No {
  constructor(tag) {
    this.tagName = (tag || '').toUpperCase();
    this.atributos = new Map();
    this.filhos = [];
    this.pai = null;
    this.texto = '';        // so' para texto puro e para o que o JS escreveu
    this._innerHTML = null; // memoria do que foi setado, se foi
  }

  get id() { return this.getAttribute('id') || ''; }
  get className() { return this.getAttribute('class') || ''; }

  set className(v) { this.setAttribute('class', v); }

  get classList() {
    const dono = this;
    const lista = () => (dono.getAttribute('class') || '').split(/\s+/).filter(Boolean);
    const grava = (arr) => {
      dono.setAttribute('class', arr.join(' '));
      return dono;
    };
    return {
      contains: (c) => lista().includes(c),
      add: (...cs) => grava(Array.from(new Set(lista().concat(cs)))),
      remove: (...cs) => grava(lista().filter((x) => !cs.includes(x))),
      toggle: (c, on) => {
        const tem = lista().includes(c);
        const quer = on === undefined ? !tem : !!on;
        grava(quer ? Array.from(new Set(lista().concat([c])))
                   : lista().filter((x) => x !== c));
        return quer;
      },
    };
  }

  get children() { return this.filhos; }

  get textContent() {
    if (this._textoProprio !== undefined) return this._textoProprio;
    return this.filhos.map((f) => f.textContent).join('') + this.texto;
  }

  set textContent(v) {
    this.filhos = [];
    this.texto = '';
    this._textoProprio = String(v);
    this._innerHTML = null;
  }

  get innerHTML() {
    if (this._innerHTML !== null) return this._innerHTML;
    return this.filhos.map((f) => f.outerHTML).join('');
  }

  set innerHTML(v) {
    /* Parseia de verdade. A primeira versao so' guardava a string, e isso
       derrubava o proposito do harness: o `renderRail` escreve 1045 chars no
       `[data-rail-list]`, o teste contava `.rail-item` e achava ZERO -- o
       markup estava certo e a medicao, cega. Medir o que o JS produziu e' o
       ponto; guardar a string seria so' uma regex mais cara. */
    this._innerHTML = String(v);
    this.filhos = [];
    this.texto = '';
    this._textoProprio = undefined;
    const temp = parsear(String(v));
    for (const filho of temp.filhos.slice()) {
      filho.pai = this;
      this.filhos.push(filho);
    }
    // Depois de parsear, o `innerHTML` reconstroi de `filhos` para que o que
    // se le de volta case com o que foi escrito.
    this._innerHTML = null;
  }

  get outerHTML() {
    if (!this.tagName) return this.texto;
    const attrs = Array.from(this.atributos.entries())
      .map(([k, val]) => (val === '' ? ` ${k}` : ` ${k}="${val}"`)).join('');
    const nome = this.tagName.toLowerCase();
    //: Elementos VAZIOS nao tem fechamento. Sem esta lista o `innerHTML`
    //: devolvia `<img ...></img>`, que nao e' o que o navegador produz -- e o
    //: teste que compara a string acabava medindo o serializador, nao a pagina.
    const VAZIOS = ['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                    'link', 'meta', 'param', 'source', 'track', 'wbr'];
    if (VAZIOS.indexOf(nome) >= 0) return `<${nome}${attrs}>`;
    return `<${nome}${attrs}>${this.innerHTML}</${nome}>`;
  }

  setAttribute(nome, valor) {
    this.atributos.set(nome, String(valor));
    if (nome === 'hidden') this._hidden = true;
  }

  getAttribute(nome) {
    return this.atributos.has(nome) ? this.atributos.get(nome) : null;
  }

  hasAttribute(nome) { return this.atributos.has(nome); }

  removeAttribute(nome) {
    this.atributos.delete(nome);
    if (nome === 'hidden') this._hidden = false;
  }

  get hidden() {
    return this._hidden === true || this.atributos.has('hidden');
  }

  set hidden(v) {
    this._hidden = !!v;
    if (v) this.atributos.set('hidden', '');
    else this.atributos.delete('hidden');
  }

  appendChild(filho) {
    filho.pai = this;
    this.filhos.push(filho);
    this._innerHTML = null;
    return filho;
  }

  removeChild(filho) {
    const i = this.filhos.indexOf(filho);
    if (i >= 0) this.filhos.splice(i, 1);
    return filho;
  }

  remove() { if (this.pai) this.pai.removeChild(this); }

  addEventListener(tipo, fn) {
    (this._eventos || (this._eventos = {}))[tipo] =
      ((this._eventos || {})[tipo] || []).concat([fn]);
  }

  removeEventListener() {}

  dispatchEvent(ev) {
    const fns = (this._eventos || {})[ev.type] || [];
    fns.forEach((fn) => fn.call(this, ev));
    return true;
  }

  closest(seletor) {
    let no = this;
    while (no) {
      if (casa(no, seletor)) return no;
      no = no.pai;
    }
    return null;
  }

  matches(seletor) { return casa(this, seletor); }

  querySelector(seletor) {
    const achados = procurar(this, seletor, true);
    return achados.length ? achados[0] : null;
  }

  querySelectorAll(seletor) { return procurar(this, seletor, false); }

  getBoundingClientRect() {
    return { x: 0, y: 0, width: 0, height: 0, top: 0, right: 0, bottom: 0, left: 0 };
  }

  focus() { this._focado = true; }

  get style() { return (this._style || (this._style = {})); }

  get scrollTop() { return this._scrollTop || 0; }

  set scrollTop(v) { this._scrollTop = Number(v) || 0; }

  /* `dataset` do browser: `data-foo-bar` vira `dataset.fooBar`. O painel le
     `btn.dataset.filter` e `host.dataset.railList`, entao o camelCase tem de
     ser o de verdade -- um mapa cru por nome de atributo mediria undefined. */
  get dataset() {
    const dono = this;
    const paraCamel = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const alvo = {};
    for (const [k, v] of dono.atributos) {
      if (k.startsWith('data-')) alvo[paraCamel(k.slice(5))] = v;
    }
    return new Proxy(alvo, {
      get(t, prop) { return t[prop]; },
      set(t, prop, valor) {
        t[prop] = String(valor);
        dono.setAttribute('data-' + String(prop).replace(/[A-Z]/g, (c) => '-' + c.toLowerCase()), valor);
        return true;
      },
    });
  }

  get options() { return this.filhos.filter((f) => f.tagName === 'OPTION'); }

  get value() { return this.getAttribute('value') || ''; }

  set value(v) { this.setAttribute('value', v); }

  get selectedIndex() { return 0; }
}

function novoTexto(t) {
  const no = new No('');
  no.texto = t;
  return no;
}

// ---------- seletor ----------

function partesDoSeletor(sel) {
  // "tag.classe[attr=\"valor\"]#id" -> { tag, classes, id, attrs }
  sel = sel.trim();
  const alvo = { tag: null, classes: [], id: null, attrs: [] };
  if (!sel) throw new Error('seletor vazio');
  // Rejeita o que promete semantica que este DOM nao tem.
  if (/[\s>+~,:]/.test(sel.replace(/\[[^\]]*\]/g, ''))) {
    throw new Error(
      `seletor nao suportado pelo DOM de teste: "${sel}". ` +
      'Suportado: tag, .classe, #id, [attr], [attr="v"] e combinacoes dos ' +
      'quatro. Nao ha descendente, irmao nem pseudo.');
  }
  let resto = sel;
  const re = /(\[[^\]]*\]|\.[^.#\[]+|#[^.#\[]+)|^([a-zA-Z][a-zA-Z0-9-]*)/g;
  let m;
  let primeiro = true;
  while ((m = re.exec(resto)) !== null) {
    if (m[2]) { alvo.tag = m[2].toUpperCase(); primeiro = false; continue; }
    const tok = m[1];
    if (tok[0] === '.') alvo.classes.push(tok.slice(1));
    else if (tok[0] === '#') alvo.id = tok.slice(1);
    else {
      const dentro = tok.slice(1, -1);
      const eq = dentro.indexOf('=');
      if (eq < 0) alvo.attrs.push([dentro.trim(), null]);
      else {
        let v = dentro.slice(eq + 1).trim();
        if ((v[0] === '"' && v.endsWith('"')) || (v[0] === "'" && v.endsWith("'"))) {
          v = v.slice(1, -1);
        }
        alvo.attrs.push([dentro.slice(0, eq).trim(), v]);
      }
    }
  }
  if (!alvo.tag && !alvo.classes.length && !alvo.id && !alvo.attrs.length) {
    throw new Error(`seletor nao entendido: "${sel}"`);
  }
  return alvo;
}

function casa(no, sel) {
  if (!no.tagName) return false;
  const a = partesDoSeletor(sel);
  if (a.tag && no.tagName !== a.tag) return false;
  if (a.id && no.id !== a.id) return false;
  if (a.classes.length) {
    const cls = no.className.split(/\s+/).filter(Boolean);
    if (!a.classes.every((c) => cls.includes(c))) return false;
  }
  for (const [k, v] of a.attrs) {
    if (!no.hasAttribute(k)) return false;
    if (v !== null && no.getAttribute(k) !== v) return false;
  }
  return true;
}

function procurar(raiz, sel, umSo) {
  // Seletor descendente ("a b") e' o unico combinador que o painel usa, e
  // aparece de verdade (`[data-rail-picker] .menu-btn`). Resolve da direita
  // para a esquerda: acha todos os `b` e fica com os que tem um `a` acima.
  if (/\s/.test(sel.trim()) && !/\[[^\]]*\s[^\]]*\]/.test(sel)) {
    const partes = sel.trim().split(/\s+/);
    const direita = partes.pop();
    const candidatos = procurar(raiz, direita, false);
    const achados = candidatos.filter((no) => {
      let atual = no.pai;
      for (let i = partes.length - 1; i >= 0; i--) {
        let achou = false;
        let sobe = atual;
        while (sobe) {
          if (casa(sobe, partes[i])) { achou = true; atual = sobe.pai; break; }
          sobe = sobe.pai;
        }
        if (!achou) return false;
      }
      return true;
    });
    return umSo ? achados.slice(0, 1) : achados;
  }
  const achados = [];
  const pilha = raiz.filhos.slice();
  while (pilha.length) {
    const no = pilha.shift();
    if (casa(no, sel)) {
      achados.push(no);
      if (umSo) return achados;
    }
    pilha.unshift(...no.filhos);
  }
  return achados;
}

// ---------- parser de HTML ----------

function parsear(html) {
  html = html.replace(RE_COMENTARIO, '');
  const raiz = new No('#document');
  const pilha = [raiz];
  let pos = 0;
  let m;
  RE_TAG.lastIndex = 0;
  while ((m = RE_TAG.exec(html)) !== null) {
    const texto = html.slice(pos, m.index);
    if (texto.trim()) pilha[pilha.length - 1].appendChild(novoTexto(texto));
    pos = RE_TAG.lastIndex;

    const fechando = m[0][1] === '/';
    const nome = m[1].toLowerCase();
    const attrs = m[2] || '';

    if (fechando) {
      // Fecha ate' achar a tag correspondente (tolera aninhamento ja' fechado).
      for (let i = pilha.length - 1; i > 0; i--) {
        if (pilha[i].tagName === nome.toUpperCase()) {
          pilha.length = i;
          break;
        }
      }
      continue;
    }

    const no = new No(nome);
    let a;
    RE_ATRIBUTO.lastIndex = 0;
    while ((a = RE_ATRIBUTO.exec(attrs)) !== null) {
      const chave = a[1];
      const valor = a[2] !== undefined ? a[2]
        : a[3] !== undefined ? a[3]
        : a[4] !== undefined ? a[4] : '';
      no.setAttribute(chave, valor);
    }
    if (no.hasAttribute('hidden')) no._hidden = true;
    pilha[pilha.length - 1].appendChild(no);

    if (!VAZIAS.has(nome) && !m[0].endsWith('/>')) pilha.push(no);
  }
  const resto = html.slice(pos);
  if (resto.trim()) {
    // O que sobra depois do <script> e do </body> nao interessa ao teste.
  }
  return raiz;
}

// ---------- documento ----------

function criarDocumento(html) {
  const raiz = parsear(html);
  const doc = {
    _raiz: raiz,
    readyState: 'complete',
    title: '',
    documentElement: raiz,
    body: raiz.querySelector('body') || raiz,
    createElement: (tag) => new No(tag),
    createTextNode: (t) => novoTexto(t),
    querySelector: (s) => raiz.querySelector(s),
    querySelectorAll: (s) => raiz.querySelectorAll(s),
    getElementById: (id) => raiz.querySelector('#' + id),
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true,
  };
  return doc;
}

/* Monta uma pagina do painel e devolve o DOM DEPOIS do JS rodar.

   `html` e' o conteudo de um web/*.html. Cada `src` de <script> que nao seja
   http(s) e' lido de `web/` e executado na ordem em que aparece -- que e' a
   ordem em que o browser executaria, e a razao de o `comum.js` vir antes do
   script da pagina. */
function montarPagina(html, opcoes) {
  opcoes = opcoes || {};
  const dir = opcoes.dir || require('path').join(__dirname, '..', 'web');
  const caminho = require('path');
  const doc = criarDocumento(html);

  const janela = {
    document: doc,
    location: { pathname: opcoes.pathname || '/', href: '', search: '' },
    innerWidth: opcoes.innerWidth || 1280,
    innerHeight: opcoes.innerHeight || 900,
    isSecureContext: true,
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true,
    open: () => null,
    setTimeout: () => 0,
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: () => 0,
    matchMedia: () => ({ matches: false, addEventListener: () => {} }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
  };

  // A ordem dos <script> do documento, com o corpo de cada um.
  const scripts = [];
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const attrs = m[1] || '';
    const corpo = m[2] || '';
    const srcMatch = /\bsrc\s*=\s*["']([^"']+)["']/.exec(attrs);
    if (srcMatch) {
      const src = srcMatch[1];
      if (/^(https?:)?\/\//.test(src)) continue; // externo: nao busca
      const arquivo = src.split('?')[0].split('/').pop();
      // `soComum`: so' o /comum.js. Os scripts de pagina fazem fetch no load
      // (o `poll` do index.js, por exemplo) e o objetivo destes testes e' o
      // markup que o comum monta, nao o app inteiro rodando.
      if (opcoes.soComum && arquivo !== 'comum.js') continue;
      const caminhoCompleto = caminho.join(dir, arquivo);
      scripts.push({ arquivo, fonte: fs.readFileSync(caminhoCompleto, 'utf8') });
    } else if (corpo.trim()) {
      if (!opcoes.soComum) scripts.push({ arquivo: '(inline)', fonte: corpo });
    }
  }

  const erros = [];
  // O `comum.js` publica `esc`, `enhanceSelect`, `renderRail` em `window`, e os
  // scripts de pagina os chamam como identificadores SOLTOS (`esc(x)`, nao
  // `window.esc(x)`). No browser isso funciona porque `window` E' o escopo
  // global: toda propriedade dele e' um identificador visivel. O harness tem de
  // replicar isso, ou mediria um ReferenceError que o usuario nunca ve.
  const nomesGlobais = [];
  for (const s of scripts) {
    const antes = new Set(Object.keys(janela));
    try {
      const args = ['window', 'document', 'console'].concat(
        Array.from(janela.globalNames || []));
      const fonte = `"use strict";\n${s.fonte}\n//# sourceURL=${s.arquivo}`;
      const fn = new Function(...args, fonte);
      fn(janela, doc, opcoes.console || console,
         ...Array.from(janela.globalNames || []).map((n) => janela[n]));
      // Toda chave nova em `window` vira um nome global para os proximos
      // scripts. A lista vive em `globalNames` para nao colidir com as
      // propriedades reais de `window` (document, location, ...).
      const novas = Object.keys(janela).filter(
        (k) => !['document', 'location', 'navigator', 'innerWidth',
          'innerHeight', 'isSecureContext', 'addEventListener',
          'removeEventListener', 'dispatchEvent', 'open', 'setTimeout',
          'clearTimeout', 'setInterval', 'clearInterval',
          'requestAnimationFrame', 'matchMedia', 'getComputedStyle',
          'globalNames'].includes(k) && !antes.has(k));
      if (novas.length) {
        janela.globalNames = (janela.globalNames || []).concat(novas);
        nomesGlobais.push(...novas);
      }
    } catch (e) {
      erros.push({ arquivo: s.arquivo, erro: String(e && e.message || e) });
    }
  }

  return { documento: doc, janela, scripts, erros, globais: nomesGlobais };
}

module.exports = { parsear, criarDocumento, montarPagina, No };
